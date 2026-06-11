"""
MODULE: scanner.py — Optimised parallel scanner

SPEED OPTIMISATIONS:
====================
1. PARALLEL API CALLS per coin (ThreadPoolExecutor)
   All independent calls run simultaneously:
   - OHLCV daily + hourly in parallel
   - OI + funding + L/S + liquidations + order book in parallel
   - Result: 10+ sequential calls → ~3 parallel batches
   Time saved: ~8s → ~2s per coin

2. RATE_LIMIT_DELAY reduced 0.4s → 0.1s
   CoinGecko already batched. Other APIs handle the load fine.

3. OHLCV history reduced: 90d/120h → 60d/72h
   Still enough for all TA (BB needs 20, ATR 14, patterns 40).
   Saves ~0.1s per coin on data transfer.

4. SOCIAL/NEWS only for promising coins
   Coins with pre_score < SOCIAL_MIN_PRESCORE skip community/news calls.
   These slow calls only run for coins that might actually alert.

5. TWO-TIER ROTATION (from previous version, kept)
   Priority queue (always scanned) + rotation (round-robin coverage).
   Guarantees every eligible coin gets scanned regardless of pool size.

RESULT: ~12s per coin → ~2.5s per coin
        90 coins × 2.5s = ~4 minutes per scan (was 18 minutes)
"""

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime
from modules.logger import get_logger
import config

log = get_logger("scanner")

ROTATION_STATE_PATH = "data_ls/rotation_state.json"


def _pre_score(ticker: dict, mc: float = None) -> float:
    """Fast pre-score from ticker data only — zero extra API calls."""
    score = 0.0
    vol   = ticker.get("volume_usdt", 0) or 0
    chg   = ticker.get("price_change_pct", 0) or 0

    if vol > 500_000_000:   score += 3
    elif vol > 100_000_000: score += 2
    elif vol > 50_000_000:  score += 1

    if -5 < chg < 5:     score += 3
    elif -15 < chg < -5: score += 1
    elif chg > 30:       score -= 3
    elif chg > 15:       score -= 1
    elif chg < -25:      score -= 2

    if mc:
        if mc < 20_000_000:    score += 4
        elif mc < 50_000_000:  score += 3
        elif mc < 100_000_000: score += 2
        elif mc < 300_000_000: score += 1

    return score


def _load_rotation_state() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(ROTATION_STATE_PATH):
        try:
            with open(ROTATION_STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"position": 0, "queue": [], "scan_count": 0}


def _save_rotation_state(state: dict) -> None:
    os.makedirs("data", exist_ok=True)
    try:
        with open(ROTATION_STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log.warning(f"Failed to save rotation state: {e}")


def _fetch_coin_data(sym: str, ticker: dict, mc_data: dict,
                     bf, af, ta, sf, nf,
                     btc_ctx: dict, fear_greed: dict,
                     prescore: float) -> dict:
    """
    Fetch ALL data for one coin with internal parallelism.
    Independent API calls run concurrently using threads.
    Returns merged data dict ready for scoring.
    """
    data = {}
    data.update(ticker)
    data.update(mc_data)

    # ── BATCH 1: OHLCV (daily + hourly in parallel) ──────────────
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_daily = ex.submit(bf.get_klines, sym, "1d", 60)   # 60 days (was 90)
        f_1h    = ex.submit(bf.get_klines, sym, "1h", 72)   # 72 hours (was 120)
        df_daily = f_daily.result()
        df_1h    = f_1h.result()

    if df_daily is None or df_daily.empty:
        return {}  # signal to skip this coin

    # ── BATCH 2: All futures data in parallel ─────────────────────
    # OI + funding + L/S + liquidations + order book all at once
    with ThreadPoolExecutor(max_workers=6) as ex:
        f_oi    = ex.submit(af.get_aggregated_oi,           sym)
        f_fund  = ex.submit(af.get_aggregated_funding,      sym)
        f_ls    = ex.submit(af.get_aggregated_ls_ratio,     sym)
        f_liq   = ex.submit(af.get_aggregated_liquidations, sym)
        f_ob    = ex.submit(af.get_aggregated_order_book,   sym)
        f_basis = ex.submit(bf.get_basis, sym, ticker.get("price", 0))

        data.update(f_oi.result()    or {})
        data.update(f_fund.result()  or {})
        data.update(f_ls.result()    or {})
        data.update(f_liq.result()   or {})
        data.update(f_ob.result()    or {})
        data.update(f_basis.result() or {})

    # ── Technical Analysis (CPU only, fast) ───────────────────────
    data.update(ta.run_all_ta(
        df_daily, df_1h,
        oi_usd=data.get("oi_usd"),
        market_cap_usd=data.get("market_cap_usd")
    ))

    # ── BATCH 3: Social/news — only for promising coins ───────────
    # Skip slow social calls for coins with very low pre-score
    # They're unlikely to alert anyway
    if prescore >= config.SOCIAL_MIN_PRESCORE:
        with ThreadPoolExecutor(max_workers=3) as ex:
            if config.ENABLE_LUNARCRUSH:
                f_social = ex.submit(sf.get_lunarcrush_data, sym)
            else:
                f_social = ex.submit(sf.get_coingecko_community, sym)

            f_news = ex.submit(nf.get_all_news_and_onchain, sym, data)

            if config.ENABLE_TELEGRAM_ACTIVITY:
                f_tg = ex.submit(nf.get_telegram_activity, sym)
            else:
                f_tg = None

            data.update(f_social.result() or {})
            data.update(f_news.result()   or {})
            if f_tg:
                data.update(f_tg.result() or {})
    else:
        # Still need default social values so scorer doesn't crash
        data.update({"social_spike": False, "news_catalyst": False,
                     "news_negative": False, "news_headlines": []})

    # Optional slow sources
    if config.ENABLE_GOOGLE_TRENDS and prescore >= config.SOCIAL_MIN_PRESCORE:
        data.update(sf.get_google_trends(sym))

    if config.ENABLE_COINMARKETCAL:
        data.update(sf.get_token_unlock_risk(sym))

    if config.ENABLE_GLASSNODE:
        data.update(sf.get_glassnode_data(sym))

    if config.ENABLE_CRYPTOQUANT:
        data.update(sf.get_cryptoquant_flow(sym))

    # Shared signals
    data.update(btc_ctx)
    data.update(fear_greed)

    return data


def run_scan() -> list:
    from modules import (
        binance_fetcher    as bf,
        aggregated_fetcher as af,
        technical_analysis as ta,
        sentiment_fetcher  as sf,
        news_fetcher       as nf,
        btc_market         as btc,
        scorer,
    )

    scan_start = time.time()
    log.info("Scan starting — parallel mode")

    # ── Shared signals (once) ──────────────────────────────────────
    btc_ctx    = btc.get_btc_context() if config.ENABLE_BTC_FILTER else {}
    fear_greed = sf.get_fear_greed()
    log.info(
        f"F&G: {fear_greed.get('fear_greed_value')} | "
        f"BTC: {btc_ctx.get('btc_trend','?')} "
        f"({btc_ctx.get('btc_change_4h','?')}% 4h)"
    )

    # ── All tickers — 1 call ──────────────────────────────────────
    tickers = bf.get_24h_tickers()
    if not tickers:
        log.error("Failed to fetch tickers")
        return []

    # ── Volume filter ─────────────────────────────────────────────
    vol_filtered = {
        sym: t for sym, t in tickers.items()
        if (t.get("volume_usdt") or 0) >= config.MIN_VOLUME_USDT
    }

    # ── CoinGecko batch (1-3 calls for ALL coins) ─────────────────
    log.info(f"Batch fetching market caps for {len(vol_filtered)} coins...")
    sf.prefetch_coingecko_batch(list(vol_filtered.keys()))

    # ── Market cap filter ─────────────────────────────────────────
    eligible = []
    for sym, ticker in vol_filtered.items():
        mc_data = sf.get_market_data(sym)
        mc      = mc_data.get("market_cap_usd")
        if mc is None or mc <= config.MAX_MARKET_CAP_USD:
            eligible.append((sym, ticker, mc_data, mc))

    log.info(
        f"Eligible: {len(eligible)} coins "
        f"(from {len(tickers)} total, "
        f"{len(vol_filtered)} passed volume filter)"
    )

    if not eligible:
        return []

    # ── Pre-score all eligible coins ──────────────────────────────
    prescored = []
    for sym, ticker, mc_data, mc in eligible:
        ps = _pre_score(ticker, mc)
        prescored.append((ps, sym, ticker, mc_data))
    prescored.sort(key=lambda x: x[0], reverse=True)

    total_eligible  = len(prescored)
    priority_limit  = min(config.PRIORITY_SCAN_LIMIT, total_eligible)
    rotation_batch  = min(config.ROTATION_BATCH_SIZE, total_eligible)

    # ── Tier 1: Priority queue ────────────────────────────────────
    priority_coins = prescored[:priority_limit]
    priority_syms  = {sym for _, sym, _, _ in priority_coins}

    # ── Tier 2: Rotation queue ────────────────────────────────────
    rotation_pool = [
        (ps, sym, t, mc_d)
        for ps, sym, t, mc_d in prescored
        if sym not in priority_syms
    ]

    state = _load_rotation_state()
    state["scan_count"] = state.get("scan_count", 0) + 1

    current_syms = [sym for _, sym, _, _ in rotation_pool]
    saved_queue  = state.get("queue", [])
    overlap      = len(set(saved_queue) & set(current_syms))

    if not saved_queue or overlap < len(current_syms) * 0.8:
        import random
        shuffled = current_syms.copy()
        random.shuffle(shuffled)
        state["queue"]    = shuffled
        state["position"] = 0
        log.info(f"Rotation queue rebuilt: {len(shuffled)} coins")
    else:
        new_syms = [s for s in current_syms if s not in saved_queue]
        if new_syms:
            state["queue"] = new_syms + [s for s in state["queue"] if s in current_syms]

    pos   = state.get("position", 0) % max(len(state["queue"]), 1)
    queue = state["queue"]
    n     = len(queue)

    rotation_syms_batch = []
    if queue and rotation_batch > 0:
        end = pos + rotation_batch
        if end <= n:
            rotation_syms_batch = queue[pos:end]
            state["position"] = end % n
        else:
            rotation_syms_batch = queue[pos:] + queue[:end - n]
            state["position"] = end - n

    sym_to_data    = {sym: (ps, sym, t, mc_d) for ps, sym, t, mc_d in prescored}
    rotation_coins = [sym_to_data[sym] for sym in rotation_syms_batch if sym in sym_to_data]
    _save_rotation_state(state)

    to_scan = priority_coins + rotation_coins

    rotation_period = max(1, (len(rotation_pool) + rotation_batch - 1) // rotation_batch)
    eta_hours = rotation_period * config.SCAN_INTERVAL_MINUTES / 60

    log.info(
        f"Scanning {len(to_scan)} coins: "
        f"{len(priority_coins)} priority + {len(rotation_coins)} rotation | "
        f"Full coverage every {rotation_period} scans (~{eta_hours:.1f}h)"
    )

    # ── PARALLEL COIN SCAN ────────────────────────────────────────
    # Process coins in parallel batches using COIN_PARALLEL_WORKERS threads
    # Each coin's internal calls are also parallelised inside _fetch_coin_data
    results     = []
    scan_errors = 0
    workers     = min(config.COIN_PARALLEL_WORKERS, len(to_scan))

    log.info(f"Using {workers} parallel workers")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_sym = {}
        for prescore, sym, ticker, mc_data in to_scan:
            tier = "P" if sym in priority_syms else "R"
            future = executor.submit(
                _fetch_coin_data,
                sym, ticker, mc_data,
                bf, af, ta, sf, nf,
                btc_ctx, fear_greed, prescore
            )
            future_to_sym[future] = (sym, prescore, tier)

        completed = 0
        for future in as_completed(future_to_sym):
            sym, prescore, tier = future_to_sym[future]
            completed += 1
            try:
                data = future.result()
                if not data:
                    log.warning(f"[{completed}/{len(to_scan)}][{tier}] {sym} — no data")
                    continue

                score_result = scorer.score_coin(data)
                data.update({
                    "symbol":    sym,
                    "pre_score": prescore,
                    "scan_tier": tier,
                    **score_result
                })
                results.append(data)
                log.info(
                    f"[{completed}/{len(to_scan)}][{tier}] {sym} "
                    f"score={data.get('score')}/{data.get('max_score')}"
                )
            except Exception as e:
                scan_errors += 1
                log.error(f"[{tier}] {sym} error: {e}", exc_info=False)

    elapsed = time.time() - scan_start
    mins    = int(elapsed // 60)
    secs    = int(elapsed % 60)
    per_c   = elapsed / len(to_scan) if to_scan else 0

    log.info(
        f"Scan done in {mins}m {secs}s "
        f"({per_c:.1f}s/coin avg) — "
        f"{len(results)} results, {scan_errors} errors"
    )

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results
