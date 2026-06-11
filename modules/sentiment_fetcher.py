"""
MODULE: sentiment_fetcher.py

COINGECKO STRATEGY — Binance-first approach:
  We only care about the ~600 coins listed on Binance Futures.
  No need to download 14,000 CoinGecko IDs.

  Step 1 (once, saved to disk): Resolve CoinGecko IDs for Binance coins only
           → uses /coins/markets?ids=... with symbols from Binance
           → CoinGecko /coins/markets accepts symbol search too
           → saves to data/cg_id_map.json (~600 entries, not 14,000)
           → refreshed automatically when new symbols appear

  Step 2 (once per scan): Batch fetch ALL market data in 1-3 API calls
           → /coins/markets?ids=bitcoin,ethereum,... (250 per call)
           → ~600 Binance coins = 3 calls maximum
           → results cached in memory for the duration of the scan

  Step 3 (per coin): get_market_data() reads from cache — 0 API calls

  For coins not found on CoinGecko (brand new listings, derivatives-only):
           → returns empty dict tagged mc_unknown=True
           → still included in scan (new listings often pump hardest)
"""

import os
import json
import time
import requests
from typing import Optional
from modules.logger import get_logger
import config

log = get_logger("sentiment")

ID_MAP_PATH = "data_ls/cg_id_map.json"

# Runtime caches
_cg_id_map:       dict  = {}   # coin_ticker → coingecko_id  (loaded from disk)
_cg_market_cache: dict  = {}   # SYMBOL → parsed market data dict (per-scan)
_id_map_loaded:   bool  = False


# Fallback ID map — covers ~200 most traded Binance Futures coins
# Used immediately while the full map is being downloaded, or if download fails
_FALLBACK_IDS = {
    "btc":"bitcoin","eth":"ethereum","sol":"solana","bnb":"binancecoin",
    "xrp":"ripple","ada":"cardano","avax":"avalanche-2","dot":"polkadot",
    "matic":"matic-network","pol":"matic-network","near":"near",
    "atom":"cosmos","ftm":"fantom","egld":"elrond-erd-2","algo":"algorand",
    "icp":"internet-computer","hbar":"hedera-hashgraph","flow":"flow",
    "vet":"vechain","xlm":"stellar","xtz":"tezos","eos":"eos","trx":"tron",
    "ton":"the-open-network","ltc":"litecoin","bch":"bitcoin-cash",
    "etc":"ethereum-classic","xmr":"monero","zec":"zcash","dash":"dash",
    "arb":"arbitrum","op":"optimism","lrc":"loopring","imx":"immutable-x",
    "strk":"starknet","manta":"manta-network","metis":"metis-token",
    "mnt":"mantle","zil":"zilliqa","cfx":"conflux-token",
    "uni":"uniswap","aave":"aave","crv":"curve-dao-token","mkr":"maker",
    "comp":"compound-governance-token","snx":"synthetix-network-token",
    "ldo":"lido-dao","bal":"balancer","sushi":"sushi","cake":"pancakeswap-token",
    "1inch":"1inch","gmx":"gmx","dydx":"dydx","gns":"gains-network",
    "pendle":"pendle","rdnt":"radiant-capital","ssv":"ssv-network",
    "rpl":"rocket-pool","cvx":"convex-finance",
    "fet":"fetch-ai","rndr":"render-token","ocean":"ocean-protocol",
    "grt":"the-graph","agix":"singularitynet","wld":"worldcoin-wld",
    "akt":"akash-network","io":"io-net","tao":"bittensor",
    "axs":"axie-infinity","sand":"the-sandbox","mana":"decentraland",
    "enj":"enjincoin","ilv":"illuvium","gala":"gala",
    "pixel":"pixels","beam":"beam-2","ygg":"yield-guild-games",
    "link":"chainlink","band":"band-protocol","api3":"api3",
    "fil":"filecoin","ar":"arweave","storj":"storj","hnt":"helium",
    "iotx":"iotex","ankr":"ankr",
    "sui":"sui","apt":"aptos","sei":"sei-network","inj":"injective-protocol",
    "tia":"celestia","pyth":"pyth-network","jup":"jupiter-ag",
    "jto":"jito-governance-token","wif":"dogwifcoin","bonk":"bonk",
    "popcat":"popcat","mew":"cat-in-a-dogs-world","bome":"book-of-meme",
    "hype":"hyperliquid","eigen":"eigenlayer","ena":"ethena",
    "w":"wormhole","zro":"layerzero","kava":"kava","celo":"celo",
    "core":"coredaoorg","btt":"bittorrent-new",
    "gal":"galxe","id":"space-id","arkm":"arkham",
    "doge":"dogecoin","shib":"shiba-inu","pepe":"pepe","floki":"floki",
    "ape":"apecoin","gmt":"stepn","blur":"blur","magic":"magic",
    "spell":"spell-token","joe":"traderjoe","ray":"raydium",
    "neo":"neo","iota":"iota","waves":"waves","icx":"icon","lsk":"lisk",
    "zeta":"zetachain","rose":"oasis-network","ens":"ethereum-name-service",
    "kcs":"kucoin-token","okb":"okb","ht":"huobi-token","gt":"gate-token",
    "btt":"bittorrent-new","win":"wink","chr":"chromaway",
    "alice":"my-neighbor-alice","gods":"gods-unchained","tlm":"alien-worlds",
    "oxy":"oxygen","atlas":"star-atlas","polis":"star-atlas-dao",
    "steth":"staked-ether","reth":"rocket-pool-eth",
    "sc":"siacoin","dgb":"digibyte","sys":"syscoin","ark":"ark",
    "ont":"ontology","nano":"nano","dcr":"decred","zen":"zencash",
    "neiro":"first-neiro-on-ethereum","goat":"goat",
    "act":"act-i-the-ai-prophecy","strax":"stratis",
}


def _get(url: str, params: dict = {}, headers: dict = {},
         retries: int = 2) -> Optional[any]:
    """HTTP GET with automatic 429 retry backoff."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers,
                             timeout=config.REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = (attempt + 1) * 60  # 60s, 120s
                log.warning(f"CoinGecko rate limited, waiting {wait}s (attempt {attempt+1}/{retries+1})...")
                time.sleep(wait)
                continue
            log.warning(f"HTTP {r.status_code} from {url[:60]}")
            return None
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                continue
            log.warning(f"Request failed {url[:60]}: {e}")
            return None
    return None


# ══════════════════════════════════════════════════════════════════════
#  STEP 1 — BUILD COMPLETE COIN ID MAP (once ever, saved to disk)
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  STEP 1 — BUILD ID MAP FOR BINANCE COINS ONLY (not all 14k CoinGecko)
# ══════════════════════════════════════════════════════════════════════

def _load_id_map() -> None:
    """Load saved ID map from disk."""
    global _cg_id_map, _id_map_loaded
    os.makedirs("data", exist_ok=True)
    if os.path.exists(ID_MAP_PATH):
        try:
            with open(ID_MAP_PATH) as f:
                _cg_id_map = json.load(f)
            _id_map_loaded = True
            log.info(f"CoinGecko ID map loaded: {len(_cg_id_map)} Binance coins")
        except Exception as e:
            log.warning(f"Failed to load ID map: {e}")


def build_binance_id_map(symbols: list = None, force: bool = False) -> int:
    """
    Builds the CoinGecko ID map for all Binance Futures coins.

    METHOD: Download ALL ~14,000 CoinGecko coin IDs in ONE single API call
            via /coins/list, then filter to only the coins on Binance.

    Why /coins/list instead of individual searches:
      - Individual search: 416 coins × 1 API call each = 416 calls → rate limited
      - /coins/list: ALL 14,000 coins in 1 call → no rate limit issues
      - The file is ~1.5MB but only downloaded once, saved to disk forever
      - On subsequent runs: loads from disk in milliseconds

    The map is only re-downloaded when:
      - data/cg_id_map.json doesn't exist (first run)
      - force=True (/refresh_ids command)
      - New Binance symbols appear that aren't in the map (auto-detected)
    """
    global _cg_id_map, _id_map_loaded

    # Load existing map
    existing = {}
    if os.path.exists(ID_MAP_PATH) and not force:
        try:
            with open(ID_MAP_PATH) as f:
                existing = json.load(f)
        except Exception:
            pass

    merged = {**_FALLBACK_IDS, **existing}

    # Determine which Binance coins need resolving
    if symbols:
        coins_to_resolve = [
            sym.replace("USDT", "").lower()
            for sym in symbols
            if sym.replace("USDT", "").lower() not in merged
        ]
    else:
        coins_to_resolve = []

    if not coins_to_resolve and not force:
        _cg_id_map    = merged
        _id_map_loaded = True
        return len(merged)

    # ── ONE API CALL: download all ~14,000 CoinGecko IDs ──────────────
    # This is much better than 400+ individual search calls
    log.info(f"Downloading CoinGecko coin list (1 API call for all ~14k coins)...")

    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/coins/list",
                params={"include_platform": "false"},
                timeout=30
            )
            if r.status_code == 200:
                all_coins = r.json()
                break
            elif r.status_code == 429:
                wait = (attempt + 1) * 60
                log.warning(f"CoinGecko /coins/list rate limited, waiting {wait}s...")
                time.sleep(wait)
                all_coins = None
            else:
                log.warning(f"CoinGecko /coins/list returned HTTP {r.status_code}")
                all_coins = None
                break
        except Exception as e:
            log.warning(f"CoinGecko /coins/list failed: {e}")
            all_coins = None
            break
    else:
        all_coins = None

    if not all_coins:
        log.warning("Could not download CoinGecko coin list — using fallback map only")
        _cg_id_map    = merged
        _id_map_loaded = True
        return len(merged)

    # Build symbol → id lookup from the full list
    # For duplicate symbols, prefer the shorter ID (e.g. "bitcoin" over "bitcoin-sv-abc-1")
    full_map = {}
    for coin in all_coins:
        sym = coin.get("symbol", "").lower().strip()
        cid = coin.get("id", "").strip()
        if not sym or not cid:
            continue
        if sym not in full_map or len(cid) < len(full_map[sym]):
            full_map[sym] = cid

    log.info(f"CoinGecko coin list: {len(full_map)} unique symbols downloaded")

    # Now resolve only the Binance coins we care about
    new_found = 0
    still_unknown = coins_to_resolve if not force else [
        sym.replace("USDT", "").lower()
        for sym in (symbols or list(merged.keys()))
    ]

    for coin in still_unknown:
        if coin in full_map:
            merged[coin] = full_map[coin]
            new_found   += 1
        # If not found in full_map: coin genuinely not on CoinGecko
        # (new listing, derivatives-only, tokenized asset etc.) — skip silently

    not_found = len(still_unknown) - new_found
    log.info(
        f"Resolved: {new_found} coins mapped | "
        f"{not_found} not on CoinGecko (new listings / derivatives)"
    )

    # Save to disk
    os.makedirs("data", exist_ok=True)
    with open(ID_MAP_PATH, "w") as f:
        json.dump(merged, f)

    _cg_id_map    = merged
    _id_map_loaded = True
    log.info(f"ID map saved: {len(merged)} coins → {ID_MAP_PATH}")
    return len(merged)




def _ensure_id_map() -> None:
    """Load map from disk or use fallback. Never downloads 14k coins."""
    global _cg_id_map, _id_map_loaded
    if not _id_map_loaded:
        if os.path.exists(ID_MAP_PATH):
            _load_id_map()
        else:
            # Use fallback immediately — build_binance_id_map called later
            # from prefetch_coingecko_batch which has the symbol list
            _cg_id_map    = dict(_FALLBACK_IDS)
            _id_map_loaded = True


# Keep backward compatibility
def build_full_id_map(force: bool = False) -> int:
    """Backward compat wrapper — now builds Binance-only map."""
    return build_binance_id_map(symbols=None, force=force)


def get_cg_id(symbol: str) -> Optional[str]:
    """Get CoinGecko ID for a Binance symbol like SOLUSDT → 'solana'."""
    _ensure_id_map()
    coin = symbol.replace("USDT", "").lower().strip()
    return _cg_id_map.get(coin)


# Backward compat
def _get_cg_id(symbol: str) -> Optional[str]:
    return get_cg_id(symbol)


# ══════════════════════════════════════════════════════════════════════
#  STEP 2 — BATCH MARKET DATA FETCH (once per scan, 1-3 API calls)
# ══════════════════════════════════════════════════════════════════════

def prefetch_coingecko_batch(symbols: list) -> dict:
    """
    Called ONCE per scan with all Binance symbols.
    1. Checks for any symbols not yet in our ID map → resolves them
    2. Fetches market data for all known symbols in chunks of 250
    3. Results cached in _cg_market_cache for the scan

    Max API calls: ~600 Binance coins / 250 per call = 3 calls
    Unknown coins (not on CoinGecko): tagged mc_unknown=True
    """
    global _cg_market_cache

    _ensure_id_map()
    _cg_market_cache = {}

    if not symbols:
        return {}

    # Find symbols not yet resolved in our map
    unresolved = [
        sym for sym in symbols
        if sym.replace("USDT", "").lower() not in _cg_id_map
    ]

    # Resolve new symbols (new Binance listings since last run)
    if unresolved:
        log.info(f"Found {len(unresolved)} new Binance symbols not in ID map — resolving...")
        build_binance_id_map(symbols=unresolved, force=False)

    # Now build (symbol, cg_id) pairs for all known symbols
    known   = []
    unknown = []
    for sym in symbols:
        cg_id = get_cg_id(sym)
        if cg_id:
            known.append((sym, cg_id))
        else:
            unknown.append(sym)
            _cg_market_cache[sym] = None  # mark as not on CoinGecko

    log.info(
        f"CoinGecko batch: {len(known)} resolved, "
        f"{len(unknown)} not on CoinGecko (new listings / derivatives-only)"
    )

    if not known:
        return {}

    # Batch fetch market data — 250 coins per call
    CHUNK         = 250
    total_fetched = 0

    for i in range(0, len(known), CHUNK):
        chunk      = known[i:i + CHUNK]
        ids_str    = ",".join(cg_id for _, cg_id in chunk)
        chunk_num  = i // CHUNK + 1
        n_chunks   = (len(known) + CHUNK - 1) // CHUNK

        log.info(f"CoinGecko market data batch {chunk_num}/{n_chunks} ({len(chunk)} coins)...")

        # Retry with exponential backoff on 429
        data = None
        for attempt in range(3):
            try:
                r = requests.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency":             "usd",
                        "ids":                     ids_str,
                        "order":                   "market_cap_desc",
                        "per_page":                250,
                        "page":                    1,
                        "sparkline":               "false",
                        "price_change_percentage": "7d,30d",
                    },
                    timeout=config.REQUEST_TIMEOUT
                )
                if r.status_code == 200:
                    data = r.json()
                    break
                elif r.status_code == 429:
                    wait = (attempt + 1) * 60  # 60s, 120s, 180s
                    log.warning(f"CoinGecko rate limited on batch {chunk_num}, "
                                f"waiting {wait}s (attempt {attempt+1}/3)...")
                    time.sleep(wait)
                else:
                    log.warning(f"CoinGecko batch {chunk_num} HTTP {r.status_code}")
                    break
            except Exception as e:
                log.warning(f"CoinGecko batch {chunk_num} error: {e}")
                break

        if not data or not isinstance(data, list):
            log.warning(f"CoinGecko batch call {chunk_num} failed after retries")
            for sym, _ in chunk:
                _cg_market_cache[sym] = None
            if i + CHUNK < len(known):
                time.sleep(60)  # wait 60s before trying next chunk after failure
            continue

        id_to_data = {d["id"]: d for d in data}

        for sym, cg_id in chunk:
            if cg_id in id_to_data:
                _cg_market_cache[sym] = _parse_cg_market(id_to_data[cg_id])
                total_fetched += 1
            else:
                _cg_market_cache[sym] = None

        if i + CHUNK < len(known):
            time.sleep(5)   # 5s pause between successful chunks (CoinGecko is strict)

    hit_rate = total_fetched / len(symbols) * 100 if symbols else 0
    log.info(
        f"CoinGecko batch done: {total_fetched}/{len(symbols)} coins "
        f"({hit_rate:.0f}% coverage) in {(len(known)+CHUNK-1)//CHUNK} API call(s)"
    )

    return _cg_market_cache


# ══════════════════════════════════════════════════════════════════════
#  STEP 3 — GET MARKET DATA PER COIN (reads from cache, 0 API calls)
# ══════════════════════════════════════════════════════════════════════

def get_market_data(symbol: str) -> dict:
    """
    Returns market data from cache (populated by prefetch_coingecko_batch).
    Falls back to individual API call in --test / --diagnose mode.
    Returns empty dict with flags if coin not on CoinGecko.
    """
    # Try cache first
    cached = _cg_market_cache.get(symbol)
    if cached is not None:        # {} or real data — already fetched
        return cached
    if symbol in _cg_market_cache:  # explicitly None = not on CoinGecko
        return _unknown_mc_result(symbol)

    # Individual fallback (used in --test/--diagnose mode, no batch prefetch)
    cg_id = get_cg_id(symbol)
    if not cg_id:
        return _unknown_mc_result(symbol)

    data = _get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency":             "usd",
            "ids":                     cg_id,
            "price_change_percentage": "7d,30d",
        }
    )
    if not data or not isinstance(data, list) or not data:
        return _unknown_mc_result(symbol)

    result = _parse_cg_market(data[0])
    _cg_market_cache[symbol] = result
    return result


def _unknown_mc_result(symbol: str) -> dict:
    """Returned when a coin is not found on CoinGecko."""
    return {
        "market_cap_usd":       None,
        "circulating_supply":   None,
        "total_supply":         None,
        "fully_diluted_val":    None,
        "small_market_cap":     True,   # assume small cap if unknown
        "low_float":            False,
        "float_pct":            None,
        "coingecko_id":         None,
        "total_volume_all_exchanges": None,
        "mc_unknown":           True,   # flag: not on CoinGecko
    }


def _parse_cg_market(d: dict) -> dict:
    mc  = d.get("market_cap")
    cs  = d.get("circulating_supply")
    ts  = d.get("total_supply")
    fdv = d.get("fully_diluted_valuation")
    p7  = d.get("price_change_percentage_7d_in_currency")
    p30 = d.get("price_change_percentage_30d_in_currency")
    low_float = bool(cs and ts and ts > 0 and cs / ts < 0.30)
    return {
        "market_cap_usd":       mc,
        "circulating_supply":   cs,
        "total_supply":         ts,
        "fully_diluted_val":    fdv,
        "small_market_cap":     bool(mc and mc < config.MAX_MARKET_CAP_USD),
        "low_float":            low_float,
        "float_pct":            round(cs / ts * 100, 1) if cs and ts and ts > 0 else None,
        "coingecko_id":         d.get("id"),
        "total_volume_all_exchanges": d.get("total_volume"),
        "price_change_7d":      round(p7, 2) if p7 else None,
        "price_change_30d":     round(p30, 2) if p30 else None,
        "mc_unknown":           False,
        # Community data is included in /coins/markets response for free
        # No extra API call needed
        "twitter_followers":    d.get("twitter_followers_count"),
        "reddit_subscribers":   d.get("subreddit_subscribers"),
        "sentiment_up_pct":     d.get("sentiment_votes_up_percentage"),
    }


# ══════════════════════════════════════════════════════════════════════
#  FEAR & GREED
# ══════════════════════════════════════════════════════════════════════

def get_fear_greed() -> dict:
    if not config.ENABLE_FEAR_GREED:
        return {"fear_greed_value": None, "fear_greed_label": None, "fear_greed_low": False}
    data = _get("https://api.alternative.me/fng/?limit=1")
    if not data or "data" not in data:
        return {"fear_greed_value": None, "fear_greed_label": None, "fear_greed_low": False}
    latest = data["data"][0]
    value  = int(latest.get("value", 50))
    label  = latest.get("value_classification", "Unknown")
    return {
        "fear_greed_value": value,
        "fear_greed_label": label,
        "fear_greed_low":   value <= 35,
    }


# ══════════════════════════════════════════════════════════════════════
#  SOCIAL — LunarCrush / CoinGecko community
# ══════════════════════════════════════════════════════════════════════

def get_lunarcrush_data(symbol: str) -> dict:
    if not config.ENABLE_LUNARCRUSH or not config.LUNARCRUSH_API_KEY:
        return get_coingecko_community(symbol)
    coin    = symbol.replace("USDT", "").lower()
    headers = {"Authorization": f"Bearer {config.LUNARCRUSH_API_KEY}"}
    try:
        r = requests.get(
            f"https://lunarcrush.com/api4/public/coins/{coin}/v1",
            headers=headers,
            timeout=config.REQUEST_TIMEOUT
        )
        if r.status_code == 401:
            log.debug(f"LunarCrush 401 for {coin} — key invalid or expired")
            return get_coingecko_community(symbol)
        if r.status_code == 429:
            log.debug(f"LunarCrush rate limited for {coin}")
            return get_coingecko_community(symbol)
        if r.status_code != 200:
            return get_coingecko_community(symbol)
        data = r.json()
    except Exception:
        return get_coingecko_community(symbol)
    if not data or "data" not in data:
        return get_coingecko_community(symbol)
    d     = data["data"]
    delta = d.get("social_volume_24h_percent_change")
    return {
        "galaxy_score":     d.get("galaxy_score"),
        "alt_rank":         d.get("alt_rank"),
        "social_vol_24h":   d.get("social_volume_24h"),
        "social_delta_pct": delta,
        "social_spike":     bool(delta and delta > 50),
        "source":           "lunarcrush",
    }


def get_coingecko_community(symbol: str) -> dict:
    """
    Returns community data from the batch cache (already fetched in prefetch_coingecko_batch).
    Zero extra API calls — data comes from /coins/markets which we already call.
    The individual /coins/{id} endpoint is NOT used — it causes 429 errors at scale.
    """
    cached = _cg_market_cache.get(symbol)
    if cached:
        return {
            "twitter_followers":  cached.get("twitter_followers"),
            "reddit_subscribers": cached.get("reddit_subscribers"),
            "sentiment_up_pct":   cached.get("sentiment_up_pct"),
            "social_spike":       False,
            "source":             "coingecko_batch",
        }
    # If not in cache (e.g. --test mode), return empty — avoids rate limit
    return {"social_spike": False, "source": "none"}


# ══════════════════════════════════════════════════════════════════════
#  GOOGLE TRENDS
# ══════════════════════════════════════════════════════════════════════

_gt_last_call: float = 0
_GT_MIN_INTERVAL = 15.0  # minimum seconds between Google Trends calls


def get_google_trends(symbol: str) -> dict:
    """
    Google Trends via pytrends.
    Rate limited to 1 call per 15 seconds to avoid 429s.
    Disabled by default (ENABLE_GOOGLE_TRENDS = False in config).
    Enable only if you don't mind slower scans.
    """
    global _gt_last_call
    if not config.ENABLE_GOOGLE_TRENDS:
        return {"google_trend_spike": False, "google_trend_score": None}

    # Rate limit: don't call more than once per 15 seconds
    elapsed = time.time() - _gt_last_call
    if elapsed < _GT_MIN_INTERVAL:
        return {"google_trend_spike": False, "google_trend_score": None}

    try:
        from pytrends.request import TrendReq
        coin = symbol.replace("USDT", "")
        pt   = TrendReq(hl="en-US", tz=0, timeout=(10, 25),
                        retries=1, backoff_factor=1.0)
        pt.build_payload([coin], timeframe="now 7-d", geo="")
        df   = pt.interest_over_time()
        _gt_last_call = time.time()

        if df is None or df.empty or coin not in df.columns:
            return {"google_trend_spike": False, "google_trend_score": None}
        series = df[coin]
        cur    = float(series.iloc[-1])
        avg    = float(series[:-1].mean())
        return {
            "google_trend_score": round(cur, 1),
            "google_trend_avg":   round(avg, 1),
            "google_trend_spike": bool(cur > avg * 1.5 and cur > 20),
        }
    except ImportError:
        return {"google_trend_spike": False, "google_trend_score": None}
    except Exception as e:
        if "429" in str(e) or "Too Many" in str(e):
            log.debug(f"Google Trends rate limited for {symbol} — skipping")
        else:
            log.debug(f"Google Trends failed for {symbol}: {e}")
        return {"google_trend_spike": False, "google_trend_score": None}


# ══════════════════════════════════════════════════════════════════════
#  TOKEN UNLOCK — CoinMarketCal
# ══════════════════════════════════════════════════════════════════════

def get_token_unlock_risk(symbol: str) -> dict:
    if not config.ENABLE_COINMARKETCAL or not config.COINMARKETCAL_API_KEY:
        return {"unlock_risk": False, "unlock_event": None}
    try:
        coin = symbol.replace("USDT", "")
        r    = requests.get(
            "https://developers.coinmarketcal.com/v1/events",
            headers={"x-api-key": config.COINMARKETCAL_API_KEY,
                     "Accept": "application/json",
                     "Accept-Encoding": "deflate, gzip"},
            params={"coins": coin, "max": 10,
                    "dateRangeStart": "today", "dateRangeEnd": "+30d"},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        events   = r.json().get("body", [])
        keywords = ["unlock", "vesting", "release", "cliff", "token release"]
        for ev in events:
            title = ev.get("title", {}).get("en", "").lower()
            if any(kw in title for kw in keywords):
                return {
                    "unlock_risk":  True,
                    "unlock_event": ev.get("title", {}).get("en", ""),
                    "unlock_date":  ev.get("date_event", ""),
                }
    except Exception as e:
        log.warning(f"CoinMarketCal failed for {symbol}: {e}")
    return {"unlock_risk": False, "unlock_event": None}


# ══════════════════════════════════════════════════════════════════════
#  PAID: GLASSNODE / CRYPTOQUANT
# ══════════════════════════════════════════════════════════════════════

def get_glassnode_data(symbol: str) -> dict:
    if not config.ENABLE_GLASSNODE or not config.GLASSNODE_API_KEY:
        return {}
    coin    = symbol.replace("USDT", "").lower()
    result  = {}
    metrics = {
        "new_addresses":    "addresses/new_non_zero_count",
        "active_addresses": "addresses/active_count",
        "exchange_outflow": "transactions/transfers_volume_from_exchanges_sum",
        "exchange_inflow":  "transactions/transfers_volume_to_exchanges_sum",
    }
    for key, endpoint in metrics.items():
        try:
            r = requests.get(
                f"https://api.glassnode.com/v1/metrics/{endpoint}",
                params={"a": coin, "i": "24h", "f": "JSON",
                        "api_key": config.GLASSNODE_API_KEY},
                timeout=config.REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                d = r.json()
                if d and isinstance(d, list):
                    result[f"glassnode_{key}"] = d[-1].get("v")
        except Exception as e:
            log.debug(f"Glassnode {key} failed: {e}")
    inflow  = result.get("glassnode_exchange_inflow") or 0
    outflow = result.get("glassnode_exchange_outflow") or 0
    if inflow and outflow:
        result["glassnode_net_flow"]     = round(outflow - inflow, 0)
        result["glassnode_accumulating"] = outflow > inflow
    return result


def get_cryptoquant_flow(symbol: str) -> dict:
    if not config.ENABLE_CRYPTOQUANT or not config.CRYPTOQUANT_API_KEY:
        return {}
    coin    = symbol.replace("USDT", "").lower()
    headers = {"Authorization": f"Bearer {config.CRYPTOQUANT_API_KEY}"}
    try:
        r = requests.get(
            f"https://api.cryptoquant.com/v1/{coin}/exchange-flows/netflow",
            headers=headers, params={"window": "day", "limit": 2},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("result", {}).get("data", [])
        if data:
            netflow = float(data[-1].get("netflow_total", 0))
            return {
                "cq_netflow":      round(netflow, 2),
                "cq_accumulating": netflow < 0,
            }
    except Exception as e:
        log.debug(f"CryptoQuant failed for {symbol}: {e}")
    return {}
