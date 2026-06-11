"""
MODULE: backtester.py
Two-mode backtesting system:

MODE 1 — Historical Pump Scanner (OHLCV-based, unlimited history)
  - Fetches all Binance Futures symbols
  - Scans OHLCV history to find where price moved 30%+ in 1-7 days
  - Goes back to 7 days BEFORE each pump
  - Checks which TA signals were present at that pre-pump point
  - Aggregates: which signals appeared most often before real pumps
  - Data available: OHLCV (unlimited), OI/funding/LS (last 30 days only)

MODE 2 — Alert Accuracy Tracker (scan_log.csv based)
  - Uses your existing scan_log.csv and pump_tracker.csv
  - Compares: what signals were present when alert fired
  - vs: did the coin actually pump 20%+ afterwards
  - Much more accurate than Mode 1 because it uses ALL signals
  - Gets better over time as you accumulate more scan data

OUTPUT:
  - Signal accuracy table (% of pumps each signal preceded)
  - Average metric values before pumps vs non-pumps
  - Suggested weight adjustments
  - Saved to data/backtest_results.json
"""

import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional
from modules.logger import get_logger
import config

log = get_logger("backtester")

BACKTEST_PATH = "data/backtest_results.json"


def _get(url, params={}, timeout=15):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"Request failed {url}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  PUMP DETECTION
# ══════════════════════════════════════════════════════════════════════

def find_pumps_in_ohlcv(df: pd.DataFrame,
                         min_pump_pct: float = 30.0,
                         max_days: int = 7,
                         lookback_candles: int = 90) -> list:
    """
    Scan OHLCV data and find all pumps.
    A pump = price rises min_pump_pct% within max_days candles.

    Returns list of dicts:
      {
        pump_start_idx: int,    # candle index where pump started
        pump_end_idx:   int,    # candle index where pump peaked
        pump_pct:       float,  # how much it pumped
        pump_days:      int,    # how many candles it took
        pre_pump_idx:   int,    # 7 candles before pump (where to measure signals)
        pump_start_date: str,
        pump_end_date:  str,
      }
    """
    if df is None or len(df) < max_days + 10:
        return []

    pumps     = []
    n         = len(df)
    start_idx = max(0, n - lookback_candles)

    for i in range(start_idx, n - max_days):
        base_price = float(df["low"].iloc[i])
        if base_price <= 0:
            continue

        # Look forward up to max_days for a pump
        best_pump  = 0
        best_end   = i
        for j in range(i + 1, min(i + max_days + 1, n)):
            high = float(df["high"].iloc[j])
            pct  = (high - base_price) / base_price * 100
            if pct > best_pump:
                best_pump = pct
                best_end  = j

        if best_pump >= min_pump_pct:
            # Make sure this isn't overlapping with a recent pump we already found
            overlap = any(
                abs(p["pump_start_idx"] - i) < max_days
                for p in pumps
            )
            if not overlap:
                pre_idx = max(0, i - 7)
                pumps.append({
                    "pump_start_idx":   i,
                    "pump_end_idx":     best_end,
                    "pump_pct":         round(best_pump, 1),
                    "pump_days":        best_end - i,
                    "pre_pump_idx":     pre_idx,
                    "pump_start_date":  str(df["open_time"].iloc[i])[:10],
                    "pump_end_date":    str(df["open_time"].iloc[best_end])[:10],
                })

    return pumps


# ══════════════════════════════════════════════════════════════════════
#  TA SIGNALS AT A SPECIFIC HISTORICAL POINT
# ══════════════════════════════════════════════════════════════════════

def compute_ta_at_index(df: pd.DataFrame, idx: int) -> dict:
    """
    Compute all TA signals using only data available up to index `idx`.
    This simulates what the bot would have seen at that point in time.
    """
    from modules.technical_analysis import (
        bollinger_bands, atr, volume_and_price_analysis,
        detect_accumulation, distance_from_ath, rsi, macd,
        detect_chart_patterns, compute_cvd
    )

    # Slice data up to and including idx (no future leakage)
    df_slice = df.iloc[:idx + 1].copy().reset_index(drop=True)

    if len(df_slice) < 15:
        return {}

    signals = {}
    signals.update(bollinger_bands(df_slice))
    signals.update(atr(df_slice))
    signals.update(volume_and_price_analysis(df_slice))
    signals.update(detect_accumulation(df_slice))
    signals.update(distance_from_ath(df_slice))
    signals.update(detect_chart_patterns(df_slice))
    signals.update(compute_cvd(df_slice))
    signals["rsi_daily"]     = rsi(df_slice)
    signals["daily_macd_cross"] = macd(df_slice).get("macd_cross", False)

    signals["price_at_point"] = float(df_slice["close"].iloc[-1])

    return signals


# ══════════════════════════════════════════════════════════════════════
#  MODE 1: HISTORICAL PUMP SCANNER
# ══════════════════════════════════════════════════════════════════════

def run_historical_backtest(
    symbols:       list   = None,
    lookback_days: int    = 90,
    min_pump_pct:  float  = 30.0,
    max_pump_days: int    = 7,
    progress_cb           = None,   # callback(current, total, message)
) -> dict:
    """
    Scans all Binance Futures symbols for historical pumps.
    For each pump found, checks which TA signals were present 7 days before.
    Aggregates results to show which signals best predict pumps.

    Args:
      symbols:       list of BTCUSDT-style symbols. None = fetch all from Binance
      lookback_days: how many days of history to scan
      min_pump_pct:  what counts as a pump (default 30%)
      max_pump_days: pump must happen within this many days
      progress_cb:   optional callback for progress updates

    Returns: dict with results
    """
    # Get symbols if not provided
    if symbols is None:
        log.info("Fetching all Binance Futures symbols...")
        data = _get("https://fapi.binance.com/fapi/v1/exchangeInfo")
        if not data:
            return {"error": "Failed to fetch Binance symbols"}
        symbols = [
            s["symbol"] for s in data.get("symbols", [])
            if s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        ]
    log.info(f"Scanning {len(symbols)} symbols for pumps ({lookback_days}d lookback, >{min_pump_pct}%)")

    all_pumps      = []
    signal_counts  = {}   # signal_name → count of pumps where it was True
    signal_values  = {}   # signal_name → list of values before pump
    total_symbols  = len(symbols)
    errors         = 0

    # TA boolean signals to track
    BOOL_SIGNALS = [
        "vol_spike", "bb_squeeze", "low_atr", "higher_lows", "sideways",
        "far_from_ath", "cvd_rising", "cvd_divergence",
        "pattern_falling_wedge", "pattern_bull_flag",
        "pattern_coiling_resistance", "pattern_descending_triangle_breakout",
        "pattern_cup_handle", "daily_macd_cross",
    ]
    # Numeric signals to average
    NUM_SIGNALS = [
        "vol_ratio", "atr_pct", "rsi_daily", "bb_width",
        "days_sideways", "pct_from_ath", "price_range_pct",
    ]

    for sig in BOOL_SIGNALS:
        signal_counts[sig] = 0
    for sig in NUM_SIGNALS:
        signal_values[sig] = []

    for idx, sym in enumerate(symbols):
        if progress_cb:
            progress_cb(idx + 1, total_symbols, f"Scanning {sym}...")

        try:
            # Fetch daily OHLCV (enough for lookback + TA calculation buffer)
            limit = min(lookback_days + 60, 1000)
            data  = _get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": sym, "interval": "1d", "limit": limit}
            )
            if not data or len(data) < 20:
                continue

            df = pd.DataFrame(data, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","quote_vol","trades","taker_buy_base",
                "taker_buy_quote","ignore"
            ])
            for col in ["open","high","low","close","volume","quote_vol","taker_buy_quote"]:
                df[col] = pd.to_numeric(df[col])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")

            # Find pumps in this coin's history
            pumps = find_pumps_in_ohlcv(
                df,
                min_pump_pct=min_pump_pct,
                max_days=max_pump_days,
                lookback_candles=lookback_days
            )

            if not pumps:
                continue

            log.info(f"  {sym}: {len(pumps)} pump(s) found")

            # For each pump, compute TA at the pre-pump point
            for pump in pumps:
                pre_idx = pump["pre_pump_idx"]
                ta      = compute_ta_at_index(df, pre_idx)
                if not ta:
                    continue

                pump["symbol"]   = sym
                pump["ta_before"] = ta
                all_pumps.append(pump)

                # Count boolean signals
                for sig in BOOL_SIGNALS:
                    if ta.get(sig):
                        signal_counts[sig] += 1

                # Collect numeric values
                for sig in NUM_SIGNALS:
                    val = ta.get(sig)
                    if val is not None:
                        signal_values[sig].append(float(val))

        except Exception as e:
            errors += 1
            log.debug(f"Error on {sym}: {e}")
            continue

        time.sleep(0.1)  # rate limit

    # ── Compute accuracy stats ──────────────────────────────────
    n_pumps = len(all_pumps)
    if n_pumps == 0:
        return {
            "error":      "No pumps found",
            "symbols":    len(symbols),
            "lookback":   lookback_days,
            "min_pump":   min_pump_pct,
        }

    signal_accuracy = {}
    for sig in BOOL_SIGNALS:
        pct = signal_counts[sig] / n_pumps * 100
        signal_accuracy[sig] = {
            "present_before_pumps": signal_counts[sig],
            "total_pumps":          n_pumps,
            "accuracy_pct":         round(pct, 1),
        }

    signal_averages = {}
    for sig in NUM_SIGNALS:
        vals = signal_values[sig]
        if vals:
            signal_averages[sig] = {
                "mean":   round(float(np.mean(vals)), 3),
                "median": round(float(np.median(vals)), 3),
                "min":    round(float(np.min(vals)), 3),
                "max":    round(float(np.max(vals)), 3),
            }

    # ── Compute suggested weights ────────────────────────────────
    # Signals that appear before >60% of pumps should have high weight
    # Signals that appear before <30% of pumps should have low weight
    suggested_weights = {}
    for sig in BOOL_SIGNALS:
        acc = signal_accuracy[sig]["accuracy_pct"]
        if acc >= 70:
            suggested_weights[sig] = 3
        elif acc >= 55:
            suggested_weights[sig] = 2
        elif acc >= 40:
            suggested_weights[sig] = 1
        else:
            suggested_weights[sig] = 0

    # Top pumps sorted by size
    top_pumps = sorted(all_pumps, key=lambda x: x["pump_pct"], reverse=True)[:20]

    result = {
        "mode":             "historical",
        "run_at":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "symbols_scanned":  len(symbols),
        "lookback_days":    lookback_days,
        "min_pump_pct":     min_pump_pct,
        "total_pumps_found": n_pumps,
        "errors":           errors,
        "signal_accuracy":  signal_accuracy,
        "signal_averages":  signal_averages,
        "suggested_weights": suggested_weights,
        "top_pumps":        [
            {
                "symbol":     p["symbol"],
                "pump_pct":   p["pump_pct"],
                "pump_days":  p["pump_days"],
                "pump_date":  p["pump_start_date"],
                "pre_pump_signals": {
                    k: v for k, v in p["ta_before"].items()
                    if k in BOOL_SIGNALS and v
                },
                "pre_pump_rsi": p["ta_before"].get("rsi_daily"),
                "pre_pump_vol_ratio": p["ta_before"].get("vol_ratio"),
                "pre_pump_days_sideways": p["ta_before"].get("days_sideways"),
                "pre_pump_pattern": p["ta_before"].get("detected_pattern", "none"),
            }
            for p in top_pumps
        ],
    }

    # Save to disk
    os.makedirs("data", exist_ok=True)
    with open(BACKTEST_PATH, "w") as f:
        json.dump(result, f, indent=2, default=str)

    log.info(f"Backtest done: {n_pumps} pumps found across {len(symbols)} coins")
    return result


# ══════════════════════════════════════════════════════════════════════
#  MODE 2: ALERT ACCURACY TRACKER (uses scan_log.csv)
# ══════════════════════════════════════════════════════════════════════

def run_alert_accuracy_analysis() -> dict:
    """
    Reads scan_log.csv and pump_tracker.csv.
    Compares: which signals were present at alert time
    vs: did the coin actually pump afterwards.

    This is much more accurate than Mode 1 because:
    - Uses ALL 28 signals (not just TA)
    - Based on your actual bot's data, not simulated

    Returns: dict with per-signal accuracy stats.
    """
    scan_path  = config.CSV_LOG_PATH
    pump_path  = "data/pump_tracker.csv"

    if not os.path.isfile(scan_path):
        return {"error": f"No scan log found at {scan_path}. Run the bot first."}
    if not os.path.isfile(pump_path):
        return {"error": "No pump tracker found. Run bot for at least 10 days."}

    try:
        scan_df = pd.read_csv(scan_path)
        pump_df = pd.read_csv(pump_path)
    except Exception as e:
        return {"error": f"Failed to read CSV: {e}"}

    # Only use completed pump tracker rows
    completed = pump_df[pump_df["pumped"].isin(["YES", "NO"])].copy()
    if len(completed) == 0:
        return {"error": "No completed pump outcomes yet. Wait 10 days after first alerts."}

    # Join on symbol and alert time (approximate)
    # scan_log has every scan, pump_tracker has only alerted coins
    # Match by symbol — take the scan_log row closest to alert time

    results      = []
    signal_cols  = [
        "vol_spike", "oi_rising", "negative_funding", "short_heavy",
        "cvd_divergence", "bb_squeeze", "low_atr", "higher_lows",
        "far_from_ath", "small_market_cap", "high_leverage", "negative_basis",
        "whales_short", "low_float", "social_spike", "fear_greed_low",
        "ob_large_buy_wall_agg", "daily_macd_cross",
        "pattern_falling_wedge", "pattern_bull_flag",
        "pattern_coiling_resistance", "cvd_rising",
    ]
    # Only use columns that actually exist in the CSV
    available_signals = [c for c in signal_cols if c in scan_df.columns]

    for _, pump_row in completed.iterrows():
        sym       = pump_row.get("symbol")
        pumped    = pump_row.get("pumped") == "YES"
        alert_t   = pump_row.get("alert_time", "")
        pump_pct  = pump_row.get("pump_pct")

        # Find matching scan_log rows for this symbol around alert time
        sym_rows = scan_df[scan_df["symbol"] == sym]
        if sym_rows.empty:
            continue

        # Take the row closest to alert time
        if "timestamp" in sym_rows.columns and alert_t:
            sym_rows = sym_rows.copy()
            sym_rows["time_diff"] = (
                pd.to_datetime(sym_rows["timestamp"], errors="coerce") -
                pd.to_datetime(alert_t, errors="coerce")
            ).abs()
            row = sym_rows.nsmallest(1, "time_diff").iloc[0]
        else:
            row = sym_rows.iloc[-1]

        entry = {"symbol": sym, "pumped": pumped, "pump_pct": pump_pct}
        for sig in available_signals:
            val = row.get(sig)
            if pd.isna(val):
                entry[sig] = None
            else:
                try:
                    entry[sig] = bool(val) if str(val).lower() in ("true","false") else val
                except Exception:
                    entry[sig] = None
        entry["score"] = row.get("score")
        results.append(entry)

    if not results:
        return {"error": "Could not match scan logs to pump outcomes."}

    results_df = pd.DataFrame(results)
    pumped_df  = results_df[results_df["pumped"] == True]
    failed_df  = results_df[results_df["pumped"] == False]

    signal_accuracy = {}
    for sig in available_signals:
        if sig not in results_df.columns:
            continue
        # Among coins that pumped: how often was this signal True?
        if len(pumped_df) > 0:
            pump_rate = pumped_df[sig].apply(
                lambda x: bool(x) if x is not None else False
            ).mean() * 100
        else:
            pump_rate = 0

        # Among coins that did NOT pump: how often was this signal True?
        if len(failed_df) > 0:
            fail_rate = failed_df[sig].apply(
                lambda x: bool(x) if x is not None else False
            ).mean() * 100
        else:
            fail_rate = 0

        # Lift = how much more often it appears before pumps vs non-pumps
        lift = pump_rate / fail_rate if fail_rate > 0 else pump_rate

        signal_accuracy[sig] = {
            "rate_before_pumps":    round(pump_rate, 1),
            "rate_before_failures": round(fail_rate, 1),
            "lift":                 round(lift, 2),
        }

    # Score distribution
    score_pumped = pumped_df["score"].dropna().tolist() if "score" in pumped_df else []
    score_failed = failed_df["score"].dropna().tolist() if "score" in failed_df else []

    # Suggested weights — based on lift (how predictive each signal is)
    suggested = {}
    for sig, stats in signal_accuracy.items():
        lift = stats["lift"]
        if lift >= 2.0:
            suggested[sig] = 3
        elif lift >= 1.5:
            suggested[sig] = 2
        elif lift >= 1.0:
            suggested[sig] = 1
        else:
            suggested[sig] = 0

    result = {
        "mode":               "alert_accuracy",
        "run_at":             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_alerts":       len(results),
        "pumped":             int(results_df["pumped"].sum()),
        "not_pumped":         int((~results_df["pumped"]).sum()),
        "win_rate_pct":       round(results_df["pumped"].mean() * 100, 1),
        "avg_pump_pct":       round(float(pumped_df["pump_pct"].mean()), 1) if len(pumped_df) else 0,
        "avg_score_pumped":   round(float(np.mean(score_pumped)), 1) if score_pumped else None,
        "avg_score_failed":   round(float(np.mean(score_failed)), 1) if score_failed else None,
        "signal_accuracy":    signal_accuracy,
        "suggested_weights":  suggested,
        "available_signals":  available_signals,
    }

    os.makedirs("data", exist_ok=True)
    with open("data/alert_accuracy.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


# ══════════════════════════════════════════════════════════════════════
#  SINGLE COIN BACKTEST
# ══════════════════════════════════════════════════════════════════════

def backtest_single_coin(symbol: str,
                          lookback_days: int = 180,
                          min_pump_pct:  float = 20.0) -> dict:
    """
    Full backtest for one specific coin.
    Fetches up to 2 years of history.
    Finds all pumps. Shows what signals looked like before each one.
    Also fetches OI/funding for the last 30 days of data.
    """
    log.info(f"Single coin backtest: {symbol} ({lookback_days}d, >{min_pump_pct}%)")

    # Fetch daily OHLCV (up to 1000 candles = ~2.7 years)
    limit = min(lookback_days + 60, 1000)
    data  = _get("https://fapi.binance.com/fapi/v1/klines",
                 params={"symbol": symbol, "interval": "1d", "limit": limit})

    if not data or len(data) < 20:
        return {"error": f"No OHLCV data for {symbol}"}

    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ["open","high","low","close","volume","quote_vol","taker_buy_quote"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")

    # Find pumps
    pumps = find_pumps_in_ohlcv(df, min_pump_pct=min_pump_pct, lookback_candles=lookback_days)

    if not pumps:
        return {
            "symbol":     symbol,
            "pumps_found": 0,
            "message":    f"No pumps >{min_pump_pct}% found in last {lookback_days} days",
            "current_price": float(df["close"].iloc[-1]),
            "current_ta":    compute_ta_at_index(df, len(df) - 1),
        }

    # For each pump: get TA at pre-pump point
    detailed = []
    for pump in pumps:
        pre_idx = pump["pre_pump_idx"]
        ta      = compute_ta_at_index(df, pre_idx)
        pump["ta_before"] = ta
        pump["symbol"]    = symbol
        detailed.append(pump)

    # Current state of the coin (right now, latest candle)
    current_ta = compute_ta_at_index(df, len(df) - 1)

    # How does current state compare to pre-pump states?
    bool_sigs = [
        "vol_spike","bb_squeeze","low_atr","higher_lows",
        "cvd_divergence","daily_macd_cross",
        "pattern_falling_wedge","pattern_bull_flag","pattern_coiling_resistance",
    ]
    current_matches = {}
    for sig in bool_sigs:
        current_val = current_ta.get(sig, False)
        pre_pump_rate = (
            sum(1 for p in detailed if p["ta_before"].get(sig)) /
            len(detailed) * 100
        ) if detailed else 0
        current_matches[sig] = {
            "current": bool(current_val),
            "was_true_before_pct": round(pre_pump_rate, 1),
        }

    return {
        "symbol":           symbol,
        "pumps_found":      len(detailed),
        "lookback_days":    lookback_days,
        "min_pump_pct":     min_pump_pct,
        "current_price":    float(df["close"].iloc[-1]),
        "current_ta":       {k: v for k, v in current_ta.items()
                             if k in bool_sigs + ["rsi_daily","vol_ratio",
                                                   "days_sideways","detected_pattern"]},
        "signal_match":     current_matches,
        "pumps":            [
            {
                "pump_pct":   p["pump_pct"],
                "pump_days":  p["pump_days"],
                "date":       p["pump_start_date"],
                "signals_true": [
                    s for s in bool_sigs if p["ta_before"].get(s)
                ],
                "rsi_before":       p["ta_before"].get("rsi_daily"),
                "vol_ratio_before": p["ta_before"].get("vol_ratio"),
                "days_sideways":    p["ta_before"].get("days_sideways"),
                "pattern":          p["ta_before"].get("detected_pattern","none"),
            }
            for p in sorted(detailed, key=lambda x: x["pump_pct"], reverse=True)
        ],
    }


# ══════════════════════════════════════════════════════════════════════
#  FORMAT RESULTS FOR TELEGRAM
# ══════════════════════════════════════════════════════════════════════

def format_historical_backtest(result: dict) -> list:
    """Returns list of Telegram messages (split for 4096 char limit)."""
    if "error" in result:
        return [f"❌ Backtest error: {result['error']}"]

    msgs = []

    # ── Message 1: Overview ──
    n     = result.get("total_pumps_found", 0)
    syms  = result.get("symbols_scanned", 0)
    days  = result.get("lookback_days", 0)
    minp  = result.get("min_pump_pct", 30)
    ts    = result.get("run_at", "?")

    m1 = (
        f"<b>📊 Backtest Results</b>\n\n"
        f"Scanned: <b>{syms}</b> coins\n"
        f"Lookback: <b>{days}</b> days\n"
        f"Min pump: <b>{minp}%</b>\n"
        f"Pumps found: <b>{n}</b>\n"
        f"Run: {ts}\n\n"
        f"<b>Which signals appeared before pumps:</b>"
    )
    msgs.append(m1)

    # ── Message 2: Signal accuracy ──
    accuracy = result.get("signal_accuracy", {})
    sorted_sigs = sorted(
        accuracy.items(),
        key=lambda x: x[1]["accuracy_pct"],
        reverse=True
    )

    m2 = "<b>📐 Signal Accuracy (% of pumps each was present before)</b>\n\n"
    for sig, stats in sorted_sigs:
        acc = stats["accuracy_pct"]
        cnt = stats["present_before_pumps"]
        bar = "█" * int(acc / 10) + "░" * (10 - int(acc / 10))
        m2 += f"<code>{sig[:30]:<30}</code> {bar} {acc:.0f}%\n"
    msgs.append(m2)

    # ── Message 3: Numeric averages ──
    avgs = result.get("signal_averages", {})
    m3 = "<b>📈 Average values 7 days before pump</b>\n\n"
    labels = {
        "vol_ratio":       "Volume ratio",
        "rsi_daily":       "RSI",
        "atr_pct":         "ATR %",
        "bb_width":        "BB width",
        "days_sideways":   "Days sideways",
        "pct_from_ath":    "% from ATH",
        "price_range_pct": "Price range %",
    }
    for sig, label in labels.items():
        if sig in avgs:
            a = avgs[sig]
            m3 += (f"{label}: avg={a['mean']} | "
                   f"median={a['median']} | "
                   f"range={a['min']}–{a['max']}\n")
    msgs.append(m3)

    # ── Message 4: Suggested weights ──
    suggested = result.get("suggested_weights", {})
    current   = config.SCORE_WEIGHTS
    m4 = "<b>💡 Suggested Weight Changes</b>\n\n"
    m4 += "<code>Signal                        Now  → Suggested</code>\n"
    changes = 0
    for sig, new_w in suggested.items():
        old_w = current.get(sig, 1)
        if new_w != old_w:
            arrow = "⬆️" if new_w > old_w else "⬇️"
            m4 += f"<code>{sig[:30]:<30}</code> {old_w} → <b>{new_w}</b> {arrow}\n"
            changes += 1
    if changes == 0:
        m4 += "Current weights look good based on backtest data ✅"
    m4 += f"\n<i>Based on {n} real pumps. Update in config.py SCORE_WEIGHTS.</i>"
    msgs.append(m4)

    # ── Message 5: Top pumps ──
    top = result.get("top_pumps", [])[:10]
    m5  = "<b>🚀 Biggest Pumps Found</b>\n\n"
    for p in top:
        sym  = p["symbol"].replace("USDT","")
        pct  = p["pump_pct"]
        days = p["pump_days"]
        date = p["pump_date"]
        pat  = p.get("pre_pump_pattern","none")
        rsi  = p.get("pre_pump_rsi")
        vol  = p.get("pre_pump_vol_ratio")
        sigs = list(p.get("pre_pump_signals",{}).keys())
        m5  += (
            f"<b>{sym}</b> +{pct}% in {days}d ({date})\n"
            f"  Before: RSI={rsi:.0f}" + (f", vol={vol:.1f}x" if vol else "") +
            f", pattern={pat}\n"
            f"  Signals: {', '.join(sigs[:4]) or 'none'}\n\n"
        )
    msgs.append(m5)

    return msgs


def format_single_coin_backtest(result: dict, symbol: str) -> list:
    if "error" in result:
        return [f"❌ {result['error']}"]

    coin     = symbol.replace("USDT","")
    n        = result.get("pumps_found", 0)
    days     = result.get("lookback_days", 180)
    min_p    = result.get("min_pump_pct", 20)
    cur_ta   = result.get("current_ta", {})
    pumps    = result.get("pumps", [])
    cur_price= result.get("current_price", 0)

    msgs = []

    # ── Message 1: Overview + current state ──
    m1 = (
        f"<b>🔍 Backtest: #{coin}</b>\n\n"
        f"Lookback: {days} days  |  Min pump: {min_p}%\n"
        f"Pumps found: <b>{n}</b>\n"
        f"Current price: ${cur_price:.6g}\n\n"
        f"<b>Current TA signals:</b>\n"
        f"RSI: {cur_ta.get('rsi_daily','?')}  |  "
        f"Vol ratio: {cur_ta.get('vol_ratio','?')}x\n"
        f"Days sideways: {cur_ta.get('days_sideways','?')}\n"
        f"Pattern: {cur_ta.get('detected_pattern','none')}\n"
    )

    if n == 0:
        m1 += f"\n⚠️ No pumps &gt;{min_p}% found in last {days} days for {coin}"
        return [m1]

    msgs.append(m1)

    # ── Message 2: Current vs historical ──
    match = result.get("signal_match", {})
    m2    = f"<b>📊 Current state vs pre-pump conditions</b>\n\n"
    m2   += "<code>Signal                  Now   Before pumps</code>\n"
    for sig, info in match.items():
        cur   = "✅" if info["current"] else "❌"
        rate  = info["was_true_before_pct"]
        label = sig.replace("pattern_","").replace("_"," ")[:22]
        m2   += f"<code>{label:<22}</code> {cur}    {rate:.0f}%\n"
    msgs.append(m2)

    # ── Message 3: Past pumps detail ──
    m3 = f"<b>🚀 Past pumps for #{coin}</b>\n\n"
    for p in pumps[:8]:
        pct  = p["pump_pct"]
        pdays= p["pump_days"]
        date = p["date"]
        rsi  = p.get("rsi_before")
        vol  = p.get("vol_ratio_before")
        dsw  = p.get("days_sideways")
        pat  = p.get("pattern","none")
        sigs = p.get("signals_true",[])
        m3  += (
            f"<b>+{pct}%</b> in {pdays}d  ({date})\n"
            f"  RSI={f'{rsi:.0f}' if rsi else '?'}"
            f"  Vol={f'{vol:.1f}x' if vol else '?'}"
            f"  {dsw or '?'}d sideways\n"
            f"  Pattern: {pat}\n"
            f"  Signals: {', '.join(sigs[:3]) or 'none'}\n\n"
        )
    msgs.append(m3)

    return msgs


def format_alert_accuracy(result: dict) -> list:
    if "error" in result:
        return [f"❌ {result['error']}"]

    total   = result.get("total_alerts", 0)
    pumped  = result.get("pumped", 0)
    win_r   = result.get("win_rate_pct", 0)
    avg_p   = result.get("avg_pump_pct", 0)
    avg_sp  = result.get("avg_score_pumped")
    avg_sf  = result.get("avg_score_failed")
    ts      = result.get("run_at","?")

    bar = "█" * int(win_r/10) + "░" * (10-int(win_r/10))
    msgs = []

    # ── Message 1: Overview ──
    m1 = (
        f"<b>🎯 Alert Accuracy Analysis</b>\n"
        f"{ts}\n\n"
        f"Total alerts tracked:  <b>{total}</b>\n"
        f"Pumped (≥20% in 10d):  <b>{pumped}</b>\n"
        f"Win rate: [{bar}] <b>{win_r}%</b>\n"
        f"Avg pump of winners:   +{avg_p}%\n\n"
        f"Avg score (pumped):    {avg_sp}\n"
        f"Avg score (not pumped):{avg_sf}\n"
    )
    msgs.append(m1)

    # ── Message 2: Signal accuracy ──
    accuracy = result.get("signal_accuracy", {})
    sorted_sigs = sorted(
        accuracy.items(),
        key=lambda x: x[1]["lift"],
        reverse=True
    )

    m2 = "<b>📐 Signal Predictiveness (lift = pumped rate / failed rate)</b>\n\n"
    m2 += "<code>Signal                   Pumped  Failed  Lift</code>\n"
    for sig, stats in sorted_sigs:
        pr   = stats["rate_before_pumps"]
        fr   = stats["rate_before_failures"]
        lift = stats["lift"]
        icon = "🟢" if lift >= 1.5 else "🟡" if lift >= 1.0 else "🔴"
        label = sig[:25]
        m2  += f"{icon} <code>{label:<25}</code> {pr:.0f}%  {fr:.0f}%  {lift:.1f}x\n"
    msgs.append(m2)

    # ── Message 3: Suggested weights ──
    suggested = result.get("suggested_weights", {})
    current   = config.SCORE_WEIGHTS
    m3 = "<b>💡 Suggested Weight Changes (based on YOUR alert data)</b>\n\n"
    changes = 0
    for sig, new_w in suggested.items():
        old_w = current.get(sig, 1)
        if new_w != old_w:
            arrow = "⬆️" if new_w > old_w else "⬇️"
            m3 += f"<code>{sig[:30]:<30}</code> {old_w} → <b>{new_w}</b> {arrow}\n"
            changes += 1
    if changes == 0:
        m3 += "Current weights are already well-tuned ✅"
    m3 += f"\n\n<i>Update SCORE_WEIGHTS in config.py and restart bot.</i>"
    msgs.append(m3)

    return msgs
