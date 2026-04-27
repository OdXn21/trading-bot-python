# TJR Bot — Automated NAS100 Trading Bot

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![MetaTrader5](https://img.shields.io/badge/MetaTrader5-API-1A1A2E?style=for-the-badge&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-22c98b?style=for-the-badge)
![Status](https://img.shields.io/badge/Status-Active-22c98b?style=for-the-badge)
![Strategy](https://img.shields.io/badge/Strategy-SMC%20%2F%20ICT-9d8cf5?style=for-the-badge)

**Algorithmic trading bot for the NAS100 / US100 index built on MetaTrader 5.**  
Implements a Smart Money Concepts (SMC/ICT) strategy with multi-timeframe analysis,  
automated order execution, dynamic position sizing and real-time risk management.

</div>

---

## Backtest Results — 15 months

<div align="center">

| Metric | Result |
|:---:|:---:|
| Net Profit | +27,013€ |
| Max Drawdown | -312€ |
| Negative Months | 0 |
| Base Risk per Trade | 100€ |
| Risk/Reward Ratio | 1 : 3.5 |

</div>

> Results obtained on H4/H1/M5 historical data. Past performance does not guarantee future results.

---

## How it works

The bot runs a **three-stage entry logic** on every New York session (15:30–22:00 CET):

```
┌──────────────┐     ┌──────────────────┐     ┌───────────────────┐
│   STAGE 1    │     │     STAGE 2       │     │      STAGE 3      │
│              │     │                  │     │                   │
│  H4 Bias     │ ──► │  H1 Liquidity    │ ──► │  M5 Order Block   │
│  Detection   │     │  Sweep Detection │     │  Entry            │
│              │     │                  │     │                   │
│ BULL / BEAR  │     │ Wick beyond key  │     │ Entry from last   │
│ based on     │     │ pivot + closes   │     │ M5 candle at OB   │
│ swing H/L    │     │ back inside      │     │ zone + BOS conf.  │
└──────────────┘     └──────────────────┘     └───────────────────┘
                                                        │
                                                        ▼
                                          ┌─────────────────────────┐
                                          │     RISK MANAGEMENT     │
                                          │  SL = sweep wick +10pts │
                                          │  TP = SL × 3.5R         │
                                          │  Max 2 trades/day       │
                                          │  Force-close at 22:00   │
                                          └─────────────────────────┘
```

### Stage 1 — H4 Bias
Loads the last 40 H4 candles and detects swing highs/lows using a configurable pivot confirmation window:
- **BULL** → Higher Highs and Higher Lows
- **BEAR** → Lower Highs and Lower Lows
- **NEUTRAL** → inconclusive structure — no trade taken

### Stage 2 — H1 Liquidity Sweep
On every new closed H1 candle, scans the last confirmed pivot levels. A **sweep** is detected when:
- Price wicks beyond a key pivot by at least `MIN_WICK` points
- The candle **closes back** on the opposite side (rejection)
- The level hasn't been used before (tracked via `used_levels`)

This indicates institutional liquidity collection — a core concept in ICT/SMC methodology.

### Stage 3 — H1 BOS + M5 Order Block Entry
After a sweep, waits for a **Break of Structure** on H1. Once confirmed, finds the M5 Order Block (last opposing candle before the impulse near the swept level) and places a market order with precise SL/TP derived from the real execution price.

---

## Risk Management

| Parameter | Value | Description |
|---|---|---|
| `BASE_RISK` | 100€ | Base risk per trade |
| `TP_RATIO` | 3.5R | Take profit = SL distance × 3.5 |
| `SL_BUFFER` | 10 pts | Extra buffer beyond the sweep wick |
| `MIN_SL` | 25 pts | Minimum stop loss distance |
| `MAX_SL` | 140 pts | Maximum stop loss distance |
| `MAX_TRADES` | 2 | Maximum concurrent trades per day |
| `close_all` | 22:00 CET | Force-close all positions at NY session end |

### Dynamic Position Sizing

Lot size is calculated from the real broker point value (`tick_value / tick_size`). Statistical backtesting identified conditions with higher win rate — multipliers are applied accordingly:

| Condition | Multiplier |
|---|---|
| Thursday or Friday | × 1.2 |
| Week 1 or Week 5 of month | × 1.3 |
| Bearish morning session (H1 13:00 close < H1 09:00 open) | × 1.2 |

---

## Architecture

```
main()
├── MT5 init + symbol auto-detection
├── Initial H4/H1 data load + state sync
└── Main loop (15s tick)
    ├── daily_reset()        — resets counters at midnight
    ├── update_h4()          — every 4 hours
    ├── close_all()          — at 22:00 CET
    ├── manage_breakeven()   — optional BE trigger
    ├── new_h1_closed()
    │   ├── update_h1()
    │   ├── check_for_sweep()
    │   └── check_bos()
    │       └── place_order()
    └── check_trade_closed()
```

**State management** is handled by a `BotState` class tracking pivot levels, pending setups, sweep timestamps and open positions — synchronized with MT5 on startup to survive restarts without losing context.

**Order execution** uses market orders with automatic filling type fallback (`IOC → FOK → RETURN`) and refreshes the live price on each retry to avoid broker rejection due to stale quotes.

**Symbol auto-detection** tries a list of common NAS100 broker symbol names and falls back to keyword search across all available symbols if none match.

---

## Changelog

### v2.2 — Critical Bug Fixes

| Severity | Fix |
|---|---|
| 🔴 CRITICAL | `check_bos` — now only validates candles strictly posterior to the sweep candle timestamp |
| 🔴 CRITICAL | `find_m5_entry` — fixed UTC naive vs UTC aware datetime comparison (caused wrong candle selection) |
| 🔴 CRITICAL | `close_all` — added missing `'position'` key in order request (was opening opposite trades instead of closing) |
| 🟡 IMPORTANT | `in_trade` — now synced with real MT5 open positions on startup |
| 🟡 IMPORTANT | `trades_today` — only incremented when order execution succeeds |
| 🟢 MINOR | `MAX_SL` increased 130 → 140 to allow for BOS movement |
| 🟢 MINOR | Removed deprecated `datetime.utcnow()` |
| 🟢 MINOR | Tuesday trading now configurable via `SKIP_TUESDAY` flag |

---

## Requirements

```
Python 3.10+
MetaTrader 5 terminal (installed and logged in to your broker account)
```

```bash
pip install MetaTrader5 pandas numpy pytz
```

---

## Setup & Usage

**1. Clone the repository**
```bash
git clone https://github.com/OdXn21/trading-bot-python.git
cd trading-bot-python
```

**2. Open MetaTrader 5** and enable Algo Trading (Tools → Options → Expert Advisors → Allow automated trading)

**3. Add NAS100/US100** to your Market Watch if not already there

**4. Configure parameters** at the top of `Bot_Trading_NQ100.py`:
```python
BASE_RISK    = 100.0   # Risk per trade in your account currency
SYMBOL       = ""      # Leave empty for auto-detection, or set exact broker symbol name
SKIP_TUESDAY = False   # Set True to skip Tuesday trading
BE_TRIGGER   = False   # Set to e.g. 1.0 to move SL to break-even at 1R profit
```

**5. Run**
```bash
python Bot_Trading_NQ100.py
```

All activity is logged to both console and `tjr_bot.log`.

> ⚠️ **Always test on a demo account first.** This bot places real market orders with real money.

---

## Sample Log Output

```
2025-04-26 15:47:03 INFO  ✓ Symbol detected: NAS100
2025-04-26 15:47:03 INFO  H4 OK → Bias: BULL (12 pivots)
2025-04-26 15:47:03 INFO  H1 OK → 8 highs, 7 lows tracked
2025-04-26 16:02:15 INFO  SWEEP LONG | Level: 19847.50 | Wick: 12.3 pts | Bias: BULL
2025-04-26 16:03:17 INFO  BOS LONG confirmed: 19861.00 > 19853.20
2025-04-26 16:03:18 INFO  LONG | OB entry: 19849.00 | Fill: 19862.50 | SL: 19837.20 | TP: 19909.90 | Lots: 0.08 | Risk: ~100€
2025-04-26 16:03:18 INFO  Order OK — Ticket #12345678
```

---

## What I learned building this

- **MetaTrader 5 Python API** — connecting to a live terminal, retrieving OHLCV data across multiple timeframes, sending market orders and managing open positions programmatically
- **Datetime and timezone handling** — the difference between UTC-naive and UTC-aware datetimes caused a critical bug in the BOS candle validation; fixing it required tracing exactly how MT5 timestamps are returned and how Python compares them
- **State machine design** — managing a multi-step trading setup (sweep → BOS → entry) across loop iterations without race conditions, duplicate triggers or stale state
- **Financial risk calculation** — computing correct lot sizes from real broker tick values (`tick_value / tick_size`) rather than assuming fixed point values, then clamping to broker volume constraints
- **Debugging production issues** — identifying that `close_all()` was opening opposite trades instead of closing positions due to a missing `'position'` key in the MT5 order request; found by reading error codes and tracing the full execution path step by step

---

## Project Structure

```
├── Bot_Trading_NQ100.py   — Main bot file
├── tjr_bot.log            — Runtime log (auto-generated, gitignored)
├── .gitignore
├── LICENSE
└── README.md
```

---

## Disclaimer

This project is for **educational purposes only**. Trading financial instruments involves significant risk of loss and is not suitable for all investors. The author is not a financial advisor. Past backtest results do not guarantee future performance. Use at your own risk.

---

<div align="center">
  <sub>Built with Python 3.12 · MetaTrader5 · pandas · numpy · pytz</sub>
</div>
