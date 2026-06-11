"""
MODULE: aggregated_fetcher.py
Fetches futures data from ALL major exchanges and combines them.

Why this matters:
  A coin may show negative funding on Binance but positive on Bybit — 
  the TRUE market sentiment is the weighted average across all exchanges.
  Aggregated OI, funding, L/S, and liquidations are far more reliable signals.

Exchange coverage:
  - Binance Futures  (fapi.binance.com)    — always, no key
  - Bybit            (api.bybit.com)        — always, no key  
  - OKX              (www.okx.com)          — always, no key
  - Bitget           (api.bitget.com)       — always, no key
  - CoinGlass        (open-api.coinglass.com) — optional free key (best aggregator)
  - Coinalyze        (api.coinalyze.net)    — optional free key

Priority:
  CoinGlass key set  → use CoinGlass (already aggregates everything)
  Coinalyze key set  → use Coinalyze (already aggregates everything)
  Neither key        → fetch from Binance + Bybit + OKX + Bitget individually and combine
"""

import requests
import time
from typing import Optional
from modules.logger import get_logger
import config

log = get_logger("aggregated")

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _get(url: str, params: dict = {}, headers: dict = {}, timeout: int = None) -> Optional[any]:
    try:
        h = {**HEADERS, **headers}
        r = requests.get(url, params=params, headers=h,
                         timeout=timeout or config.REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        log.debug(f"HTTP {r.status_code} from {url}")
        return None
    except Exception as e:
        log.debug(f"Request failed {url}: {e}")
        return None


# ═══════════════════════════════════════════════════════
#  SECTION 1 — OPEN INTEREST  (aggregated across exchanges)
# ═══════════════════════════════════════════════════════

def _oi_coinglass(symbol: str) -> Optional[dict]:
    """CoinGlass aggregated OI — best source, needs free key."""
    if not config.COINGLASS_API_KEY:
        return None
    base = symbol.replace("USDT", "")
    data = _get(
        "https://open-api.coinglass.com/public/v2/indicator/open_interest_history",
        params={"symbol": base, "interval": "h1", "limit": 25},
        headers={"coinglassSecret": config.COINGLASS_API_KEY}
    )
    if not data or data.get("code") != "0":
        return None
    rows = data.get("data", [])
    if len(rows) < 2:
        return None
    oi_now = float(rows[-1].get("openInterest", 0))
    oi_24h = float(rows[0].get("openInterest", oi_now))
    change = (oi_now - oi_24h) / oi_24h * 100 if oi_24h > 0 else 0
    return {"oi_usd": round(oi_now, 0), "oi_change_24h": round(change, 2),
            "oi_rising": change >= config.OI_CHANGE_THRESHOLD, "oi_source": "coinglass_agg"}


def _oi_coinalyze(symbol: str) -> Optional[dict]:
    """Coinalyze aggregated OI — needs free key from coinalyze.net."""
    if not config.COINALYZE_API_KEY:
        return None
    # Coinalyze aggregated symbol format: BTCUSDT_PERP.A  (.A = all exchanges)
    agg_sym = symbol + "_PERP.A"
    data = _get(
        "https://api.coinalyze.net/v1/open-interest-history",
        params={"symbols": agg_sym, "interval": "1hour", "limit": 25},
        headers={"api_key": config.COINALYZE_API_KEY}
    )
    if not data or not isinstance(data, list) or not data:
        return None
    rows = data[0].get("history", [])
    if len(rows) < 2:
        return None
    oi_now = float(rows[-1].get("o", 0))   # open interest in USD
    oi_24h = float(rows[0].get("o", oi_now))
    change = (oi_now - oi_24h) / oi_24h * 100 if oi_24h > 0 else 0
    return {"oi_usd": round(oi_now, 0), "oi_change_24h": round(change, 2),
            "oi_rising": change >= config.OI_CHANGE_THRESHOLD, "oi_source": "coinalyze_agg"}


def _oi_binance(symbol: str) -> Optional[dict]:
    """Binance OI — Binance exchange only, fallback."""
    data = _get("https://fapi.binance.com/futures/data/openInterestHist",
                params={"symbol": symbol, "period": "1h", "limit": 25})
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    oi_now = float(data[-1].get("sumOpenInterestValue", 0))
    oi_24h = float(data[0].get("sumOpenInterestValue", oi_now))
    change = (oi_now - oi_24h) / oi_24h * 100 if oi_24h > 0 else 0
    return {"oi_usd": round(oi_now, 0), "oi_change_24h": round(change, 2),
            "oi_rising": change >= config.OI_CHANGE_THRESHOLD, "oi_source": "binance_only"}


def _oi_bybit(symbol: str) -> Optional[float]:
    """Bybit current OI in USD."""
    data = _get("https://api.bybit.com/v5/market/open-interest",
                params={"category": "linear", "symbol": symbol,
                        "intervalTime": "1h", "limit": 25})
    if not data:
        return None
    rows = data.get("result", {}).get("list", [])
    if not rows:
        return None
    return float(rows[0].get("openInterest", 0))


def _oi_okx(symbol: str) -> Optional[float]:
    """OKX current OI in USD."""
    # OKX symbol format: BTC-USDT-SWAP
    parts = symbol.replace("USDT", "")
    okx_sym = f"{parts}-USDT-SWAP"
    data = _get("https://www.okx.com/api/v5/public/open-interest",
                params={"instType": "SWAP", "instId": okx_sym})
    if not data:
        return None
    rows = data.get("data", [])
    if not rows:
        return None
    return float(rows[0].get("oiUsd", 0))


def _oi_bitget(symbol: str) -> Optional[float]:
    """Bitget current OI in USD — v2 API."""
    data = _get("https://api.bitget.com/api/v2/mix/market/open-interest",
                params={"symbol": symbol, "productType": "USDT-FUTURES"})
    if not data or data.get("code") != "00000":
        return None
    d = data.get("data", {})
    oi    = float(d.get("openInterestList", [{}])[0].get("size", 0)) if d.get("openInterestList") else 0
    price = float(d.get("openInterestList", [{}])[0].get("price", 0)) if d.get("openInterestList") else 0
    return oi * price if oi and price else None


def get_aggregated_oi(symbol: str) -> dict:
    """
    Returns aggregated OI across all exchanges.
    Priority: CoinGlass → Coinalyze → Manual aggregation (Binance+Bybit+OKX+Bitget)
    """
    # Try aggregators first
    result = _oi_coinglass(symbol) or _oi_coinalyze(symbol)
    if result:
        return result

    # Manual aggregation
    oi_parts = {}
    binance_data = _oi_binance(symbol)
    if binance_data:
        oi_parts["binance"] = binance_data["oi_usd"]

    bybit_oi = _oi_bybit(symbol)
    if bybit_oi:
        oi_parts["bybit"] = bybit_oi

    okx_oi = _oi_okx(symbol)
    if okx_oi:
        oi_parts["okx"] = okx_oi

    bitget_oi = _oi_bitget(symbol)
    if bitget_oi:
        oi_parts["bitget"] = bitget_oi

    if not oi_parts:
        return {"oi_usd": None, "oi_change_24h": None, "oi_rising": False,
                "oi_source": "none", "oi_exchanges": {}}

    total_oi = sum(oi_parts.values())
    # For change % we use Binance as baseline (has history)
    change = binance_data["oi_change_24h"] if binance_data else None
    rising = change >= config.OI_CHANGE_THRESHOLD if change else False

    return {
        "oi_usd":        round(total_oi, 0),
        "oi_change_24h": change,
        "oi_rising":     rising,
        "oi_source":     f"manual_agg({','.join(oi_parts.keys())})",
        "oi_exchanges":  {k: round(v, 0) for k, v in oi_parts.items()},
        "oi_binance_pct": round(oi_parts.get("binance", 0) / total_oi * 100, 1) if total_oi else None,
    }


# ═══════════════════════════════════════════════════════
#  SECTION 2 — FUNDING RATE  (aggregated across exchanges)
# ═══════════════════════════════════════════════════════

def _funding_coinglass(symbol: str) -> Optional[dict]:
    if not config.COINGLASS_API_KEY:
        return None
    base = symbol.replace("USDT", "")
    data = _get(
        "https://open-api.coinglass.com/public/v2/indicator/funding_rates_oi_weight",
        params={"symbol": base},
        headers={"coinglassSecret": config.COINGLASS_API_KEY}
    )
    if not data or data.get("code") != "0":
        return None
    d = data.get("data", {})
    rate = float(d.get("weightedFundingRate", 0))
    return {
        "funding_rate":      round(rate * 100, 5),
        "negative_funding":  rate <= config.FUNDING_RATE_MAX,
        "funding_source":    "coinglass_weighted_agg",
        "funding_exchanges": d.get("fundingRates", {}),
    }


def _funding_coinalyze(symbol: str) -> Optional[dict]:
    if not config.COINALYZE_API_KEY:
        return None
    agg_sym = symbol + "_PERP.A"
    data = _get(
        "https://api.coinalyze.net/v1/funding-rate",
        params={"symbols": agg_sym},
        headers={"api_key": config.COINALYZE_API_KEY}
    )
    if not data or not isinstance(data, list) or not data:
        return None
    rate = float(data[0].get("value", 0))
    return {
        "funding_rate":     round(rate * 100, 5),
        "negative_funding": rate <= config.FUNDING_RATE_MAX,
        "funding_source":   "coinalyze_agg",
    }


def _funding_binance(symbol: str) -> Optional[float]:
    data = _get("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})
    if not data:
        return None
    return float(data.get("lastFundingRate", 0))


def _funding_bybit(symbol: str) -> Optional[float]:
    data = _get("https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": symbol})
    if not data:
        return None
    items = data.get("result", {}).get("list", [])
    if not items:
        return None
    return float(items[0].get("fundingRate", 0))


def _funding_okx(symbol: str) -> Optional[float]:
    parts = symbol.replace("USDT", "")
    okx_sym = f"{parts}-USDT-SWAP"
    data = _get("https://www.okx.com/api/v5/public/funding-rate",
                params={"instId": okx_sym})
    if not data:
        return None
    rows = data.get("data", [])
    if not rows:
        return None
    return float(rows[0].get("fundingRate", 0))


def _funding_bitget(symbol: str) -> Optional[float]:
    """Bitget funding rate — v2 API."""
    data = _get("https://api.bitget.com/api/v2/mix/market/current-fund-rate",
                params={"symbol": symbol, "productType": "USDT-FUTURES"})
    if not data or data.get("code") != "00000":
        return None
    rows = data.get("data", [])
    if not rows:
        return None
    return float(rows[0].get("fundingRate", 0))


def get_aggregated_funding(symbol: str) -> dict:
    """
    Weighted average funding rate across all exchanges.
    Priority: CoinGlass → Coinalyze → Manual average
    """
    result = _funding_coinglass(symbol) or _funding_coinalyze(symbol)
    if result:
        return result

    # Manual: collect from each exchange
    rates = {}
    r = _funding_binance(symbol)
    if r is not None:
        rates["binance"] = r

    r = _funding_bybit(symbol)
    if r is not None:
        rates["bybit"] = r

    r = _funding_okx(symbol)
    if r is not None:
        rates["okx"] = r

    r = _funding_bitget(symbol)
    if r is not None:
        rates["bitget"] = r

    if not rates:
        return {"funding_rate": None, "negative_funding": False,
                "funding_source": "none", "funding_per_exchange": {}}

    avg_rate  = sum(rates.values()) / len(rates)
    neg_count = sum(1 for v in rates.values() if v <= config.FUNDING_RATE_MAX)
    # negative_funding = True if MAJORITY of exchanges show negative
    neg_majority = neg_count >= len(rates) / 2

    return {
        "funding_rate":          round(avg_rate * 100, 5),
        "funding_avg_3":         round(avg_rate * 100, 5),
        "negative_funding":      neg_majority,
        "funding_neg_exchanges": neg_count,
        "funding_total_exchanges": len(rates),
        "funding_source":        f"manual_avg({','.join(rates.keys())})",
        "funding_per_exchange":  {k: round(v * 100, 5) for k, v in rates.items()},
    }


# ═══════════════════════════════════════════════════════
#  SECTION 3 — LONG/SHORT RATIO  (aggregated)
# ═══════════════════════════════════════════════════════

def _ls_coinglass(symbol: str) -> Optional[dict]:
    if not config.COINGLASS_API_KEY:
        return None
    base = symbol.replace("USDT", "")
    data = _get(
        "https://open-api.coinglass.com/public/v2/indicator/long_short_account_ratio",
        params={"symbol": base, "interval": "h1", "limit": 3},
        headers={"coinglassSecret": config.COINGLASS_API_KEY}
    )
    if not data or data.get("code") != "0":
        return None
    rows = data.get("data", [])
    if not rows:
        return None
    ratio = float(rows[-1].get("longShortRatio", 1.0))
    return {
        "ls_ratio_global": round(ratio, 3),
        "short_heavy":     ratio < config.LONG_SHORT_RATIO_MAX,
        "ls_source":       "coinglass_agg",
    }


def _ls_binance(symbol: str) -> Optional[float]:
    data = _get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                params={"symbol": symbol, "period": "1h", "limit": 3})
    if not data or not isinstance(data, list):
        return None
    return float(data[-1].get("longShortRatio", 1.0))


def _ls_bybit(symbol: str) -> Optional[float]:
    data = _get("https://api.bybit.com/v5/market/account-ratio",
                params={"category": "linear", "symbol": symbol,
                        "period": "1h", "limit": 3})
    if not data:
        return None
    rows = data.get("result", {}).get("list", [])
    if not rows:
        return None
    b = float(rows[0].get("buyRatio", 0.5))
    s = float(rows[0].get("sellRatio", 0.5))
    return round(b / s, 3) if s > 0 else 1.0


def _ls_okx(symbol: str) -> Optional[float]:
    ccy = symbol.replace("USDT", "")
    data = _get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
                params={"ccy": ccy, "period": "1H"})
    if not data:
        return None
    rows = data.get("data", [])
    if not rows:
        return None
    return float(rows[-1][1]) if len(rows[-1]) > 1 else None


def get_aggregated_ls_ratio(symbol: str) -> dict:
    """
    Aggregated Long/Short ratio across exchanges.
    Priority: CoinGlass → Manual average
    """
    result = _ls_coinglass(symbol)
    if result:
        return result

    ratios = {}
    r = _ls_binance(symbol)
    if r:
        ratios["binance"] = r

    r = _ls_bybit(symbol)
    if r:
        ratios["bybit"] = r

    r = _ls_okx(symbol)
    if r:
        ratios["okx"] = r

    # Also get top trader ratio from Binance
    top_data = _get("https://fapi.binance.com/futures/data/topLongShortAccountRatio",
                    params={"symbol": symbol, "period": "1h", "limit": 3})
    top_ls = None
    if top_data and isinstance(top_data, list):
        top_ls = float(top_data[-1].get("longShortRatio", 1.0))

    if not ratios:
        return {"ls_ratio_global": None, "ls_ratio_top": top_ls,
                "short_heavy": False, "whales_short": False, "ls_source": "none"}

    avg_ls     = sum(ratios.values()) / len(ratios)
    short_heavy = avg_ls < config.LONG_SHORT_RATIO_MAX

    return {
        "ls_ratio_global":   round(avg_ls, 3),
        "ls_ratio_top":      round(top_ls, 3) if top_ls else None,
        "short_heavy":       short_heavy,
        "whales_short":      bool(top_ls and top_ls < 1.0),
        "ls_source":         f"manual_avg({','.join(ratios.keys())})",
        "ls_per_exchange":   {k: round(v, 3) for k, v in ratios.items()},
    }


# ═══════════════════════════════════════════════════════
#  SECTION 4 — LIQUIDATIONS  (aggregated)
# ═══════════════════════════════════════════════════════

def _liq_coinglass(symbol: str) -> Optional[dict]:
    if not config.COINGLASS_API_KEY:
        return None
    base = symbol.replace("USDT", "")
    data = _get(
        "https://open-api.coinglass.com/public/v2/indicator/liquidation_history",
        params={"symbol": base, "interval": "h1"},
        headers={"coinglassSecret": config.COINGLASS_API_KEY}
    )
    if not data or data.get("code") != "0":
        return None
    rows = data.get("data", [])[-24:]
    if not rows:
        return None
    long_liq  = sum(float(d.get("longLiquidationUsd", 0)) for d in rows)
    short_liq = sum(float(d.get("shortLiquidationUsd", 0)) for d in rows)
    return {
        "liq_long_24h_usd":   round(long_liq, 0),
        "liq_short_24h_usd":  round(short_liq, 0),
        "liq_total_24h_usd":  round(long_liq + short_liq, 0),
        "liq_short_heavy":    short_liq > long_liq,
        "liq_source":         "coinglass_agg",
    }


def _liq_coinalyze(symbol: str) -> Optional[dict]:
    if not config.COINALYZE_API_KEY:
        return None
    agg_sym = symbol + "_PERP.A"
    data = _get(
        "https://api.coinalyze.net/v1/liquidation-history",
        params={"symbols": agg_sym, "interval": "1hour", "limit": 24},
        headers={"api_key": config.COINALYZE_API_KEY}
    )
    if not data or not isinstance(data, list) or not data:
        return None
    rows = data[0].get("history", [])
    long_liq  = sum(float(r.get("l", 0)) for r in rows)
    short_liq = sum(float(r.get("s", 0)) for r in rows)
    return {
        "liq_long_24h_usd":  round(long_liq, 0),
        "liq_short_24h_usd": round(short_liq, 0),
        "liq_total_24h_usd": round(long_liq + short_liq, 0),
        "liq_short_heavy":   short_liq > long_liq,
        "liq_source":        "coinalyze_agg",
    }


def _liq_bybit(symbol: str) -> dict:
    """Bybit liquidation proxy via taker volume (no direct endpoint on free tier)."""
    data = _get("https://api.bybit.com/v5/market/recent-trade",
                params={"category": "linear", "symbol": symbol, "limit": 200})
    if not data:
        return {}
    trades = data.get("result", {}).get("list", [])
    buy_vol  = sum(float(t.get("size", 0)) * float(t.get("price", 0))
                   for t in trades if t.get("side") == "Buy")
    sell_vol = sum(float(t.get("size", 0)) * float(t.get("price", 0))
                   for t in trades if t.get("side") == "Sell")
    return {"bybit_buy_vol": round(buy_vol, 0), "bybit_sell_vol": round(sell_vol, 0)}


def get_aggregated_liquidations(symbol: str) -> dict:
    """
    Aggregated liquidations across all exchanges.
    Priority: CoinGlass → Coinalyze → Binance proxy
    """
    result = _liq_coinglass(symbol) or _liq_coinalyze(symbol)
    if result:
        return result

    # Fallback: Binance taker proxy
    from modules.binance_fetcher import get_taker_buy_data
    taker = get_taker_buy_data(symbol)
    return {
        "liq_long_24h_usd":  None,
        "liq_short_24h_usd": None,
        "liq_total_24h_usd": None,
        "liq_short_heavy":   None,
        "taker_buy_pct":     taker.get("taker_buy_pct"),
        "cvd_proxy":         taker.get("cvd_proxy"),
        "cvd_positive":      taker.get("cvd_positive"),
        "liq_source":        "binance_proxy",
    }


# ═══════════════════════════════════════════════════════
#  SECTION 5 — VOLUME  (aggregated across exchanges)
# ═══════════════════════════════════════════════════════

def get_aggregated_volume(symbol: str) -> dict:
    """
    Total spot + futures volume across all exchanges.
    Uses CoinGecko market data (already fetched in sentiment module).
    Supplements Binance-only volume with cross-exchange total.
    """
    coin = symbol.replace("USDT", "").lower()

    # CoinGecko /coins/{id}/market_chart gives total market volume
    from modules.sentiment_fetcher import _get_cg_id
    coin_id = _get_cg_id(symbol)
    if not coin_id:
        return {}

    data = _get(
        f"https://api.coingecko.com/api/v3/coins/markets",
        params={"vs_currency": "usd", "ids": coin_id}
    )
    if not data or not isinstance(data, list) or not data:
        return {}

    d = data[0]
    total_vol = d.get("total_volume")
    return {
        "total_volume_all_exchanges": total_vol,
        "vol_all_exchanges_m": round(total_vol / 1e6, 2) if total_vol else None,
    }


# ═══════════════════════════════════════════════════════
#  SECTION 6 — ORDER BOOK  (per-exchange fetch + combine)
# ═══════════════════════════════════════════════════════
#
#  True aggregated order book doesn't exist for free.
#  We fetch top-of-book from each exchange separately and combine:
#
#  What we extract per exchange:
#    - best bid price + size
#    - best ask price + size
#    - top-N bid levels (price + cumulative USDT depth)
#    - top-N ask levels (price + cumulative USDT depth)
#
#  What we compute after combining all exchanges:
#    - total_bid_depth_usdt  — sum of all bid depth across all exchanges
#    - total_ask_depth_usdt  — sum of all ask depth across all exchanges
#    - agg_bid_ask_ratio     — total bids / total asks (>1.5 = bullish)
#    - best_bid_all          — highest bid price anywhere (true best price)
#    - best_ask_all          — lowest ask price anywhere (true best price)
#    - cross_exchange_spread — price gap between exchanges (arb signal)
#    - largest_wall_usdt     — biggest single order level across ALL exchanges
#    - largest_wall_exchange — which exchange has that wall
#    - large_buy_wall_agg    — True if any wall > 10% of total bid depth
#    - large_sell_wall_agg   — True if any wall > 10% of total ask depth
#    - ob_imbalance_pct      — (bids - asks) / (bids + asks) * 100
#                              positive = buy pressure, negative = sell pressure
#    - thin_book_agg         — True if total depth < $2M (easy to push price)
#    - exchanges_with_data   — which exchanges responded
# ═══════════════════════════════════════════════════════

def _parse_book(bids_raw: list, asks_raw: list, limit: int = 20) -> dict:
    """Parse raw [price, qty] lists into (price, usdt_value) tuples."""
    bids = []
    asks = []
    try:
        for b in bids_raw[:limit]:
            p, q = float(b[0]), float(b[1])
            bids.append((p, p * q))
        for a in asks_raw[:limit]:
            p, q = float(a[0]), float(a[1])
            asks.append((p, p * q))
    except Exception:
        pass
    return {"bids": bids, "asks": asks}


def _ob_binance(symbol: str) -> Optional[dict]:
    """Binance Futures order book — top 20 levels."""
    data = _get(f"https://fapi.binance.com/fapi/v1/depth",
                params={"symbol": symbol, "limit": 20})
    if not data:
        return None
    parsed = _parse_book(data.get("bids", []), data.get("asks", []))
    parsed["exchange"] = "binance"
    return parsed


def _ob_bybit(symbol: str) -> Optional[dict]:
    """Bybit linear perp order book — top 20 levels."""
    data = _get("https://api.bybit.com/v5/market/orderbook",
                params={"category": "linear", "symbol": symbol, "limit": 20})
    if not data:
        return None
    result_data = data.get("result", {})
    parsed = _parse_book(result_data.get("b", []), result_data.get("a", []))
    parsed["exchange"] = "bybit"
    return parsed


def _ob_okx(symbol: str) -> Optional[dict]:
    """OKX swap order book — top 20 levels."""
    parts   = symbol.replace("USDT", "")
    okx_sym = f"{parts}-USDT-SWAP"
    data    = _get("https://www.okx.com/api/v5/market/books",
                   params={"instId": okx_sym, "sz": 20})
    if not data:
        return None
    rows = data.get("data", [])
    if not rows:
        return None
    parsed = _parse_book(rows[0].get("bids", []), rows[0].get("asks", []))
    parsed["exchange"] = "okx"
    return parsed


def _ob_bitget(symbol: str) -> Optional[dict]:
    """Bitget USDT-M perp order book — v2 API."""
    data = _get("https://api.bitget.com/api/v2/mix/market/merge-depth",
                params={"symbol": symbol, "productType": "USDT-FUTURES", "limit": "20"})
    if not data or data.get("code") != "00000":
        return None
    d      = data.get("data", {})
    parsed = _parse_book(d.get("bids", []), d.get("asks", []))
    parsed["exchange"] = "bitget"
    return parsed


def get_aggregated_order_book(symbol: str) -> dict:
    """
    Fetches order book from Binance + Bybit + OKX + Bitget,
    merges all levels, and returns a combined order book picture.

    This is NOT a true aggregated book (impossible free) but gives:
    - Total depth across all exchanges
    - True best bid/ask across all markets
    - Cross-exchange price gap (arbitrage signal)
    - Largest wall anywhere in the market
    - Overall buy/sell imbalance
    """
    books = []

    # Fetch from each exchange (errors are silently skipped)
    for fetch_fn in [_ob_binance, _ob_bybit, _ob_okx, _ob_bitget]:
        try:
            result = fetch_fn(symbol)
            if result and (result.get("bids") or result.get("asks")):
                books.append(result)
        except Exception as e:
            log.debug(f"Order book fetch failed: {e}")

    if not books:
        return {
            "ob_total_bid_usdt":      None,
            "ob_total_ask_usdt":      None,
            "ob_bid_ask_ratio_agg":   None,
            "ob_imbalance_pct":       None,
            "ob_best_bid_all":        None,
            "ob_best_ask_all":        None,
            "ob_cross_exchange_spread_pct": None,
            "ob_largest_wall_usdt":   None,
            "ob_largest_wall_side":   None,
            "ob_largest_wall_exchange": None,
            "ob_large_buy_wall_agg":  False,
            "ob_large_sell_wall_agg": False,
            "ob_thin_book_agg":       False,
            "ob_exchanges":           [],
            "ob_source":              "none",
        }

    # ── Combine all levels from all exchanges ──
    all_bids = []   # (price, usdt_val, exchange)
    all_asks = []

    for book in books:
        ex = book["exchange"]
        for price, usdt in book.get("bids", []):
            all_bids.append((price, usdt, ex))
        for price, usdt in book.get("asks", []):
            all_asks.append((price, usdt, ex))

    # ── Totals ──
    total_bid_usdt = sum(v for _, v, _ in all_bids)
    total_ask_usdt = sum(v for _, v, _ in all_asks)

    ratio = total_bid_usdt / total_ask_usdt if total_ask_usdt > 0 else None
    imbalance = ((total_bid_usdt - total_ask_usdt) /
                 (total_bid_usdt + total_ask_usdt) * 100
                 ) if (total_bid_usdt + total_ask_usdt) > 0 else None

    # ── Best prices across all exchanges ──
    best_bid_all = max((p for p, _, _ in all_bids), default=None)
    best_ask_all = min((p for p, _, _ in all_asks), default=None)

    # ── Cross-exchange spread (arbitrage signal) ──
    # If best ask on one exchange < best bid on another = arbitrage opportunity
    # In practice this means price is about to converge = volatile
    cross_spread = None
    if best_bid_all and best_ask_all and best_ask_all > 0:
        cross_spread = round((best_bid_all - best_ask_all) / best_ask_all * 100, 4)
        # positive = bid > ask = crossed book = arb = price about to move fast

    # ── Largest single wall across ALL exchanges ──
    largest_wall_usdt = 0
    largest_wall_side = None
    largest_wall_ex   = None

    for price, usdt, ex in all_bids:
        if usdt > largest_wall_usdt:
            largest_wall_usdt = usdt
            largest_wall_side = "bid"
            largest_wall_ex   = ex

    for price, usdt, ex in all_asks:
        if usdt > largest_wall_usdt:
            largest_wall_usdt = usdt
            largest_wall_side = "ask"
            largest_wall_ex   = ex

    # ── Wall signals ──
    # Large wall = single level > 8% of that side's total depth
    large_buy_wall  = any(
        v > total_bid_usdt * 0.08 for _, v, _ in all_bids
    ) if total_bid_usdt > 0 else False

    large_sell_wall = any(
        v > total_ask_usdt * 0.08 for _, v, _ in all_asks
    ) if total_ask_usdt > 0 else False

    # ── Thin book — total depth < $2M across ALL exchanges ──
    thin_book = bool((total_bid_usdt + total_ask_usdt) < 2_000_000)

    # ── Per-exchange depth breakdown ──
    exchange_depth = {}
    for book in books:
        ex        = book["exchange"]
        ex_bids   = sum(v for _, v in book.get("bids", []))
        ex_asks   = sum(v for _, v in book.get("asks", []))
        ex_ratio  = round(ex_bids / ex_asks, 3) if ex_asks > 0 else None
        exchange_depth[ex] = {
            "bid_usdt": round(ex_bids, 0),
            "ask_usdt": round(ex_asks, 0),
            "ratio":    ex_ratio,
        }

    return {
        # Combined totals
        "ob_total_bid_usdt":           round(total_bid_usdt, 0),
        "ob_total_ask_usdt":           round(total_ask_usdt, 0),
        "ob_bid_ask_ratio_agg":        round(ratio, 3) if ratio else None,
        "ob_imbalance_pct":            round(imbalance, 2) if imbalance else None,

        # Best prices
        "ob_best_bid_all":             round(best_bid_all, 6) if best_bid_all else None,
        "ob_best_ask_all":             round(best_ask_all, 6) if best_ask_all else None,
        "ob_cross_exchange_spread_pct": cross_spread,
        "ob_arb_signal":               bool(cross_spread and cross_spread > 0.1),

        # Walls
        "ob_largest_wall_usdt":        round(largest_wall_usdt, 0),
        "ob_largest_wall_side":        largest_wall_side,
        "ob_largest_wall_exchange":    largest_wall_ex,
        "ob_large_buy_wall_agg":       large_buy_wall,
        "ob_large_sell_wall_agg":      large_sell_wall,

        # Market quality
        "ob_thin_book_agg":            thin_book,
        "ob_exchanges":                [b["exchange"] for b in books],
        "ob_exchange_depth":           exchange_depth,
        "ob_source":                   f"manual_agg({','.join(b['exchange'] for b in books)})",
        "ob_exchanges_count":          len(books),
    }


# ═══════════════════════════════════════════════════════
#  MASTER FUNCTION — run all aggregated fetches
# ═══════════════════════════════════════════════════════

def get_all_aggregated(symbol: str) -> dict:
    """
    Single call to get ALL multi-exchange aggregated data.
    Returns merged dict ready to update the main data dict.
    """
    result = {}

    log.debug(f"    Fetching aggregated OI for {symbol}...")
    result.update(get_aggregated_oi(symbol))

    log.debug(f"    Fetching aggregated funding for {symbol}...")
    result.update(get_aggregated_funding(symbol))

    log.debug(f"    Fetching aggregated L/S for {symbol}...")
    result.update(get_aggregated_ls_ratio(symbol))

    log.debug(f"    Fetching aggregated liquidations for {symbol}...")
    result.update(get_aggregated_liquidations(symbol))

    log.debug(f"    Fetching aggregated order books for {symbol}...")
    result.update(get_aggregated_order_book(symbol))

    # Log which sources were used
    log.debug(
        f"    Sources: OI={result.get('oi_source','?')} | "
        f"Fund={result.get('funding_source','?')} | "
        f"LS={result.get('ls_source','?')} | "
        f"Liq={result.get('liq_source','?')} | "
        f"OB={result.get('ob_source','?')}"
    )

    return result
