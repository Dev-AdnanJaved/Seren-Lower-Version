"""
MODULE: btc_market.py
BTC/Market trend filter — the most important context signal.

Logic:
  If BTC is crashing → suppress alt alerts (alts bleed harder)
  If BTC is stable/pumping → allow alt alerts
  If BTC just pumped → alts may follow (rotation signal)

All free, uses Binance API only.
"""

import requests
import pandas as pd
import numpy as np
from typing import Optional
from modules.logger import get_logger
import config

log = get_logger("btc_market")

BASE = "https://fapi.binance.com"


def _get(url, params={}):
    try:
        r = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"BTC market fetch failed: {e}")
        return None


def get_btc_context() -> dict:
    """
    Returns BTC trend data used to:
    1. Filter out bad market conditions (BTC crashing = avoid alts)
    2. Detect rotation (BTC dominance dropping = money moving to alts)
    3. Detect BTC consolidation (sideways BTC = alts can pump freely)

    Returns dict with:
      btc_price           — current BTC price
      btc_change_1h       — 1h % change
      btc_change_4h       — 4h % change
      btc_change_24h      — 24h % change
      btc_trend           — 'bull' | 'bear' | 'sideways'
      btc_crashing        — True if BTC down >3% in 4h (suppress all alt alerts)
      btc_pumping         — True if BTC up >5% in 4h (rotation to alts coming)
      btc_sideways        — True if BTC range < 2% in 4h (best for alt pumps)
      btc_dominance       — BTC.D % (from CoinGecko, if available)
      btc_dom_falling     — True if dominance falling (alt season signal)
      market_ok_for_alts  — Final verdict: True = good conditions for alt pumps
    """
    result = {
        "btc_price": None, "btc_change_1h": None,
        "btc_change_4h": None, "btc_change_24h": None,
        "btc_trend": "unknown", "btc_crashing": False,
        "btc_pumping": False, "btc_sideways": False,
        "btc_dominance": None, "btc_dom_falling": False,
        "market_ok_for_alts": True,  # default True so bot works if BTC data fails
    }

    # ── BTC 4h OHLCV ──
    klines = _get(f"{BASE}/fapi/v1/klines",
                  {"symbol": "BTCUSDT", "interval": "4h", "limit": 10})
    if klines:
        df = pd.DataFrame(klines, columns=[
            "open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
        ])
        for col in ["open","high","low","close"]:
            df[col] = pd.to_numeric(df[col])

        cur   = float(df["close"].iloc[-1])
        prev4 = float(df["open"].iloc[-1])   # 4h ago
        prev1 = float(df["close"].iloc[-2])  # ~4h ago close

        result["btc_price"]      = round(cur, 2)
        result["btc_change_4h"]  = round((cur - prev4) / prev4 * 100, 2)
        result["btc_change_1h"]  = round((cur - prev1) / prev1 * 100, 2)

        # 4h range %
        high4 = float(df["high"].iloc[-1])
        low4  = float(df["low"].iloc[-1])
        range4 = (high4 - low4) / low4 * 100

        # Trend flags
        chg4 = result["btc_change_4h"]
        result["btc_crashing"]  = chg4 < -3.0
        result["btc_pumping"]   = chg4 > 5.0
        result["btc_sideways"]  = abs(chg4) < 2.0 and range4 < 3.0
        result["btc_trend"]     = ("bear" if chg4 < -2 else
                                   "bull" if chg4 > 2 else "sideways")

    # ── BTC 24h change ──
    ticker = _get(f"{BASE}/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT"})
    if ticker:
        result["btc_change_24h"] = round(float(ticker.get("priceChangePercent", 0)), 2)

    # ── BTC Dominance from CoinGecko (free) ──
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", {})
            dom = data.get("market_cap_percentage", {}).get("btc")
            if dom:
                result["btc_dominance"] = round(float(dom), 2)
                # Check if dominance is falling (alt season incoming)
                # We store the last value in a simple file-based cache
                _check_dom_trend(result)
    except Exception as e:
        log.debug(f"BTC dominance fetch failed: {e}")

    # ── Final verdict ──
    # Market is BAD for alts if BTC is crashing hard
    result["market_ok_for_alts"] = not result["btc_crashing"]

    log.info(
        f"BTC: ${result.get('btc_price','?')} | "
        f"4h: {result.get('btc_change_4h','?')}% | "
        f"trend: {result.get('btc_trend','?')} | "
        f"ok_for_alts: {result.get('market_ok_for_alts')}"
    )
    return result


def _check_dom_trend(result: dict):
    """Cache BTC dominance to detect if it's falling (alt season)."""
    import os, json
    cache_path = "data/btc_dom_cache.json"
    os.makedirs("data", exist_ok=True)
    cur_dom = result.get("btc_dominance")
    if not cur_dom:
        return
    try:
        prev_dom = None
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                prev_dom = json.load(f).get("dom")
        with open(cache_path, "w") as f:
            json.dump({"dom": cur_dom}, f)
        if prev_dom:
            result["btc_dom_falling"] = bool(cur_dom < prev_dom - 0.3)
    except Exception:
        pass


def apply_btc_filter(score: int, btc_ctx: dict) -> tuple:
    """
    Adjusts score based on BTC market conditions.
    Returns (adjusted_score, penalty_reason)
    """
    if not config.ENABLE_BTC_FILTER:
        return score, None

    if btc_ctx.get("btc_crashing"):
        chg = btc_ctx.get("btc_change_4h", 0)
        penalty = config.BTC_CRASH_SCORE_PENALTY
        new_score = max(0, score - penalty)
        return new_score, f"🔴 BTC crashing {chg:.1f}% in 4h — penalty -{penalty}"

    return score, None
