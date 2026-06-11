"""
MODULE: binance_fetcher.py
Fetches ALL available data from Binance Futures public API — NO KEY required.
"""

import requests
import pandas as pd
from typing import Optional
from modules.logger import get_logger
import config

log = get_logger("binance")

BASE = "https://fapi.binance.com"
SPOT = "https://api.binance.com"


def _get(url: str, params: dict = {}) -> Optional[any]:
    try:
        r = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"GET {url} failed: {e}")
        return None


def get_futures_symbols() -> list:
    data = _get(f"{BASE}/fapi/v1/exchangeInfo")
    if not data:
        return []
    return [
        s["symbol"] for s in data.get("symbols", [])
        if s["quoteAsset"] == config.SCAN_QUOTE_ASSET
        and s["contractType"] == "PERPETUAL"
        and s["status"] == "TRADING"
    ]


# Symbols to always skip — not real altcoins, never pump like crypto
_SKIP_SYMBOLS = {
    "XAUUSDT", "XAGUSDT",                          # commodities (gold, silver)
    "USDCUSDT", "TUSDUSDT", "USDPUSDT",            # stablecoins
    "FDUSDUSDT", "PYUSDUSDT", "FRAXUSDT",
    "SOXLUSDT", "SOXSUSDT",                        # leveraged ETF tokens
    "BNXUSDT",                                     # BNX leveraged
}

# Base suffixes that indicate leveraged/bear/bull tokens
_SKIP_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S", "3X", "1X")

# Base prefixes that indicate leveraged products
_SKIP_PREFIXES = ("SOX",)  # SOXL (Semiconductor 3x), SOXS (Semiconductor -3x)


def _is_real_crypto(sym: str) -> bool:
    """Returns True if this symbol is a genuine crypto coin perpetual."""
    if sym in _SKIP_SYMBOLS:
        return False
    base = sym.replace("USDT", "")
    if len(base) < 2:
        return False
    for sfx in _SKIP_SUFFIXES:
        if base.endswith(sfx):
            return False
    for pfx in _SKIP_PREFIXES:
        if base.startswith(pfx):
            return False
    return True


def get_24h_tickers() -> dict:
    data = _get(f"{BASE}/fapi/v1/ticker/24hr")
    if not data:
        return {}
    result  = {}
    skipped = 0
    for t in data:
        sym = t.get("symbol", "")
        if not sym.endswith(config.SCAN_QUOTE_ASSET):
            continue
        if not _is_real_crypto(sym):
            skipped += 1
            continue
        result[sym] = {
            "price":            float(t.get("lastPrice", 0)),
            "price_change_pct": float(t.get("priceChangePercent", 0)),
            "volume_usdt":      float(t.get("quoteVolume", 0)),
            "count":            int(t.get("count", 0)),
            "high_24h":         float(t.get("highPrice", 0)),
            "low_24h":          float(t.get("lowPrice", 0)),
        }
    if skipped:
        log.debug(f"Filtered {skipped} non-crypto symbols")
    return result


def get_klines(symbol: str, interval: str = "1d", limit: int = 60) -> Optional[pd.DataFrame]:
    data = _get(f"{BASE}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if not data:
        return None
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ["open","high","low","close","volume","quote_vol","taker_buy_base","taker_buy_quote"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


# Cache symbols that have no spot market — avoids repeated 400 errors
_no_spot_cache: set = set()


def get_spot_price(symbol: str) -> Optional[float]:
    """
    Get spot price to calculate basis (futures premium/discount).
    Some Binance Futures coins have no spot market (tokenized stocks, new listings).
    These are cached to avoid repeated 400 errors.
    """
    if symbol in _no_spot_cache:
        return None
    try:
        r = requests.get(
            f"{SPOT}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=config.REQUEST_TIMEOUT
        )
        if r.status_code == 400:
            _no_spot_cache.add(symbol)
            log.debug(f"{symbol} has no spot market — skipping basis")
            return None
        if r.status_code != 200:
            return None
        return float(r.json().get("price", 0))
    except Exception:
        return None


def get_funding_rate(symbol: str) -> Optional[float]:
    data = _get(f"{BASE}/fapi/v1/premiumIndex", {"symbol": symbol})
    if not data:
        return None
    try:
        return float(data.get("lastFundingRate", 0))
    except Exception:
        return None


def get_funding_history(symbol: str, limit: int = 10) -> list:
    data = _get(f"{BASE}/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
    if not data:
        return []
    return [float(x["fundingRate"]) for x in data]


def get_open_interest(symbol: str) -> Optional[float]:
    data = _get(f"{BASE}/fapi/v1/openInterest", {"symbol": symbol})
    if not data:
        return None
    try:
        return float(data.get("openInterest", 0))
    except Exception:
        return None


def get_open_interest_history(symbol: str, period: str = "1h", limit: int = 25) -> Optional[pd.DataFrame]:
    data = _get(f"{BASE}/futures/data/openInterestHist", {
        "symbol": symbol, "period": period, "limit": limit
    })
    if not data:
        return None
    df = pd.DataFrame(data)
    df["sumOpenInterest"]      = pd.to_numeric(df["sumOpenInterest"])
    df["sumOpenInterestValue"] = pd.to_numeric(df["sumOpenInterestValue"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def get_long_short_ratio(symbol: str, period: str = "1h", limit: int = 5) -> Optional[float]:
    data = _get(f"{BASE}/futures/data/globalLongShortAccountRatio", {
        "symbol": symbol, "period": period, "limit": limit
    })
    if not data:
        return None
    try:
        return float(data[-1]["longShortRatio"])
    except Exception:
        return None


def get_top_trader_ls_ratio(symbol: str, period: str = "1h", limit: int = 3) -> Optional[float]:
    data = _get(f"{BASE}/futures/data/topLongShortAccountRatio", {
        "symbol": symbol, "period": period, "limit": limit
    })
    if not data:
        return None
    try:
        return float(data[-1]["longShortRatio"])
    except Exception:
        return None


def get_taker_buy_data(symbol: str, hours: int = 24) -> dict:
    """Taker buy vs sell pressure — proxy for exchange outflow / CVD."""
    df = get_klines(symbol, "1h", hours)
    if df is None or df.empty:
        return {"taker_buy_pct": None, "cvd_proxy": None}
    taker_buy  = df["taker_buy_quote"].sum()
    total_vol  = df["quote_vol"].sum()
    taker_sell = total_vol - taker_buy
    pct  = (taker_buy / total_vol * 100) if total_vol > 0 else None
    cvd  = taker_buy - taker_sell  # positive = net buying pressure
    return {
        "taker_buy_pct":  round(pct, 2) if pct else None,
        "cvd_proxy":      round(cvd, 0),
        "cvd_positive":   bool(cvd > 0),
    }


def get_order_book_depth(symbol: str, limit: int = 50) -> dict:
    """
    Full order book analysis:
    - Total bid/ask depth
    - Bid/ask ratio
    - Large wall detection (top 5 levels)
    - Order book thinness (spread relative to price)
    """
    data = _get(f"{BASE}/fapi/v1/depth", {"symbol": symbol, "limit": limit})
    if not data:
        return {}

    bids = [(float(b[0]), float(b[1])) for b in data.get("bids", [])]
    asks = [(float(a[0]), float(a[1])) for a in data.get("asks", [])]

    total_bid_usdt = sum(p * q for p, q in bids)
    total_ask_usdt = sum(p * q for p, q in asks)
    ratio = (total_bid_usdt / total_ask_usdt) if total_ask_usdt > 0 else None

    # Large wall = any single level > 10% of total side depth
    large_buy_wall  = any((p * q) > total_bid_usdt * 0.10 for p, q in bids[:20]) if bids else False
    large_sell_wall = any((p * q) > total_ask_usdt * 0.10 for p, q in asks[:20]) if asks else False

    # Spread
    best_bid = bids[0][0] if bids else 0
    best_ask = asks[0][0] if asks else 0
    spread_pct = ((best_ask - best_bid) / best_bid * 100) if best_bid > 0 else None

    # Thin book = low total depth relative to 24h volume (calculated in scanner)
    return {
        "bid_depth_usdt":   round(total_bid_usdt, 2),
        "ask_depth_usdt":   round(total_ask_usdt, 2),
        "bid_ask_ratio":    round(ratio, 3) if ratio else None,
        "large_buy_wall":   large_buy_wall,
        "large_sell_wall":  large_sell_wall,
        "spread_pct":       round(spread_pct, 4) if spread_pct else None,
        "book_thin":        bool(total_bid_usdt + total_ask_usdt < 500_000),
    }


def get_basis(symbol: str, futures_price: float) -> dict:
    """
    Basis = (Futures Price - Spot Price) / Spot Price * 100
    Negative basis = futures trading below spot = bearish sentiment (potential squeeze if reverses)
    """
    spot = get_spot_price(symbol)
    if not spot or spot == 0:
        return {"basis_pct": None, "negative_basis": False}
    basis = (futures_price - spot) / spot * 100
    return {
        "spot_price":     round(spot, 6),
        "basis_pct":      round(basis, 4),
        "negative_basis": basis < -0.1,  # futures >0.1% below spot
    }
