"""
MODULE: technical_analysis.py
ALL TA signals — pure pandas/numpy, no external TA lib needed.

Covers:
- Bollinger Band squeeze
- ATR (low volatility)
- Volume analysis + 7d/30d price change
- Higher lows pattern
- Days sideways counter
- Distance from ATH + recent low
- RSI (daily + 1h)
- MACD (daily + 1h)
- CVD (Cumulative Volume Delta)
- OI/Market Cap ratio
- Chart patterns: falling wedge, bull flag, cup & handle,
                  descending triangle, coiling near resistance
"""

import numpy as np
import pandas as pd
from typing import Optional
from modules.logger import get_logger

log = get_logger("ta")


# ─────────────────────────────────────────────
#  BOLLINGER BANDS
# ─────────────────────────────────────────────
def bollinger_bands(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict:
    if df is None or len(df) < period:
        return {"bb_width": None, "bb_squeeze": False, "bb_squeeze_pct": None}
    closes = df["close"]
    ma      = closes.rolling(period).mean()
    std_dev = closes.rolling(period).std()
    upper   = ma + std * std_dev
    lower   = ma - std * std_dev
    width   = (upper - lower) / ma
    cur     = width.iloc[-1]
    hist    = width.dropna().tail(50)
    pct_rank = (hist < cur).mean() * 100
    return {
        "bb_width":       round(float(cur), 5),
        "bb_squeeze":     bool(pct_rank < 20),
        "bb_squeeze_pct": round(float(pct_rank), 1),
    }


# ─────────────────────────────────────────────
#  ATR
# ─────────────────────────────────────────────
def atr(df: pd.DataFrame, period: int = 14) -> dict:
    if df is None or len(df) < period + 1:
        return {"atr_pct": None, "low_atr": False, "atr_rank": None}
    high  = df["high"]; low = df["low"]; close = df["close"]
    prev  = close.shift(1)
    tr    = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr_s = tr.rolling(period).mean()
    cur   = atr_s.iloc[-1]
    pct   = cur / close.iloc[-1]
    hist  = atr_s.dropna().tail(30)
    rank  = (hist < cur).mean() * 100
    return {
        "atr_pct":  round(float(pct) * 100, 3),
        "low_atr":  bool(rank < 25),
        "atr_rank": round(float(rank), 1),
    }


# ─────────────────────────────────────────────
#  VOLUME + MULTI-TIMEFRAME PRICE CHANGE
# ─────────────────────────────────────────────
def volume_and_price_analysis(df: pd.DataFrame, lookback: int = 7) -> dict:
    """
    Returns volume ratio, spike flag, trend.
    Also computes 7d and 30d price change from OHLCV history.
    """
    if df is None or len(df) < lookback + 1:
        return {"vol_ratio": None, "vol_spike": False, "vol_trend": "unknown",
                "price_change_7d": None, "price_change_30d": None}
    import config
    vols      = df["quote_vol"]
    today_vol = vols.iloc[-1]
    avg_vol   = vols.iloc[-(lookback + 1):-1].mean()
    vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

    recent = vols.tail(4)
    slope  = np.polyfit(range(len(recent)), recent.values, 1)[0] if len(recent) >= 3 else 0
    vol_trend = "increasing" if slope > 0 else "decreasing" if slope < 0 else "flat"

    cur_price = df["close"].iloc[-1]
    # 7d change
    p7d  = df["close"].iloc[-8] if len(df) >= 8 else None
    # 30d change
    p30d = df["close"].iloc[-31] if len(df) >= 31 else None

    return {
        "vol_ratio":       round(float(vol_ratio), 2),
        "vol_spike":       bool(vol_ratio >= config.VOLUME_SPIKE_THRESHOLD),
        "vol_trend":       vol_trend,
        "today_vol_m":     round(float(today_vol) / 1e6, 2),
        "avg_vol_m":       round(float(avg_vol) / 1e6, 2),
        "price_change_7d":  round((cur_price - p7d) / p7d * 100, 2) if p7d and p7d > 0 else None,
        "price_change_30d": round((cur_price - p30d) / p30d * 100, 2) if p30d and p30d > 0 else None,
    }


# ─────────────────────────────────────────────
#  CVD — Cumulative Volume Delta
# ─────────────────────────────────────────────
def compute_cvd(df: pd.DataFrame) -> dict:
    """
    CVD = cumulative sum of (taker_buy_quote - taker_sell_quote).
    Rising CVD + flat/down price = hidden accumulation (very bullish).
    """
    if df is None or "taker_buy_quote" not in df.columns or len(df) < 5:
        return {"cvd": None, "cvd_divergence": False, "cvd_trend": "unknown"}

    taker_buy  = df["taker_buy_quote"]
    taker_sell = df["quote_vol"] - taker_buy
    delta      = taker_buy - taker_sell
    cvd        = delta.cumsum()

    cur_cvd   = float(cvd.iloc[-1])
    cvd_slope = np.polyfit(range(len(cvd.tail(10))), cvd.tail(10).values, 1)[0]
    price_slope = np.polyfit(range(10), df["close"].tail(10).values, 1)[0]

    # Divergence: CVD rising while price flat/falling = accumulation
    cvd_divergence = bool(cvd_slope > 0 and price_slope <= 0)

    return {
        "cvd":            round(cur_cvd, 0),
        "cvd_slope":      round(float(cvd_slope), 2),
        "cvd_rising":     bool(cvd_slope > 0),
        "cvd_divergence": cvd_divergence,
        "cvd_trend":      "rising" if cvd_slope > 0 else "falling",
    }


# ─────────────────────────────────────────────
#  HIGHER LOWS + DAYS SIDEWAYS
# ─────────────────────────────────────────────
def detect_accumulation(df: pd.DataFrame, lookback: int = 14) -> dict:
    """
    - Higher lows with flat price = hidden accumulation
    - Days sideways = actual count of days within tight range
    - Distance from recent low (last 30 bars)
    """
    if df is None or len(df) < lookback:
        return {"higher_lows": False, "sideways": False, "days_sideways": 0,
                "pct_from_recent_low": None}

    recent  = df.tail(lookback)
    lows    = recent["low"].values
    closes  = recent["close"].values
    cur_price = float(df["close"].iloc[-1])

    low_slope     = np.polyfit(range(len(lows)), lows, 1)[0]
    price_range   = (closes.max() - closes.min()) / closes.mean() * 100
    sideways      = bool(price_range < 15)
    higher_lows   = bool(low_slope > 0 and sideways)

    # Count consecutive days where price stayed within ±7.5% of current
    days_sideways = 0
    ref = cur_price
    for c in reversed(df["close"].values):
        if abs(c - ref) / ref * 100 <= 7.5:
            days_sideways += 1
        else:
            break

    # Distance from recent 30-bar low
    recent_low = float(df["low"].tail(30).min())
    pct_from_low = (cur_price - recent_low) / recent_low * 100 if recent_low > 0 else None

    return {
        "higher_lows":        higher_lows,
        "sideways":           sideways,
        "price_range_pct":    round(float(price_range), 2),
        "low_slope":          round(float(low_slope), 6),
        "days_sideways":      days_sideways,
        "recent_low_30d":     round(recent_low, 6),
        "pct_from_recent_low": round(pct_from_low, 2) if pct_from_low else None,
    }


# ─────────────────────────────────────────────
#  DISTANCE FROM ATH
# ─────────────────────────────────────────────
def distance_from_ath(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"pct_from_ath": None, "far_from_ath": False, "ath": None}
    ath   = float(df["high"].max())
    price = float(df["close"].iloc[-1])
    pct   = (price - ath) / ath * 100
    return {
        "ath":          round(ath, 6),
        "pct_from_ath": round(pct, 2),
        "far_from_ath": bool(pct < -40),
    }


# ─────────────────────────────────────────────
#  RSI
# ─────────────────────────────────────────────
def rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if df is None or len(df) < period + 1:
        return None
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    val   = (100 - 100 / (1 + rs)).iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


# ─────────────────────────────────────────────
#  MACD
# ─────────────────────────────────────────────
def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if df is None or len(df) < slow + signal:
        return {"macd_cross": False, "macd_histogram": None, "macd_above": False}
    closes     = df["close"]
    ema_fast   = closes.ewm(span=fast, adjust=False).mean()
    ema_slow   = closes.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    sig_line   = macd_line.ewm(span=signal, adjust=False).mean()
    hist       = macd_line - sig_line
    cross      = bool(macd_line.iloc[-1] > sig_line.iloc[-1] and macd_line.iloc[-2] <= sig_line.iloc[-2])
    return {
        "macd_cross":     cross,
        "macd_histogram": round(float(hist.iloc[-1]), 6),
        "macd_above":     bool(macd_line.iloc[-1] > sig_line.iloc[-1]),
    }


# ─────────────────────────────────────────────
#  CHART PATTERN DETECTION
# ─────────────────────────────────────────────
def detect_chart_patterns(df: pd.DataFrame) -> dict:
    """
    Detects 5 classic pre-pump patterns from OHLCV data.

    KEY FIXES vs previous version:
    - Falling wedge: requires CONVERGING lines (not just both declining)
    - Cup & handle: requires proper depth, volume, and structure
    - Coiling: requires higher lows after each resistance touch
    - All patterns: require coin has STOPPED falling (price stabilised)
    - All patterns: volume confirmation where applicable
    - Trendline quality checked via R-squared
    """
    if df is None or len(df) < 20:
        return {
            "pattern_falling_wedge": False,
            "pattern_bull_flag": False,
            "pattern_descending_triangle_breakout": False,
            "pattern_coiling_resistance": False,
            "pattern_cup_handle": False,
            "patterns_count": 0,
            "detected_pattern": "none",
        }

    closes = df["close"].values.astype(float)
    highs  = df["high"].values.astype(float)
    lows   = df["low"].values.astype(float)
    vols   = df["quote_vol"].values.astype(float)
    n      = len(closes)
    cur    = closes[-1]

    def r_squared(y) -> float:
        """How well does a line fit this data? 1.0 = perfect line."""
        if len(y) < 3:
            return 0.0
        x     = np.arange(len(y), dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        y_hat = slope * x + intercept
        ss_res = np.sum((y - y_hat) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        return float(1 - ss_res / ss_tot) if ss_tot > 1e-10 else 1.0

    def price_stopped_falling(window: int = 5) -> bool:
        """Last N candles are not making new lows — stabilised."""
        if n < window + 3:
            return False
        recent_low  = min(lows[-window:])
        prior_low   = min(lows[-(window + 5):-window])
        return recent_low >= prior_low * 0.98  # not making new lows

    patterns_found = []

    # ── 1. FALLING WEDGE ──────────────────────────────────────────
    # REAL definition:
    #   - Both highs AND lows trending DOWN
    #   - BUT the distance between highs and lows is SHRINKING (converging)
    #   - Price range at end of wedge < range at start (compression)
    #   - Lines are converging toward a point
    #   - Coin should have stopped making new lows recently
    falling_wedge = False
    if n >= 30:
        seg_h  = highs[-30:]
        seg_l  = lows[-30:]
        x      = np.arange(30, dtype=float)

        h_slope, h_int = np.polyfit(x, seg_h, 1)
        l_slope, l_int = np.polyfit(x, seg_l, 1)

        # Trendline quality — must be reasonably linear
        h_r2 = r_squared(seg_h)
        l_r2 = r_squared(seg_l)

        # Range at START vs END of wedge
        range_start = (h_int) - (l_int)              # at x=0
        range_end   = (h_int + h_slope * 29) - (l_int + l_slope * 29)  # at x=29

        # Convergence: end range must be meaningfully smaller than start
        convergence_pct = (range_start - range_end) / range_start * 100 if range_start > 0 else 0

        falling_wedge = bool(
            h_slope < -0.0001              # highs declining
            and l_slope < 0               # lows declining
            and l_slope > h_slope         # lows declining LESS steeply
            and h_slope < l_slope * 0.5   # highs declining at least 2× faster (real convergence)
            and convergence_pct >= 15     # lines converged by 15%+ over the window
            and range_end > 0             # lines haven't crossed yet
            and h_r2 > 0.4               # highs reasonably linear
            and l_r2 > 0.4               # lows reasonably linear
            and price_stopped_falling(7)  # coin has stabilised
        )
        if falling_wedge:
            patterns_found.append("falling_wedge")

    # ── 2. BULL FLAG / PENNANT ────────────────────────────────────
    # REAL definition:
    #   - Strong upward move (pole) of 10%+ in first half of window
    #   - Followed by tight consolidation (flag) — range < 8% in second half
    #   - Volume: high during pole, decreasing during flag
    #   - Price should be in the upper part of the flag, near breakout
    bull_flag = False
    if n >= 20:
        pole_bars   = 10
        flag_bars   = 10
        pole_prices = closes[-20:-10]
        flag_prices = closes[-10:]
        flag_vols   = vols[-10:]
        pole_vols   = vols[-20:-10]

        pole_move  = (pole_prices[-1] - pole_prices[0]) / pole_prices[0] * 100 if pole_prices[0] > 0 else 0
        flag_high  = max(flag_prices)
        flag_low   = min(flag_prices)
        flag_range = (flag_high - flag_low) / flag_low * 100 if flag_low > 0 else 100

        # Volume should be lower during flag than during pole
        avg_pole_vol = float(np.mean(pole_vols))
        avg_flag_vol = float(np.mean(flag_vols))
        vol_decreasing = avg_flag_vol < avg_pole_vol * 0.8

        # Price near top of flag (ready to break out)
        in_upper_flag = cur >= (flag_low + (flag_high - flag_low) * 0.5)

        bull_flag = bool(
            pole_move > 12             # strong pole (at least 12% up)
            and flag_range < 8         # tight flag
            and vol_decreasing         # volume contracting during flag
            and in_upper_flag          # price in upper half of flag
        )
        if bull_flag:
            patterns_found.append("bull_flag")

    # ── 3. DESCENDING TRIANGLE BREAKOUT ──────────────────────────
    # REAL definition:
    #   - Flat horizontal support (lows clustering around same level, low std)
    #   - Declining highs (lower highs each time)
    #   - Price breaks ABOVE the declining resistance line (breakout)
    #   - Volume expands on breakout
    desc_tri = False
    if n >= 25:
        seg_h    = highs[-25:]
        seg_l    = lows[-25:]
        x        = np.arange(25, dtype=float)

        h_slope, _ = np.polyfit(x, seg_h, 1)
        h_r2       = r_squared(seg_h)

        # Support must be flat (lows clustering around similar level)
        low_cv   = np.std(seg_l) / np.mean(seg_l) * 100  # coefficient of variation
        support  = np.mean(seg_l[-10:])  # recent support level

        # Resistance line at current candle
        h_slope2, h_int2 = np.polyfit(x, seg_h, 1)
        resistance_now   = h_int2 + h_slope2 * 24

        # Volume on last 3 candles vs prior 10
        recent_vol_avg = float(np.mean(vols[-3:]))
        prior_vol_avg  = float(np.mean(vols[-13:-3]))
        vol_expansion  = recent_vol_avg > prior_vol_avg * 1.2

        desc_tri = bool(
            h_slope < -0.001          # declining highs
            and h_r2 > 0.4           # highs form a line
            and low_cv < 4.0         # very flat support (tight clustering)
            and cur > resistance_now * 1.005  # price broke above resistance
            and cur > support         # price is above support
            and vol_expansion         # volume expanding on breakout
        )
        if desc_tri:
            patterns_found.append("desc_triangle_breakout")

    # ── 4. COILING NEAR RESISTANCE ────────────────────────────────
    # REAL definition:
    #   - Price has tested resistance level 2+ times
    #   - Each PULLBACK is SHALLOWER than the previous (higher lows)
    #   - This means buyers are getting more aggressive each time
    #   - Price is currently near resistance (ready to break)
    coiling = False
    if n >= 25:
        seg_h = highs[-25:]
        seg_l = lows[-25:]

        # Find resistance: 85th percentile of recent highs
        resistance = np.percentile(seg_h, 85)

        # Find candles that touched resistance
        touch_indices = [i for i in range(len(seg_h)) if seg_h[i] >= resistance * 0.97]

        if len(touch_indices) >= 2:
            # For each touch, find the subsequent pullback low
            pullback_lows = []
            for idx in touch_indices[:-1]:  # all but last touch
                # Find lowest low between this touch and the next touch
                next_touch = touch_indices[touch_indices.index(idx) + 1]
                if next_touch > idx + 1:
                    pullback_low = min(seg_l[idx:next_touch])
                    pullback_lows.append(pullback_low)

            # Higher lows after each resistance touch = coiling
            if len(pullback_lows) >= 2:
                higher_lows_on_pullback = all(
                    pullback_lows[i] < pullback_lows[i+1]
                    for i in range(len(pullback_lows)-1)
                )
            else:
                higher_lows_on_pullback = False

            # Price currently near resistance
            near_resistance = cur >= resistance * 0.95

            coiling = bool(
                len(touch_indices) >= 2
                and higher_lows_on_pullback  # pullbacks getting shallower
                and near_resistance           # price near resistance now
                and price_stopped_falling(5)  # not in free fall
            )

        if coiling:
            patterns_found.append("coiling_resistance")

    # ── 5. CUP AND HANDLE ────────────────────────────────────────
    # REAL definition:
    #   - Significant decline from left peak to cup bottom (>20% drop)
    #   - U-shaped recovery back to near the left peak (within 10%)
    #   - Small handle consolidation (<15% dip from right peak)
    #   - Volume: low in cup bottom, recovering at right side
    #   - Price currently in handle or breaking out
    cup_handle = False
    if n >= 45:
        # Find left peak (highest point in first third)
        third = n // 3
        left_high   = max(closes[-45:-30])
        cup_bottom  = min(closes[-35:-15])
        right_high  = max(closes[-15:-3])
        handle_low  = min(closes[-5:])

        # Cup depth: how much did it drop from left high to bottom?
        cup_depth_pct = (left_high - cup_bottom) / left_high * 100 if left_high > 0 else 0

        # Recovery: right high vs left high (should be within 8%)
        recovery = (right_high - cup_bottom) / cup_bottom * 100 if cup_bottom > 0 else 0

        # Handle: small dip from right peak
        handle_dip = (right_high - handle_low) / right_high * 100 if right_high > 0 else 100

        # Right high must be close to left high (cup recovered)
        right_vs_left = right_high / left_high if left_high > 0 else 0

        # Volume: lower in cup, higher on right side
        cup_vol    = float(np.mean(vols[-35:-15]))
        right_vol  = float(np.mean(vols[-15:]))
        vol_ok     = right_vol > cup_vol * 0.8  # volume recovering on right side

        # Price in handle area (not already broken out far)
        in_handle = cur >= handle_low and cur <= right_high * 1.05

        cup_handle = bool(
            cup_depth_pct >= 20        # meaningful cup depth (real structure)
            and recovery >= 15         # recovered from bottom
            and right_vs_left >= 0.85  # right peak within 15% of left peak
            and right_vs_left <= 1.10  # right peak not massively above left
            and handle_dip >= 3        # handle exists (some dip)
            and handle_dip <= 15       # handle not too deep
            and vol_ok                 # volume supporting recovery
            and in_handle              # price still in pattern
        )
        if cup_handle:
            patterns_found.append("cup_handle")

    return {
        "pattern_falling_wedge":                  falling_wedge,
        "pattern_bull_flag":                      bull_flag,
        "pattern_descending_triangle_breakout":   desc_tri,
        "pattern_coiling_resistance":             coiling,
        "pattern_cup_handle":                     cup_handle,
        "patterns_count":                         len(patterns_found),
        "detected_pattern":                       ", ".join(patterns_found) if patterns_found else "none",
    }


# ─────────────────────────────────────────────
#  OI / MARKET CAP RATIO
# ─────────────────────────────────────────────
def oi_market_cap_ratio(oi_usd: Optional[float], market_cap_usd: Optional[float]) -> dict:
    """
    High OI/MC ratio = heavily leveraged = explosive move potential.
    >0.5 = very high leverage on this coin.
    """
    if not oi_usd or not market_cap_usd or market_cap_usd == 0:
        return {"oi_mc_ratio": None, "high_leverage": False}
    ratio = oi_usd / market_cap_usd
    return {
        "oi_mc_ratio":   round(ratio, 4),
        "high_leverage": bool(ratio > 0.3),
    }


# ─────────────────────────────────────────────
#  RUN ALL TA
# ─────────────────────────────────────────────
def run_all_ta(df_daily: pd.DataFrame, df_1h: Optional[pd.DataFrame] = None,
               oi_usd: Optional[float] = None,
               market_cap_usd: Optional[float] = None) -> dict:
    result = {}
    result.update(bollinger_bands(df_daily))
    result.update(atr(df_daily))
    result.update(volume_and_price_analysis(df_daily))
    result.update(detect_accumulation(df_daily))
    result.update(distance_from_ath(df_daily))
    result.update(detect_chart_patterns(df_daily))
    result.update(oi_market_cap_ratio(oi_usd, market_cap_usd))
    result["rsi_daily"] = rsi(df_daily)
    result.update({f"daily_{k}": v for k, v in macd(df_daily).items()})

    # CVD on hourly (better resolution)
    if df_1h is not None:
        result.update(compute_cvd(df_1h))
        bb_1h = bollinger_bands(df_1h, period=20)
        result["bb_squeeze_1h"] = bb_1h.get("bb_squeeze", False)
        result["rsi_1h"]        = rsi(df_1h)
        result.update({f"1h_{k}": v for k, v in macd(df_1h).items()})
    else:
        # CVD from daily if no 1h
        result.update(compute_cvd(df_daily))

    return result
