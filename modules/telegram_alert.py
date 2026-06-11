"""
MODULE: telegram_alert.py

HTML SAFETY RULES (Telegram parse_mode=HTML only allows these tags):
  <b>bold</b>  <i>italic</i>  <code>code</code>  <pre>pre</pre>
  <a href="...">link</a>

ANY other tag or unescaped < > & in the message = parse error = alert dropped.

SOLUTION:
  - esc(str) sanitizes ALL user-supplied data before inserting into messages
  - Converts:  &→&amp;   <→&lt;   >→&gt;
  - Applied to: symbol, news headlines, pattern names, exchange names,
                reasons list, unlock events, any field from external APIs

  - Links use plain text URLs instead of <a href> tags
    Reason: <a> tags require exact URL encoding; plain URLs auto-link in Telegram
    and NEVER cause parse errors
"""

import time
import requests
from datetime import datetime, timezone
from modules.logger import get_logger
import config

log  = get_logger("telegram")
BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
_last_alerts: dict = {}


def esc(text) -> str:
    """
    Escape any string for safe insertion into Telegram HTML messages.
    Must be applied to ALL data from external sources.
    """
    if text is None:
        return "N/A"
    s = str(text)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def _safe_float(val, fmt: str, fallback: str = "N/A") -> str:
    """Format a float safely, returning fallback if None."""
    if val is None:
        return fallback
    try:
        return f"{val:{fmt}}"
    except Exception:
        return fallback


def _send_raw(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message. On parse error, retry without parse_mode (plain text)."""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.warning("Telegram not configured — printing to console")
        print("\n" + "="*60 + "\n" + text + "\n" + "="*60 + "\n")
        return True
    try:
        r = requests.post(
            f"{BASE}/sendMessage",
            json={
                "chat_id":    config.TELEGRAM_CHAT_ID,
                "text":       text[:4096],
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if r.status_code == 200:
            return True

        # If HTML parse failed, retry as plain text so alert is never lost
        if r.status_code == 400 and "parse" in r.text.lower():
            log.warning(f"HTML parse error — retrying as plain text")
            import re
            plain = re.sub(r"<[^>]+>", "", text)   # strip all HTML tags
            plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            r2 = requests.post(
                f"{BASE}/sendMessage",
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "text":    plain[:4096],
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            return r2.status_code == 200

        log.error(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
        return False

    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def is_on_cooldown(symbol: str) -> bool:
    last = _last_alerts.get(symbol, 0)
    return (time.time() - last) < config.ALERT_COOLDOWN_HOURS * 3600


def send_pump_alert(symbol: str, data: dict, score_result: dict) -> bool:
    if is_on_cooldown(symbol):
        log.info(f"⏳ {symbol} on cooldown")
        return False

    score    = score_result.get("score", 0)
    max_s    = score_result.get("max_score", 34)
    strength = score_result.get("strength", "?")
    reasons  = score_result.get("reasons", [])
    pct      = score_result.get("pct_score", 0)

    # ── Pull all values (None-safe) ──────────────────────────────
    price     = data.get("price") or 0
    chg_24h   = data.get("price_change_pct") or 0
    chg_7d    = data.get("price_change_7d")
    chg_30d   = data.get("price_change_30d")
    vol_ratio = data.get("vol_ratio")
    oi_chg    = data.get("oi_change_24h")
    funding   = data.get("funding_rate")
    ls        = data.get("ls_ratio_global")
    ls_top    = data.get("ls_ratio_top")
    rsi_d     = data.get("rsi_daily")
    rsi_1h    = data.get("rsi_1h")
    mc        = data.get("market_cap_usd")
    fg        = data.get("fear_greed_value")
    fg_label  = data.get("fear_greed_label", "")
    pct_ath   = data.get("pct_from_ath")
    pct_low   = data.get("pct_from_recent_low")
    days_sw   = data.get("days_sideways") or 0
    pattern   = data.get("detected_pattern", "none")
    basis     = data.get("basis_pct")
    oi_mc     = data.get("oi_mc_ratio")
    unlock    = data.get("unlock_risk", False)
    gt_score  = data.get("google_trend_score")
    float_pct = data.get("float_pct")
    liq_long  = data.get("liq_long_24h_usd")
    liq_short = data.get("liq_short_24h_usd")
    ob_imb    = data.get("ob_imbalance_pct")
    ob_ratio  = data.get("ob_bid_ask_ratio_agg") or data.get("bid_ask_ratio")
    ob_exs    = data.get("ob_exchanges", [])
    ob_thin   = data.get("ob_thin_book_agg", False)
    cross_spr = data.get("ob_cross_exchange_spread_pct")
    wall_side = data.get("ob_largest_wall_side") or ""
    wall_ex   = data.get("ob_largest_wall_exchange") or ""
    wall_usd  = data.get("ob_largest_wall_usdt")
    buy_wall  = data.get("ob_large_buy_wall_agg") or data.get("large_buy_wall", False)
    sell_wall = data.get("ob_large_sell_wall_agg") or data.get("large_sell_wall", False)
    oi_src    = data.get("oi_source", "?")
    ob_src    = data.get("ob_source", "?")

    # ── Safe formatted values ────────────────────────────────────
    sf = _safe_float

    coin_name = esc(symbol.replace("USDT", ""))
    mc_str    = f"${mc/1e6:.0f}M" if mc else "N/A"
    fg_str    = f"{fg} ({esc(fg_label)})" if fg else "N/A"
    liq_str   = (f"L:${liq_long/1e3:.0f}K / S:${liq_short/1e3:.0f}K"
                 if liq_long and liq_short else "N/A")
    ob_exs_str = esc(", ".join(ob_exs)) if ob_exs else "binance"
    float_str = (f"{float_pct:.0f}% circulating" if float_pct else "unknown")
    wall_str  = (f"${wall_usd/1e3:.0f}K {esc(wall_side)} on {esc(wall_ex)}"
                 if wall_usd else "N/A")
    pat_str   = esc(pattern)
    bar_fill  = int((score / max_s) * 10)
    bar       = "█" * bar_fill + "░" * (10 - bar_fill)
    ts        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    unlock_hdr = "  ⛔ UNLOCK RISK" if unlock else ""

    # ── Build message ────────────────────────────────────────────
    lines = [
        f"<b>🚀 PUMP SIGNAL{unlock_hdr}</b>",
        f"",
        f"<b>#{coin_name}</b>  |  {esc(strength)}",
        f"Score: <b>{score}/{max_s}</b>  [{bar}]  {pct}%",
        f"",
        f"<b>📊 Price</b>",
        f"├ Price:      ${sf(price,'.6g')}",
        f"├ 24h:        {chg_24h:+.2f}%",
        f"├ 7d:         {sf(chg_7d,'+.1f','N/A')}%",
        f"├ 30d:        {sf(chg_30d,'+.1f','N/A')}%",
        f"├ From ATH:   {sf(pct_ath,'.1f','N/A')}%",
        f"└ From Low:   +{sf(pct_low,'.1f','?')}% | {days_sw}d sideways",
        f"",
        f"<b>📈 Futures</b>  <i>src: {esc(oi_src)}</i>",
        f"├ Vol Ratio:  {sf(vol_ratio,'.1f','N/A')}x",
        f"├ OI Change:  {sf(oi_chg,'+.1f','N/A')}%",
        f"├ OI/MC:      {sf(oi_mc,'.3f','N/A')}",
        f"├ Funding:    {sf(funding,'.4f','N/A')}%",
        f"├ L/S Global: {sf(ls,'.2f','N/A')}",
        f"├ L/S Whales: {sf(ls_top,'.2f','N/A')}",
        f"├ Basis:      {sf(basis,'.4f','N/A')}%",
        f"└ Liq 24h:    {liq_str}",
        f"",
        f"<b>🔬 Technical</b>",
        f"├ RSI Daily:  {sf(rsi_d,'.0f','N/A')}  |  RSI 1h: {sf(rsi_1h,'.0f','N/A')}",
        f"├ BB Squeeze: {'✅' if data.get('bb_squeeze') else '❌'}",
        f"├ CVD Diverg: {'✅' if data.get('cvd_divergence') else '❌'}",
        f"└ Pattern:    {pat_str}",
        f"",
        f"<b>📋 Market</b>",
        f"├ Market Cap: {mc_str}",
        f"├ Float:      {float_str}",
        f"├ Fear/Greed: {fg_str}",
        f"└ GTrends:    {sf(gt_score,'.0f','N/A')}",
        f"",
        f"<b>📖 Order Book</b>  ({ob_exs_str})  <i>src: {esc(ob_src)}</i>",
        f"├ Bid/Ask:    {sf(ob_ratio,'.3f','N/A')} ({'⚠️ THIN' if ob_thin else 'ok'})",
        f"├ Imbalance:  {sf(ob_imb,'+.1f','N/A')}%",
        f"├ Buy Wall:   {'✅' if buy_wall else '❌'}  Sell Wall: {'✅' if sell_wall else '❌'}",
        f"├ Biggest:    {wall_str}",
        f"└ Cross-ex:   {sf(cross_spr,'.3f','N/A')}%",
        f"",
        f"<b>✅ Signals</b>",
    ]

    # Reasons — each one escaped individually
    for reason in reasons:
        lines.append(f"  {esc(reason)}")

    # Plain text links (no <a> tags — avoids all URL encoding issues)
    lines += [
        f"",
        f"<b>🔗 Links</b>",
        f"TradingView: https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}",
        f"CoinGlass:   https://www.coinglass.com/futures/{symbol.replace('USDT','')}",
        f"Binance:     https://www.binance.com/en/futures/{symbol}",
        f"",
        f"⚠️ <i>DYOR. Not financial advice.</i>",
        f"🕐 {ts}",
    ]

    msg = "\n".join(lines)

    ok = _send_raw(msg)
    if ok:
        _last_alerts[symbol] = time.time()
        log.info(f"✅ Alert sent: {symbol} score {score}/{max_s}")
    return ok


def send_scan_summary(results: list, scan_time: str) -> bool:
    if not results:
        return _send_raw(f"🔍 Scan complete ({esc(scan_time)}) — no signals above threshold.")

    top = sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:8]
    lines = [f"<b>📋 Scan Summary</b> — {esc(scan_time)}", ""]

    for i, r in enumerate(top, 1):
        sym = esc(r.get("symbol", "?").replace("USDT", ""))
        sc  = r.get("score", 0)
        ms  = r.get("max_score", 34)
        chg = r.get("price_change_pct") or 0
        vol = r.get("vol_ratio") or 0
        pat = r.get("detected_pattern", "none")
        bar = "█" * int(sc / ms * 5) + "░" * (5 - int(sc / ms * 5))
        pat_str = f" [{esc(pat)}]" if pat and pat != "none" else ""
        lines.append(
            f"{i}. <b>{sym}</b> {bar} {sc}/{ms}"
            f" ({chg:+.1f}% vol:{vol:.1f}x){pat_str}"
        )

    return _send_raw("\n".join(lines))


def send_startup_message() -> None:
    max_s      = sum(config.SCORE_WEIGHTS.values())
    deep_limit = getattr(config, "DEEP_SCAN_LIMIT", config.TOP_N_COINS)
    cap_m      = int(config.MAX_MARKET_CAP_USD / 1e6)
    _send_raw(
        f"🤖 <b>Crypto Pump Scanner STARTED</b>\n"
        f"Signals: 28  |  Max score: {max_s}\n"
        f"Scan interval: {config.SCAN_INTERVAL_MINUTES} min\n"
        f"Alert threshold: {config.ALERT_MIN_SCORE}/{max_s}\n"
        f"Market cap filter: &lt;${cap_m}M\n"
        f"Deep scan: top {deep_limit} coins\n"
        f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def send_error_alert(msg: str) -> None:
    _send_raw(f"⚠️ <b>Bot Error</b>\n<code>{esc(msg[:400])}</code>")
