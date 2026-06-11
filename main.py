"""
=============================================================
  CRYPTO PUMP SCANNER v5 — MAIN ENTRY POINT

  Run modes:
    python main.py              → scan forever + listen for commands
    python main.py --once       → single scan then exit
    python main.py --diagnose   → full diagnostic then exit
    python main.py --test COIN  → test data fetch for one coin
=============================================================
"""

import time
import sys
import traceback
from datetime import datetime, timezone, timedelta

import config
from modules.logger import get_logger
from modules.scanner import run_scan
from modules.telegram_alert import (
    send_pump_alert, send_scan_summary,
    send_startup_message, send_error_alert
)
from modules.data_logger import (
    log_scan_result, log_alert,
    update_pump_tracker, get_accuracy_stats
)
from modules.telegram_commands import (
    start_command_listener, update_last_results, is_paused
)
from modules import signal_tracker
from modules import ws_monitor

log = get_logger("main")


def process_results(results: list) -> None:
    """Log, alert, summarise one scan cycle's results."""
    alerted   = 0
    all_above = []

    # Build price map for signal tracker updates
    current_prices = {
        d["symbol"]: d["price"]
        for d in results
        if d.get("symbol") and d.get("price")
    }
    # Full scan data for TP snapshots
    current_scan_data = {
        d["symbol"]: d
        for d in results
        if d.get("symbol")
    }

    # Update all active signal trackers
    notifications = signal_tracker.update_prices(
        current_prices, {}, current_scan_data
    )
    for notif in notifications:
        _send_signal_notification(notif)

    for data in results:
        sym = data.get("symbol", "?")
        sr  = {
            "score":     data.get("score"),
            "max_score": data.get("max_score"),
            "strength":  data.get("strength"),
            "pct_score": data.get("pct_score"),
            "signals":   data.get("signals", {}),
            "reasons":   data.get("reasons", []),
            "penalties": data.get("penalties", []),
        }
        log_scan_result(sym, data, sr)

        score        = data.get("score", 0)
        has_momentum = sr.get("has_momentum", False)
        mom_score    = sr.get("momentum_score", 0)

        # MOMENTUM GATE: only alert if at least one real momentum signal fired
        # This prevents spamming alerts that are just fear&greed + pattern noise
        momentum_ok = (
            not config.REQUIRE_MOMENTUM_SIGNAL or   # gate can be disabled
            has_momentum or                          # has real signal
            score >= config.MOMENTUM_BYPASS_SCORE    # very high score bypasses gate
        )

        if score >= config.ALERT_MIN_SCORE and momentum_ok:
            all_above.append(data)
            if alerted < config.ALERT_MAX_PER_SCAN:
                if send_pump_alert(sym, data, sr):
                    log_alert(sym, data, sr)
                    alerted += 1
                    # Track signals above paper trade threshold
                    if score >= config.PAPER_TRADE_SCORE:
                        signal_tracker.record_signal(sym, data, sr)
                    time.sleep(1)

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    send_scan_summary(all_above[:8], ts)

    if alerted > 0:
        log.info(f"📨 {alerted} alert(s) sent this cycle")
        # Refresh WS monitor to include any new signal coins
        ws_monitor.refresh()

    # Send daily summary at midnight UTC
    _maybe_send_daily_summary()


def _fmt_time(days: float) -> str:
    """Format elapsed time: '45min' / '2h 30min' / '5d 4h'"""
    total_min = int(days * 24 * 60)
    hrs = total_min // 60
    mins = total_min % 60
    d = hrs // 24
    rh = hrs % 24
    if hrs < 1:   return f"{mins}min"
    elif d < 3:   return f"{hrs}h {mins}min" if mins else f"{hrs}h"
    else:         return f"{d}d {rh}h" if rh else f"{d}d"


def _send_signal_notification(notif: dict) -> None:
    """Send a price threshold notification for a tracked signal."""
    from modules.telegram_alert import _send_raw, esc
    sym     = notif["symbol"].replace("USDT", "")
    peak_pct = notif["pct_change"]      # this is now peak%
    cur_pct = notif.get("current_pct", peak_pct)  # where price is NOW
    days    = notif["days"]
    entry   = notif["entry"]
    cur     = notif["current"]
    peak    = notif.get("peak", cur)
    score   = notif["score"]
    t       = notif["threshold"]

    if notif["type"] == "pump":
        icon = "🚀" if t >= 50 else "📈" if t >= 20 else "📊"
        # Show both peak and current so you know if it held or pulled back
        if abs(cur_pct - peak_pct) > 2:
            price_line = (
                f"Entry: ${entry:.6g} → Peak: ${peak:.6g} (+{peak_pct:.1f}%)\n"
                f"Currently: ${cur:.6g} ({cur_pct:+.1f}% from entry)"
            )
        else:
            price_line = f"Entry: ${entry:.6g} → Now: ${cur:.6g} (+{peak_pct:.1f}%)"

        msg = (
            f"{icon} <b>SIGNAL HIT +{t}% MILESTONE</b>\n\n"
            f"<b>#{esc(sym)}</b> — alerted {_fmt_time(days)} ago\n"
            f"{price_line}\n"
            f"Score: {score}/34  |  Peak milestone: +{t}% ✅"
        )
    else:
        msg = (
            f"⚠️ <b>SIGNAL DOWN {t}%</b>\n\n"
            f"<b>#{esc(sym)}</b> — alerted {_fmt_time(days)} ago\n"
            f"Entry: ${entry:.6g} → Now: ${cur:.6g} ({cur_pct:+.1f}%)\n"
            f"Score: {score}/34  |  Down milestone: {t}% hit"
        )
    _send_raw(msg)


_last_daily_date: str = ""

def _maybe_send_daily_summary() -> None:
    """Send daily completed signals summary once per day at ~00:00 UTC."""
    global _last_daily_date
    from modules.telegram_alert import _send_raw, esc
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today == _last_daily_date:
        return
    hour = datetime.now(timezone.utc).hour
    if hour != 0:
        return
    _last_daily_date = today

    # Yesterday's completed signals
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    path = signal_tracker.get_daily_file_path(yesterday)
    if not path:
        return

    import json
    with open(path) as f:
        signals = json.load(f)

    if not signals:
        return

    lines = [f"<b>📊 Daily Signal Report — {yesterday}</b>\n"]
    lines.append(f"Signals completed: {len(signals)}\n")
    for sig in list(signals.values())[:20]:
        sym    = esc(sig["symbol"].replace("USDT",""))
        pump   = sig.get("max_pump_pct", 0)
        dump   = sig.get("max_dump_pct", 0)
        result = sig.get("final_result","?")
        score  = sig.get("score","?")
        icon   = "🟢" if result == "pumped" else "🔴" if result == "dumped" else "⬜"
        lines.append(f"{icon} <b>{sym}</b> score={score} peak=+{pump:.0f}% trough={dump:.0f}%")

    _send_raw("\n".join(lines))


def run_forever() -> None:
    """Main loop — scans every N minutes + listens for Telegram commands."""

    # Start Telegram command listener in background thread
    start_command_listener()

    send_startup_message()
    log.info(f"🚀 Bot running. Scan every {config.SCAN_INTERVAL_MINUTES}min. "
             f"Telegram commands active.")
    log.info(f"Send /help to your bot for all commands.")

    cycle = 0
    while True:
        if is_paused():
            log.info("⏸ Scanning paused (use /resume in Telegram)")
            time.sleep(60)
            continue

        cycle += 1
        log.info(f"\n{'='*55}")
        log.info(f"🔄 Cycle #{cycle} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        log.info(f"{'='*55}")

        try:
            results = run_scan()
            update_last_results(results)  # make available to Telegram commands
            process_results(results)

            # Daily pump tracker update
            if cycle % 48 == 0:
                prices = {d["symbol"]: d["price"] for d in results if "symbol" in d}
                update_pump_tracker(prices)
                stats = get_accuracy_stats()
                if stats.get("total_alerts", 0) > 0:
                    log.info(f"📊 Accuracy: {stats}")

        except KeyboardInterrupt:
            log.info("⛔ Stopped by user")
            sys.exit(0)
        except Exception as e:
            err = traceback.format_exc()
            log.error(f"❌ Scan error: {e}\n{err}")
            send_error_alert(str(e))

        log.info(f"⏳ Next scan in {config.SCAN_INTERVAL_MINUTES} min...")
        time.sleep(config.SCAN_INTERVAL_MINUTES * 60)


def run_once() -> None:
    """Single scan — useful for testing."""
    log.info("🧪 Single scan mode...")
    results = run_scan()
    update_last_results(results)

    print(f"\n{'='*60}")
    print(f"RESULTS — {len(results)} coins scanned")
    print(f"{'='*60}")

    for d in results[:10]:
        sym  = d.get("symbol","?")
        sc   = d.get("score",0)
        maxs = d.get("max_score",34)
        chg  = d.get("price_change_pct",0)
        vol  = d.get("vol_ratio") or 0
        oi   = d.get("oi_change_24h")
        fund = d.get("funding_rate")
        bar  = "█"*int(sc/maxs*10) + "░"*(10-int(sc/maxs*10))
        src  = d.get("oi_source","?")[:12]

        print(f"\n{sym:<15} {sc}/{maxs} [{bar}]  src:{src}")
        print(f"  {chg:+.2f}% | vol:{vol:.1f}x | "
              f"OI:{f'{oi:+.1f}%' if oi else 'N/A'} | "
              f"fund:{f'{fund:.4f}%' if fund is not None else 'N/A'}")
        for r in d.get("reasons",[]):
            print(f"  {r}")

    process_results(results)
    print(f"\n✅ Done. Saved to {config.CSV_LOG_PATH}")


def run_diagnose(test_coin: str = "BTCUSDT") -> None:
    """Full diagnostic — tests every API and data source."""
    from modules.diagnostics import run_full_diagnostic, format_diagnostic_for_telegram

    print(f"\n🔬 Running full diagnostic on {test_coin}...\n")
    results = run_full_diagnostic(test_coin)
    msgs    = format_diagnostic_for_telegram(results)

    # Print to console
    for msg in msgs:
        # Strip HTML tags for console
        import re
        clean = re.sub(r"<[^>]+>", "", msg)
        print(clean)
        print("─" * 60)

    # Also send to Telegram if configured
    from modules.telegram_commands import _send_multi
    if (config.TELEGRAM_BOT_TOKEN and
            config.TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN_HERE"):
        print("\nSending to Telegram...")
        _send_multi(msgs)

    summary = results["summary"]
    print(f"\n✅ Diagnostic complete: "
          f"{summary['apis_ok']}/{summary['apis_total']} APIs OK, "
          f"{summary['warnings']} warnings, "
          f"{summary['errors']} errors")


def run_test_coin(sym: str) -> None:
    """Fetch and display all data for one coin."""
    if not sym.endswith("USDT"):
        sym = sym.upper() + "USDT"
    sym = sym.upper()

    print(f"\n🔍 Fetching all data for {sym}...\n")

    from modules import (binance_fetcher as bf, aggregated_fetcher as af,
                         technical_analysis as ta, sentiment_fetcher as sf,
                         btc_market as btc, scorer)

    data = {}
    tickers = bf.get_24h_tickers()
    data.update(tickers.get(sym, {}))

    agg = af.get_all_aggregated(sym)
    data.update(agg)
    data.update(bf.get_basis(sym, data.get("price", 0)))
    data.update(sf.get_market_data(sym))

    df_d = bf.get_klines(sym, "1d", 30)
    df_h = bf.get_klines(sym, "1h", 48)
    if df_d is not None:
        data.update(ta.run_all_ta(df_d, df_h,
            oi_usd=data.get("oi_usd"),
            market_cap_usd=data.get("market_cap_usd")))

    data.update(sf.get_fear_greed())
    data.update(btc.get_btc_context())

    sr = scorer.score_coin(data)
    data.update(sr)

    # Print all fields
    print(f"{'FIELD':<40} {'VALUE'}")
    print("─" * 60)
    for k, v in sorted(data.items()):
        if k in ("signals", "reasons", "penalties", "ob_exchange_depth",
                 "funding_per_exchange", "ls_per_exchange", "oi_exchanges"):
            continue
        print(f"{k:<40} {str(v)[:40]}")

    print(f"\n{'─'*60}")
    print(f"SCORE: {data.get('score')}/{data.get('max_score')} — {data.get('strength')}")
    print(f"\nTriggered signals:")
    for r in data.get("reasons", []):
        print(f"  {r}")

    # Sources
    print(f"\nData sources:")
    print(f"  OI:          {data.get('oi_source')}")
    print(f"  Funding:     {data.get('funding_source')}")
    print(f"  L/S:         {data.get('ls_source')}")
    print(f"  Liquidations:{data.get('liq_source')}")
    print(f"  Order book:  {data.get('ob_source')}")
    print(f"  OB exchanges:{data.get('ob_exchanges')}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--once" in args or "-1" in args:
        run_once()

    elif "--diagnose" in args or "--diag" in args:
        coin = next((a for a in args if not a.startswith("--")), "BTCUSDT")
        run_diagnose(coin)

    elif "--test" in args:
        idx  = args.index("--test")
        coin = args[idx + 1] if idx + 1 < len(args) else "BTCUSDT"
        run_test_coin(coin)

    else:
        run_forever()
