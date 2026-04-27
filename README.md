# TJR Bot — Automated NAS100 Trading Bot (SMC Strategy)

> Algorithmic trading bot for the NAS100 / US100 index built on MetaTrader 5.  
> Implements a **Smart Money Concepts (SMC/ICT)** strategy with multi-timeframe analysis,  
> automated order execution, dynamic position sizing and real-time risk management.

---

## Overview

TJR Bot is a Python-based automated trading system designed to trade the Nasdaq 100 index (NAS100/US100) exclusively during the **New York session** (15:30–22:00 CET). It connects to MetaTrader 5 via its official Python API and runs a three-stage entry logic based on Smart Money Concepts methodology:

1. **H4 bias detection** — determines the higher-timeframe directional bias
2. **H1 liquidity sweep detection** — identifies manipulation candles that sweep key pivot levels
3. **M5 Order Block entry** — precise entry on the 5-minute timeframe after structure confirmation

The bot was backtested over 15 months on H4/H1/M5 data with a result of **+27,013€ profit, -312€ max drawdown and 0 negative months** on a fixed 100€/trade base risk.

---

## Strategy Logic

### Step 1 — H4 Bias
The bot loads the last 40 H4 candles and detects swing highs and lows using a configurable pivot confirmation window. The market bias is determined as:
- **BULL** → Higher Highs and Higher Lows
- **BEAR** → Lower Highs and Lower Lows
- **NEUTRAL** → inconclusive structure

Only trades aligned with H4 bias are considered (e.g., long setups in BULL bias).

### Step 2 — H1 Liquidity Sweep Detection
On every new closed H1 candle, the bot scans the last confirmed H1 pivot highs/lows. A **sweep** is detected when:
- Price wicks beyond a key pivot level by at least `MIN_WICK` points
- The H1 candle **closes back** on the opposite side of the level (rejection)
- The swept level has not been used already (tracked via `used_levels` set)

This pattern indicates institutional liquidity collection — a core concept in ICT/SMC methodology.

### Step 3 — H1 BOS (Break of Structure) Confirmation
After a sweep is detected, the bot waits for a **Break of Structure** on H1:
- For SHORT sweeps: a subsequent H1 candle closes **below** the sweep candle's low
- For LONG sweeps: a subsequent H1 candle closes **above** the sweep candle's high

The BOS must occur on candles strictly **posterior** to the sweep candle (a critical bug fix from v2.1). A 4-hour timeout cancels the pending setup if BOS doesn't confirm.

### Step 4 — M5 Order Block Entry
Once BOS is confirmed, the bot looks for an **Order Block** on M5 (the last opposing candle before the impulse move near the swept level). The entry price is derived from the most recent M5 candle that touched the OB zone. If no valid OB is found, a fallback entry 5 points from the swept level is used.

---

## Risk Management

| Parameter | Value | Description |
|---|---|---|
| `BASE_RISK` | 100€ | Base risk per trade |
| `TP_RATIO` | 3.5R | Take profit = SL distance × 3.5 |
| `SL_BUFFER` | 10 pts | Extra points beyond the sweep wick |
| `MIN_SL` | 25 pts | Minimum SL distance |
| `MAX_SL` | 140 pts | Maximum SL distance (prevents entries far from OB) |
| `MAX_TRADES` | 2 | Maximum trades per day |
| `close_all()` | 22:00 CET | All open positions closed at end of NY session |

### Dynamic Position Sizing
The bot applies risk multipliers based on statistical backtesting results:

| Condition | Multiplier |
|---|---|
| Thursday | ×1.2 |
| Friday | ×1.2 |
| Week 1 of month | ×1.3 |
| Week 5 of month | ×1.3 |
| Bearish morning session (H1 13:00 close < H1 09:00 open) | ×1.2 |

Lot size is calculated using the **real point value** obtained from MT5 (`trade_tick_value / trade_tick_size`), accounting for broker-specific contract specifications. Volume is clamped to `volume_min/volume_max` and rounded to `volume_step`.

### Optional Break-Even
When `BE_TRIGGER` is set (e.g., `1.0`), the SL is automatically moved to the entry price once the trade reaches 1R in profit. Disabled by default to match backtest conditions.

---

## Technical Implementation

### Architecture
```
main()
├── MT5 initialization + symbol auto-detection
├── Initial H4/H1 data load + state sync
└── Main loop (15s tick)
    ├── daily_reset()          — resets counters at midnight
    ├── update_h4()            — every 4 hours
    ├── close_all()            — at 22:00 CET
    ├── manage_breakeven()     — if BE_TRIGGER active
    ├── new_h1_closed()        — detects new H1 candle
    │   ├── update_h1()
    │   ├── check_for_sweep()  — scan for liquidity sweep
    │   └── check_bos()        — wait for structure break
    │       └── place_order()  — calculate size + send to MT5
    └── check_trade_closed()   — monitors open position
```

### State Management
All runtime state is encapsulated in the `BotState` class:
- `h1_highs / h1_lows` — tracked pivot levels
- `h4_pivots` — higher timeframe structure
- `pending_sweep` — active setup waiting for BOS
- `sweep_candle_time` — timestamp used to filter posterior candles (bug fix)
- `in_trade` — synchronized with MT5 real positions on startup
- `used_levels` — prevents re-trading the same level

### Order Execution
Orders are sent as market orders with automatic **filling type fallback**:  
`IOC → FOK → RETURN`  

The price is refreshed on each attempt to avoid broker rejection due to stale prices. SL and TP are recalculated from the real execution price, not the M5 reference price.

### Symbol Auto-Detection
The bot tries a list of common NAS100 symbol names across different brokers:
```python
["NAS100", "NAS100.cash", "US100", "US100.cash", "USTEC", "NASDAQ", "NDX", "NQ100", ...]
```
Falls back to keyword search across all available symbols if none match.

---

## Bug Fixes (v2.1 → v2.2)

| Severity | Fix |
|---|---|
| 🔴 CRITICAL | `check_bos`: now only validates candles **strictly posterior** to the sweep candle |
| 🔴 CRITICAL | `find_m5_entry`: fixed timezone mismatch (UTC naive vs aware datetime comparison) |
| 🔴 CRITICAL | `close_all`: added `'position': p.ticket` key — previously could open opposite trade |
| 🟡 IMPORTANT | `in_trade`: now synced with real MT5 positions on startup |
| 🟡 IMPORTANT | `trades_today`: only incremented if order execution succeeds |
| 🟢 MINOR | `MAX_SL` increased 130 → 140 to allow for BOS movement |
| 🟢 MINOR | Removed deprecated `datetime.utcnow()` |
| 🟢 MINOR | Tuesday trading now configurable via `SKIP_TUESDAY` |

---

## Requirements

```
Python 3.10+
MetaTrader 5 (desktop terminal installed and logged in)
```

```bash
pip install MetaTrader5 pandas numpy pytz
```

---

## Setup & Usage

1. Clone the repository
2. Open MetaTrader 5 and log into your broker account
3. Enable **Algo Trading** in MT5 (Tools → Options → Expert Advisors)
4. Add the NAS100/US100 symbol to your Market Watch
5. Configure parameters in the `CONFIGURATION` section:

```python
BASE_RISK    = 100.0   # Risk per trade in your account currency
SYMBOL       = ""      # Leave empty for auto-detection, or set exact broker name
SKIP_TUESDAY = False   # Set True to skip Tuesdays
BE_TRIGGER   = False   # Set to e.g. 1.0 to enable break-even at 1R
```

6. Run the bot:
```bash
python Bot_Trading_NQ100.py
```

The bot will log all activity to `tjr_bot.log` and the console.

> ⚠️ **Important:** This bot executes real orders. Always test on a **demo account** first. Past backtest results do not guarantee future performance.

---

## Logging

All events are logged with timestamps to both console and `tjr_bot.log`:

```
2025-04-26 15:47:03 INFO ✓ Símbolo: NAS100
2025-04-26 15:47:03 INFO H4 OK → Bias: BULL (12 pivots)
2025-04-26 15:47:03 INFO H1 OK → 8 highs, 7 lows
2025-04-26 16:02:15 INFO 🟢 SWEEP LONG | Level:19847.50 Wick:12.3pts Bias:BULL
2025-04-26 16:03:17 INFO ✓ BOS LONG confirmed: 19861.00 > 19853.20
2025-04-26 16:03:18 INFO ▶ LONG | EntryRef≈19849.00 | PrecioReal=19862.50 | SL=19837.20 | TP=19909.90 | Lots=0.08 | Riesgo≈100€
2025-04-26 16:03:18 INFO ✅ Orden OK. Ticket #12345678
```

---

## Project Structure

```
├── Bot_Trading_NQ100.py   # Main bot file
├── tjr_bot.log            # Runtime log (auto-generated)
└── README.md
```

---

## What I Learned Building This

- **MetaTrader 5 Python API**: connecting to a trading terminal, retrieving OHLCV data across multiple timeframes, sending market orders and managing open positions programmatically
- **Datetime and timezone handling**: working with UTC-naive vs UTC-aware datetimes, a source of a critical bug that caused the BOS check to use the wrong candle
- **State machine design**: managing a multi-step trading setup (sweep → BOS → entry) across multiple loop iterations without race conditions
- **Financial risk calculation**: computing correct lot sizes from real broker point values (`tick_value / tick_size`) rather than assuming fixed values, and clamping to broker volume constraints
- **Error handling and resilience**: retry logic for data loading, fallback order filling types, graceful handling of MT5 disconnections
- **Debugging production issues**: identifying and fixing a bug where `close_all()` was opening opposite trades instead of closing positions due to a missing `'position'` key in the order request

---

## Disclaimer

This project is for **educational purposes**. Trading financial instruments involves significant risk of loss. The author is not a financial advisor. Use at your own risk.

---

*Built with Python 3.12 · MetaTrader5 · pandas · numpy · pytz*
