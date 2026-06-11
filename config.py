"""
=============================================================
  SEREN-LS — Less Strict Scanner
  Parallel bot running alongside Seren (strict).
  
  PURPOSE: Catch more signals daily (1-5/day target)
  for comparison with Seren strict bot.
  
  KEY DIFFERENCES FROM SEREN (STRICT):
    ALERT_MIN_SCORE      6  (was 10) — lower bar
    REQUIRE_MOMENTUM     False       — no momentum gate
    ALERT_COOLDOWN_HOURS 24          — daily re-alerts allowed
    PAPER_TRADE_SCORE    10          — track more signals
    MOMENTUM_BYPASS_SCORE 12         — lower bypass threshold
  
  EVERYTHING ELSE IS IDENTICAL:
    Same APIs, same signal logic, same 28 signals,
    same pattern detection, same scoring weights,
    same data collection, same signal tracker.
    
  Uses a SEPARATE Telegram bot so alerts don't mix.
  Uses SEPARATE data/ folder (data_ls/) to avoid conflicts.
=============================================================
"""

# ═════════════════════════════════════════════
#  TELEGRAM — USE A DIFFERENT BOT TOKEN
#  Create a new bot via @BotFather for Seren-LS
# ═════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = "YOUR_LS_BOT_TOKEN_HERE"   # DIFFERENT from Seren strict
TELEGRAM_CHAT_ID   = "YOUR_CHAT_ID_HERE"         # same chat ID is fine

# ═════════════════════════════════════════════
#  FREE SOURCES (same as strict bot)
# ═════════════════════════════════════════════
BINANCE_API_KEY    = ""
BINANCE_API_SECRET = ""
ENABLE_BYBIT_FETCH  = True
ENABLE_OKX_FETCH    = True
ENABLE_BITGET_FETCH = True
ENABLE_FEAR_GREED   = True
ENABLE_COINGECKO_NEWS = True
ENABLE_GOOGLE_TRENDS = False
ENABLE_BTC_FILTER         = True
BTC_CRASH_THRESHOLD_PCT   = -3.0
BTC_CRASH_SCORE_PENALTY   = 4
BTC_SIDEWAYS_BONUS        = 1
ENABLE_TELEGRAM_ACTIVITY  = True

# ═════════════════════════════════════════════
#  PAID SOURCES (same as strict bot)
# ═════════════════════════════════════════════
COINGLASS_API_KEY    = ""
COINALYZE_API_KEY    = ""
ENABLE_COINGLASS     = True
ENABLE_COINALYZE     = True
ENABLE_CRYPTOPANIC   = False
CRYPTOPANIC_API_KEY  = ""
LUNARCRUSH_API_KEY   = ""
ENABLE_LUNARCRUSH    = True
COINMARKETCAL_API_KEY = ""
ENABLE_COINMARKETCAL  = False
GLASSNODE_API_KEY    = ""
ENABLE_GLASSNODE     = False
CRYPTOQUANT_API_KEY  = ""
ENABLE_CRYPTOQUANT   = False
NANSEN_API_KEY       = ""
ENABLE_NANSEN        = False
ARKHAM_API_KEY       = ""
ENABLE_ARKHAM        = False
TWITTER_BEARER_TOKEN = ""
ENABLE_TWITTER       = False
SANTIMENT_API_KEY    = ""
ENABLE_SANTIMENT     = False
HYBLOCK_API_KEY      = ""
ENABLE_HYBLOCK       = False
NEWS_LOOKBACK_HOURS  = 24

# ═════════════════════════════════════════════
#  SCANNER SETTINGS (same as strict bot)
# ═════════════════════════════════════════════
SCAN_INTERVAL_MINUTES  = 15
SCAN_QUOTE_ASSET       = "USDT"
MIN_VOLUME_USDT        = 500_000
MAX_MARKET_CAP_USD     = 500_000_000
VOLUME_SPIKE_THRESHOLD = 2.5
OI_CHANGE_THRESHOLD    = 8.0
FUNDING_RATE_MAX       = 0.0
LONG_SHORT_RATIO_MAX   = 1.0
PRIORITY_SCAN_LIMIT    = 60
ROTATION_BATCH_SIZE    = 150
COIN_PARALLEL_WORKERS  = 5
SOCIAL_MIN_PRESCORE    = 2
RATE_LIMIT_DELAY       = 0.1

# ═════════════════════════════════════════════
#  LS-SPECIFIC ALERT SETTINGS
#  These are the ONLY differences from strict bot
# ═════════════════════════════════════════════

# Lower score threshold — catches coins that strict bot misses
ALERT_MIN_SCORE        = 8     # strict=10, LS=8
                               # 7 points given free in bear market (fear+btc+pattern+funding)
                               # Score 8 requires at least one extra real signal on top

# Light momentum gate — less strict than main bot
# Bypass at 14 (vs strict's 20) — catches more borderline cases
REQUIRE_MOMENTUM_SIGNAL = True   # strict=True, LS=True (but easier bypass)
MOMENTUM_BYPASS_SCORE   = 14    # strict=20, LS=14

# 24h cooldown — same coin can alert daily
ALERT_COOLDOWN_HOURS   = 24    # strict=72, LS=24

# Track more signals in signal tracker
PAPER_TRADE_SCORE      = 10    # strict=13, LS=10
SIGNAL_MONITOR_DAYS    = 15
PUMP_NOTIFY_THRESHOLDS = [10, 20, 30, 50, 75, 100]
DUMP_NOTIFY_THRESHOLDS = [-10, -20, -30]

ALERT_MAX_PER_SCAN     = 10

# ═════════════════════════════════════════════
#  PENALTIES (same as strict bot)
# ═════════════════════════════════════════════
PENALTY_UNLOCK_RISK    = 3
PENALTY_BTC_CRASH      = 4
PENALTY_NEGATIVE_NEWS  = 3
PENALTY_ALREADY_PUMPED = 2
PENALTY_HIGH_FUNDING   = 2

# ═════════════════════════════════════════════
#  SCORING WEIGHTS (identical to strict bot)
#  Same 28 signals, same point values
# ═════════════════════════════════════════════
SCORE_WEIGHTS = {
    # Core futures (2 pts each)
    "volume_spike":      2,
    "oi_rising":         2,
    "negative_funding":  2,
    "short_heavy":       2,
    "cvd_divergence":    2,
    "chart_pattern":     2,
    # Technical (1 pt each)
    "bb_squeeze":        1,
    "low_atr":           1,
    "higher_lows":       1,
    "far_from_ath":      1,
    # Market structure (1 pt each)
    "small_market_cap":  1,
    "high_leverage":     1,
    "negative_basis":    1,
    "whales_short":      1,
    "low_float":         1,
    # Sentiment (1 pt each)
    "social_spike":      1,
    "google_trends":     1,
    "fear_greed_low":    1,
    "news_catalyst":     1,
    "twitter_spike":     1,
    # Order book (1 pt each)
    "exchange_outflow":  1,
    "buy_wall":          1,
    "ob_imbalance":      1,
    "arb_signal":        1,
    # On-chain / paid (1 pt each)
    "smart_money_buying":   1,
    "whale_accumulating":   1,
    "liq_magnet_above":     1,
    "btc_sideways_bonus":   1,
}

# ═════════════════════════════════════════════
#  DATA PATHS — separate from strict bot
#  so both bots can run simultaneously
# ═════════════════════════════════════════════
CSV_LOG_PATH    = "data_ls/scan_log.csv"
ALERT_LOG_PATH  = "data_ls/alert_log.csv"
PUMP_TRACK_PATH = "data_ls/pump_tracker.csv"
PUMP_TRACK_DAYS = 10

# ═════════════════════════════════════════════
#  LOGGING
# ═════════════════════════════════════════════
ENABLE_CSV_LOGGING   = True
ENABLE_PUMP_TRACKING = True

# ═════════════════════════════════════════════
#  PUMP SCANNER DEFAULTS (/pumps command)
# ═════════════════════════════════════════════
PUMPS_DEFAULT_LOOKBACK = 7
PUMPS_DEFAULT_MIN_PCT  = 50
PUMPS_MIN_VOLUME_USDT  = 100_000

# ═════════════════════════════════════════════
#  LEGACY (kept for compatibility)
# ═════════════════════════════════════════════
TOP_N_COINS          = 210
DEEP_SCAN_LIMIT      = 210
REQUEST_TIMEOUT      = 10
ENABLE_CSV_LOGGING   = True
ENABLE_PUMP_TRACKING = True
