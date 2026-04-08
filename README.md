# FIFTO AI Trading

Fully automated options trading bot for NIFTY index. Generates short strangle signals and executes trades automatically via AngelOne API.

## Features

- **Fully Automated** - Entry, exit, and position monitoring without manual intervention
- **Short Strangle Strategy** - Sells OTM CE and PE options simultaneously
- **Real-time Risk Management** - Stop loss (2x), profit target (50%), and EOD exit
- **Live Dashboard** - Web UI at http://localhost:8080 with real-time P&L and market data
- **AngelOne Integration** - Direct order execution via Smart API
- **Trade History** - Complete audit trail of all trades with exit reasons

## Architecture

Single-file Flask application (`dev_server.py`) with all logic inline:
- Market data fetching (NIFTY, VIX, PCR, IV)
- Signal generation based on market conditions
- Automated order placement
- Position monitoring with SL/target/EOD exits
- Web dashboard with live updates

## Requirements

- Python 3.11+ (tested with 3.15)
- AngelOne trading account with API access
- pip packages: `flask`, `smartapi-python`, `pyotp`, `requests`, `mss`, `Pillow`

## Installation

1. Clone the repository:
```bash
git clone https://github.com/maniraja5599/fifito-copilot-trade.git
cd fifito-copilot-trade
```

2. Create virtual environment and install dependencies:
```bash
python -m venv .venv
.venv\Scripts\activate  # On Windows
pip install flask smartapi-python pyotp requests mss Pillow
```

3. Configure environment variables in `.env`:
```bash
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_PASSWORD=your_password
ANGEL_TOTP_SECRET=your_totp_secret
TELEGRAM_TOKEN=your_telegram_token  # optional
TELEGRAM_CHAT_ID=your_chat_id      # optional
```

4. Start the server:
```bash
.venv\Scripts\python.exe dev_server.py
```

5. Open dashboard: http://localhost:8080

## Configuration

Edit `config.py` or set environment variables to customize:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `capital` | 1,500,000 | Trading capital (₹) |
| `base_lots` | 2 | Number of lots per leg |
| `lot_size` | 75 | NIFTY lot size |
| `profit_target_pct` | 0.50 | Exit at 50% profit |
| `sl_multiplier` | 2.0 | Stop loss at 2x premium |
| `daily_loss_limit` | 45000 | Max daily loss before halting |
| `max_trades_per_day` | 3 | Maximum trades per day |
| `vix_min` / `vix_max` | 13 / 28 | VIX range for trading |
| `entry_start` | 09:30 | Entry window start |
| `entry_end` | 11:00 | Entry window end |
| `execution_mode` | AUTO | AUTO or MANUAL |

## Trading Logic

### Entry Conditions
- Market open (9:15 AM - 3:30 PM)
- Within entry window (9:30 AM - 11:00 AM)
- VIX between 13-28
- Daily loss gate not breached
- All checks pass (auto mode)

### Exit Conditions
- **Target:** 50% profit achieved
- **Stop Loss:** 2x premium loss
- **EOD:** Auto-exit at 3:20 PM

### Position Sizing
- Short strangle: 1 CE + 1 PE
- Quantity: `base_lots × lot_size` (default: 2 × 75 = 150 shares per leg)

## Dashboard

The web dashboard shows:
- **Connection Status** - AngelOne connectivity
- **Market Data** - NIFTY spot, VIX, PCR, IV, ATR, Delta
- **Setup Checks** - Real-time pass/fail with actual values
- **Risk Meters** - Daily/weekly loss limits
- **Position Details** - Active trades with P&L
- **Signal History** - Last signal and reason

## Trade Tracking

All trades are recorded in `state["trade_history"]` with:
- Entry/exit timestamps
- Strikes and symbols
- Premium received, target, stop loss
- Final P&L and exit reason (TARGET/SL/EOD)
- Order IDs for audit

## Automation

The system runs fully automatically:
1. **Signal Engine** - Checks conditions every 5 minutes during entry window
2. **Position Monitor** - Checks SL/target every 30 seconds
3. **Market Data** - Updates every 15 seconds
4. **Auto-Execution** - Immediately places orders when signal generated (AUTO mode)

## Project Structure

```
fifito-copilot-trade/
├── dev_server.py          # Main Flask server (all logic)
├── nifty_dashboard.html   # Dashboard UI
├── config.py              # Configuration
├── .env                   # Secrets (not in git)
├── .gitignore
├── start_fifto.bat        # Windows startup script
├── logs/                  # Log files
└── README.md
```

## Notes

- Python 3.15 alpha may have compatibility issues with some packages (Pillow, OpenAI)
- Recommended: Python 3.11 or 3.12 for production
- Paper trading mode available via `PAPER_TRADE=true` in `.env`
- Telegram alerts optional (requires `python-telegram-bot`)

## License

Proprietary - For personal use only.

## Author

Maniraja Nachimuthu
