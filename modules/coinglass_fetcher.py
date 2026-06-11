"""
MODULE: coinglass_fetcher.py
Fetches Open Interest history, funding rates, and liquidation data.

PRIMARY:  CoinGlass API (free tier available)
FALLBACK: Binance built-in OI endpoints (always free, no key)
"""

import time
import requests
import pandas as pd
from typing import Optional
from modules.logger import get_logger
import config

log = get_logger("coinglass")

COINGLASS_BASE = "https://open-api.coinglass.com/public/v2"
HEADERS_CG     = {"coinglassSecret": config.COINGLASS_API_KEY}


def _cg_get(endpoint: str, params: dict = {}) -> Optional[dict]:
    """Call CoinGlass API if key is set, else return None (triggers fallback)."""
    if not config.COINGLASS_API_KEY:
        return None
    try:
        url = f"{COINGLASS_BASE}/{endpoint}"
        r   = requests.get(url, params=params, headers=HEADERS_CG,
                           timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("code") == "0":
            return data.get("data")
        log.warning(f"CoinGlass error: {data.get('msg')}")
        return None
    except Exception as e:
        log.warning(f"CoinGlass request failed: {e}")
        return None


# ──────────────────────────────────────────────
#  OPEN INTEREST (with Binance fallback)
# ──────────────────────────────────────────────
def get_oi_change(symbol: str) -> dict:
    """
    Returns OI change % over the last 24h.
    CoinGlass → Binance OI history fallback.
    """
    # --- Try CoinGlass first ---
    if config.ENABLE_COINGLASS and config.COINGLASS_API_KEY:
        base = symbol.replace("USDT", "")
        data = _cg_get("indicator/open_interest", {"symbol": base, "interval": "h1"})
        if data and isinstance(data, list) and len(data) >= 24:
            oi_now  = float(data[-1].get("openInterest", 0))
            oi_24h  = float(data[-24].get("openInterest", 0))
            if oi_24h > 0:
                change_pct = (oi_now - oi_24h) / oi_24h * 100
                return {
                    "oi_usd":        round(oi_now, 0),
                    "oi_change_24h": round(change_pct, 2),
                    "oi_rising":     change_pct >= config.OI_CHANGE_THRESHOLD,
                    "source":        "coinglass",
                }

    # --- Fallback: Binance OI history ---
    try:
        from modules.binance_fetcher import get_open_interest_history, get_open_interest
        df = get_open_interest_history(symbol, period="1h", limit=25)
        oi_now_raw = get_open_interest(symbol)

        if df is not None and not df.empty:
            oi_now = float(df["sumOpenInterestValue"].iloc[-1])
            oi_24h = float(df["sumOpenInterestValue"].iloc[0])
            if oi_24h > 0:
                change_pct = (oi_now - oi_24h) / oi_24h * 100
                return {
                    "oi_usd":        round(oi_now, 0),
                    "oi_change_24h": round(change_pct, 2),
                    "oi_rising":     change_pct >= config.OI_CHANGE_THRESHOLD,
                    "source":        "binance",
                }
    except Exception as e:
        log.warning(f"OI fallback failed for {symbol}: {e}")

    return {"oi_usd": None, "oi_change_24h": None, "oi_rising": False, "source": "none"}


# ──────────────────────────────────────────────
#  FUNDING RATE (with Binance fallback)
# ──────────────────────────────────────────────
def get_funding_data(symbol: str) -> dict:
    """
    Returns current funding rate and whether it's negative (squeeze setup).
    CoinGlass → Binance fallback.
    """
    if config.ENABLE_COINGLASS and config.COINGLASS_API_KEY:
        base = symbol.replace("USDT", "")
        data = _cg_get("indicator/funding_rates_ohlc", {"symbol": base, "interval": "h8"})
        if data and isinstance(data, list):
            latest = data[-1]
            rate   = float(latest.get("c", 0))  # close funding rate
            avg_3  = sum(float(d.get("c", 0)) for d in data[-3:]) / 3
            return {
                "funding_rate":     round(rate * 100, 5),
                "funding_avg_3":    round(avg_3 * 100, 5),
                "negative_funding": rate <= config.FUNDING_RATE_MAX,
                "source":           "coinglass",
            }

    # --- Fallback: Binance ---
    try:
        from modules.binance_fetcher import get_funding_rate, get_funding_history
        rate    = get_funding_rate(symbol)
        history = get_funding_history(symbol, limit=5)
        if rate is not None:
            avg = sum(history) / len(history) if history else rate
            return {
                "funding_rate":     round(rate * 100, 5),
                "funding_avg_3":    round(avg * 100, 5),
                "negative_funding": rate <= config.FUNDING_RATE_MAX,
                "source":           "binance",
            }
    except Exception as e:
        log.warning(f"Funding fallback failed for {symbol}: {e}")

    return {"funding_rate": None, "funding_avg_3": None, "negative_funding": False, "source": "none"}


# ──────────────────────────────────────────────
#  LONG / SHORT RATIO (Binance — always free)
# ──────────────────────────────────────────────
def get_ls_ratio(symbol: str) -> dict:
    """
    Returns global + top trader L/S ratio.
    < 1.0 = more accounts are SHORT (squeeze candidate).
    """
    try:
        from modules.binance_fetcher import get_long_short_ratio, get_top_trader_ls_ratio
        global_ls = get_long_short_ratio(symbol)
        top_ls    = get_top_trader_ls_ratio(symbol)
        return {
            "ls_ratio_global": global_ls,
            "ls_ratio_top":    top_ls,
            "short_heavy":     bool(global_ls is not None and global_ls < config.LONG_SHORT_RATIO_MAX),
            "whales_short":    bool(top_ls is not None and top_ls < 1.0),
        }
    except Exception as e:
        log.warning(f"L/S ratio failed for {symbol}: {e}")
        return {"ls_ratio_global": None, "ls_ratio_top": None, "short_heavy": False, "whales_short": False}


# ──────────────────────────────────────────────
#  LIQUIDATION DATA
# ──────────────────────────────────────────────
def get_liquidation_data(symbol: str) -> dict:
    """
    Returns 24h liquidation summary.
    CoinGlass has the best data here.
    Fallback: estimate from taker buy volume imbalance.
    """
    if config.ENABLE_COINGLASS and config.COINGLASS_API_KEY:
        base = symbol.replace("USDT", "")
        data = _cg_get("indicator/liquidation_history", {"symbol": base, "interval": "h1"})
        if data and isinstance(data, list):
            recent = data[-24:]
            long_liq  = sum(float(d.get("longLiquidationUsd", 0)) for d in recent)
            short_liq = sum(float(d.get("shortLiquidationUsd", 0)) for d in recent)
            total     = long_liq + short_liq
            return {
                "liq_long_24h_usd":  round(long_liq, 0),
                "liq_short_24h_usd": round(short_liq, 0),
                "liq_total_24h_usd": round(total, 0),
                "liq_short_heavy":   short_liq > long_liq,
                "source":            "coinglass",
            }

    # --- Fallback: taker volume proxy ---
    try:
        from modules.binance_fetcher import get_liquidations_24h
        taker = get_liquidations_24h(symbol)
        return {
            "liq_long_24h_usd":  None,
            "liq_short_24h_usd": None,
            "liq_total_24h_usd": None,
            "liq_short_heavy":   None,
            "taker_buy_pct":     taker.get("taker_buy_pct"),
            "source":            "binance_proxy",
        }
    except Exception as e:
        log.warning(f"Liquidation fallback failed for {symbol}: {e}")
        return {}
