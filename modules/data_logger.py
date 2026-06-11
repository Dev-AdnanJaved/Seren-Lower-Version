"""
MODULE: data_logger.py
CSV logging of every scan result + pump outcome tracker.
"""

import os, csv, json
from datetime import datetime, timezone, timedelta
from modules.logger import get_logger
import config

log = get_logger("data_logger")
os.makedirs("data", exist_ok=True)

SCAN_FIELDS = [
    "timestamp","symbol","price","price_change_pct","price_change_7d","price_change_30d",
    "volume_usdt","vol_ratio","vol_spike","today_vol_m","avg_vol_m",
    "oi_usd","oi_change_24h","oi_rising","oi_mc_ratio","high_leverage",
    "funding_rate","funding_avg_3","negative_funding",
    "ls_ratio_global","ls_ratio_top","short_heavy","whales_short",
    "liq_long_24h_usd","liq_short_24h_usd","liq_total_24h_usd","liq_short_heavy",
    "taker_buy_pct","cvd_proxy","cvd_rising","cvd_divergence",
    "bid_depth_usdt","ask_depth_usdt","bid_ask_ratio","large_buy_wall","large_sell_wall",
    "spread_pct","book_thin",
    "spot_price","basis_pct","negative_basis",
    "bb_width","bb_squeeze","bb_squeeze_pct","bb_squeeze_1h",
    "atr_pct","low_atr","atr_rank",
    "rsi_daily","rsi_1h","daily_macd_cross","daily_macd_above",
    "higher_lows","sideways","price_range_pct","days_sideways",
    "pct_from_ath","far_from_ath","recent_low_30d","pct_from_recent_low",
    "pattern_falling_wedge","pattern_bull_flag","pattern_descending_triangle_breakout",
    "pattern_coiling_resistance","pattern_cup_handle","patterns_count","detected_pattern",
    "market_cap_usd","circulating_supply","total_supply","low_float","float_pct",
    "social_spike","galaxy_score","alt_rank","social_vol_24h","social_delta_pct",
    "twitter_followers","reddit_subscribers",
    "google_trend_score","google_trend_spike",
    "unlock_risk","unlock_event",
    "fear_greed_value","fear_greed_label","fear_greed_low",
    "score","max_score","pct_score","strength",
]


def _write_csv(path, row, fields):
    exists = os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)


def log_scan_result(symbol: str, data: dict, score_result: dict) -> None:
    if not config.ENABLE_CSV_LOGGING:
        return
    row = {**data, **score_result,
           "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
           "symbol": symbol}
    _write_csv(config.CSV_LOG_PATH, row, SCAN_FIELDS)


def log_alert(symbol: str, data: dict, score_result: dict) -> None:
    fields = ["timestamp","symbol","price","score","max_score","strength",
              "oi_change_24h","funding_rate","vol_ratio","ls_ratio_global",
              "detected_pattern","cvd_divergence","days_sideways","signals"]
    row = {
        "timestamp":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol":          symbol,
        "price":           data.get("price"),
        "score":           score_result.get("score"),
        "max_score":       score_result.get("max_score"),
        "strength":        score_result.get("strength"),
        "oi_change_24h":   data.get("oi_change_24h"),
        "funding_rate":    data.get("funding_rate"),
        "vol_ratio":       data.get("vol_ratio"),
        "ls_ratio_global": data.get("ls_ratio_global"),
        "detected_pattern": data.get("detected_pattern"),
        "cvd_divergence":  data.get("cvd_divergence"),
        "days_sideways":   data.get("days_sideways"),
        "signals":         json.dumps({k: v[0] for k, v in score_result.get("signals", {}).items()}),
    }
    _write_csv(getattr(config, "ALERT_LOG_PATH", "data/alert_log.csv"), row, fields)

    if config.ENABLE_PUMP_TRACKING:
        check_date = (datetime.now(timezone.utc) + timedelta(days=config.PUMP_TRACK_DAYS)).strftime("%Y-%m-%d")
        pump_row = {
            "alert_time": row["timestamp"], "symbol": symbol,
            "alert_price": data.get("price"), "score": score_result.get("score"),
            "check_date": check_date, "price_at_check": "", "pump_pct": "", "pumped": "",
        }
        _write_csv(getattr(config, "PUMP_TRACK_PATH", "data/pump_tracker.csv"), pump_row,
                   ["alert_time","symbol","alert_price","score","check_date",
                    "price_at_check","pump_pct","pumped"])


def update_pump_tracker(prices: dict) -> None:
    if not config.ENABLE_PUMP_TRACKING or not os.path.isfile(getattr(config, "PUMP_TRACK_PATH", "data/pump_tracker.csv")):
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows  = []
    updated = 0
    with open(getattr(config, "PUMP_TRACK_PATH", "data/pump_tracker.csv"), "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        for row in reader:
            if row.get("check_date") == today and not row.get("pumped"):
                sym = row.get("symbol")
                price = prices.get(sym)
                if price and row.get("alert_price"):
                    try:
                        ap  = float(row["alert_price"])
                        pp  = float(price)
                        pct = (pp - ap) / ap * 100
                        row["price_at_check"] = pp
                        row["pump_pct"]       = round(pct, 2)
                        row["pumped"]         = "YES" if pct >= 20 else "NO"
                        updated += 1
                    except Exception:
                        pass
            rows.append(row)
    if updated > 0:
        with open(getattr(config, "PUMP_TRACK_PATH", "data/pump_tracker.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        log.info(f"📊 Updated {updated} pump tracker rows")


def get_accuracy_stats() -> dict:
    if not os.path.isfile(getattr(config, "PUMP_TRACK_PATH", "data/pump_tracker.csv")):
        return {}
    with open(getattr(config, "PUMP_TRACK_PATH", "data/pump_tracker.csv"), "r", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("pumped") in ("YES","NO")]
    if not rows:
        return {"total_alerts": 0}
    total  = len(rows)
    pumped = sum(1 for r in rows if r["pumped"] == "YES")
    pumps  = [float(r["pump_pct"]) for r in rows if r.get("pump_pct")]
    return {
        "total_alerts": total,
        "pumped":       pumped,
        "win_rate_pct": round(pumped / total * 100, 1),
        "avg_pump_pct": round(sum(pumps)/len(pumps), 1) if pumps else 0,
        "max_pump_pct": round(max(pumps), 1) if pumps else 0,
    }
