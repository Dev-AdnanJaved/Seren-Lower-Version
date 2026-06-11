"""
MODULE: diagnostics.py
Tests every data source, every API, every field.
Used by Telegram /check command and python main.py --diagnose

Tests:
  - Every API connection (pass/fail/latency)
  - Sample data fetch for one coin (BTC)
  - Every field populated or not
  - Exchange aggregation working
  - Config validation
"""

import time
import requests
import traceback
from datetime import datetime, timezone
from modules.logger import get_logger
import config

log = get_logger("diagnostics")


def _test_url(name: str, url: str, params: dict = {}, headers: dict = {},
              key_field: str = None) -> dict:
    """Test a single URL and return result dict."""
    start = time.time()
    try:
        r = requests.get(url, params=params, headers=headers, timeout=8)
        ms = int((time.time() - start) * 1000)
        if r.status_code == 200:
            data = r.json()
            has_data = bool(data)
            if key_field:
                # Check specific field exists
                if isinstance(data, list):
                    has_data = len(data) > 0
                elif isinstance(data, dict):
                    has_data = key_field in data or bool(data.get("data"))
            return {"name": name, "status": "✅ OK", "ms": ms, "error": None}
        else:
            return {"name": name, "status": f"❌ HTTP {r.status_code}",
                    "ms": ms, "error": r.text[:80]}
    except requests.Timeout:
        return {"name": name, "status": "⏱ TIMEOUT", "ms": 8000, "error": "Request timed out"}
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        return {"name": name, "status": "❌ ERROR", "ms": ms, "error": str(e)[:80]}


def run_full_diagnostic(test_symbol: str = "BTCUSDT") -> dict:
    """
    Runs complete diagnostic. Returns structured results dict.
    test_symbol: which coin to use for data fetch tests (default BTCUSDT)
    """
    results = {
        "timestamp":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "test_symbol":  test_symbol,
        "config":       {},
        "apis":         [],
        "data_fields":  {},
        "aggregation":  {},
        "warnings":     [],
        "errors":       [],
    }

    # ══ 1. CONFIG VALIDATION ══════════════════════════════
    cfg = results["config"]
    cfg["telegram_token_set"]  = bool(config.TELEGRAM_BOT_TOKEN and
                                      config.TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN_HERE")
    cfg["telegram_chat_set"]   = bool(config.TELEGRAM_CHAT_ID and
                                      config.TELEGRAM_CHAT_ID != "YOUR_CHAT_ID_HERE")
    cfg["coinglass_key_set"]   = bool(config.COINGLASS_API_KEY)
    cfg["coinalyze_key_set"]   = bool(config.COINALYZE_API_KEY)
    cfg["cryptopanic_key_set"] = bool(config.CRYPTOPANIC_API_KEY)
    cfg["lunarcrush_key_set"]  = bool(config.LUNARCRUSH_API_KEY and config.ENABLE_LUNARCRUSH)
    cfg["glassnode_key_set"]   = bool(config.GLASSNODE_API_KEY and config.ENABLE_GLASSNODE)
    cfg["cryptoquant_key_set"] = bool(config.CRYPTOQUANT_API_KEY and config.ENABLE_CRYPTOQUANT)
    cfg["nansen_key_set"]      = bool(config.NANSEN_API_KEY and config.ENABLE_NANSEN)
    cfg["hyblock_key_set"]     = bool(config.HYBLOCK_API_KEY and config.ENABLE_HYBLOCK)
    cfg["twitter_key_set"]     = bool(config.TWITTER_BEARER_TOKEN and config.ENABLE_TWITTER)
    cfg["scan_interval"]       = config.SCAN_INTERVAL_MINUTES
    cfg["top_n_coins"]         = config.TOP_N_COINS
    cfg["alert_min_score"]     = config.ALERT_MIN_SCORE
    cfg["max_score"]           = sum(config.SCORE_WEIGHTS.values())

    if not cfg["telegram_token_set"]:
        results["errors"].append("Telegram bot token not configured")
    if not cfg["telegram_chat_set"]:
        results["errors"].append("Telegram chat ID not configured")
    if not cfg["coinglass_key_set"]:
        results["warnings"].append("No CoinGlass key — using manual exchange aggregation")
    if not cfg["coinalyze_key_set"]:
        results["warnings"].append("No Coinalyze key — liquidation heatmap unavailable")
    if not cfg["cryptopanic_key_set"]:
        results["warnings"].append("No CryptoPanic key — news will hit rate limits")

    # ══ 2. API CONNECTION TESTS ═══════════════════════════
    coin = test_symbol.replace("USDT", "")
    apis = results["apis"]

    # Free APIs — no key
    apis.append(_test_url("Binance Futures (price)",
        "https://fapi.binance.com/fapi/v1/ticker/price",
        params={"symbol": test_symbol}))

    apis.append(_test_url("Binance Futures (OI)",
        "https://fapi.binance.com/fapi/v1/openInterest",
        params={"symbol": test_symbol}))

    apis.append(_test_url("Binance Futures (funding)",
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        params={"symbol": test_symbol}))

    apis.append(_test_url("Binance Futures (L/S ratio)",
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
        params={"symbol": test_symbol, "period": "1h", "limit": 1}))

    apis.append(_test_url("Binance Futures (OI history)",
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": test_symbol, "period": "1h", "limit": 2}))

    apis.append(_test_url("Binance Spot (price)",
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": test_symbol}))

    apis.append(_test_url("Bybit (OI)",
        "https://api.bybit.com/v5/market/open-interest",
        params={"category": "linear", "symbol": test_symbol,
                "intervalTime": "1h", "limit": 1}))

    apis.append(_test_url("Bybit (funding)",
        "https://api.bybit.com/v5/market/tickers",
        params={"category": "linear", "symbol": test_symbol}))

    apis.append(_test_url("Bybit (L/S ratio)",
        "https://api.bybit.com/v5/market/account-ratio",
        params={"category": "linear", "symbol": test_symbol,
                "period": "1h", "limit": 1}))

    apis.append(_test_url("OKX (OI)",
        "https://www.okx.com/api/v5/public/open-interest",
        params={"instType": "SWAP", "instId": f"{coin}-USDT-SWAP"}))

    apis.append(_test_url("OKX (funding)",
        "https://www.okx.com/api/v5/public/funding-rate",
        params={"instId": f"{coin}-USDT-SWAP"}))

    apis.append(_test_url("Bitget (OI)",
        "https://api.bitget.com/api/v2/mix/market/open-interest",
        params={"symbol": test_symbol, "productType": "USDT-FUTURES"}))

    apis.append(_test_url("Bitget (funding)",
        "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
        params={"symbol": test_symbol, "productType": "USDT-FUTURES"}))

    apis.append(_test_url("CoinGecko (market data)",
        "https://api.coingecko.com/api/v3/coins/markets",
        params={"vs_currency": "usd", "ids": "bitcoin"}))

    apis.append(_test_url("Alternative.me (Fear & Greed)",
        "https://api.alternative.me/fng/?limit=1"))

    apis.append(_test_url("Binance (order book)",
        "https://fapi.binance.com/fapi/v1/depth",
        params={"symbol": test_symbol, "limit": 5}))

    apis.append(_test_url("Bybit (order book)",
        "https://api.bybit.com/v5/market/orderbook",
        params={"category": "linear", "symbol": test_symbol, "limit": 5}))

    apis.append(_test_url("OKX (order book)",
        "https://www.okx.com/api/v5/market/books",
        params={"instId": f"{coin}-USDT-SWAP", "sz": 5}))

    apis.append(_test_url("CryptoPanic (news)",
        "https://cryptopanic.com/api/v1/posts/",
        params={"currencies": coin, "public": "true",
                **({"auth_token": config.CRYPTOPANIC_API_KEY}
                   if config.CRYPTOPANIC_API_KEY else {})}))

    # With-key APIs
    if config.COINGLASS_API_KEY:
        apis.append(_test_url("CoinGlass (OI)",
            "https://open-api.coinglass.com/public/v2/indicator/open_interest",
            params={"symbol": coin},
            headers={"coinglassSecret": config.COINGLASS_API_KEY}))

    if config.COINALYZE_API_KEY:
        apis.append(_test_url("Coinalyze (OI)",
            "https://api.coinalyze.net/v1/open-interest",
            params={"symbols": test_symbol + "_PERP.A"},
            headers={"api_key": config.COINALYZE_API_KEY}))

    if config.LUNARCRUSH_API_KEY and config.ENABLE_LUNARCRUSH:
        apis.append(_test_url("LunarCrush (social)",
            f"https://lunarcrush.com/api4/public/coins/{coin.lower()}/v1",
            headers={"Authorization": f"Bearer {config.LUNARCRUSH_API_KEY}"}))

    if config.GLASSNODE_API_KEY and config.ENABLE_GLASSNODE:
        apis.append(_test_url("Glassnode (addresses)",
            "https://api.glassnode.com/v1/metrics/addresses/active_count",
            params={"a": coin.lower(), "api_key": config.GLASSNODE_API_KEY,
                    "i": "24h", "f": "JSON"}))

    if config.CRYPTOQUANT_API_KEY and config.ENABLE_CRYPTOQUANT:
        apis.append(_test_url("CryptoQuant (flows)",
            f"https://api.cryptoquant.com/v1/{coin.lower()}/exchange-flows/inflow",
            headers={"Authorization": f"Bearer {config.CRYPTOQUANT_API_KEY}"},
            params={"window": "day", "limit": 1}))

    if config.HYBLOCK_API_KEY and config.ENABLE_HYBLOCK:
        apis.append(_test_url("Hyblock (liquidations)",
            "https://api.hyblock.io/v1/liquidation-heatmap",
            headers={"Authorization": f"Bearer {config.HYBLOCK_API_KEY}"},
            params={"symbol": test_symbol, "timeframe": "4h"}))

    if config.TWITTER_BEARER_TOKEN and config.ENABLE_TWITTER:
        apis.append(_test_url("Twitter API (mentions)",
            "https://api.twitter.com/2/tweets/counts/recent",
            headers={"Authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}"},
            params={"query": f"#{coin} lang:en -is:retweet", "granularity": "hour"}))

    # ══ 3. FULL DATA FETCH TEST ════════════════════════════
    try:
        from modules import (
            binance_fetcher    as bf,
            aggregated_fetcher as af,
            technical_analysis as ta,
            sentiment_fetcher  as sf,
            btc_market         as btc,
        )

        data = {}

        # Ticker
        tickers = bf.get_24h_tickers()
        ticker  = tickers.get(test_symbol, {})
        data.update(ticker)

        # OHLCV
        df_daily = bf.get_klines(test_symbol, "1d", 30)
        df_1h    = bf.get_klines(test_symbol, "1h", 48)

        # Aggregated futures
        agg = af.get_all_aggregated(test_symbol)
        data.update(agg)

        # Basis
        data.update(bf.get_basis(test_symbol, data.get("price", 0)))

        # Market data
        data.update(sf.get_market_data(test_symbol))

        # TA
        if df_daily is not None:
            data.update(ta.run_all_ta(df_daily, df_1h,
                                      oi_usd=data.get("oi_usd"),
                                      market_cap_usd=data.get("market_cap_usd")))

        # BTC context
        data.update(btc.get_btc_context())

        # Fear & Greed
        data.update(sf.get_fear_greed())

        # Now check which fields are populated
        fields = results["data_fields"]

        field_groups = {
            "Price & Volume": [
                "price", "price_change_pct", "volume_usdt", "vol_ratio",
                "price_change_7d", "price_change_30d", "high_24h", "low_24h"
            ],
            "Open Interest": [
                "oi_usd", "oi_change_24h", "oi_rising", "oi_mc_ratio",
                "high_leverage", "oi_source"
            ],
            "Funding Rate": [
                "funding_rate", "negative_funding", "funding_source"
            ],
            "Long/Short Ratio": [
                "ls_ratio_global", "ls_ratio_top", "short_heavy",
                "whales_short", "ls_source"
            ],
            "Liquidations": [
                "liq_long_24h_usd", "liq_short_24h_usd", "taker_buy_pct",
                "cvd_proxy", "liq_source"
            ],
            "Order Book (Aggregated)": [
                "ob_total_bid_usdt", "ob_total_ask_usdt", "ob_bid_ask_ratio_agg",
                "ob_imbalance_pct", "ob_large_buy_wall_agg", "ob_exchanges_count",
                "ob_cross_exchange_spread_pct", "ob_source"
            ],
            "Basis": [
                "spot_price", "basis_pct", "negative_basis"
            ],
            "Technical Analysis": [
                "bb_squeeze", "bb_width", "atr_pct", "low_atr",
                "rsi_daily", "rsi_1h", "daily_macd_cross",
                "cvd_divergence", "cvd_rising", "higher_lows",
                "days_sideways", "pct_from_ath", "pct_from_recent_low"
            ],
            "Chart Patterns": [
                "patterns_count", "detected_pattern",
                "pattern_falling_wedge", "pattern_bull_flag",
                "pattern_coiling_resistance"
            ],
            "Market Cap & Supply": [
                "market_cap_usd", "circulating_supply", "low_float",
                "float_pct", "small_market_cap"
            ],
            "BTC Context": [
                "btc_price", "btc_change_4h", "btc_trend",
                "btc_crashing", "btc_sideways", "btc_dominance"
            ],
            "Fear & Greed": [
                "fear_greed_value", "fear_greed_label", "fear_greed_low"
            ],
        }

        for group, field_list in field_groups.items():
            populated = []
            missing   = []
            for f in field_list:
                val = data.get(f)
                if val is not None and val != "" and not (hasattr(val, "__len__") and len(val) == 0 if not isinstance(val, (str, dict)) else val == ""):
                    populated.append(f"{f}={_fmt(val)}")
                else:
                    missing.append(f)
            fields[group] = {
                "populated": len(populated),
                "total":     len(field_list),
                "pct":       int(len(populated) / len(field_list) * 100),
                "values":    populated,
                "missing":   missing,
            }

        # Aggregation quality
        agg_info = results["aggregation"]
        agg_info["oi_source"]        = data.get("oi_source", "none")
        agg_info["funding_source"]   = data.get("funding_source", "none")
        agg_info["ls_source"]        = data.get("ls_source", "none")
        agg_info["liq_source"]       = data.get("liq_source", "none")
        agg_info["ob_source"]        = data.get("ob_source", "none")
        agg_info["ob_exchanges"]     = data.get("ob_exchanges", [])
        agg_info["oi_exchanges"]     = data.get("oi_exchanges", {})
        agg_info["funding_per_ex"]   = data.get("funding_per_exchange", {})
        agg_info["ls_per_ex"]        = data.get("ls_per_exchange", {})
        agg_info["ob_exchange_depth"] = data.get("ob_exchange_depth", {})

    except Exception as e:
        results["errors"].append(f"Data fetch failed: {str(e)}")
        log.error(f"Diagnostic data fetch error: {traceback.format_exc()}")

    # Summary
    ok_apis    = sum(1 for a in results["apis"] if "✅" in a["status"])
    total_apis = len(results["apis"])
    results["summary"] = {
        "apis_ok":      ok_apis,
        "apis_total":   total_apis,
        "apis_pct":     int(ok_apis / total_apis * 100) if total_apis else 0,
        "warnings":     len(results["warnings"]),
        "errors":       len(results["errors"]),
    }

    return results


def _fmt(val) -> str:
    """Format a value for display."""
    if isinstance(val, float):
        return f"{val:.4g}"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, (list, dict)):
        return f"[{len(val)} items]"
    return str(val)[:30]


def format_diagnostic_for_telegram(results: dict) -> list:
    """
    Formats diagnostic results into multiple Telegram messages
    (split because Telegram has 4096 char limit per message).
    Returns list of message strings.
    """
    msgs = []

    # ── Message 1: Overview + Config ──
    cfg     = results["config"]
    summary = results["summary"]
    ts      = results["timestamp"]
    sym     = results["test_symbol"]

    def tick(val): return "✅" if val else "❌"

    msg1 = f"""🔬 <b>DIAGNOSTIC REPORT</b>
{ts}  |  Test coin: #{sym.replace('USDT','')}

<b>⚙️ Config Status</b>
├ Telegram token:    {tick(cfg['telegram_token_set'])}
├ Telegram chat ID:  {tick(cfg['telegram_chat_set'])}
├ CoinGlass key:     {tick(cfg['coinglass_key_set'])}  {'(aggregated OI)' if cfg['coinglass_key_set'] else '(manual fallback)'}
├ Coinalyze key:     {tick(cfg['coinalyze_key_set'])}
├ CryptoPanic key:   {tick(cfg['cryptopanic_key_set'])}
├ LunarCrush key:    {tick(cfg['lunarcrush_key_set'])}
├ Glassnode key:     {tick(cfg['glassnode_key_set'])}
├ CryptoQuant key:   {tick(cfg['cryptoquant_key_set'])}
├ Nansen key:        {tick(cfg['nansen_key_set'])}
├ Hyblock key:       {tick(cfg['hyblock_key_set'])}
└ Twitter key:       {tick(cfg['twitter_key_set'])}

<b>📊 Overall</b>
├ APIs working:  {summary['apis_ok']}/{summary['apis_total']} ({summary['apis_pct']}%)
├ Warnings:      {summary['warnings']}
└ Errors:        {summary['errors']}

Scan: every {cfg['scan_interval']}min  |  Top {cfg['top_n_coins']} coins  |  Alert ≥{cfg['alert_min_score']}/{cfg['max_score']}"""

    if results["errors"]:
        msg1 += "\n\n<b>❌ Errors:</b>"
        for e in results["errors"]:
            msg1 += f"\n  • {e}"
    if results["warnings"]:
        msg1 += "\n\n<b>⚠️ Warnings:</b>"
        for w in results["warnings"]:
            msg1 += f"\n  • {w}"

    msgs.append(msg1)

    # ── Message 2: API Status ──
    apis = results["apis"]
    msg2 = "<b>🌐 API Connection Tests</b>\n\n"
    for a in apis:
        status = a["status"]
        ms     = a["ms"]
        name   = a["name"]
        err    = f" — {a['error'][:40]}" if a.get("error") else ""
        msg2  += f"{status} <b>{name}</b> ({ms}ms){err}\n"
    msgs.append(msg2)

    # ── Message 3: Data Field Coverage ──
    fields = results["data_fields"]
    msg3   = "<b>📋 Data Field Coverage</b>\n\n"
    for group, info in fields.items():
        bar = "█" * (info["pct"] // 10) + "░" * (10 - info["pct"] // 10)
        msg3 += f"<b>{group}</b> [{bar}] {info['pct']}%\n"
        if info["missing"]:
            msg3 += f"  ❌ Missing: {', '.join(info['missing'])}\n"
        msg3 += "\n"
    msgs.append(msg3)

    # ── Message 4: Aggregation Sources ──
    agg  = results["aggregation"]
    msg4 = "<b>🔗 Exchange Aggregation Status</b>\n\n"

    def src_icon(src):
        if "coinglass" in str(src): return "🥇"
        if "coinalyze" in str(src): return "🥈"
        if "manual" in str(src):    return "🥉"
        if "binance" in str(src):   return "⚠️"
        return "❓"

    msg4 += f"OI Source:       {src_icon(agg.get('oi_source'))} {agg.get('oi_source','none')}\n"
    msg4 += f"Funding Source:  {src_icon(agg.get('funding_source'))} {agg.get('funding_source','none')}\n"
    msg4 += f"L/S Source:      {src_icon(agg.get('ls_source'))} {agg.get('ls_source','none')}\n"
    msg4 += f"Liq Source:      {src_icon(agg.get('liq_source'))} {agg.get('liq_source','none')}\n"
    msg4 += f"OB Source:       {src_icon(agg.get('ob_source'))} {agg.get('ob_source','none')}\n\n"

    ob_exs = agg.get("ob_exchanges", [])
    msg4 += f"<b>Order Book exchanges:</b> {', '.join(ob_exs) if ob_exs else 'none'}\n\n"

    ob_depth = agg.get("ob_exchange_depth", {})
    if ob_depth:
        msg4 += "<b>Order Book depth per exchange:</b>\n"
        for ex, d in ob_depth.items():
            bid = d.get("bid_usdt", 0)
            ask = d.get("ask_usdt", 0)
            ratio = d.get("ratio", 0)
            msg4 += f"  {ex}: bid ${bid/1e3:.0f}K / ask ${ask/1e3:.0f}K (ratio {ratio})\n"

    fund_ex = agg.get("funding_per_ex", {})
    if fund_ex:
        msg4 += "\n<b>Funding rate per exchange:</b>\n"
        for ex, rate in fund_ex.items():
            icon = "🔴" if rate < 0 else "🟢"
            msg4 += f"  {ex}: {icon} {rate:.5f}%\n"

    ls_ex = agg.get("ls_per_ex", {})
    if ls_ex:
        msg4 += "\n<b>L/S ratio per exchange:</b>\n"
        for ex, ratio in ls_ex.items():
            icon = "🩳" if ratio < 1 else "📈"
            msg4 += f"  {ex}: {icon} {ratio:.3f}\n"

    msgs.append(msg4)

    return msgs
