"""
MODULE: telegram_commands.py
Telegram bot command handler — listens for commands and responds.

Commands:
  /start        — Welcome message + command list
  /help         — Full command list with descriptions
  /check        — Full diagnostic: all APIs, all fields, aggregation sources
  /check COIN   — Diagnostic for a specific coin (e.g. /check ETH)
  /scan         — Trigger a manual scan right now
  /top          — Show top 10 coins from last scan
  /coin SYMBOL  — Full data dump for one coin (e.g. /coin SOLUSDT)
  /signals SYMBOL — Show all 28 signals for a coin with values
  /sources SYMBOL — Show which exchange provided each data point
  /watchlist    — Show your current watchlist
  /stats        — Accuracy stats from pump tracker
  /btc          — Current BTC context and market conditions
  /config       — Show current bot configuration
  /apis         — Quick check of all API connections
  /stop         — Pause scanning (keeps bot running)
  /resume       — Resume scanning after /stop
  /setthreshold N — Change alert score threshold (e.g. /setthreshold 8)
  /addwatch SYMBOL — Add coin to watchlist
  /removewatch SYMBOL — Remove coin from watchlist
"""

import time
import threading
import requests
import traceback
from datetime import datetime, timezone
from modules.logger import get_logger
import config

log = get_logger("commands")

BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# Shared state
_last_results:    list  = []
_scanning_paused: bool  = False
_watchlist:       set   = set()
_last_update_id:  int   = 0


def esc(text) -> str:
    """Escape any string for safe insertion into Telegram HTML messages."""
    if text is None:
        return "N/A"
    s = str(text)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def fmt_elapsed(days_float: float) -> str:
    """
    Format elapsed time nicely:
      < 1 hour  → '45min'
      < 3 days  → '2h 30min' or '14h'
      >= 3 days → '5d 4h' or '7d'
    """
    total_minutes = int(days_float * 24 * 60)
    hours   = total_minutes // 60
    minutes = total_minutes % 60
    days    = hours // 24
    rem_hrs = hours % 24

    if hours < 1:
        return f"{minutes}min"
    elif days < 3:
        if minutes > 0:
            return f"{hours}h {minutes}min"
        return f"{hours}h"
    else:
        if rem_hrs > 0:
            return f"{days}d {rem_hrs}h"
        return f"{days}d"


def fmt_remaining(days_float: float) -> str:
    """Same as fmt_elapsed but for remaining time."""
    return fmt_elapsed(max(0, days_float))


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print(f"[CMD RESPONSE]\n{text}\n")
        return True
    try:
        r = requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id":    config.TELEGRAM_CHAT_ID,
                  "text":       text[:4096],
                  "parse_mode": parse_mode,
                  "disable_web_page_preview": True},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"Command send failed: {e}")
        return False


def _send_multi(texts: list) -> None:
    """Send multiple messages with a small delay between them."""
    for t in texts:
        _send(t)
        time.sleep(0.5)


def update_last_results(results: list) -> None:
    """Called by scanner to keep results fresh for commands."""
    global _last_results
    _last_results = results


def is_paused() -> bool:
    return _scanning_paused


# ═══════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════

def cmd_start(args: list) -> None:
    _send("""🤖 <b>Crypto Pump Scanner Bot</b>

I scan Binance Futures every 30 minutes across ALL exchanges and alert you before coins pump.

<b>Quick Commands:</b>
/help — Full command list
/check — Test all APIs and data sources
/scan — Run a scan right now
/top — Top coins from last scan
/btc — Current market conditions
/stats — Your accuracy stats

Type any command to get started!""")


def cmd_help(args: list) -> None:
    _send("""📖 <b>All Commands</b>

<b>Diagnostics:</b>
/check — Full diagnostic (all APIs, all fields)
/check BTC — Diagnostic for specific coin
/apis — Quick API connection test

<b>Scanning:</b>
/scan — Run manual scan now
/top — Top 10 coins from last scan
/top 20 — Top N coins from last scan

<b>Coin Data:</b>
/coin SOLUSDT — All data for one coin
/signals SOLUSDT — All 28 signals with values
/sources SOLUSDT — Which exchange provided each field

<b>Market:</b>
/btc — BTC context and market conditions
/fear — Fear and Greed index

<b>Bot Control:</b>
/stop — Pause automatic scanning
/resume — Resume scanning
/setthreshold 8 — Change alert threshold
/config — Show current settings

<b>Watchlist:</b>
/watchlist — Show watchlist
/addwatch SOLUSDT — Add to watchlist
/removewatch SOLUSDT — Remove from watchlist

<b>Stats:</b>
/stats — Accuracy stats from pump tracker
/history — Last 10 alerts sent

<b>CoinGecko ID Map:</b>
/idmap — Show map coverage stats
/refresh_ids — Rebuild ID map (run after new listings)

<b>Signal Tracker (15-day monitoring):</b>
/portfolio — clean table: all signals with score, entry, current, peak, lowest
/signals_active — detailed view with sort options (score/best/worst/fresh)
/signals_history — last 20 completed signals with outcomes
/signals_stats — win rate and accuracy by score range
/daily_report — signals that completed today
/daily_report 2024-01-15 — signals for specific date
/give_file — download all lifetime signals as JSON files

<b>Backtesting:</b>
/backtest — Scan all coins for historical pumps (takes 5-15 min)
/backtest SOLUSDT — Backtest one specific coin
/pumps — Show coins that pumped most in last 7 days
/pumps 30 20 — Pumped >20% in last 30 days
/accuracy — Signal accuracy from your alert history
/suggest_weights — Recommended weight changes""")


def cmd_check(args: list) -> None:
    """Full diagnostic — tests every API and every data field."""
    test_symbol = "BTCUSDT"
    if args:
        sym = args[0].upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        test_symbol = sym

    _send(f"🔬 Running full diagnostic on #{test_symbol.replace('USDT','')}... (may take 30 seconds)")

    try:
        from modules.diagnostics import run_full_diagnostic, format_diagnostic_for_telegram
        results = run_full_diagnostic(test_symbol)
        msgs    = format_diagnostic_for_telegram(results)
        _send_multi(msgs)
    except Exception as e:
        _send(f"❌ Diagnostic failed: {esc(str(e)[:200])}")
        log.error(f"Diagnostic error: {traceback.format_exc()}")


def cmd_apis(args: list) -> None:
    """Quick API status check."""
    _send("🌐 Testing API connections...")
    try:
        from modules.diagnostics import run_full_diagnostic
        results = run_full_diagnostic("BTCUSDT")
        apis    = results["apis"]
        msg     = "<b>🌐 API Status</b>\n\n"
        ok      = 0
        for a in apis:
            if "✅" in a["status"]:
                ok += 1
            msg += f"{a['status']} {a['name']} ({a['ms']}ms)\n"
        msg += f"\n<b>{ok}/{len(apis)} APIs working</b>"
        _send(msg)
    except Exception as e:
        _send(f"❌ API check failed: {esc(str(e)[:200])}")


def cmd_scan(args: list) -> None:
    """Trigger a manual scan."""
    _send("🔍 Starting manual scan... (takes 1-3 minutes)")
    try:
        from modules.scanner import run_scan
        from modules.telegram_alert import send_pump_alert, send_scan_summary
        from modules.data_logger import log_scan_result, log_alert
        from modules.scorer import score_coin

        results = run_scan()
        update_last_results(results)

        alerted = 0
        above   = [r for r in results if r.get("score", 0) >= config.ALERT_MIN_SCORE]

        for data in above[:config.ALERT_MAX_PER_SCAN]:
            sym = data.get("symbol", "?")
            sr  = {k: data.get(k) for k in
                   ["score","max_score","strength","pct_score","signals","reasons"]}
            log_scan_result(sym, data, sr)
            if send_pump_alert(sym, data, sr):
                log_alert(sym, data, sr)
                alerted += 1
                time.sleep(1)

        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
        send_scan_summary(above[:8], ts)
        _send(f"✅ Manual scan complete — {len(results)} coins scanned, {alerted} alerts sent")
    except Exception as e:
        _send(f"❌ Scan failed: {esc(str(e)[:200])}")
        log.error(f"Manual scan error: {traceback.format_exc()}")


def cmd_top(args: list) -> None:
    """Show top N coins from last scan."""
    n = 10
    if args:
        try:
            n = int(args[0])
        except Exception:
            pass
    n = min(n, 30)

    if not _last_results:
        _send("⚠️ No scan data yet. Run /scan first.")
        return

    top = sorted(_last_results, key=lambda x: x.get("score", 0), reverse=True)[:n]
    msg = f"<b>🏆 Top {len(top)} Coins (Last Scan)</b>\n\n"

    for i, d in enumerate(top, 1):
        sym     = d.get("symbol","?").replace("USDT","")
        score   = d.get("score", 0)
        maxs    = d.get("max_score", 34)
        chg     = d.get("price_change_pct", 0)
        vol     = d.get("vol_ratio") or 0
        fund    = d.get("funding_rate")
        oi_chg  = d.get("oi_change_24h")
        pat     = d.get("detected_pattern", "none")
        bar     = "█" * int(score/maxs*5) + "░" * (5 - int(score/maxs*5))
        src     = d.get("oi_source","?")[:10]

        msg += (f"{i}. <b>{sym}</b> {bar} {score}/{maxs}\n"
                f"   {chg:+.1f}% | vol:{vol:.1f}x | "
                f"fund:{f'{fund:.4f}%' if fund else 'N/A'} | "
                f"OI:{f'{oi_chg:+.1f}%' if oi_chg else 'N/A'}\n")
        if pat and pat != "none":
            msg += f"   📐 {esc(pat)}\n"
        msg += "\n"

    _send(msg)


def cmd_coin(args: list) -> None:
    """Full data dump for one specific coin."""
    if not args:
        _send("Usage: /coin SOLUSDT or /coin SOL")
        return

    sym = args[0].upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    # Check last results first
    cached = next((r for r in _last_results if r.get("symbol") == sym), None)

    if not cached:
        _send(f"🔍 Fetching live data for #{sym.replace('USDT','')}...")
        try:
            from modules import (binance_fetcher as bf, aggregated_fetcher as af,
                                 technical_analysis as ta, sentiment_fetcher as sf,
                                 btc_market as btc)
            tickers = bf.get_24h_tickers()
            ticker  = tickers.get(sym, {})
            if not ticker:
                _send(f"❌ Symbol {sym} not found on Binance Futures")
                return
            cached = {}
            cached.update(ticker)
            cached.update(af.get_all_aggregated(sym))
            cached.update(bf.get_basis(sym, cached.get("price", 0)))
            cached.update(sf.get_market_data(sym))
            df_d = bf.get_klines(sym, "1d", 30)
            df_h = bf.get_klines(sym, "1h", 48)
            if df_d is not None:
                cached.update(ta.run_all_ta(df_d, df_h,
                    oi_usd=cached.get("oi_usd"),
                    market_cap_usd=cached.get("market_cap_usd")))
            cached.update(btc.get_btc_context())
            cached.update(sf.get_fear_greed())
            from modules.scorer import score_coin
            sr = score_coin(cached)
            cached.update(sr)
            cached["symbol"] = sym
        except Exception as e:
            _send(f"❌ Failed to fetch {sym}: {str(e)[:200]}")
            return

    d = cached
    def fv(key, fmt=".4g", suffix=""):
        v = d.get(key)
        return f"{v:{fmt}}{suffix}" if v is not None else "N/A"

    mc   = d.get("market_cap_usd")
    mc_s = f"${mc/1e6:.0f}M" if mc else "N/A"
    ob_exs = d.get("ob_exchanges", [])

    msg = f"""<b>📊 Full Data: #{sym.replace('USDT','')}</b>

<b>Price</b>
├ Price:        ${fv('price','.6g')}
├ 24h:          {fv('price_change_pct','+.2f','%')}
├ 7d:           {fv('price_change_7d','+.1f','%')}
├ 30d:          {fv('price_change_30d','+.1f','%')}
├ From ATH:     {fv('pct_from_ath','.1f','%')}
└ From 30d Low: +{fv('pct_from_recent_low','.1f','%')}

<b>Futures (All Exchanges)</b>
├ OI:           ${fv('oi_usd','.3g')} ({fv('oi_change_24h','+.1f','%')} 24h)
├ OI/MC ratio:  {fv('oi_mc_ratio','.3f')}
├ Funding:      {fv('funding_rate','.5f','%')} [src:{d.get('funding_source','?')[:15]}]
├ L/S Global:   {fv('ls_ratio_global','.3f')} [src:{d.get('ls_source','?')[:15]}]
├ L/S Whales:   {fv('ls_ratio_top','.3f')}
├ Liq Long 24h: ${fv('liq_long_24h_usd','.3g')}
├ Liq Short 24h:${fv('liq_short_24h_usd','.3g')}
├ CVD:          {fv('cvd','.3g')} ({'rising ✅' if d.get('cvd_rising') else 'falling ❌'})
└ Basis:        {fv('basis_pct','.4f','%')}

<b>Order Book ({', '.join(ob_exs) if ob_exs else 'binance'})</b>
├ Bid depth:    ${fv('ob_total_bid_usdt','.3g')}
├ Ask depth:    ${fv('ob_total_ask_usdt','.3g')}
├ Ratio:        {fv('ob_bid_ask_ratio_agg','.3f')}
├ Imbalance:    {fv('ob_imbalance_pct','+.1f','%')}
├ Biggest wall: ${fv('ob_largest_wall_usdt','.3g')} {d.get('ob_largest_wall_side','')} on {d.get('ob_largest_wall_exchange','')}
└ Cross-ex gap: {fv('ob_cross_exchange_spread_pct','.4f','%')}

<b>Technical</b>
├ Vol ratio:    {fv('vol_ratio','.2f','x')}
├ BB squeeze:   {'✅' if d.get('bb_squeeze') else '❌'} (width {fv('bb_width','.5f')})
├ ATR:          {fv('atr_pct','.2f','%')} ({'low ✅' if d.get('low_atr') else 'normal'})
├ RSI daily:    {fv('rsi_daily','.0f')}  |  RSI 1h: {fv('rsi_1h','.0f')}
├ MACD cross:   {'✅' if d.get('daily_macd_cross') else '❌'}
├ CVD diverg:   {'✅' if d.get('cvd_divergence') else '❌'}
├ Higher lows:  {'✅' if d.get('higher_lows') else '❌'} ({fv('days_sideways','.0f')}d sideways)
└ Pattern:      {d.get('detected_pattern','none')}

<b>Market</b>
├ Market cap:   {mc_s}
├ Float:        {fv('float_pct','.1f','%')} circulating {'(low ✅)' if d.get('low_float') else ''}
├ Fear/Greed:   {fv('fear_greed_value','.0f')} ({d.get('fear_greed_label','?')})
└ BTC trend:    {d.get('btc_trend','?')} ({fv('btc_change_4h','+.1f','%')} 4h)

<b>Score: {d.get('score','?')}/{d.get('max_score','?')} — {d.get('strength','?')}</b>"""

    _send(msg)


def cmd_signals(args: list) -> None:
    """Show all 28 signals with their current values for a coin."""
    if not args:
        _send("Usage: /signals SOLUSDT or /signals SOL")
        return

    sym = args[0].upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    cached = next((r for r in _last_results if r.get("symbol") == sym), None)
    if not cached:
        _send(f"⚠️ {sym} not in last scan. Run /coin {sym} first for live data.")
        return

    signals = cached.get("signals", {})
    reasons = cached.get("reasons", [])
    score   = cached.get("score", 0)
    maxs    = cached.get("max_score", 34)

    msg = f"<b>📐 All Signals: #{esc(sym.replace('USDT',''))}</b>\nScore: {score}/{maxs}\n\n"
    for sig, (triggered, pts) in signals.items():
        icon = "✅" if triggered else "⬜"
        msg += f"{icon} {sig:<28} +{pts if triggered else 0}\n"

    msg += f"\n<b>Triggered reasons:</b>\n"
    for r in reasons:
        msg += f"  {esc(r)}\n"

    _send(msg)


def cmd_sources(args: list) -> None:
    """Show which exchange/source provided each data point."""
    if not args:
        _send("Usage: /sources SOLUSDT or /sources SOL")
        return

    sym = args[0].upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    cached = next((r for r in _last_results if r.get("symbol") == sym), None)
    if not cached:
        _send(f"⚠️ {sym} not in last scan. Run /scan first.")
        return

    d = cached

    def src_badge(src):
        if not src or src == "none": return "❓"
        if "coinglass" in str(src):  return "🥇 CoinGlass"
        if "coinalyze" in str(src):  return "🥈 Coinalyze"
        if "manual_agg" in str(src): return f"🥉 Manual({esc(src)})"
        if "binance" in str(src):    return "⚠️ Binance only"
        return f"ℹ️ {esc(str(src)[:20])}"

    ob_exs = d.get("ob_exchanges", [])
    fund_ex = d.get("funding_per_exchange", {})
    ls_ex   = d.get("ls_per_exchange", {})
    oi_ex   = d.get("oi_exchanges", {})

    msg = f"<b>🔗 Data Sources: #{esc(sym.replace('USDT',''))}</b>\n\n"
    msg += f"OI:           {src_badge(d.get('oi_source'))}\n"
    msg += f"Funding:      {src_badge(d.get('funding_source'))}\n"
    msg += f"L/S Ratio:    {src_badge(d.get('ls_source'))}\n"
    msg += f"Liquidations: {src_badge(d.get('liq_source'))}\n"
    msg += f"Order Book:   {src_badge(d.get('ob_source'))}\n"
    msg += f"Market Cap:   ℹ️ CoinGecko\n"
    msg += f"Social:       ℹ️ {'LunarCrush' if config.ENABLE_LUNARCRUSH else 'CoinGecko'}\n"
    msg += f"News:         ℹ️ {'CryptoPanic' if config.ENABLE_CRYPTOPANIC else 'Disabled'}\n"
    msg += f"BTC Context:  ℹ️ Binance + CoinGecko\n\n"

    if oi_ex:
        msg += "<b>OI per exchange (USD):</b>\n"
        total = sum(oi_ex.values())
        for ex, val in oi_ex.items():
            pct = val/total*100 if total else 0
            msg += f"  {ex}: ${val/1e6:.1f}M ({pct:.0f}%)\n"
        msg += "\n"

    if fund_ex:
        msg += "<b>Funding per exchange:</b>\n"
        for ex, rate in fund_ex.items():
            icon = "🔴" if rate < 0 else "🟢"
            msg += f"  {ex}: {icon} {rate:.5f}%\n"
        msg += "\n"

    if ls_ex:
        msg += "<b>L/S ratio per exchange:</b>\n"
        for ex, ratio in ls_ex.items():
            icon = "🩳" if ratio < 1 else "📈"
            msg += f"  {ex}: {icon} {ratio:.3f}\n"
        msg += "\n"

    if ob_exs:
        ob_depth = d.get("ob_exchange_depth", {})
        msg += f"<b>Order book depth ({len(ob_exs)} exchanges):</b>\n"
        for ex in ob_exs:
            depth = ob_depth.get(ex, {})
            bid   = depth.get("bid_usdt", 0)
            ask   = depth.get("ask_usdt", 0)
            ratio = depth.get("ratio", 0) or 0
            msg  += f"  {ex}: bid ${bid/1e3:.0f}K / ask ${ask/1e3:.0f}K (ratio {ratio:.2f})\n"

    _send(msg)


def cmd_btc(args: list) -> None:
    """Show BTC context and market conditions."""
    try:
        from modules.btc_market import get_btc_context
        ctx = get_btc_context()

        def fv(k, fmt=".2f", sfx=""):
            v = ctx.get(k)
            return f"{v:{fmt}}{sfx}" if v is not None else "N/A"

        trend_icon = {"bull": "🟢", "bear": "🔴", "sideways": "🟡"}.get(ctx.get("btc_trend","?"), "❓")
        crash_txt  = "🔴 YES — Alt alerts penalised" if ctx.get("btc_crashing") else "✅ No"
        sideways   = "✅ Yes — Good for alts" if ctx.get("btc_sideways") else "❌ No"
        dom_fall   = "✅ Alt season signal" if ctx.get("btc_dom_falling") else "❌ No"

        _send(f"""<b>₿ BTC Market Context</b>

<b>Price</b>
├ BTC Price:     ${fv('btc_price','.0f')}
├ 1h change:     {fv('btc_change_1h','+.2f','%')}
├ 4h change:     {fv('btc_change_4h','+.2f','%')}
└ 24h change:    {fv('btc_change_24h','+.2f','%')}

<b>Trend</b>
├ Trend:         {trend_icon} {ctx.get('btc_trend','?').upper()}
├ BTC Crashing:  {crash_txt}
└ BTC Sideways:  {sideways}

<b>Dominance</b>
├ BTC.D:         {fv('btc_dominance','.2f','%')}
└ Dom Falling:   {dom_fall}

<b>Alt Conditions</b>
└ Market OK:     {'✅ Good for alt pumps' if ctx.get('market_ok_for_alts') else '⚠️ Risky for alts right now'}""")
    except Exception as e:
        _send(f"❌ BTC context failed: {esc(str(e)[:200])}")


def cmd_fear(args: list) -> None:
    """Show Fear and Greed index."""
    try:
        from modules.sentiment_fetcher import get_fear_greed
        fg = get_fear_greed()
        val   = fg.get("fear_greed_value", 50)
        label = fg.get("fear_greed_label", "Unknown")
        low   = fg.get("fear_greed_low", False)

        bar   = "█" * (val // 10) + "░" * (10 - val // 10)
        icon  = "😱" if val < 25 else "😨" if val < 50 else "😏" if val < 75 else "🤑"
        note  = "→ Extreme accumulation zone" if val < 25 else \
                "→ Good alt buy zone" if val < 40 else \
                "→ Neutral" if val < 60 else \
                "→ Caution — overleveraged market" if val < 80 else \
                "→ ⚠️ Euphoria — be careful"

        _send(f"""{icon} <b>Fear & Greed Index</b>

Value:  <b>{val}</b> — {label}
[{bar}]

{note}
{'✅ Below 35 = alt buy zone signal active' if low else ''}""")
    except Exception as e:
        _send(f"❌ Fear & Greed failed: {esc(str(e)[:200])}")


def cmd_stats(args: list) -> None:
    """Show accuracy stats from pump tracker."""
    try:
        from modules.data_logger import get_accuracy_stats
        import os
        s = get_accuracy_stats()

        if not s or s.get("total_alerts", 0) == 0:
            _send("📊 No completed alerts in pump tracker yet.\nAlerts need 10 days to mature.")
            return

        total   = s.get("total_alerts", 0)
        pumped  = s.get("pumped", 0)
        win_r   = s.get("win_rate_pct", 0)
        avg_p   = s.get("avg_pump_pct", 0)
        max_p   = s.get("max_pump_pct", 0)
        bar     = "█" * int(win_r / 10) + "░" * (10 - int(win_r / 10))

        _send(f"""📊 <b>Accuracy Statistics</b>

Total alerts tracked:  {total}
Pumped (≥20% in 10d): {pumped}
Win rate:  [{bar}] <b>{win_r}%</b>

Avg pump of winners:  +{avg_p}%
Best pump:            +{max_p}%

<i>Pump = coin went up ≥20% within 10 days of alert</i>""")
    except Exception as e:
        _send(f"❌ Stats failed: {esc(str(e)[:200])}")


def cmd_config(args: list) -> None:
    """Show current bot configuration."""
    max_s = sum(config.SCORE_WEIGHTS.values())

    def tick(v): return "✅" if v else "❌"

    _send(f"""⚙️ <b>Bot Configuration</b>

<b>Scanning</b>
├ Interval:      every {config.SCAN_INTERVAL_MINUTES} min
├ Coins/scan:    top {config.TOP_N_COINS} by volume
├ Min volume:    ${config.MIN_VOLUME_USDT/1e6:.0f}M daily
└ Max market cap: ${config.MAX_MARKET_CAP_USD/1e6:.0f}M

<b>Filters</b>
├ Vol spike threshold: {config.VOLUME_SPIKE_THRESHOLD}x
├ OI change threshold: {config.OI_CHANGE_THRESHOLD}%
├ Funding max:         {config.FUNDING_RATE_MAX}%
└ L/S ratio max:       {config.LONG_SHORT_RATIO_MAX}

<b>Alerts</b>
├ Min score to alert:  {config.ALERT_MIN_SCORE}/{max_s}
├ Cooldown:            {config.ALERT_COOLDOWN_HOURS}h per coin
└ Max per scan:        {config.ALERT_MAX_PER_SCAN}

<b>Penalties</b>
├ Token unlock:  -{config.PENALTY_UNLOCK_RISK}
├ BTC crash:     -{config.PENALTY_BTC_CRASH}
├ Negative news: -{config.PENALTY_NEGATIVE_NEWS}
├ Already pumped: -{config.PENALTY_ALREADY_PUMPED}
└ High funding:  -{config.PENALTY_HIGH_FUNDING}

<b>Data Sources</b>
├ CoinGlass:     {tick(config.COINGLASS_API_KEY)}
├ Coinalyze:     {tick(config.COINALYZE_API_KEY)}
├ CryptoPanic:   {tick(config.ENABLE_CRYPTOPANIC)}
├ LunarCrush:    {tick(config.ENABLE_LUNARCRUSH and config.LUNARCRUSH_API_KEY)}
├ Google Trends: {tick(config.ENABLE_GOOGLE_TRENDS)}
├ Glassnode:     {tick(config.ENABLE_GLASSNODE and config.GLASSNODE_API_KEY)}
├ CryptoQuant:   {tick(config.ENABLE_CRYPTOQUANT and config.CRYPTOQUANT_API_KEY)}
├ Nansen:        {tick(config.ENABLE_NANSEN and config.NANSEN_API_KEY)}
├ Hyblock:       {tick(config.ENABLE_HYBLOCK and config.HYBLOCK_API_KEY)}
└ Twitter:       {tick(config.ENABLE_TWITTER and config.TWITTER_BEARER_TOKEN)}""")


def cmd_stop(args: list) -> None:
    global _scanning_paused
    _scanning_paused = True
    _send("⏸ Scanning paused. Send /resume to continue.")


def cmd_resume(args: list) -> None:
    global _scanning_paused
    _scanning_paused = False
    _send("▶️ Scanning resumed.")


def cmd_setthreshold(args: list) -> None:
    if not args:
        _send("Usage: /setthreshold 8")
        return
    try:
        n = int(args[0])
        max_s = sum(config.SCORE_WEIGHTS.values())
        if not (1 <= n <= max_s):
            _send(f"❌ Threshold must be between 1 and {max_s}")
            return
        config.ALERT_MIN_SCORE = n
        _send(f"✅ Alert threshold set to {n}/{max_s}")
    except ValueError:
        _send("❌ Must be a number. Usage: /setthreshold 8")


def cmd_watchlist(args: list) -> None:
    if not _watchlist:
        _send("📋 Watchlist is empty.\nAdd coins with /addwatch SOLUSDT")
        return
    msg = "📋 <b>Watchlist</b>\n\n"
    for sym in sorted(_watchlist):
        cached = next((r for r in _last_results if r.get("symbol") == sym), None)
        if cached:
            score = cached.get("score", "?")
            maxs  = cached.get("max_score", 34)
            chg   = cached.get("price_change_pct", 0)
            msg  += f"• <b>{sym}</b> — score {score}/{maxs} ({chg:+.1f}%)\n"
        else:
            msg += f"• <b>{sym}</b> — not in last scan\n"
    _send(msg)


def cmd_addwatch(args: list) -> None:
    if not args:
        _send("Usage: /addwatch SOLUSDT")
        return
    sym = args[0].upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    _watchlist.add(sym)
    _send(f"✅ Added {sym} to watchlist ({len(_watchlist)} coins)")


def cmd_removewatch(args: list) -> None:
    if not args:
        _send("Usage: /removewatch SOLUSDT")
        return
    sym = args[0].upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    _watchlist.discard(sym)
    _send(f"✅ Removed {sym} from watchlist")


def cmd_history(args: list) -> None:
    """Show last 10 alerts from alert log."""
    import os, csv
    path = "data/alert_log.csv"
    if not os.path.isfile(path):
        _send("📜 No alert history yet.")
        return
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        _send("📜 No alert history yet.")
        return
    recent = rows[-10:][::-1]
    msg = "<b>📜 Last 10 Alerts</b>\n\n"
    for r in recent:
        ts    = r.get("timestamp","?")[:16]
        sym   = r.get("symbol","?").replace("USDT","")
        score = r.get("score","?")
        maxs  = r.get("max_score","?")
        pat   = r.get("detected_pattern","none")
        msg  += f"• <b>{esc(sym)}</b> {esc(score)}/{esc(maxs)} [{esc(ts)}]"
        if pat and pat != "none":
            msg += f" [{esc(pat)}]"
        msg += "\n"
    _send(msg)


# ═══════════════════════════════════════════════════════
#  COMMAND ROUTER
# ═══════════════════════════════════════════════════════

def cmd_backtest(args: list) -> None:
    """
    /backtest              — scan all coins, last 30 days, find pumps ≥30%
    /backtest SOLUSDT      — backtest specific coin (180 days, ≥20% pumps)
    /backtest 60           — scan all coins, last 60 days
    /backtest 60 20        — last 60 days, pumps ≥20%
    """
    from modules.backtester import (
        run_historical_backtest, backtest_single_coin,
        format_historical_backtest, format_single_coin_backtest
    )

    # Parse args
    symbol      = None
    lookback    = 30
    min_pump    = 30.0

    for arg in args:
        arg = arg.upper()
        if arg.endswith("USDT") or (len(arg) >= 2 and not arg.isdigit()):
            # Looks like a coin symbol
            symbol = arg if arg.endswith("USDT") else arg + "USDT"
        elif arg.isdigit():
            if lookback == 30:
                lookback = int(arg)
            else:
                min_pump = float(arg)

    if symbol:
        _send(
            f"🔍 Backtesting <b>{esc(symbol.replace('USDT',''))}</b> "
            f"({lookback}d, &gt;{min_pump}% pumps)... Fetching up to 2 years of data..."
        )
        try:
            result = backtest_single_coin(symbol, lookback_days=lookback, min_pump_pct=min_pump)
            msgs   = format_single_coin_backtest(result, symbol)
            _send_multi(msgs)
        except Exception as e:
            _send(f"❌ Backtest failed: {esc(str(e)[:200])}")
    else:
        _send(
            f"🔍 Scanning ALL Binance Futures coins for pumps &gt;{min_pump}% "
            f"in last {lookback} days...\n\n"
            f"This scans ~600 coins x OHLCV data.\n"
            f"Estimated time: 5-15 minutes.\n\n"
            f"You'll get results when done. Bot continues scanning normally."
        )
        try:
            sent_count = [0]
            total      = [600]

            def progress(cur, tot, msg):
                total[0] = tot
                # Send progress update every 100 coins
                if cur % 100 == 0 or cur == tot:
                    _send(f"⏳ Progress: {cur}/{tot} coins scanned...")

            result = run_historical_backtest(
                lookback_days=lookback,
                min_pump_pct=min_pump,
                progress_cb=progress,
            )
            msgs = format_historical_backtest(result)
            _send_multi(msgs)
        except Exception as e:
            _send(f"❌ Backtest failed: {esc(str(e)[:200])}")


def cmd_accuracy(args: list) -> None:
    """Show signal accuracy from your own alert history vs actual pump outcomes."""
    from modules.backtester import run_alert_accuracy_analysis, format_alert_accuracy
    _send("📊 Analysing your alert history vs pump outcomes...")
    try:
        result = run_alert_accuracy_analysis()
        msgs   = format_alert_accuracy(result)
        _send_multi(msgs)
    except Exception as e:
        _send(f"❌ Accuracy analysis failed: {esc(str(e)[:200])}")


def cmd_suggest_weights(args: list) -> None:
    """Show suggested SCORE_WEIGHTS based on backtest data."""
    import os, json
    backtest_path  = "data/backtest_results.json"
    accuracy_path  = "data/alert_accuracy.json"

    # Load both sources if available
    suggestions = {}

    if os.path.isfile(accuracy_path):
        with open(accuracy_path) as f:
            acc = json.load(f)
        suggestions["alert_accuracy"] = acc.get("suggested_weights", {})
        _send("📊 Using your alert accuracy data (most reliable source)")

    if os.path.isfile(backtest_path):
        with open(backtest_path) as f:
            bt = json.load(f)
        suggestions["historical"] = bt.get("suggested_weights", {})

    if not suggestions:
        _send(
            "⚠️ No backtest data yet.\n\n"
            "Run first:\n"
            "/backtest — historical pump scan\n"
            "/accuracy — alert accuracy analysis"
        )
        return

    current = config.SCORE_WEIGHTS
    msg     = "<b>💡 Suggested SCORE_WEIGHTS</b>\n\n"

    # Prefer alert_accuracy (more reliable) over historical
    primary = suggestions.get("alert_accuracy") or suggestions.get("historical", {})

    msg += "<code>Signal                        Current  Suggested</code>\n"
    changes = 0
    for sig, new_w in sorted(primary.items()):
        old_w = current.get(sig, "—")
        changed = str(old_w) != str(new_w)
        if changed:
            changes += 1
            arrow = "up" if (isinstance(new_w,int) and isinstance(old_w,int) and new_w > old_w) else "down"
            msg += f"<code>{sig[:30]:<30}</code> {old_w}  ->  <b>{new_w}</b> ({arrow})\n"
        else:
            msg += f"<code>{sig[:30]:<30}</code> {old_w}  ok\n"

    msg += f"\n{changes} changes suggested.\n\n"
    msg += "<b>To apply:</b> Edit SCORE_WEIGHTS in config.py, restart bot."
    _send(msg)


def cmd_pumps(args: list) -> None:
    """
    /pumps            — show coins that pumped most in last 7 days (ALL coins)
    /pumps 30         — pumped most in last 30 days
    /pumps 30 15      — pumped >15% in last 30 days
    """
    from modules.backtester import run_historical_backtest
    from modules.binance_fetcher import _is_real_crypto
    import requests

    lookback = getattr(config, "PUMPS_DEFAULT_LOOKBACK", 7)
    min_pump = float(getattr(config, "PUMPS_DEFAULT_MIN_PCT", 15))
    for arg in args:
        if arg.replace(".","").isdigit():
            val = float(arg)
            if val == int(val) and val <= 365 and lookback == getattr(config, "PUMPS_DEFAULT_LOOKBACK", 7):
                lookback = int(val)
            else:
                min_pump = val

    _send(
        f"🔍 Finding coins that pumped &gt;{min_pump:.0f}% in last {lookback} days...\n"
        f"Scanning ALL Binance Futures coins (takes 2-5 min)..."
    )

    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
        r.raise_for_status()
        tickers = r.json()

        # Scan ALL real crypto coins with min $100K volume (catches small caps)
        all_syms = [
            t["symbol"] for t in tickers
            if t["symbol"].endswith("USDT")
            and _is_real_crypto(t["symbol"])
            and float(t.get("quoteVolume", 0)) >= 100_000
        ]

        _send(f"Scanning {len(all_syms)} coins...")

        result = run_historical_backtest(
            symbols=all_syms,
            lookback_days=lookback,
            min_pump_pct=min_pump,
            max_pump_days=lookback,
        )

        n = result.get("total_pumps_found", 0)
        if n == 0:
            _send(
                f"No coins pumped &gt;{min_pump:.0f}% in last {lookback} days.\n\n"
                f"Try /pumps {lookback} 15 to lower threshold to 15%"
            )
            return

        top = result.get("top_pumps", [])[:20]
        msg = f"<b>Pumped &gt;{min_pump:.0f}% in last {lookback}d — {n} coins</b>\n\n"
        for p in top:
            sym  = esc(p["symbol"].replace("USDT",""))
            pct  = p["pump_pct"]
            days = p["pump_days"]
            date = p["pump_date"]
            pat  = esc(p.get("pre_pump_pattern","none"))
            rsi  = p.get("pre_pump_rsi")
            vol  = p.get("pre_pump_vol_ratio")
            sigs = [esc(s) for s in list(p.get("pre_pump_signals",{}).keys())[:3]]
            rsi_str = f"RSI={rsi:.0f}" if rsi else "RSI=?"
            vol_str = f"vol={vol:.1f}x" if vol else "vol=?"
            msg += (
                f"<b>{sym}</b> <b>+{pct:.0f}%</b> in {days}d ({date})\n"
                f"  Before: {rsi_str}, {vol_str}, pattern={pat}\n"
                f"  Signals: {', '.join(sigs) or 'none'}\n\n"
            )
        _send(msg)

    except Exception as e:
        _send(f"❌ Failed: {esc(str(e)[:200])}")


def cmd_signals_active(args: list) -> None:
    """
    Show all active signals with live P&L.
    /signals_active         — sorted by current P&L (best first)
    /signals_active score   — sorted by score
    /signals_active time    — sorted by alert time (newest first)
    """
    from modules import signal_tracker
    import time as _time

    signals = signal_tracker.get_active_signals()
    if not signals:
        _send(
            "No signals being monitored yet.\n\n"
            f"Signals scoring >= {config.PAPER_TRADE_SCORE} are tracked automatically "
            f"for {config.SIGNAL_MONITOR_DAYS} days.\n"
            "Run /scan to start generating alerts."
        )
        return

    # Sort order
    sort_by = args[0].lower() if args else "pnl"
    if sort_by == "score":
        signals = sorted(signals, key=lambda x: x.get("score", 0), reverse=True)
    elif sort_by == "time":
        signals = sorted(signals, key=lambda x: x.get("alert_ts", 0), reverse=True)
    else:
        # Default: sort by current P&L (best performing first)
        signals = sorted(signals, key=lambda x: x.get("max_pump_pct", 0), reverse=True)

    now = _time.time()
    n   = len(signals)

    # Summary line
    winners  = sum(1 for s in signals if s.get("max_pump_pct", 0) >= 10)
    losers   = sum(1 for s in signals if s.get("max_dump_pct", 0) <= -10)
    avg_pump = sum(s.get("max_pump_pct", 0) for s in signals) / n if n else 0

    msg  = f"<b>Active Signals — {n} monitored</b>\n"
    msg += f"Up >10%: {winners} | Down >10%: {losers} | Avg peak: {avg_pump:+.1f}%\n"
    msg += f"Sort: {sort_by} | Use /signals_active score or time\n\n"
    msg += "<code>Coin       Score  Entry     Now%   Peak   Trough  Days</code>\n"
    msg += "<code>" + "─" * 55 + "</code>\n"

    for sig in signals[:25]:  # Telegram limit
        sym    = esc(sig["symbol"].replace("USDT", ""))[:9]
        entry  = sig.get("entry_price", 0) or 0
        score  = sig.get("score", 0)
        pump   = sig.get("max_pump_pct", 0)
        dump   = sig.get("max_dump_pct", 0)
        days   = (now - sig.get("alert_ts", now)) / 86400
        left   = (sig.get("expire_ts", now) - now) / 86400

        # Current price from last check
        checks   = sig.get("price_checks", [])
        cur_pct  = checks[-1]["pct_change"] if checks else 0.0
        cur_price = checks[-1]["price"] if checks else entry

        # Visual indicator
        if cur_pct >= 20:    icon = "🚀"
        elif cur_pct >= 10:  icon = "📈"
        elif cur_pct >= 0:   icon = "🟢"
        elif cur_pct >= -10: icon = "🟡"
        elif cur_pct >= -20: icon = "🔴"
        else:                icon = "💀"

        # Format current P&L with sign
        cur_str  = f"{cur_pct:+.1f}%"
        pump_str = f"+{pump:.1f}%"
        dump_str = f"{dump:.1f}%"
        days_str = f"{days:.1f}d"

        msg += (
            f"{icon} <b>{sym:<9}</b> {score:>2}  "
            f"${entry:.5g}  {cur_str:>7}  "
            f"{pump_str:>7}  {dump_str:>7}  {days_str}\n"
        )

    if n > 25:
        msg += f"\n<i>... and {n-25} more signals</i>\n"

    msg += f"\n<i>Peak = best price reached | Trough = worst | Now% = current vs entry</i>"
    _send(msg)


def cmd_signals_history(args: list) -> None:
    """Show last 20 completed signals."""
    from modules import signal_tracker
    signals = signal_tracker.get_completed_signals(20)
    if not signals:
        _send("No completed signals yet. Signals complete after 15 days of monitoring.")
        return
    n   = len(signals)
    msg = f"<b>Completed Signals (last {n})</b>\n\n"
    for sig in signals:
        sym    = esc(sig["symbol"].replace("USDT",""))
        score  = sig.get("score","?")
        pump   = sig.get("max_pump_pct",0)
        dump   = sig.get("max_dump_pct",0)
        result = sig.get("final_result","?")
        date   = str(sig.get("alert_time","?"))[:10]
        icon   = "🟢" if result=="pumped" else "🔴" if result=="dumped" else "⬜"
        msg   += f"{icon} <b>{sym}</b> [{date}] score={score}\n"
        msg   += f"   Peak: +{pump:.0f}% | Trough: {dump:.0f}% | {result}\n\n"
    _send(msg)


def cmd_signals_stats(args: list) -> None:
    """Show accuracy stats from all tracked signals."""
    from modules import signal_tracker
    s = signal_tracker.get_stats()
    if s["total_completed"] == 0:
        active = s['active']
        _send(f"No completed signals yet. Currently tracking: {active} active signals.")
        return
    bar = "█" * int(s["win_rate"]/10) + "░" * (10-int(s["win_rate"]/10))
    msg = (
        f"<b>Signal Tracker Stats</b>\n\n"
        f"Total completed: <b>{s['total_completed']}</b>\n"
        f"Still active:    <b>{s['active']}</b>\n\n"
        f"Pumped (>=20%):  <b>{s['pumped']}</b>\n"
        f"Dumped (<=-20%): <b>{s['dumped']}</b>\n"
        f"Flat:           <b>{s['flat']}</b>\n\n"
        f"Win rate: [{bar}] <b>{s['win_rate']}%</b>\n"
        f"Avg pump:  +{s['avg_pump']}%\n"
        f"Best pump: +{s['best_pump']}%\n"
        f"Worst:     {s['worst_dump']}%\n"
    )
    buckets = s.get("score_buckets", {})
    if buckets:
        msg += "\n<b>Win rate by score range:</b>\n"
        for bucket, bdata in sorted(buckets.items()):
            total  = bdata["total"]
            pumped = bdata["pumped"]
            rate   = int(pumped/total*100) if total else 0
            bar2   = "█" * (rate//10) + "░" * (10-rate//10)
            msg   += f"  {bucket}: [{bar2}] {rate}% ({pumped}/{total})\n"
    _send(msg)


def cmd_give_file(args: list) -> None:
    """Send all lifetime signal files (100 signals per JSON file)."""
    from modules import signal_tracker
    import os
    paths = signal_tracker.get_lifetime_batch_paths()
    if not paths:
        _send("No lifetime signal files yet.\nSignals are saved after their 15-day monitoring period completes.")
        return
    n = len(paths)
    _send(f"Sending {n} signal file(s) ({n*100} signals max)...")
    for i, path in enumerate(paths, 1):
        try:
            import requests as req
            with open(path, "rb") as f:
                r = req.post(
                    f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendDocument",
                    data={"chat_id": config.TELEGRAM_CHAT_ID,
                          "caption": f"Signals batch {i}/{n} — {os.path.basename(path)}"},
                    files={"document": (os.path.basename(path), f, "application/json")},
                    timeout=30
                )
                if r.status_code != 200:
                    _send(f"Failed to send {esc(os.path.basename(path))}: {esc(r.text[:100])}")
        except Exception as e:
            _send(f"Error sending file: {esc(str(e)[:100])}")
        __import__("time").sleep(1)
    _send(f"Done. {n} file(s) sent.")


def cmd_daily_report(args: list) -> None:
    """Send daily report for a specific date or today."""
    from modules import signal_tracker
    import json, os
    date_str = args[0] if args else None
    path     = signal_tracker.get_daily_file_path(date_str)
    if not path:
        date_str = date_str or __import__("datetime").datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _send(f"📅 No signals completed on {date_str}.")
        return
    with open(path) as f:
        signals = json.load(f)
    if not signals:
        _send(f"📅 No signals completed on {date_str}.")
        return
    date_label = os.path.basename(path).replace(".json","")
    n   = len(signals)
    msg = f"<b>Daily Report — {date_label}</b>\n"
    msg += f"Signals completed: {n}\n\n"
    for sig in list(signals.values()):
        sym    = esc(sig["symbol"].replace("USDT",""))
        pump   = sig.get("max_pump_pct",0)
        dump   = sig.get("max_dump_pct",0)
        result = sig.get("final_result","?")
        score  = sig.get("score","?")
        icon   = "🟢" if result=="pumped" else "🔴" if result=="dumped" else "⬜"
        msg   += f"{icon} <b>{sym}</b> score={score} +{pump:.0f}% / {dump:.0f}% — {result}\n"
    _send(msg)



def cmd_portfolio(args: list) -> None:
    """
    /portfolio — clean table showing all active signals:
    Coin | Score | Entry | Current | Peak | Lowest | Days left

    /portfolio score  — sort by score (highest first)
    /portfolio best   — sort by peak % (best performing first)
    /portfolio worst  — sort by current % (worst performing first)
    /portfolio fresh  — sort by newest alerts first
    """
    from modules import signal_tracker
    import time as _time

    signals = signal_tracker.get_active_signals()
    if not signals:
        _send(
            f"No active signals yet.\n"
            f"Signals with score >= {config.PAPER_TRADE_SCORE} are tracked for "
            f"{config.SIGNAL_MONITOR_DAYS} days automatically."
        )
        return

    sort_by = args[0].lower() if args else "best"
    if sort_by == "score":
        signals = sorted(signals, key=lambda x: x.get("score", 0), reverse=True)
    elif sort_by == "worst":
        signals = sorted(signals, key=lambda x: x.get("price_checks", [{}])[-1].get("pct_change", 0) if x.get("price_checks") else 0)
    elif sort_by == "fresh":
        signals = sorted(signals, key=lambda x: x.get("alert_ts", 0), reverse=True)
    else:  # best (default)
        signals = sorted(signals, key=lambda x: x.get("max_pump_pct", 0), reverse=True)

    now = _time.time()
    n   = len(signals)

    # Summary stats
    winners    = sum(1 for s in signals if s.get("max_pump_pct", 0) >= 10)
    losers     = sum(1 for s in signals if (s.get("price_checks") or [{}])[-1].get("pct_change", 0) <= -10)
    best_coin  = max(signals, key=lambda x: x.get("max_pump_pct", 0))
    best_sym   = esc(best_coin["symbol"].replace("USDT",""))
    best_pct   = best_coin.get("max_pump_pct", 0)

    header = (
        f"<b>Signal Portfolio — {n} active</b>\n"
        f"Best: {best_sym} +{best_pct:.1f}%  |  "
        f"Winning: {winners}  |  Losing: {losers}\n"
        f"Sorted by: {sort_by} | "
        f"/portfolio score · best · worst · fresh\n\n"
    )

    # Table header
    row_hdr = (
        "<code>"
        f"{'Coin':<9} {'Sc':>3} {'Entry':>10} "
        f"{'Cur%':>7} {'Peak':>7} {'Low':>7} {'Days':>5}"
        "</code>\n"
        "<code>" + "─" * 50 + "</code>\n"
    )

    rows = []
    for sig in signals[:30]:
        sym   = esc(sig["symbol"].replace("USDT", ""))[:9]
        score = sig.get("score", 0)
        entry = sig.get("entry_price", 0) or 0
        pump  = sig.get("max_pump_pct", 0)
        dump  = sig.get("max_dump_pct", 0)
        days_elapsed = (now - sig.get("alert_ts", now)) / 86400
        days_left    = (sig.get("expire_ts", now) - now) / 86400
        days_str_fmt = fmt_elapsed(days_elapsed)
        left_str_fmt = fmt_remaining(days_left)

        checks   = sig.get("price_checks", [])
        cur_pct  = checks[-1]["pct_change"] if checks else 0.0
        cur_price = checks[-1]["price"] if checks else entry

        # Icon based on current %
        if cur_pct >= 30:    icon = "🚀"
        elif cur_pct >= 15:  icon = "📈"
        elif cur_pct >= 5:   icon = "🟢"
        elif cur_pct >= -5:  icon = "➡️"
        elif cur_pct >= -15: icon = "🔴"
        else:                icon = "💀"

        # Format entry price compactly
        if entry >= 1000:    entry_str = f"${entry:,.0f}"
        elif entry >= 1:     entry_str = f"${entry:.3f}"
        elif entry >= 0.01:  entry_str = f"${entry:.4f}"
        else:                entry_str = f"${entry:.6f}"

        cur_str  = f"{cur_pct:+.1f}%"
        pump_str = f"+{pump:.1f}%"
        dump_str = f"{dump:.1f}%"
        days_str = left_str_fmt

        row = (
            f"{icon}<code>"
            f"{sym:<9} {score:>3} {entry_str:>10} "
            f"{cur_str:>7} {pump_str:>7} {dump_str:>7} {days_str:>5}"
            f"</code>\n"
        )
        rows.append(row)

    # Split into chunks if needed (Telegram 4096 char limit)
    msg = header + row_hdr
    chunk_msgs = [msg]

    for row in rows:
        if len(chunk_msgs[-1]) + len(row) > 3900:
            chunk_msgs.append("")
        chunk_msgs[-1] += row

    # Footer on last message
    chunk_msgs[-1] += (
        "\n<i>Entry = alert price | Cur% = now vs entry | "
        "Peak = best ever | Low = worst ever | Days = days left</i>"
    )

    if n > 30:
        chunk_msgs[-1] += f"\n<i>Showing 30/{n} — use sort options to see different coins</i>"

    _send_multi(chunk_msgs)


def cmd_refresh_ids(args: list) -> None:
    """Rebuild the CoinGecko ID map (download all ~14k coin IDs fresh)."""
    _send("🔄 Refreshing CoinGecko coin ID map... (~14k coins, takes ~10s)")
    try:
        from modules.sentiment_fetcher import build_full_id_map
        n = build_full_id_map(force=True)
        _send(f"✅ CoinGecko ID map refreshed: {n} coins mapped\n"
              f"Saved to data/cg_id_map.json")
    except Exception as e:
        _send(f"❌ Refresh failed: {esc(str(e)[:200])}")


def cmd_idmap(args: list) -> None:
    """Show stats about the CoinGecko ID map."""
    import os
    from modules.sentiment_fetcher import _cg_id_map, _id_map_loaded, build_full_id_map
    if not _id_map_loaded:
        build_full_id_map()
    n = len(_cg_id_map)
    path = "data/cg_id_map.json"
    size = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
    # check how many current scan symbols are covered
    covered = sum(1 for sym in _last_results if _cg_id_map.get(sym.replace("USDT","").lower()))
    _send(f"🗺 <b>CoinGecko ID Map</b>\n\n"
          f"Total coins mapped: <b>{n}</b>\n"
          f"File size: {size}KB\n"
          f"Last scan coverage: {covered}/{len(_last_results)} coins\n\n"
          f"Use /refresh_ids to update with newly listed coins")


COMMANDS = {
    "/start":         cmd_start,
    "/help":          cmd_help,
    "/check":         cmd_check,
    "/apis":          cmd_apis,
    "/scan":          cmd_scan,
    "/top":           cmd_top,
    "/coin":          cmd_coin,
    "/signals":       cmd_signals,
    "/sources":       cmd_sources,
    "/btc":           cmd_btc,
    "/fear":          cmd_fear,
    "/stats":         cmd_stats,
    "/config":        cmd_config,
    "/stop":          cmd_stop,
    "/resume":        cmd_resume,
    "/setthreshold":  cmd_setthreshold,
    "/watchlist":     cmd_watchlist,
    "/addwatch":      cmd_addwatch,
    "/removewatch":   cmd_removewatch,
    "/history":       cmd_history,
    "/refresh_ids":   cmd_refresh_ids,
    "/idmap":         cmd_idmap,
    "/signals_active":  cmd_signals_active,
    "/signals_history": cmd_signals_history,
    "/portfolio":       cmd_portfolio,
    "/signals_stats":   cmd_signals_stats,
    "/give_file":       cmd_give_file,
    "/daily_report":    cmd_daily_report,
    "/backtest":      cmd_backtest,
    "/accuracy":      cmd_accuracy,
    "/suggest_weights": cmd_suggest_weights,
    "/pumps":         cmd_pumps,
}


def handle_update(update: dict) -> None:
    """Process one Telegram update."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    text = msg.get("text", "").strip()
    if not text.startswith("/"):
        return

    # Strip bot username if present (e.g. /start@mybotname)
    parts = text.split("@")[0].split()
    cmd   = parts[0].lower()
    args  = parts[1:]

    log.info(f"Command received: {cmd} {args}")

    handler = COMMANDS.get(cmd)
    if handler:
        try:
            threading.Thread(target=handler, args=(args,), daemon=True).start()
        except Exception as e:
            _send(f"❌ Command error: {str(e)[:200]}")
            log.error(f"Command {cmd} error: {traceback.format_exc()}")
    else:
        _send(f"❓ Unknown command: {cmd}\nSend /help for all commands.")


def poll_loop() -> None:
    """
    Long-polling loop — listens for Telegram messages.
    Runs in a background thread.
    """
    global _last_update_id

    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.warning("Telegram not configured — command polling disabled")
        return

    log.info("📡 Telegram command listener started")

    while True:
        try:
            r = requests.get(
                f"{BASE_URL}/getUpdates",
                params={"offset": _last_update_id + 1, "timeout": 30},
                timeout=35,
            )
            if r.status_code == 200:
                updates = r.json().get("result", [])
                for update in updates:
                    _last_update_id = update["update_id"]
                    handle_update(update)
        except requests.Timeout:
            pass
        except Exception as e:
            log.debug(f"Poll error: {e}")
            time.sleep(5)


def start_command_listener() -> None:
    """Start the command polling in a background thread."""
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    log.info("📡 Command listener thread started")
