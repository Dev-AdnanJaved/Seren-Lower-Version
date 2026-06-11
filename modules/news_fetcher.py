"""
MODULE: news_fetcher.py
News and catalyst detection for each coin.

Sources:
  1. CryptoPanic     (free tier — best crypto news aggregator)
     https://cryptopanic.com/developers/api/
     Free: 100 requests/day, no key needed for basic
     With key: more requests + sentiment scores

  2. CoinGecko news  (free, no key — coin-specific news)

  3. Santiment       (paid — professional news + social signals)
     https://santiment.net/

Detects:
  - Exchange listing announcements  (huge pump catalyst)
  - Partnership/collaboration news
  - Mainnet/upgrade announcements
  - Negative news (hack, exploit, rug) — AVOID signal
  - General positive/negative sentiment score
"""

import requests
import time
from datetime import datetime, timezone, timedelta
from modules.logger import get_logger
import config

log = get_logger("news")

POSITIVE_KEYWORDS = [
    "listing", "listed", "launch", "mainnet", "upgrade", "partnership",
    "collaboration", "integration", "airdrop", "staking", "burn",
    "buyback", "investment", "fund", "grant", "milestone", "record",
    "adoption", "exchange", "binance", "coinbase", "okx", "bybit",
    "v2", "v3", "update", "release", "testnet", "announce",
]

NEGATIVE_KEYWORDS = [
    "hack", "exploit", "breach", "stolen", "rug", "scam", "fraud",
    "suspend", "delist", "delisted", "investigation", "sec", "lawsuit",
    "ban", "restrict", "shutdown", "exit", "dump", "ponzi", "fake",
]

LISTING_KEYWORDS = [
    "listing", "listed", "will list", "now live", "trading now",
    "binance lists", "coinbase lists", "okx lists", "bybit lists",
    "kraken lists", "gate lists", "huobi lists",
]


def _get(url, params={}, headers={}):
    try:
        r = requests.get(url, params=params, headers=headers,
                         timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"News fetch failed {url}: {e}")
        return None


# ─────────────────────────────────────────────
#  CRYPTOPANIC  (free tier, optional key)
# ─────────────────────────────────────────────
def get_cryptopanic_news(symbol: str) -> dict:
    """
    CryptoPanic removed free tier in 2024. Now paid only.
    This stub is kept for config compatibility.
    News detection now uses CoinGecko status updates + CryptoCompare.
    """
    return {"news_score": 0, "news_catalyst": False, "news_listing": False,
            "news_negative": False, "news_headlines": [], "news_source": "disabled"}


def get_cryptocompare_news(symbol: str) -> dict:
    """
    Free crypto news from CryptoCompare — no key needed.
    Returns news sentiment and catalyst detection.
    """
    coin = symbol.replace("USDT", "").upper()
    data = _get("https://min-api.cryptocompare.com/data/v2/news/",
                params={"categories": coin, "sortOrder": "latest"})
    if not data or data.get("Type") != 100:
        return {"news_score": 0, "news_catalyst": False,
                "news_listing": False, "news_negative": False, "news_headlines": []}

    articles  = data.get("Data", [])[:10]
    headlines = []
    pos_count = 0
    neg_count = 0
    listing   = False
    catalyst  = False
    negative  = False
    cutoff    = time.time() - (config.NEWS_LOOKBACK_HOURS * 3600)

    for art in articles:
        if art.get("published_on", 0) < cutoff:
            continue
        title = art.get("title", "").lower()
        headlines.append(art.get("title", ""))

        if any(kw in title for kw in POSITIVE_KEYWORDS):
            pos_count += 1
            catalyst = True
        if any(kw in title for kw in LISTING_KEYWORDS):
            listing  = True
            catalyst = True
        if any(kw in title for kw in NEGATIVE_KEYWORDS):
            neg_count += 1
            negative  = True

    return {
        "news_score":     pos_count - (neg_count * 2),
        "news_catalyst":  catalyst,
        "news_listing":   listing,
        "news_negative":  negative,
        "news_headlines": headlines[:3],
        "news_source":    "cryptocompare",
    }


# ─────────────────────────────────────────────
#  COINGECKO NEWS  (free, no key)
# ─────────────────────────────────────────────
def get_coingecko_news(symbol: str) -> dict:
    """
    Fallback news from CoinGecko status updates.
    Less detailed than CryptoPanic but always free.
    """
    if not config.ENABLE_COINGECKO_NEWS:
        return {}

    from modules.sentiment_fetcher import _get_cg_id
    coin_id = _get_cg_id(symbol)
    if not coin_id:
        return {}

    data = _get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/status_updates",
                params={"per_page": 5, "page": 1})
    if not data or "status_updates" not in data:
        return {}

    updates = data["status_updates"]
    if not updates:
        return {}

    recent = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    for u in updates:
        try:
            dt = datetime.strptime(u.get("created_at", "")[:19], "%Y-%m-%dT%H:%M:%S")
            if dt > cutoff:
                recent.append(u.get("description", ""))
        except Exception:
            pass

    return {
        "cg_status_updates": len(recent),
        "cg_recent_update":  bool(recent),
        "cg_update_text":    recent[0][:100] if recent else None,
    }


# ─────────────────────────────────────────────
#  SANTIMENT  (paid — professional grade)
# ─────────────────────────────────────────────
def get_santiment_data(symbol: str) -> dict:
    """
    Santiment provides:
    - Social dominance (% of all crypto talk about this coin)
    - Development activity (GitHub commits)
    - Network growth (new addresses — better than Glassnode free tier)
    - Whale transaction count
    Sign up: https://app.santiment.net/
    """
    if not config.ENABLE_SANTIMENT or not config.SANTIMENT_API_KEY:
        return {}

    coin   = symbol.replace("USDT", "").lower()
    # Santiment uses GraphQL
    query  = """
    {
      getMetric(metric: "social_dominance_total") {
        timeseriesData(
          slug: "%s"
          from: "utcNow-1d"
          to: "utcNow"
          interval: "1d"
        ) { datetime value }
      }
    }
    """ % coin

    try:
        r = requests.post(
            "https://api.santiment.net/graphql",
            json={"query": query},
            headers={"Authorization": f"Apikey {config.SANTIMENT_API_KEY}"},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        ts   = data.get("data", {}).get("getMetric", {}).get("timeseriesData", [])
        if ts:
            dom = float(ts[-1].get("value", 0))
            return {
                "santiment_social_dom":    round(dom, 4),
                "santiment_dom_high":      dom > 1.0,  # >1% of all crypto talk
            }
    except Exception as e:
        log.warning(f"Santiment failed for {symbol}: {e}")

    return {}


# ─────────────────────────────────────────────
#  NANSEN  (paid — whale wallet tracking)
# ─────────────────────────────────────────────
def get_nansen_data(symbol: str) -> dict:
    """
    Nansen tracks labeled whale wallets.
    Smart Money buying = very strong signal.
    Sign up: https://www.nansen.ai/ (~$150/month)
    """
    if not config.ENABLE_NANSEN or not config.NANSEN_API_KEY:
        return {}

    coin = symbol.replace("USDT", "").lower()
    try:
        r = requests.get(
            f"https://api.nansen.ai/v1/token/{coin}/smart-money",
            headers={"apiKey": config.NANSEN_API_KEY},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        net_flow = data.get("netFlow24h", 0)
        return {
            "nansen_smart_money_flow":    round(float(net_flow), 0),
            "nansen_smart_money_buying":  float(net_flow) > 0,
            "nansen_whale_count":         data.get("uniqueWhales24h"),
        }
    except Exception as e:
        log.warning(f"Nansen failed for {symbol}: {e}")
    return {}


# ─────────────────────────────────────────────
#  ARKHAM INTELLIGENCE  (paid — on-chain whale tracking)
# ─────────────────────────────────────────────
def get_arkham_data(symbol: str) -> dict:
    """
    Arkham tracks labeled wallet entities (funds, exchanges, whales).
    Sign up: https://platform.arkhamintelligence.com/
    """
    if not config.ENABLE_ARKHAM or not config.ARKHAM_API_KEY:
        return {}

    coin = symbol.replace("USDT", "").lower()
    try:
        r = requests.get(
            "https://api.arkhamintelligence.com/token/flow",
            headers={"API-Key": config.ARKHAM_API_KEY},
            params={"token": coin, "timeframe": "24h"},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "arkham_whale_inflow":   data.get("whaleInflow"),
            "arkham_whale_outflow":  data.get("whaleOutflow"),
            "arkham_exchange_flow":  data.get("exchangeNetFlow"),
            "arkham_whales_buying":  bool(
                data.get("whaleInflow", 0) > data.get("whaleOutflow", 0)
            ),
        }
    except Exception as e:
        log.warning(f"Arkham failed for {symbol}: {e}")
    return {}


# ─────────────────────────────────────────────
#  TWITTER/X MENTIONS  (paid — Twitter API v2)
# ─────────────────────────────────────────────
def get_twitter_mentions(symbol: str) -> dict:
    """
    Counts recent tweets mentioning the coin.
    Twitter API v2 Basic = $100/month minimum.
    Sign up: https://developer.twitter.com/
    Fallback: LunarCrush covers this on free tier.
    """
    if not config.ENABLE_TWITTER or not config.TWITTER_BEARER_TOKEN:
        return {}

    coin  = symbol.replace("USDT", "")
    query = f"#{coin} OR ${coin} lang:en -is:retweet"
    try:
        r = requests.get(
            "https://api.twitter.com/2/tweets/counts/recent",
            headers={"Authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}"},
            params={"query": query, "granularity": "hour"},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data    = r.json()
        buckets = data.get("data", [])
        if not buckets:
            return {}
        recent_count = sum(b.get("tweet_count", 0) for b in buckets[-6:])   # last 6h
        older_count  = sum(b.get("tweet_count", 0) for b in buckets[-24:-6])  # 6-24h ago
        avg_older    = older_count / 18 if older_count else 1
        avg_recent   = recent_count / 6
        spike_ratio  = avg_recent / avg_older if avg_older > 0 else 1
        return {
            "twitter_mentions_6h":    recent_count,
            "twitter_mention_spike":  bool(spike_ratio > 2.0),
            "twitter_spike_ratio":    round(spike_ratio, 2),
        }
    except Exception as e:
        log.warning(f"Twitter API failed for {symbol}: {e}")
    return {}


# ─────────────────────────────────────────────
#  TELEGRAM COMMUNITY ACTIVITY
#  (approximated via CoinGecko + CryptoCompare)
# ─────────────────────────────────────────────
def get_telegram_activity(symbol: str) -> dict:
    """
    True Telegram group monitoring requires being IN the group
    and using Telegram's Bot API — no public API exists.

    Best approximation: CoinGecko telegram_channel_user_count
    and CryptoCompare Telegram data (both free).
    """
    from modules.sentiment_fetcher import _get_cg_id
    coin_id = _get_cg_id(symbol)
    result  = {}

    # CoinGecko community data includes Telegram subscribers
    if coin_id:
        data = _get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={"localization": "false", "tickers": "false",
                    "market_data": "false", "community_data": "true",
                    "developer_data": "false"}
        )
        if data:
            cd = data.get("community_data", {})
            result["telegram_channel_user_count"] = cd.get("telegram_channel_user_count")

    # CryptoCompare Telegram stats (free)
    coin = symbol.replace("USDT", "")
    cc_data = _get("https://min-api.cryptocompare.com/data/social/coin/latest",
                   params={"coinId": coin})
    if cc_data and cc_data.get("Response") != "Error":
        tg = cc_data.get("Data", {}).get("Telegram", {})
        if tg:
            result["cc_telegram_members"]    = tg.get("members")
            result["cc_telegram_online"]     = tg.get("online_members")

    return result


# ─────────────────────────────────────────────
#  GLASSNODE — on-chain (paid, with free fallback)
# ─────────────────────────────────────────────
def get_glassnode_onchain(symbol: str) -> dict:
    """
    Glassnode on-chain metrics:
    - New non-zero addresses (growing community)
    - Exchange net position change (outflows = accumulation)
    - Active addresses
    Paid: https://studio.glassnode.com/
    Free tier: limited metrics, no key needed for some
    """
    if not config.ENABLE_GLASSNODE:
        return {}

    coin = symbol.replace("USDT", "").lower()
    if coin == "usdt":
        return {}

    result = {}
    headers = {}
    if config.GLASSNODE_API_KEY:
        headers["X-Api-Key"] = config.GLASSNODE_API_KEY

    metrics = {
        "new_addresses":      "addresses/new_non_zero_count",
        "active_addresses":   "addresses/active_count",
        "exchange_outflow":   "transactions/transfers_volume_from_exchanges_sum",
        "exchange_inflow":    "transactions/transfers_volume_to_exchanges_sum",
    }

    for key, endpoint in metrics.items():
        try:
            r = requests.get(
                f"https://api.glassnode.com/v1/metrics/{endpoint}",
                params={"a": coin, "i": "24h", "f": "JSON",
                        **({"api_key": config.GLASSNODE_API_KEY}
                           if config.GLASSNODE_API_KEY else {})},
                timeout=config.REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list):
                    result[f"glassnode_{key}"] = data[-1].get("v")
        except Exception as e:
            log.debug(f"Glassnode {key} failed: {e}")

    # Derived signal: more outflow than inflow = accumulation (bullish)
    inflow  = result.get("glassnode_exchange_inflow", 0) or 0
    outflow = result.get("glassnode_exchange_outflow", 0) or 0
    if inflow and outflow:
        result["glassnode_net_flow"]    = round(outflow - inflow, 0)
        result["glassnode_accumulating"] = outflow > inflow

    return result


# ─────────────────────────────────────────────
#  CRYPTOQUANT — exchange flows (paid)
# ─────────────────────────────────────────────
def get_cryptoquant_flow(symbol: str) -> dict:
    """
    CryptoQuant exchange flow data.
    Free tier exists but very limited.
    Paid: https://cryptoquant.com/
    """
    if not config.ENABLE_CRYPTOQUANT or not config.CRYPTOQUANT_API_KEY:
        return {}

    coin = symbol.replace("USDT", "").lower()
    if coin in ("usdt", "usdc", "busd"):
        return {}

    headers = {"Authorization": f"Bearer {config.CRYPTOQUANT_API_KEY}"}
    result  = {}

    try:
        r = requests.get(
            f"https://api.cryptoquant.com/v1/{coin}/exchange-flows/inflow",
            headers=headers,
            params={"window": "day", "limit": 3},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("result", {}).get("data", [])
        if len(data) >= 2:
            now  = float(data[-1].get("inflow_mean", 0))
            prev = float(data[-2].get("inflow_mean", 1))
            chg  = (now - prev) / prev * 100 if prev > 0 else 0
            result.update({
                "cq_exchange_inflow":        round(now, 2),
                "cq_inflow_change_pct":      round(chg, 2),
                "cq_exchange_outflow_signal": bool(chg < -20),
            })
    except Exception as e:
        log.debug(f"CryptoQuant inflow failed for {symbol}: {e}")

    try:
        r = requests.get(
            f"https://api.cryptoquant.com/v1/{coin}/exchange-flows/netflow",
            headers=headers,
            params={"window": "day", "limit": 2},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("result", {}).get("data", [])
        if data:
            netflow = float(data[-1].get("netflow_total", 0))
            result.update({
                "cq_netflow":       round(netflow, 2),
                "cq_accumulating":  netflow < 0,  # negative netflow = coins leaving exchanges
            })
    except Exception as e:
        log.debug(f"CryptoQuant netflow failed for {symbol}: {e}")

    return result


# ─────────────────────────────────────────────
#  HYBLOCK CAPITAL  (paid — liquidation heatmap)
# ─────────────────────────────────────────────
def get_hyblock_liquidation_levels(symbol: str) -> dict:
    """
    Hyblock shows WHERE liquidation clusters are above/below price.
    Clusters above price = magnets = price will be pulled up.
    Sign up: https://app.hyblock.io/ (~$30-50/month)
    """
    if not config.ENABLE_HYBLOCK or not config.HYBLOCK_API_KEY:
        return {}

    coin = symbol.replace("USDT", "")
    try:
        r = requests.get(
            "https://api.hyblock.io/v1/liquidation-heatmap",
            headers={"Authorization": f"Bearer {config.HYBLOCK_API_KEY}"},
            params={"symbol": f"{coin}USDT", "timeframe": "4h"},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        # Find clusters ABOVE current price = upside magnets
        price        = data.get("currentPrice", 0)
        levels       = data.get("levels", [])
        above_levels = [l for l in levels if l.get("price", 0) > price]
        below_levels = [l for l in levels if l.get("price", 0) < price]

        nearest_above = min(above_levels, key=lambda x: x["price"], default=None) if above_levels else None
        total_above   = sum(l.get("liquidationUsd", 0) for l in above_levels[:5])

        return {
            "hyblock_liq_cluster_above_usd": round(total_above, 0),
            "hyblock_nearest_liq_above":     nearest_above.get("price") if nearest_above else None,
            "hyblock_liq_magnet":            bool(total_above > 1_000_000),  # >$1M above = magnet
        }
    except Exception as e:
        log.warning(f"Hyblock failed for {symbol}: {e}")
    return {}


# ─────────────────────────────────────────────
#  ORDER BOOK SPOOFING DETECTION
#  (approximated — true detection needs websocket)
# ─────────────────────────────────────────────
def detect_spoofing_proxy(symbol: str, ob_data: dict) -> dict:
    """
    True spoofing detection requires watching walls appear/disappear
    in real-time via WebSocket. This is an approximation:

    Proxy signals for spoofing:
    1. Book is very asymmetric (huge bid wall + thin ask = fake support)
    2. Spread is extremely tight but book is very thin
    3. Large wall right at round price level (common spoofing location)

    These are NOT definitive but flag suspicious order book patterns.
    """
    result = {"spoof_proxy_flag": False, "spoof_proxy_reason": None}

    bid_depth = ob_data.get("ob_total_bid_usdt", 0) or 0
    ask_depth = ob_data.get("ob_total_ask_usdt", 0) or 0
    ratio     = ob_data.get("ob_bid_ask_ratio_agg", 1.0) or 1.0
    wall_usd  = ob_data.get("ob_largest_wall_usdt", 0) or 0
    wall_side = ob_data.get("ob_largest_wall_side", "")
    best_bid  = ob_data.get("ob_best_bid_all", 0) or 0

    # Flag 1: Ratio extremely high (>3x) — suspiciously bid heavy
    if ratio > 3.0 and bid_depth > 500_000:
        result["spoof_proxy_flag"]   = True
        result["spoof_proxy_reason"] = f"Extremely bid-heavy book ({ratio:.1f}x) — possible spoofing"
        return result

    # Flag 2: Single wall > 30% of entire side — abnormally large
    total_side = bid_depth if wall_side == "bid" else ask_depth
    if total_side > 0 and wall_usd / total_side > 0.30:
        result["spoof_proxy_flag"]   = True
        result["spoof_proxy_reason"] = f"Single wall = {wall_usd/total_side*100:.0f}% of {wall_side} side — suspicious"
        return result

    # Flag 3: Wall sitting exactly at round number price (e.g., 1.0000, 0.5000)
    if best_bid > 0:
        # Check if best bid is suspiciously round
        rounded = round(best_bid, 2)
        if abs(best_bid - rounded) / best_bid < 0.0001 and wall_side == "bid":
            result["spoof_proxy_reason"] = f"Large wall at round price ${rounded} — watch for removal"

    return result


# ─────────────────────────────────────────────
#  MASTER: run all news + paid signals
# ─────────────────────────────────────────────
def get_all_news_and_onchain(symbol: str, ob_data: dict = {}) -> dict:
    result = {}

    # News — CryptoCompare (free, no key) + CoinGecko status updates
    result.update(get_cryptocompare_news(symbol))
    if config.ENABLE_COINGECKO_NEWS:
        result.update(get_coingecko_news(symbol))

    # Telegram approximation (free)
    result.update(get_telegram_activity(symbol))

    # Paid sources (only called if enabled + key set)
    if config.ENABLE_SANTIMENT and config.SANTIMENT_API_KEY:
        result.update(get_santiment_data(symbol))
    if config.ENABLE_NANSEN and config.NANSEN_API_KEY:
        result.update(get_nansen_data(symbol))
    if config.ENABLE_ARKHAM and config.ARKHAM_API_KEY:
        result.update(get_arkham_data(symbol))
    if config.ENABLE_TWITTER and config.TWITTER_BEARER_TOKEN:
        result.update(get_twitter_mentions(symbol))
    if config.ENABLE_GLASSNODE:
        result.update(get_glassnode_onchain(symbol))
    if config.ENABLE_CRYPTOQUANT and config.CRYPTOQUANT_API_KEY:
        result.update(get_cryptoquant_flow(symbol))
    if config.ENABLE_HYBLOCK and config.HYBLOCK_API_KEY:
        result.update(get_hyblock_liquidation_levels(symbol))

    # Spoofing proxy (always, uses existing ob_data)
    result.update(detect_spoofing_proxy(symbol, ob_data))

    return result
