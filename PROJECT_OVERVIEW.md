# FIFTO AI Trading — Project Overview

> **One-page guide to understand the entire system**

---

## What Is FIFTO?

**FIFTO** is a fully-automated NIFTY options trading bot that runs 24/7 on your Windows PC.

It sells a **Short Strangle** on NIFTY every morning — one OTM Call (CE) + one OTM Put (PE) — collects the premium, and exits when it hits the profit target, stop loss, or end of day. Everything — signal, order placement, monitoring, exit — happens automatically.

---

## The Trading Strategy at a Glance

```
9:30 AM   →  Bot scans for a valid setup
              If all checks pass → SELL CE + SELL PE (Short Strangle)
              Premium collected = CE price + PE price

During Day →  Monitor every 30 sec
              If combined buyback cost ≤ entry − target → EXIT (profit ✅)
              If combined buyback cost > stop loss       → EXIT (loss 🛑)
              If 14:30 dead zone or 15:20 EOD            → EXIT (time 🕑)

Exit       →  BUY back both legs (closing the position)
              P&L recorded to trades.json + Google Sheets
              Telegram notification sent
```

**Why Short Strangle?**
- NIFTY stays range-bound most days → both options decay → profit from time decay (theta)
- Max profit = premium collected (if NIFTY stays between strikes till expiry)
- Defined risk (stop loss exits before unlimited loss scenario)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Windows PC (auto-starts on login via Task Scheduler)       │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  dev_server.py  (Flask + Background Threads)         │   │
│  │                                                      │   │
│  │  Thread 1: Signal Engine  (every 5 min)              │   │
│  │  Thread 2: Position Monitor (every 30 sec)           │   │
│  │  Thread 3: Market Data    (every 15 sec)             │   │
│  │  Thread 4: Angel Login    (on startup/reconnect)     │   │
│  │                                                      │   │
│  │  Flask API  →  /api/state, /api/trades, etc.         │   │
│  └──────────┬─────────────────────────────┬─────────────┘   │
│             │                             │                  │
│    localhost:8080                   Background               │
│             │                             │                  │
│  ┌──────────▼──────┐        ┌────────────▼────────────────┐ │
│  │  Dashboard      │        │  AngelOne Smart API         │ │
│  │  (Browser PWA)  │        │  - Live NIFTY / VIX data    │ │
│  │                 │        │  - Option chain (strikes)   │ │
│  │  Desktop/Mobile │        │  - Order placement (SELL/BUY│ │
│  └─────────────────┘        │  - Margin check             │ │
│                             └─────────────────────────────┘ │
│                                                             │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐  │
│  │  trades.json   │  │ active_pos.json│  │  .env secrets │  │
│  │  (trade log)   │  │  (crash safe)  │  │  (API keys)   │  │
│  └────────────────┘  └────────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
         │                        │
         ▼                        ▼
  Google Sheets             Telegram Bot
  (22-col audit log)        (real-time alerts)
```

---

## Entry Conditions (All Must Pass)

Before placing any trade, the bot checks every condition:

| Check | Condition | Why |
|-------|-----------|-----|
| **VIX Range** | 13 ≤ VIX ≤ 28 | Low VIX = low premium; High VIX = too risky |
| **Entry Window** | 9:30 AM – 11:00 AM | Avoid volatile open; miss late-day theta |
| **Max Trades** | Trades today < 3 | Prevents over-trading |
| **Daily Gate** | Daily P&L > −₹45,000 | Stops trading if daily loss limit hit |
| **Margin** | Available cash > 0 | Can't trade without margin |
| **Holiday** | Not an F&O holiday | NSE holiday list checked |
| **Spot Stability** | NIFTY moved < 0.5% in 15 min | Avoid entering during fast moves |
| **PCR** *(optional)* | 0.8 ≤ PCR ≤ 1.4 | Sentiment not extreme (skipped if unavailable) |
| **IV Percentile** *(optional)* | IV% > 40 | Enough premium available |

---

## Strike Selection

```
NIFTY Spot = 23,800

ATM = round(23800 / 50) × 50 = 23,800

CE Selection: Walk up +50 each step until LTP ≥ ₹40 (min_premium)
  23,850 → ₹192  ✅  ← selected

PE Selection: Walk down −50 each step until LTP ≥ ₹40
  23,750 → ₹186  ✅  ← selected

Combined Premium = 192 + 186 = ₹378 ≥ ₹150 (min_combined) ✅

Entry: SELL NIFTY 23850 CE + SELL NIFTY 23750 PE
```

---

## Target & Stop Loss (DTE-Tiered)

The bot adjusts risk/reward based on how many days are left to expiry:

| Days to Expiry | Target | Stop Loss | Exit when combined cost... |
|----------------|--------|-----------|----------------------------|
| **0** (expiry day) | 45% of premium | 1.5× premium | drops to 55% (profit) / rises to 150% (loss) |
| **1** | 28% | 1.4× | drops to 72% / rises to 140% |
| **2** | 22% | 1.3× | drops to 78% / rises to 130% |
| **3+** | 18% | 1.25× | drops to 82% / rises to 125% |

**Example** (Premium ₹378, DTE = 4):
```
Target  = ₹378 × 18% = ₹68.1  → exit when cost drops to ₹310  (+₹13,283 profit)
Stop Loss = ₹378 × 1.25 = ₹473 → exit when cost rises to ₹473  (−₹18,525 loss)
```

---

## Trailing Stop Loss

Once the trade hits **20% profit**, the stop loss automatically tightens to breakeven:

```
Entry premium = ₹378
20% profit trigger = ₹378 × 20% = ₹75.6

If current cost drops to ₹302 (20% saved):
  → Trail SL locks at ₹378 (entry premium = breakeven)
  → Worst case: exit at ₹0 profit (not a loss)
  → Telegram: "🔒 Trailing SL — Breakeven Locked"
```

---

## Exit Triggers (Priority Order)

1. **Target Hit** → `current_cost ≤ entry − target` → square off → Telegram ✅
2. **Stop Loss Hit** → `current_cost > sl` → square off → Telegram 🛑
3. **Expiry Cut-Time** → On expiry day at 1:00 PM → force exit ⏰
4. **Dead Zone** → After 2:30 PM → force exit (low liquidity) ⏰
5. **EOD Exit** → At 3:20 PM → force exit ⏰
6. **Emergency Exit** → User clicks button in dashboard → immediate ⚠️

---

## Data Flow: Entry to Exit

```
Signal Engine generates signal
         │
         ▼
_execute_trade(signal)
  ├── SELL NIFTY 23850 CE  (AngelOne order)
  ├── SELL NIFTY 23750 PE  (AngelOne order)
  ├── Store in state["position_detail"]
  ├── Write to trades.json (exit_time = null)
  ├── Write to active_pos.json (full detail)
  └── Append row to Google Sheets (P&L columns blank)
         │
         ▼ (every 30 sec)
Position Monitor fetches CE + PE live LTP
  current_cost = CE_ltp + PE_ltp
  open_pnl = (entry_premium − current_cost) × qty
         │
         ├── 20% profit? → Lock trailing SL
         ├── Target hit? → _square_off_position()
         └── SL hit?     → _square_off_position()
                  │
                  ▼
         _square_off_position()
           ├── BUY NIFTY 23850 CE  (closing)
           ├── BUY NIFTY 23750 PE  (closing)
           ├── Calculate final_pnl
           ├── Update trades.json (complete record)
           ├── Delete active_pos.json
           └── Update Google Sheets (all 22 columns)
```

---

## Crash Recovery

If the server crashes or restarts mid-trade:

```
On startup → _restore_open_position()
  │
  ├── Check active_pos.json (priority)
  │     → Has full position detail (tokens, entry prices, SL state)
  │     → Restore directly as active position
  │
  └── Fallback: trades.json (entry with no exit_time)
        → Rebuild position from trade record
        → Re-resolve tokens after AngelOne login

Result: Bot continues monitoring the open position seamlessly
```

---

## Google Sheets (22 Columns)

| # | Column | Written at | Value |
|---|--------|-----------|-------|
| 1 | Trade ID | Entry | T1775708570 |
| 2 | Entry Time | Entry | 09-04-2026 09:52:50 |
| 3 | Exit Time | Exit | 09-04-2026 14:30:00 |
| 4 | Setup | Entry | Short Strangle |
| 5 | CE Strike | Entry | 23850 |
| 6 | PE Strike | Entry | 23750 |
| 7 | CE Symbol | Entry | NIFTY13APR2623850CE |
| 8 | PE Symbol | Entry | NIFTY13APR2623750PE |
| 9 | Qty | Entry | 195 |
| 10 | Premium | Entry | 378.45 |
| 11 | CE Entry LTP | Entry | 192.30 |
| 12 | PE Entry LTP | Entry | 186.15 |
| 13 | CE Exit LTP | Exit | 216.00 |
| 14 | PE Exit LTP | Exit | 154.55 |
| 15 | Target | Entry | 68.12 |
| 16 | Initial SL | Entry | 473.06 |
| 17 | Final SL | Exit | 378.45 *(if trailed)* |
| 18 | Trail Locked | Exit | Yes / No |
| 19 | P&L | Exit | +7,137 |
| 20 | Exit Reason | Exit | TARGET / DEAD_ZONE / SL |
| 21 | Expiry | Entry | 13-Apr-2026 |
| 22 | Duration (min) | Exit | 295 |

---

## Telegram Notifications

| Event | Alert |
|-------|-------|
| Signal ready (MANUAL mode) | 📊 Signal Ready — Action Required |
| Trade placed (AUTO mode) | ✅ Trade Executed — Short Strangle |
| Trailing SL activated | 🔒 Trailing SL — Breakeven Locked |
| Target hit | 🎯 Target Hit! |
| Stop loss hit | 🛑 Stop Loss Hit |
| Dead zone exit | ⏰ Dead Zone Exit |
| EOD auto-exit | ⏰ EOD Auto-Exit — 3:20 PM |
| Emergency exit | 🚨 Emergency Exit |

---

## Dashboard (Web UI)

Open `http://localhost:8080` in any browser.

```
┌─────────────────────────────────────────────────────────┐
│ FIFTO  Dashboard  Positions  Trade Log  Settings        │
│               NIFTY 23,982  VIX 20.1  MARKET OPEN 9:30 │
├────────┬───────────┬───────────┬──────────┬────┬────────┤
│Daily   │ Overall   │ Margin    │ Regime   │📊  │ DTE    │
│P&L     │ P&L       │ Available │          │    │ Ring   │
├────────┴───────────┴───────────┴──────────┴────┴────────┤
│ ACTIVE POSITION                              ● Active   │
│ +₹1,541  unrealised P&L                                 │
│ ┌── CE SOLD 23850 ──┐  ┌── PE SOLD 23750 ─────────┐    │
│ │ Entry  ₹192.30    │  │ Entry  ₹186.15            │    │
│ │ Now    ₹216.00▲   │  │ Now    ₹154.55▼           │    │
│ │ 3 lots × 65 = 195 │  │ 3 lots × 65 = 195        │    │
│ └───────────────────┘  └──────────────────────────┘    │
│  Premium ₹378.45  │  Target 68.1 pts  │  SL 94.6 pts  │
│  ₹73,798 (3L×65)  │  exit ₹310 (+₹13K)│ exit ₹473(-₹18K)│
│  Entry 09:52:50   │  Expiry 13-Apr    │  R:R 1:1.39   │
├─────────────────────────────────────────────────────────┤
│ TRADING SIGNAL       SYSTEM CHECKS                      │
│ ● WAIT               ✅ VIX: 20.1 (in range)           │
│ Scanning for setup   ✅ Margin: ₹15L available          │
│                      ✅ Daily gate: OK                  │
│                      ✅ No holiday                      │
│                      ⚪ PCR: N/A                        │
└─────────────────────────────────────────────────────────┘
```

**Pages:**
- **Dashboard** — Live position, P&L, checks, signal, log
- **Positions** — Open positions table
- **Trade Log** — History table; click any row for full trade detail modal
- **Settings** — Configure all parameters (lots, premium, SL, timing, etc.)

---

## Mobile (PWA)

The dashboard is a **Progressive Web App** — install it like a native app:

**Android (Chrome):**
1. Open `http://your-pc-ip:8080` in Chrome
2. Tap ⋮ → "Add to Home Screen"
3. Opens fullscreen with FIFTO icon

**iOS (Safari):**
1. Open the URL in Safari
2. Tap Share → "Add to Home Screen"

Works offline (loads cached shell); live data requires network connection to the PC running the server.

---

## Configuration (Settings Page or `.env`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `capital` | ₹15,00,000 | Capital for P&L % calculation |
| `base_lots` | 3 | Number of lots per leg |
| `lot_size` | 65 | Shares per lot (auto-updated from NSE daily) |
| `min_premium` | ₹40 | Minimum LTP per leg to select strike |
| `min_combined_premium` | ₹150 | Minimum CE+PE combined premium |
| `profit_target_pct` | 18–45% | DTE-tiered (set automatically) |
| `sl_multiplier` | 1.25–1.5× | DTE-tiered (set automatically) |
| `trail_trigger_pct` | 20% | Profit % to lock trailing SL at breakeven |
| `daily_loss_limit` | ₹45,000 | Stop trading if daily loss exceeds this |
| `max_trades_per_day` | 3 | Maximum trades allowed per day |
| `vix_min / vix_max` | 13 / 28 | VIX range for entry |
| `entry_start / entry_end` | 9:30 / 11:00 | Entry window |
| `dead_zone_start` | 14:30 | Force exit after this time |
| `expiry_cut_time` | 13:00 | Force exit on expiry day before this |
| `execution_mode` | AUTO | AUTO = immediate; MANUAL = wait for button click |
| `paper_trade` | false | If true, simulates orders without real execution |

---

## File Structure

```
NIFTY Claude Setup/
├── dev_server.py          ← Main server (Flask + all trading logic ~2400 lines)
├── nifty_dashboard.html   ← Single-file dashboard UI
├── config.py              ← Config defaults (dev_server.py reads these)
│
├── trades.json            ← Trade history (auto-created)
├── active_pos.json        ← Active position snapshot (exists only during trade)
├── gsheet_creds.json      ← Google Service Account credentials
├── .env                   ← API keys (never commit to git)
│
├── manifest.json          ← PWA manifest (installable mobile app)
├── sw.js                  ← Service Worker (offline support)
├── icon-192.svg           ← App icon (small)
├── icon-512.svg           ← App icon (large)
├── icon-mask.svg          ← Adaptive icon (Android)
│
├── setup_auto_start.bat   ← One-time Windows Task Scheduler setup
├── enable_auto_start.bat  ← Re-enable auto-start
├── disable_auto_start.bat ← Disable auto-start
└── AUTO_START_GUIDE.md    ← Auto-start instructions
```

---

## Quick Start

```bash
# 1. Install dependencies
.venv/Scripts/pip install flask python-dotenv requests gspread google-auth smart-api-python pyotp

# 2. Fill in .env
ANGEL_API_KEY=...
ANGEL_CLIENT_ID=...
ANGEL_PASSWORD=...
ANGEL_TOTP_SECRET=...
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
GSHEET_ID=...

# 3. Run
.venv/Scripts/python dev_server.py

# 4. Open dashboard
http://localhost:8080

# 5. (Optional) Auto-start on Windows login
Run setup_auto_start.bat as Administrator
```

---

## Key Numbers (Current Setup)

| Parameter | Value |
|-----------|-------|
| Capital | ₹15,00,000 |
| Lots per trade | 3 lots |
| Lot size | 65 shares |
| Quantity per leg | 195 shares |
| Typical premium | ₹300–500 |
| Typical target | ₹55–225 per lot (~₹10K–45K total) |
| Max daily loss allowed | ₹45,000 |
| Max trades per day | 3 |
| Broker | AngelOne (Smart API) |
| Exchange | NSE F&O |
| Instrument | NIFTY Weekly Options |
