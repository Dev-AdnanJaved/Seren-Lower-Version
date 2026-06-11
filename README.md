# 🚀 Crypto Pump Scanner v2

Scans Binance Futures every N minutes tracking **20 signals** across 7 categories.
Sends formatted Telegram alerts with every metric.

---

## 📁 Project Structure
```
crypto_pump_bot/
├── main.py
├── config.py                  ← ALL settings here
├── requirements.txt
├── modules/
│   ├── binance_fetcher.py     ← Price, OHLCV, OI, funding, L/S, basis, order book, CVD
│   ├── technical_analysis.py  ← BB, ATR, RSI, MACD, CVD, patterns, days sideways, OI/MC
│   ├── coinglass_fetcher.py   ← OI history, liquidations, funding (Binance fallback)
│   ├── sentiment_fetcher.py   ← Fear&Greed, LunarCrush, CoinGecko, Google Trends, unlocks
│   ├── scorer.py              ← 20-signal scoring engine
│   ├── telegram_alert.py      ← Full alerts with every metric
│   ├── data_logger.py         ← 50+ column CSV + pump tracker
│   ├── scanner.py             ← Orchestrator
│   └── logger.py
├── data/
│   ├── scan_log.csv           ← Every coin, every scan (50+ columns)
│   ├── alert_log.csv
│   └── pump_tracker.csv       ← Tracks if alerted coins pumped
└── logs/
```

---

## ⚡ Setup

```bash
pip install -r requirements.txt

# Edit config.py:
TELEGRAM_BOT_TOKEN = "your-token"   # from @BotFather
TELEGRAM_CHAT_ID   = "your-chat-id" # from /getUpdates

python main.py --once   # test
python main.py          # run forever
```

---

## 📊 All 20 Signals Tracked

| # | Signal | Points | How Detected | Source |
|---|---|---|---|---|
| 1 | Volume spike | 2 | Today's vol > 2.5x 7-day avg | Binance (free) |
| 2 | OI rising | 2 | Open Interest up ≥8% in 24h | Binance/CoinGlass |
| 3 | Negative funding | 2 | Funding rate ≤ 0% | Binance (free) |
| 4 | Short heavy | 2 | Global L/S ratio < 1.0 | Binance (free) |
| 5 | CVD divergence | 2 | CVD rising + price flat/down | Binance (free) |
| 6 | Chart pattern | 2 | Wedge/flag/triangle/coiling | Binance OHLCV |
| 7 | BB squeeze | 1 | Bands tighter than 80% of history | Binance OHLCV |
| 8 | Low ATR | 1 | Volatility in bottom 25% | Binance OHLCV |
| 9 | Higher lows | 1 | Rising lows + sideways price | Binance OHLCV |
| 10 | Small market cap | 1 | Market cap < $500M | CoinGecko (free) |
| 11 | Far from ATH | 1 | > 40% below ATH | Binance OHLCV |
| 12 | Social spike | 1 | Social volume up > 50% | LunarCrush / CoinGecko |
| 13 | Exchange outflow | 1 | Taker buy > 55% of volume | Binance (free) |
| 14 | Fear & Greed low | 1 | Index ≤ 35 | Alternative.me (free) |
| 15 | High leverage (OI/MC) | 1 | OI/Market Cap ratio > 0.3 | Binance + CoinGecko |
| 16 | Negative basis | 1 | Futures trading below spot | Binance (free) |
| 17 | Whales short | 1 | Top trader L/S < 1.0 | Binance (free) |
| 18 | Low float | 1 | Circulating supply < 30% total | CoinGecko (free) |
| 19 | Google Trends spike | 1 | Search interest up > 50% | pytrends (free) |
| 20 | Large buy wall | 1 | Single bid > 10% of book depth | Binance (free) |

**Max score: 26 points**

### Bonus info shown in alert (not scored):
- 7d and 30d price change
- Days sideways count
- Distance from recent 30d low
- MACD cross (daily)
- RSI oversold
- Token unlock risk (penalty -3 if detected)
- Sell wall warning

---

## 🔑 APIs — Free vs Paid

| Source | What For | Free? | Key? |
|---|---|---|---|
| Binance Futures API | Everything core | ✅ 100% free | No |
| Alternative.me | Fear & Greed | ✅ Free | No |
| CoinGecko | Market cap, supply, community | ✅ Free (rate limited) | No |
| pytrends | Google Trends | ✅ Free | No (pip install pytrends) |
| CoinGlass | OI history, liquidations | ✅ Free tier | Optional |
| LunarCrush | Social spikes | ✅ Free tier | Optional |
| CoinMarketCal | Token unlocks | ✅ Free tier | Optional |
| Glassnode | On-chain data | ❌ Paid | Yes |
| CryptoQuant | Exchange flows | ❌ Paid | Yes |

**The bot works 100% without any paid API.**

---

## ⚠️ Avoid Signals (auto-detected)
- Token unlock in next 30 days → score penalty -3
- High positive funding → not scored
- Large sell wall → shown as warning in alert

---

## 📈 Backtesting Your Data

`data/scan_log.csv` has 50+ columns for every coin scanned.
After 2-3 weeks, open in Excel/Pandas:
```python
import pandas as pd
df = pd.read_csv('data/scan_log.csv')
pumped = df[df['pumped'] == 'YES']
print(pumped[['symbol','score','vol_ratio','oi_change_24h','detected_pattern']].describe())
```
Adjust `SCORE_WEIGHTS` in config.py based on what correlates most with pumps.

---
⚠️ Not financial advice. DYOR.
