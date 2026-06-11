"""
MODULE: ws_monitor.py — Real-time WebSocket TP Monitor

Runs in a background thread alongside the scanner.
Connects to Binance WebSocket for ALL active signal coins.
Fires TP/dump notifications IMMEDIATELY when price crosses a threshold.
No waiting for the next 15-minute scan.

HOW IT WORKS:
  1. On startup, subscribe to miniTicker stream for all active signal coins
  2. Every time price updates (every ~1 second), check TP thresholds
  3. If threshold crossed → fire Telegram alert immediately
  4. Re-subscribes when new signals are added by scanner
  5. Reconnects automatically on disconnect

BINANCE WEBSOCKET:
  wss://fstream.binance.com/stream?streams=COIN1@miniTicker/COIN2@miniTicker/...
  miniTicker fires every second with: close price, high, low, volume
  
THREAD SAFETY:
  Signal tracker active.json is read/written by both scanner and ws_monitor.
  We use a threading.Lock to prevent corruption.
"""

import json
import time
import threading
import websocket
from datetime import datetime, timezone
from modules.logger import get_logger
import config

log = get_logger("ws_monitor")

# Shared lock for signal tracker file access
_tracker_lock = threading.Lock()

# Track which symbols we're currently subscribed to
_subscribed_symbols: set = set()
_ws_instance = None
_ws_thread   = None
_running     = False
_reconnect_delay = 5  # seconds between reconnect attempts


def _load_active() -> dict:
    """Load active signals (thread-safe)."""
    path = "data_ls/signal_tracker/active.json"
    try:
        import os
        if not os.path.exists(path):
            return {}
        with _tracker_lock:
            with open(path) as f:
                return json.load(f)
    except Exception:
        return {}


def _save_active(data: dict) -> None:
    """Save active signals (thread-safe)."""
    import os
    path = "data_ls/signal_tracker/active.json"
    os.makedirs("data_ls/signal_tracker", exist_ok=True)
    with _tracker_lock:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)


def _fmt_time(days: float) -> str:
    total_min = int(days * 24 * 60)
    hrs  = total_min // 60
    mins = total_min % 60
    d    = hrs // 24
    rh   = hrs % 24
    if hrs < 1:   return f"{mins}min"
    elif d < 3:   return f"{hrs}h {mins}min" if mins else f"{hrs}h"
    else:         return f"{d}d {rh}h" if rh else f"{d}d"


def _send_tp_alert(sig: dict, threshold: int, price: float,
                   peak_pct: float, notif_type: str) -> None:
    """Send immediate Telegram TP alert."""
    from modules.telegram_alert import _send_raw, esc

    sym   = sig["symbol"].replace("USDT", "")
    entry = sig["entry_price"]
    score = sig["score"]
    days  = (time.time() - sig["alert_ts"]) / 86400
    t_str = _fmt_time(days)

    if notif_type == "pump":
        icon = "🚀" if threshold >= 50 else "📈" if threshold >= 20 else "📊"
        msg = (
            f"{icon} <b>SIGNAL HIT +{threshold}% MILESTONE</b>\n\n"
            f"<b>#{esc(sym)}</b> — alerted {t_str} ago\n"
            f"Entry: ${entry:.6g} → Now: ${price:.6g} (+{peak_pct:.1f}%)\n"
            f"Score: {score}/34  |  Milestone: +{threshold}% ✅\n"
            f"<i>⚡ Real-time alert</i>"
        )
    else:
        msg = (
            f"⚠️ <b>SIGNAL DOWN {threshold}%</b>\n\n"
            f"<b>#{esc(sym)}</b> — alerted {t_str} ago\n"
            f"Entry: ${entry:.6g} → Now: ${price:.6g} ({peak_pct:+.1f}%)\n"
            f"Score: {score}/34  |  Stop level: {threshold}% hit\n"
            f"<i>⚡ Real-time alert</i>"
        )
    try:
        _send_raw(msg)
        log.info(f"WS TP alert: {sym} {notif_type} {threshold}% @ ${price:.6g}")
    except Exception as e:
        log.warning(f"WS alert send failed: {e}")


def _on_message(ws, message: str) -> None:
    """Handle incoming WebSocket price update."""
    try:
        data = json.loads(message)

        # Combined stream wraps in {"stream": "...", "data": {...}}
        if "data" in data:
            data = data["data"]

        symbol = data.get("s", "")  # e.g. "PLAYUSDT"
        price  = float(data.get("c", 0))  # close price
        high   = float(data.get("h", 0))  # session high

        if not symbol or not price:
            return

        # Load active signals
        active = _load_active()
        if symbol not in {sig["symbol"] for sig in active.values()}:
            return

        now_ts  = time.time()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        changed = False

        for signal_id, sig in active.items():
            if sig["symbol"] != symbol:
                continue

            entry_price = sig.get("entry_price") or 0
            if not entry_price:
                continue

            # Current % from entry
            cur_pct  = (price - entry_price) / entry_price * 100

            # Update peak (only from actual prices, never high_24h)
            if price > sig.get("peak_price", entry_price):
                sig["peak_price"] = price
                sig["peak_time"]  = now_str
                sig["max_pump_pct"] = round(
                    (price - entry_price) / entry_price * 100, 2
                )
                changed = True

            if price < sig.get("trough_price", entry_price):
                sig["trough_price"] = price
                sig["trough_time"]  = now_str
                sig["max_dump_pct"] = round(
                    (price - entry_price) / entry_price * 100, 2
                )
                changed = True

            peak_pct = sig["max_pump_pct"]
            dump_pct = sig["max_dump_pct"]

            pump_thresholds = getattr(config, "PUMP_NOTIFY_THRESHOLDS", [10, 20, 30, 50, 75, 100])
            dump_thresholds = getattr(config, "DUMP_NOTIFY_THRESHOLDS", [-10, -20, -30])

            # Check pump thresholds
            for threshold in pump_thresholds:
                notif_key = f"threshold_{threshold}"
                if notif_key not in sig.get("notifications_sent", []) and peak_pct >= threshold:
                    _send_tp_alert(sig, threshold, price, peak_pct, "pump")
                    if "notifications_sent" not in sig:
                        sig["notifications_sent"] = []
                    sig["notifications_sent"].append(notif_key)
                    changed = True

            # Check dump thresholds
            for threshold in dump_thresholds:
                notif_key = f"threshold_{threshold}"
                if notif_key not in sig.get("notifications_sent", []) and dump_pct <= threshold:
                    _send_tp_alert(sig, threshold, price, dump_pct, "dump")
                    if "notifications_sent" not in sig:
                        sig["notifications_sent"] = []
                    sig["notifications_sent"].append(notif_key)
                    changed = True

        if changed:
            _save_active(active)

    except Exception as e:
        log.debug(f"WS message error: {e}")


def _on_error(ws, error) -> None:
    log.warning(f"WS error: {error}")


def _on_close(ws, close_status_code, close_msg) -> None:
    log.warning(f"WS closed: {close_status_code} — will reconnect in {_reconnect_delay}s")


def _on_open(ws) -> None:
    log.info(f"WS connected — monitoring {len(_subscribed_symbols)} coins in real-time")


def _build_ws_url(symbols: set) -> str:
    """Build Binance combined stream URL for multiple symbols."""
    streams = "/".join(f"{sym.lower()}@miniTicker" for sym in sorted(symbols))
    return f"wss://fstream.binance.com/stream?streams={streams}"


def _run_ws(symbols: set) -> None:
    """Run WebSocket connection (blocking, call in thread)."""
    global _ws_instance
    if not symbols:
        log.info("WS monitor: no active signals to monitor")
        return

    url = _build_ws_url(symbols)
    log.info(f"WS connecting for: {', '.join(sorted(symbols))}")

    _ws_instance = websocket.WebSocketApp(
        url,
        on_message = _on_message,
        on_error   = _on_error,
        on_close   = _on_close,
        on_open    = _on_open,
    )
    _ws_instance.run_forever(ping_interval=30, ping_timeout=10)


def _monitor_loop() -> None:
    """
    Main monitor loop. Runs forever in background thread.
    Reconnects when active signals change or connection drops.
    """
    global _subscribed_symbols, _ws_instance, _running

    log.info("WS monitor thread started")

    while _running:
        try:
            # Get current active signal symbols
            active  = _load_active()
            symbols = {sig["symbol"] for sig in active.values()
                      if sig.get("status") == "active"}

            if not symbols:
                log.debug("WS monitor: no active signals, waiting...")
                time.sleep(30)
                continue

            # If symbols changed, reconnect
            if symbols != _subscribed_symbols:
                log.info(f"WS: symbols changed {_subscribed_symbols} → {symbols}, reconnecting")
                if _ws_instance:
                    try:
                        _ws_instance.close()
                    except Exception:
                        pass
                _subscribed_symbols = symbols

            # Run WebSocket (blocks until disconnected)
            _run_ws(symbols)

            # Reconnect after disconnect
            if _running:
                log.info(f"WS reconnecting in {_reconnect_delay}s...")
                time.sleep(_reconnect_delay)

        except Exception as e:
            log.error(f"WS monitor loop error: {e}")
            time.sleep(_reconnect_delay)

    log.info("WS monitor thread stopped")


def start() -> None:
    """Start the WebSocket monitor in a background thread."""
    global _ws_thread, _running

    if _running:
        log.debug("WS monitor already running")
        return

    _running  = True
    _ws_thread = threading.Thread(
        target=_monitor_loop,
        name="ws-monitor",
        daemon=True  # dies when main process dies
    )
    _ws_thread.start()
    log.info("WS real-time TP monitor started")


def stop() -> None:
    """Stop the WebSocket monitor."""
    global _running, _ws_instance
    _running = False
    if _ws_instance:
        try:
            _ws_instance.close()
        except Exception:
            pass
    log.info("WS monitor stopped")


def refresh() -> None:
    """
    Force reconnect with updated symbol list.
    Call this after new signals are added by the scanner.
    """
    global _ws_instance
    if _ws_instance:
        try:
            _ws_instance.close()  # triggers reconnect with new symbols
        except Exception:
            pass
