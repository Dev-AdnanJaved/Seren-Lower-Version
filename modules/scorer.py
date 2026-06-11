"""
MODULE: scorer.py
COMPLETE scoring engine — 28 signals, 5 penalty types.
All None-safe formatting throughout.
"""

from modules.logger import get_logger
import config

log = get_logger("scorer")


def _safe(val, fmt=".2f", default="N/A"):
    """Safely format a value that might be None."""
    if val is None:
        return default
    try:
        return f"{val:{fmt}}"
    except Exception:
        return str(val)


def score_coin(data: dict) -> dict:
    weights  = config.SCORE_WEIGHTS
    score    = 0
    signals  = {}
    reasons  = []
    penalties = []

    def add(key: str, triggered: bool, label: str, default_w: int = 1):
        nonlocal score
        w   = weights.get(key, default_w)
        pts = w if triggered else 0
        signals[key] = (triggered, pts)
        if triggered:
            reasons.append(f"{label} (+{pts})")
        score += pts

    # ══ CORE FUTURES SIGNALS (2 pts) ════════════════════════
    vol_ratio = data.get("vol_ratio") or 0
    add("volume_spike", data.get("vol_spike", False),
        f"🔥 Volume spike {vol_ratio:.1f}x 7d avg", 2)

    oi_chg = data.get("oi_change_24h")
    add("oi_rising", data.get("oi_rising", False),
        f"📈 OI up {_safe(oi_chg,'+.1f')}% across all exchanges", 2)

    fr = data.get("funding_rate")
    add("negative_funding", data.get("negative_funding", False),
        f"💰 Funding {_safe(fr,'.4f')}% negative → squeeze setup", 2)

    ls = data.get("ls_ratio_global")
    add("short_heavy", data.get("short_heavy", False),
        f"🩳 L/S {_safe(ls,'.2f')} — shorts dominating", 2)

    add("cvd_divergence", data.get("cvd_divergence", False),
        "⚡ CVD rising while price flat → hidden accumulation", 2)

    pat = data.get("detected_pattern", "none")
    add("chart_pattern", (data.get("patterns_count") or 0) > 0,
        f"📐 Pattern: {pat}", 2)

    # ══ TECHNICAL (1 pt) ════════════════════════════════════
    bb_pct = data.get("bb_squeeze_pct") or 100
    add("bb_squeeze", data.get("bb_squeeze", False),
        f"🎯 BB squeeze (tighter than {100-bb_pct:.0f}% of history)", 1)

    atr_pct = data.get("atr_pct")
    add("low_atr", data.get("low_atr", False),
        f"😴 Low ATR {_safe(atr_pct,'.2f')}% — calm before storm", 1)

    days_sw = data.get("days_sideways") or 0
    add("higher_lows", data.get("higher_lows", False),
        f"📊 Higher lows + {days_sw}d sideways → accumulation", 1)

    pct_ath = data.get("pct_from_ath")
    add("far_from_ath", data.get("far_from_ath", False),
        f"📉 {abs(pct_ath):.0f}% below ATH → room to run" if pct_ath else "📉 Far from ATH", 1)

    # ══ MARKET STRUCTURE (1 pt) ═════════════════════════════
    mc = data.get("market_cap_usd")
    add("small_market_cap", data.get("small_market_cap", False),
        f"🪙 Small cap ${mc/1e6:.0f}M → easier to pump" if mc else "🪙 Small cap", 1)

    oi_mc = data.get("oi_mc_ratio")
    add("high_leverage", data.get("high_leverage", False),
        f"⚙️ OI/MC {_safe(oi_mc,'.2f')} — heavily leveraged → explosive", 1)

    basis = data.get("basis_pct")
    add("negative_basis", data.get("negative_basis", False),
        f"🔻 Basis {_safe(basis,'.3f')}% — futures below spot", 1)

    top_ls = data.get("ls_ratio_top")
    add("whales_short", data.get("whales_short", False),
        f"🐋 Top traders L/S {_safe(top_ls,'.2f')} — whales SHORT", 1)

    add("low_float", data.get("low_float", False),
        f"🎈 Low float {data.get('float_pct') or '?'}% circulating → supply squeeze", 1)

    # ══ SENTIMENT (1 pt) ════════════════════════════════════
    sdelta = data.get("social_delta_pct")
    add("social_spike", data.get("social_spike", False),
        f"🐦 Social volume +{_safe(sdelta,'.0f')}%", 1)

    gt = data.get("google_trend_score")
    add("google_trends", data.get("google_trend_spike", False),
        f"🔎 Google Trends spike (score {_safe(gt,'.0f')})", 1)

    fg = data.get("fear_greed_value")
    add("fear_greed_low", data.get("fear_greed_low", False),
        f"😱 Fear & Greed {_safe(fg,'.0f')} — market in fear", 1)

    # News headline — escape it since it comes from external news sources
    raw_headline = (data.get("news_headlines") or [""])[0][:50]
    safe_headline = str(raw_headline).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    add("news_catalyst", data.get("news_catalyst", False),
        f"📰 News: {safe_headline}", 1)

    tw = data.get("twitter_mention_spike") or data.get("social_spike", False)
    tw_r = data.get("twitter_spike_ratio")
    add("twitter_spike", tw,
        f"🐤 Tweet spike {_safe(tw_r,'.1f')}x", 1)

    # ══ ORDER BOOK (1 pt) ═══════════════════════════════════
    tbuy = data.get("taker_buy_pct")
    add("exchange_outflow", bool(tbuy and tbuy > 55),
        f"🏦 Taker buy {_safe(tbuy,'.1f')}% of volume", 1)

    wall_usd = data.get("ob_largest_wall_usdt") or 0
    wall_ex  = data.get("ob_largest_wall_exchange") or ""
    buy_wall = data.get("ob_large_buy_wall_agg") or data.get("large_buy_wall", False)
    add("buy_wall", buy_wall,
        f"🧱 Buy wall ${wall_usd/1e3:.0f}K on {wall_ex}" if wall_usd and wall_ex else "🧱 Large buy wall", 1)

    ob_imb = data.get("ob_imbalance_pct")
    ob_exs = data.get("ob_exchanges_count") or 1
    add("ob_imbalance", bool(ob_imb and ob_imb > 20),
        f"📚 OB {_safe(ob_imb,'+.1f')}% bid-heavy ({ob_exs} exchanges)", 1)

    spr = data.get("ob_cross_exchange_spread_pct")
    add("arb_signal", data.get("ob_arb_signal", False),
        f"⚡ Cross-exchange gap {_safe(spr,'.3f')}% — price move imminent", 1)

    # ══ ON-CHAIN / PAID (1 pt) ══════════════════════════════
    add("smart_money_buying", data.get("nansen_smart_money_buying", False),
        "🧠 Smart money buying (Nansen)", 1)

    whale_acc = (data.get("glassnode_accumulating") or
                 data.get("arkham_whales_buying") or
                 data.get("cq_accumulating") or False)
    add("whale_accumulating", whale_acc,
        "🐳 Whale accumulation detected (on-chain)", 1)

    add("liq_magnet_above", data.get("hyblock_liq_magnet", False),
        f"🧲 Liq cluster ${(data.get('hyblock_liq_cluster_above_usd') or 0)/1e6:.1f}M above price", 1)

    add("btc_sideways_bonus", data.get("btc_sideways") or data.get("btc_dom_falling", False),
        "✅ BTC sideways / dom falling → good for alts", 1)

    # ══ INFORMATIONAL (not scored) ══════════════════════════
    if data.get("daily_macd_cross"):
        reasons.append("⚡ Daily MACD bullish cross [info]")
    rsi_d = data.get("rsi_daily")
    if rsi_d and rsi_d < 35:
        reasons.append(f"📍 RSI oversold {rsi_d:.0f} [info]")
    p7  = data.get("price_change_7d")
    p30 = data.get("price_change_30d")
    if p7 is not None:
        reasons.append(f"📆 7d: {p7:+.1f}%" + (f" | 30d: {p30:+.1f}%" if p30 else ""))
    if (days_sw or 0) >= 5:
        reasons.append(f"🕰️ {days_sw}d in tight range")
    prl = data.get("pct_from_recent_low")
    if prl is not None:
        reasons.append(f"📏 {prl:.1f}% above 30d low")
    if data.get("spoof_proxy_flag"):
        raw_spoof = str(data.get("spoof_proxy_reason", "suspicious book"))
        safe_spoof = raw_spoof.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        reasons.append(f"⚠️ Spoof: {safe_spoof} [info]")
    tg_m = data.get("telegram_channel_user_count") or data.get("cc_telegram_members")
    if tg_m:
        reasons.append(f"💬 Telegram: {int(tg_m):,} members [info]")
    if data.get("news_listing"):
        reasons.append("🔔 EXCHANGE LISTING DETECTED [MAJOR CATALYST]")
    near_liq = data.get("hyblock_nearest_liq_above")
    if near_liq:
        reasons.append(f"🎯 Nearest liq cluster: ${near_liq:.4f} [info]")

    # ══ PENALTIES ═══════════════════════════════════════════
    if data.get("unlock_risk"):
        p = config.PENALTY_UNLOCK_RISK
        score = max(0, score - p)
        raw_ev = str(data.get("unlock_event") or "soon")[:40]
        safe_ev = raw_ev.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        penalties.append(f"❌ Token unlock ({safe_ev}): -{p}")

    if data.get("btc_crashing"):
        p = config.PENALTY_BTC_CRASH
        score = max(0, score - p)
        penalties.append(f"🔴 BTC crashing {_safe(data.get('btc_change_4h'),'+.1f')}%: -{p}")

    if data.get("news_negative"):
        p = config.PENALTY_NEGATIVE_NEWS
        score = max(0, score - p)
        penalties.append(f"☠️ Negative news: -{p}")

    p7v = data.get("price_change_7d") or 0
    if p7v > 50:
        p = config.PENALTY_ALREADY_PUMPED
        score = max(0, score - p)
        penalties.append(f"📛 Already pumped {p7v:.0f}% in 7d: -{p}")

    fr_val = data.get("funding_rate") or 0
    if fr_val > 0.05:
        p = config.PENALTY_HIGH_FUNDING
        score = max(0, score - p)
        penalties.append(f"⚠️ Overheated funding: -{p}")

    for pen in penalties:
        reasons.append(pen)

    max_score = sum(weights.values())
    strength  = "🔴 WEAK"
    if score >= max_score * 0.75:
        strength = "🟢 VERY STRONG"
    elif score >= max_score * 0.55:
        strength = "🟡 STRONG"
    elif score >= max_score * 0.35:
        strength = "🟠 MODERATE"

    # ══ MOMENTUM GATE ════════════════════════════════════════
    # A signal is only "real" if at least one momentum signal fired.
    # Pure structural signals (fear&greed + BTC sideways + pattern) = noise.
    # Momentum signals = something is actually moving/accumulating NOW.
    MOMENTUM_SIGNALS = [
        "volume_spike",     # someone is actually buying
        "oi_rising",        # new money entering futures
        "cvd_divergence",   # hidden accumulation
        "short_heavy",      # squeeze fuel building
        "bb_squeeze",       # volatility compression (imminent move)
        "social_spike",     # community attention growing
        "news_catalyst",    # external catalyst
        "ob_imbalance",     # buyers dominating order books
        "buy_wall",         # large buyer defending price
    ]
    has_momentum = any(
        signals.get(s, (False, 0))[0]
        for s in MOMENTUM_SIGNALS
    )

    # Structural-only score: points from signals that fire on ALL coins
    # when market is in fear (fear&greed, btc_sideways, patterns)
    STRUCTURAL_ONLY = {"fear_greed_low", "btc_sideways_bonus",
                       "far_from_ath", "negative_basis", "chart_pattern"}
    structural_score = sum(
        signals.get(s, (False, 0))[1]
        for s in STRUCTURAL_ONLY
        if signals.get(s, (False, 0))[0]
    )
    momentum_score = score - structural_score

    return {
        "score":          score,
        "max_score":      max_score,
        "strength":       strength,
        "pct_score":      round(score / max_score * 100, 1),
        "signals":        signals,
        "reasons":        reasons,
        "penalties":      penalties,
        "has_momentum":   has_momentum,       # True = at least one real signal
        "momentum_score": momentum_score,     # score minus structural noise
        "structural_score": structural_score, # points from universal signals
    }
