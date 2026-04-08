"""
dev_server.py — FIFTO AI Trading
Real AngelOne + NSE data + Signal Engine + Order Execution (Auto & Manual)
Run: .venv/Scripts/python.exe dev_server.py
"""

import os, time, threading, math, json
from datetime import datetime, date as _date, time as _time, timedelta
from flask import Flask, jsonify, send_from_directory, request
from dotenv import load_dotenv
import requests as _requests

# ── Trade persistence ──
TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.json")

def _load_trades_from_disk():
    """Load saved trades from local JSON on startup."""
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _save_trade_local(trade_record):
    """Upsert trade in local trades.json (insert or update by trade_id)."""
    try:
        trades = _load_trades_from_disk()
        tid = trade_record.get("trade_id")
        idx = next((i for i, t in enumerate(trades) if t.get("trade_id") == tid), None)
        if idx is not None:
            trades[idx] = trade_record
        else:
            trades.append(trade_record)
        with open(TRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2, default=str)
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Local trade save failed: {e}")

def _get_or_create_sheet():
    """Return the Trades worksheet, creating it with headers if needed."""
    import gspread
    from google.oauth2.service_account import Credentials
    creds_file = os.path.join(BASE, "gsheet_creds.json")
    sheet_id   = os.getenv("GSHEET_ID", "")
    if not sheet_id or not os.path.exists(creds_file):
        return None
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds  = Credentials.from_service_account_file(creds_file, scopes=scopes)
    gc     = gspread.authorize(creds)
    sh     = gc.open_by_key(sheet_id)
    try:
        return sh.worksheet("Trades")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Trades", rows=1000, cols=17)
        ws.append_row([
            "Trade ID", "Entry Time", "Exit Time", "Setup",
            "CE Strike", "PE Strike", "CE Symbol", "PE Symbol",
            "Qty", "Premium", "Target", "Stop Loss", "P&L", "Exit Reason", "Expiry",
            "CE Entry LTP", "PE Entry LTP"
        ])
        return ws

def _save_entry_sheets(trade_record):
    """Write a new entry row (P&L/exit columns blank) when trade opens."""
    try:
        ws = _get_or_create_sheet()
        if not ws:
            return
        ws.append_row([
            trade_record.get("trade_id", ""),
            trade_record.get("entry_time", ""),
            "",                                   # exit time — blank at entry
            trade_record.get("setup_type", ""),
            trade_record.get("ce_strike", ""),
            trade_record.get("pe_strike", ""),
            trade_record.get("ce_symbol", ""),
            trade_record.get("pe_symbol", ""),
            trade_record.get("quantity", ""),
            trade_record.get("premium_received", ""),
            trade_record.get("target", ""),
            trade_record.get("stop_loss", ""),
            "",                                   # P&L — blank at entry
            "",                                   # exit reason — blank at entry
            trade_record.get("expiry", ""),
            trade_record.get("ce_entry_ltp", ""),
            trade_record.get("pe_entry_ltp", ""),
        ])
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Sheets entry write failed: {e}")

def _update_exit_sheets(trade_record):
    """Update the existing row with exit time, P&L, and exit reason."""
    try:
        import gspread
        ws = _get_or_create_sheet()
        if not ws:
            return
        cell = ws.find(trade_record.get("trade_id", ""), in_column=1)
        if not cell:
            # Row missing — append full record as fallback
            _save_entry_sheets(trade_record)
            cell = ws.find(trade_record.get("trade_id", ""), in_column=1)
            if not cell:
                return
        r = cell.row
        ws.update_cell(r, 3,  trade_record.get("exit_time", ""))
        ws.update_cell(r, 13, trade_record.get("final_pnl", ""))
        ws.update_cell(r, 14, trade_record.get("exit_reason", ""))
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Sheets exit update failed: {e}")

def _persist_entry(pos):
    """Called at trade entry — save partial record locally + write entry row to Sheets."""
    entry_record = {
        "trade_id":        pos["trade_id"],
        "entry_time":      _fmt_ts(pos["entry_time"]),
        "exit_time":       None,
        "setup_type":      pos.get("setup_type", "Short Strangle"),
        "ce_strike":       pos["ce_strike"],
        "pe_strike":       pos["pe_strike"],
        "ce_symbol":       pos["ce_symbol"],
        "pe_symbol":       pos["pe_symbol"],
        "quantity":        pos["quantity"],
        "premium_received": pos["premium_received"],
        "target":          pos["target"],
        "stop_loss":       pos["sl"],
        "final_pnl":       None,
        "exit_reason":     None,
        "expiry":          pos.get("expiry", ""),
        "ce_entry_ltp":    pos.get("ce_entry_price", ""),
        "pe_entry_ltp":    pos.get("pe_entry_price", ""),
    }
    _save_trade_local(entry_record)
    threading.Thread(target=_save_entry_sheets, args=(entry_record,), daemon=True).start()

def _persist_trade(trade_record):
    """Called at trade exit — update local record + update Sheets row."""
    _save_trade_local(trade_record)
    threading.Thread(target=_update_exit_sheets, args=(trade_record,), daemon=True).start()

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

app  = Flask(__name__)

# ── AngelOne connection state ──
connection = {
    "status":        "disconnected",
    "client_id":     os.getenv("ANGEL_CLIENT_ID", "—"),
    "name":          "—",
    "connected_at":  None,
    "last_ping":     None,
    "ping_ms":       None,
    "session_valid": False,
    "totp_ok":       False,
    "exchanges":     [],
    "error":         None,
    "available_margin": 0,
    "used_margin":      0,
}

angel_obj = None


def _ts():
    return datetime.now().strftime("%H:%M:%S")

def _fmt_ts(dt):
    """Format a datetime (or ISO string) as DD-MM-YYYY  HH:MM:SS for Google Sheets."""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return dt
    if isinstance(dt, datetime):
        return dt.strftime("%d-%m-%Y  %H:%M:%S")
    return str(dt)


def angel_login():
    """Real AngelOne login."""
    global angel_obj
    import pyotp
    from SmartApi import SmartConnect

    api_key  = os.getenv("ANGEL_API_KEY", "")
    client   = os.getenv("ANGEL_CLIENT_ID", "")
    password = os.getenv("ANGEL_PASSWORD", "")
    secret   = os.getenv("ANGEL_TOTP_SECRET", "")

    # Debug: log what credentials are loaded
    LOG_LINES.append(f"[DEBUG] [{_ts()}] API Key: {api_key[:4]}***, Client: {client}")
    LOG_LINES.append(f"[DEBUG] [{_ts()}] Password: {'*' * len(password)}, TOTP: {secret[:4]}***")

    if not all([api_key, client, password, secret]):
        connection["status"] = "disconnected"
        connection["error"]  = "Missing credentials in .env"
        LOG_LINES.append(f"[ERROR] [{_ts()}] AngelOne: missing credentials in .env")
        return

    connection["status"] = "reconnecting"
    connection["error"]  = None

    try:
        totp_val = pyotp.TOTP(secret).now()
        connection["totp_ok"] = True

        obj  = SmartConnect(api_key=api_key)
        data = obj.generateSession(client, password, totp_val)

        if data.get("status"):
            d = data["data"]
            angel_obj = obj
            connection["status"]        = "connected"
            connection["name"]          = d.get("name", "—").title()
            connection["client_id"]     = d.get("clientcode", client)
            connection["connected_at"]  = _ts()
            connection["last_ping"]     = _ts()
            connection["ping_ms"]       = 0
            connection["session_valid"] = True
            connection["exchanges"]     = d.get("exchanges", [])
            connection["error"]         = None
            LOG_LINES.append(f"[INFO]  [{_ts()}] AngelOne connected — {connection['name']} ({client})")
            _fetch_margin()
            _update_checks()
            # Resolve tokens for any position restored from disk
            pos = state.get("position_detail")
            if pos and pos.get("ce_token") is None and pos.get("ce_symbol"):
                try:
                    pos["ce_token"] = _get_nfo_token(pos["ce_symbol"])
                    pos["pe_token"] = _get_nfo_token(pos["pe_symbol"])
                    LOG_LINES.append(f"[INFO]  [{_ts()}] Tokens resolved for restored position: CE={pos['ce_token']} PE={pos['pe_token']}")
                except Exception as _te:
                    LOG_LINES.append(f"[WARN]  [{_ts()}] Could not resolve tokens for restored position: {_te}")
        else:
            connection["status"] = "disconnected"
            connection["error"]  = data.get("message", "Login failed")
            LOG_LINES.append(f"[ERROR] [{_ts()}] AngelOne login failed: {connection['error']}")

    except Exception as e:
        connection["status"] = "disconnected"
        connection["error"]  = str(e)
        LOG_LINES.append(f"[ERROR] [{_ts()}] AngelOne exception: {e}")


def _fetch_margin():
    """Fetch real margin from AngelOne. Tries all known field name variants."""
    if not angel_obj:
        return
    try:
        r = angel_obj.rmsLimit()
        if r and r.get("status"):
            d = r["data"]
            # AngelOne field names vary across SDK versions — try all known variants
            avail = (
                _to_float(d.get("availablecash")) or
                _to_float(d.get("net"))            or
                _to_float(d.get("availableBalance")) or
                _to_float(d.get("cashmarginavailable")) or
                0.0
            )
            used = (
                _to_float(d.get("utiliseddebits"))  or
                _to_float(d.get("utilisedAmount"))   or
                _to_float(d.get("usedmargin"))       or
                _to_float(d.get("debits"))           or
                0.0
            )
            state["funds"]["available_cash"] = avail
            state["funds"]["used_margin"]    = used
            connection["available_margin"]   = avail
            connection["used_margin"]        = used
            if avail == 0:
                if _is_market_open():
                    # Check if manual override is set in config
                    if config.get("margin_override", 0) > 0:
                        avail = float(config["margin_override"])
                        state["funds"]["available_cash"] = avail
                        connection["available_margin"]   = avail
                        LOG_LINES.append(f"[INFO]  [{_ts()}] Margin override active: {avail:,.0f}")
                    elif config.get("paper_trade"):
                        avail = float(config.get("capital", 0))
                        state["funds"]["available_cash"] = avail
                        connection["available_margin"]   = avail
                        LOG_LINES.append(f"[INFO]  [{_ts()}] Paper mode margin fallback active: {avail:,.0f}")
                    else:
                        LOG_LINES.append(f"[WARN]  [{_ts()}] Margin=0 from API. Check AngelOne account funds or enable RMS API permission. Use margin_override in settings to bypass.")
            else:
                LOG_LINES.append(f"[INFO]  [{_ts()}] Margin: Available {avail:,.0f} | Used {used:,.0f}")
        else:
            LOG_LINES.append(f"[WARN]  [{_ts()}] rmsLimit status=false: {(r or {}).get('message','')}")
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Margin fetch error: {e}")


def _to_float(v):
    """Safely convert a value to float, returning 0.0 on failure."""
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


# ── Config ──
config = {
    "angel_api_key":     os.getenv("ANGEL_API_KEY", ""),
    "angel_client_id":   os.getenv("ANGEL_CLIENT_ID", ""),
    "angel_password":    os.getenv("ANGEL_PASSWORD", ""),
    "angel_totp_secret": os.getenv("ANGEL_TOTP_SECRET", ""),
    "telegram_token":    os.getenv("TELEGRAM_TOKEN", ""),
    "telegram_chat_id":  os.getenv("TELEGRAM_CHAT_ID", ""),
    "capital":           1500000,
    "base_lots":         3,
    "lot_size":          65,
    "min_premium":       40,
    "min_combined_premium": 150,   # combined CE+PE must be ≥ this for a valid entry
    "spot_momentum_limit":  0.005, # block entry if spot moved > 0.5% in last 15 min
    "profit_target_pct": 0.30,
    "sl_multiplier":     1.5,
    "trail_trigger_pct": 0.20,
    "paper_trade":       os.getenv("PAPER_TRADE", "false").lower() == "true",
    "margin_override":   1500000,  # app-side margin bypass for test mode; broker may still reject live orders
    "daily_loss_limit":  45000,
    "weekly_loss_limit": 60000,
    "max_trades_per_day": 3,
    "vix_min":           13,
    "vix_max":           28,
    "entry_start":       "09:30",
    "entry_end":         "11:00",
    "dead_zone_start":   "14:30",
    "expiry_cut_time":   "13:00",
    "execution_mode":    "AUTO",
}

# ── Agent state ──
state = {
    "regime":           None,
    "trades_today":     0,
    "daily_pnl":        0.0,
    "closed_pnl":       0.0,   # cumulative realized P&L for today
    "bot_running":      False,
    "lot_size":         65,          # updated daily from NSE
    "holidays_count":   0,          # number of F&O holidays loaded
    "active_position":  False,
    "squaring_off":     False,      # True = square-off in progress (prevents double exit)
    "execution_mode":   config["execution_mode"],   # "AUTO" or "MANUAL" from config
    "signal_pending":   False,      # True = signal ready, awaiting manual execute
    "pending_signal":   None,       # Full signal dict stored here for execution
    "last_signal": {
        "signal":       "WAIT",
        "confidence":   0,
        "reason":       "Agent not started",
        "setup_type":   "—",
        "ce_strike":    None,
        "pe_strike":    None,
        "premium":      None,
        "approx_entry": None,
        "approx_sl":    None,
    },
    "position_detail": None,
    "trade_history":   _load_trades_from_disk(),   # persisted across restarts
    "funds": {
        "available_cash": 0,
        "used_margin":    0,
    },
    "market": {
        "nifty_spot": None,
        "vix":        None,
        "pcr":        None,
        "iv_atm":     None,
        "iv_percentile": None,
        "atr_15m":    None,
        "net_delta":  None,
        "ema_trend_flat": None,
    },
    "checks": {
        "vix": False, "iv": None, "ema": None,
        "pcr": False, "event": False, "margin": False, "gate": False,
    },
    "button_states": {
        "approve_buy":    False,
        "emergency_exit": False,
        "stop_agent":     False,
        "start_agent":    False,
    },
    "mode": "LIVE",
    "dte":          None,   # Days-To-Expiry for current weekly expiry
    "expiry_date":  None,   # e.g. "10-Apr-2026"
}

# Auto-start agent on boot
state["bot_running"]                  = True
state["button_states"]["start_agent"] = False
state["button_states"]["stop_agent"]  = False
state["last_signal"]["reason"]        = "Agent running. Connecting to AngelOne..."

# ── Restore today's realized P&L from trades.json on startup ──
def _restore_daily_pnl():
    """Sum final_pnl of all closed trades from today so daily_pnl survives restarts."""
    trades = _load_trades_from_disk()
    today = datetime.now().date()
    total = 0.0
    count = 0
    for t in trades:
        exit_t = t.get("exit_time")
        pnl    = t.get("final_pnl")
        if not exit_t or pnl is None:
            continue
        try:
            # Handle both ISO format and custom format
            exit_dt = datetime.fromisoformat(exit_t) if "T" in str(exit_t) else datetime.strptime(str(exit_t).strip(), "%d-%m-%Y  %H:%M:%S")
            if exit_dt.date() == today:
                total += float(pnl)
                count += 1
        except Exception:
            continue
    if count:
        state["closed_pnl"] = round(total, 2)
        state["daily_pnl"]  = round(total, 2)
        LOG_LINES.append(f"[INFO]  [{_ts()}] Restored daily P&L from disk: ₹{total:,.0f} ({count} closed trade{'s' if count>1 else ''})")

# ── Restore open position from trades.json on startup ──
def _restore_open_position():
    """If trades.json has a trade with no exit_time, restore it as the active position."""
    trades = _load_trades_from_disk()
    today = datetime.now().date()
    for t in reversed(trades):
        if t.get("exit_time") is None and t.get("entry_time"):
            try:
                entry_dt = datetime.fromisoformat(t["entry_time"])
                if entry_dt.date() != today:
                    continue  # only restore today's open position
            except Exception:
                continue
            state["active_position"] = True
            state["trades_today"]   += 1
            state["position_detail"] = {
                "trade_id":         t.get("trade_id", ""),
                "entry_time":       t.get("entry_time", ""),
                "ce_strike":        t.get("ce_strike"),
                "pe_strike":        t.get("pe_strike"),
                "ce_symbol":        t.get("ce_symbol", ""),
                "pe_symbol":        t.get("pe_symbol", ""),
                "ce_token":         None,   # filled in after AngelOne login
                "pe_token":         None,
                "ce_entry_price":   t.get("ce_entry_ltp") or (t.get("premium_received") / 2 if t.get("premium_received") else None),
                "pe_entry_price":   t.get("pe_entry_ltp") or (t.get("premium_received") / 2 if t.get("premium_received") else None),
                "premium_received": t.get("premium_received"),
                "target":           t.get("target"),
                "sl":               t.get("stop_loss"),
                "initial_sl":       t.get("stop_loss"),
                "quantity":         t.get("quantity"),
                "setup_type":       t.get("setup_type", "Short Strangle"),
                "expiry":           t.get("expiry", ""),
                "pnl":              0,
                "exit_reason":      None,
                "trail_locked":     False,
            }
            state["last_signal"]["signal"] = "ACTIVE"
            LOG_LINES.append(f"[INFO]  [{_ts()}] Restored open position from disk: {t.get('ce_symbol')} / {t.get('pe_symbol')}")
            break

LOG_LINES = [
    f"[INFO]  [{_ts()}] FIFTO AI Trading server starting...",
    f"[INFO]  [{_ts()}] Agent auto-started in AUTO mode.",
    f"[INFO]  [{_ts()}] Connecting to AngelOne...",
]

_restore_daily_pnl()
_restore_open_position()

# ── Notification queue (consumed by dashboard poll) ──
_NOTIF = []

def _send_telegram(text):
    """Send plain-text message to Telegram."""
    token   = config.get("telegram_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        return
    try:
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=8
        )
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Telegram error: {e}")


def _notify(title, body, level="info"):
    """Push notification to dashboard + Telegram. Levels: info / success / warning / danger."""
    _NOTIF.append({"title": title, "body": body, "level": level, "ts": _ts()})
    if len(_NOTIF) > 30:
        del _NOTIF[:-30]
    _send_telegram(f"*FIFTO* — *{title}*\n{body}")


# ── Market timing helpers ──

def _is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return _time(9, 15) <= t <= _time(15, 30)


def _is_entry_window():
    t = datetime.now().time()
    try:
        h1, m1 = map(int, config["entry_start"].split(":"))
        h2, m2 = map(int, config["entry_end"].split(":"))
        return _time(h1, m1) <= t <= _time(h2, m2)
    except Exception:
        return False


# ── Setup checks ──

def _update_checks():
    vix   = state["market"]["vix"]
    avail = state["funds"]["available_cash"]
    pnl   = state["daily_pnl"]
    pcr   = state["market"]["pcr"]
    ivp   = state["market"].get("iv_percentile")
    emaf  = state["market"].get("ema_trend_flat")
    today = _date.today().isoformat()

    # Use live NSE holiday set; fall back to known dates if not yet loaded
    holidays = _nse_holidays or {"2026-03-31", "2026-04-14", "2026-08-15",
                                  "2026-10-02", "2026-11-04", "2026-11-05"}

    mkt_open = _is_market_open()
    state["checks"]["vix"]    = (vix is not None) and (config["vix_min"] <= vix <= config["vix_max"])
    state["checks"]["iv"]     = None if ivp is None else (ivp > 40)
    state["checks"]["ema"]    = emaf
    state["checks"]["pcr"]    = None if pcr is None else (0.8 <= pcr <= 1.4)
    state["checks"]["event"]  = today not in holidays
    # Margin: True/False during market hours; None (waiting) before market opens
    state["checks"]["margin"] = (avail > 0) if (mkt_open or avail > 0) else None
    state["checks"]["gate"]   = pnl > -config["daily_loss_limit"]


def _spot_stable():
    """Return True if NIFTY spot has NOT moved more than spot_momentum_limit% in the last 15 minutes."""
    if len(_spot_history) < 2:
        return True   # not enough data — don't block
    cutoff = datetime.now() - timedelta(minutes=15)
    recent = [p for ts, p in _spot_history if ts >= cutoff]
    if len(recent) < 2:
        return True
    change_pct = abs(recent[-1] - recent[0]) / recent[0]
    limit = config.get("spot_momentum_limit", 0.005)
    if change_pct > limit:
        LOG_LINES.append(
            f"[INFO]  [{_ts()}] Spot momentum block: {change_pct*100:.2f}% move in 15 min (limit {limit*100:.1f}%)"
        )
        return False
    return True


def _all_checks_pass():
    """All mandatory checks must be True. Optional checks (pcr/iv/ema) pass when None (data unavailable)."""
    c = state["checks"]
    # Mandatory: VIX range, not a holiday, margin available, daily loss gate
    mandatory = (
        c.get("vix")    is True and
        c.get("event")  is True and
        c.get("margin") is True and
        c.get("gate")   is True
    )
    if not mandatory:
        return False
    # Mandatory: spot must not be in sharp momentum
    if not _spot_stable():
        return False
    # Optional: PCR / IV / EMA — if data is available it must pass; None = skip
    for key in ("pcr", "iv", "ema"):
        val = c.get(key)
        if val is False:   # explicitly failed (not just missing)
            return False
    return True


# ── NSE session + caches ──

_nse_session   = None
_nse_lock      = threading.Lock()
_chain_cache   = {"data": None, "ts": 0}
_nse_holidays  = set()          # populated daily from NSE API
_nifty_lotsize = 75             # updated daily from NSE CSV
_iv_history    = {"date": None, "values": []}
_angel_contract_cache = {"rows": [], "ts": 0}
_candle_cache = {}
_candle_backoff = {}
_spot_history = []

_NSE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}


def _get_nse_session():
    """Return (or create) a shared NSE requests session with cookies. Thread-safe."""
    global _nse_session
    with _nse_lock:
        if _nse_session is None:
            sess = _requests.Session()
            sess.headers.update(_NSE_HEADERS)
            try:
                sess.get("https://www.nseindia.com", timeout=12)
                sess.get("https://www.nseindia.com/option-chain", timeout=10)
            except Exception:
                pass
            _nse_session = sess
    return _nse_session


def _fetch_nifty_lot_size():
    """Fetch NIFTY current lot size.
    Primary  : NSE fo_mktlots.csv
    Fallback : AngelOne instrument master JSON (filtered by name)
    """
    global _nifty_lotsize, _nse_session

    # ── Primary: NSE market lots CSV ──
    try:
        sess = _get_nse_session()
        r = sess.get(
            "https://www.nseindia.com/content/fo/fo_mktlots.csv",
            headers={"Accept": "text/plain,text/csv,*/*"},
            timeout=15
        )
        if r.status_code == 200 and r.text.strip():
            for line in r.text.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2 and parts[0].upper() == "NIFTY":
                    try:
                        lot = int(parts[1])
                        if lot > 0:
                            _nifty_lotsize     = lot
                            config["lot_size"] = lot
                            state["lot_size"]  = lot
                            LOG_LINES.append(f"[INFO]  [{_ts()}] NIFTY lot size → {lot} (NSE CSV)")
                            return lot
                    except ValueError:
                        continue
        else:
            with _nse_lock: _nse_session = None
    except Exception as e:
        with _nse_lock: _nse_session = None
        LOG_LINES.append(f"[WARN]  [{_ts()}] NSE lot size CSV error: {e}")

    # ── Fallback: AngelOne instrument master (small filtered fetch) ──
    try:
        r2 = _requests.get(
            "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
            timeout=30, stream=True
        )
        if r2.status_code == 200:
            import json as _json
            instruments = _json.loads(r2.content)
            for inst in instruments:
                if (inst.get("exch_seg") == "NFO" and
                        inst.get("name", "").upper() == "NIFTY" and
                        inst.get("instrumenttype") == "OPTIDX" and
                        inst.get("lotsize")):
                    lot = int(inst["lotsize"])
                    if lot > 0:
                        _nifty_lotsize     = lot
                        config["lot_size"] = lot
                        state["lot_size"]  = lot
                        LOG_LINES.append(f"[INFO]  [{_ts()}] NIFTY lot size → {lot} (AngelOne master)")
                        return lot
    except Exception as e2:
        LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne instrument master error: {e2}")

    lot = config.get("lot_size", 75)
    state["lot_size"] = lot
    LOG_LINES.append(f"[INFO]  [{_ts()}] NIFTY lot size using config value: {lot}")
    return lot


def _fetch_nse_holidays():
    """Fetch NSE F&O trading holidays for the current year."""
    global _nse_holidays, _nse_session
    try:
        sess = _get_nse_session()
        r = sess.get(
            "https://www.nseindia.com/api/holiday-master?type=trading",
            headers={"Accept": "application/json, text/plain, */*",
                     "X-Requested-With": "XMLHttpRequest"},
            timeout=12
        )
        if r.status_code == 200 and r.text.strip():
            data  = r.json()
            dates = set()
            for h in data.get("FO", []):   # F&O segment holidays
                raw = h.get("tradingDate", "")
                for fmt in ("%d-%b-%Y", "%d %b %Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(raw.strip(), fmt)
                        dates.add(dt.date().isoformat())
                        break
                    except ValueError:
                        continue
            if dates:
                _nse_holidays = dates
                state["holidays_count"] = len(dates)
                LOG_LINES.append(f"[INFO]  [{_ts()}] F&O holidays loaded: {len(dates)} dates")
                return dates
            else:
                LOG_LINES.append(f"[WARN]  [{_ts()}] Holiday API returned empty FO list")
        else:
            with _nse_lock: _nse_session = None
            LOG_LINES.append(f"[WARN]  [{_ts()}] Holiday API HTTP {r.status_code}")
    except Exception as e:
        with _nse_lock: _nse_session = None
        LOG_LINES.append(f"[WARN]  [{_ts()}] Holiday fetch failed: {e}")
    return _nse_holidays   # return cached set on failure


def _fetch_option_chain():
    """Fetch and cache a normalized NIFTY option chain using AngelOne APIs."""
    now = time.time()
    if _chain_cache["data"] and now - _chain_cache["ts"] < 60:
        return _chain_cache["data"]
    if not angel_obj:
        return None

    def _ensure_angel_route(route_name, route_path):
        try:
            if hasattr(angel_obj, "_routes") and route_name not in angel_obj._routes:
                angel_obj._routes[route_name] = route_path
        except Exception:
            pass

    def _angel_post(route_name, route_path, params):
        _ensure_angel_route(route_name, route_path)
        try:
            if hasattr(angel_obj, "_postRequest"):
                return angel_obj._postRequest(route_name, params)
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne POST {route_name} failed: {e}")
        return None

    def _angel_get(route_name, route_path):
        _ensure_angel_route(route_name, route_path)
        try:
            if hasattr(angel_obj, "_getRequest"):
                return angel_obj._getRequest(route_name)
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne GET {route_name} failed: {e}")
        return None

    def _pick(item, *keys):
        for key in keys:
            if key in item and item.get(key) not in (None, ""):
                return item.get(key)
        return None

    def _extract_quote_rows(resp):
        data = (resp or {}).get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("fetched", "quotes", "data", "list"):
                rows = data.get(key)
                if isinstance(rows, list):
                    return rows
            nested = data.get("fetched")
            if isinstance(nested, dict):
                for rows in nested.values():
                    if isinstance(rows, list):
                        return rows
        return []

    try:
        expiry_dt = _find_live_nifty_expiry()
        expiry_api = expiry_dt.strftime("%d%b%Y").upper()
        expiry_code = expiry_dt.strftime("%d%b%y").upper()
        expiry_label = expiry_dt.strftime("%d-%b-%Y")
        spot = state["market"].get("nifty_spot")
        atm = round(float(spot) / 50.0) * 50 if spot else None

        contracts = [r for r in _fetch_nifty_option_contracts() if r["expiry_code"] == expiry_code]
        if not contracts:
            LOG_LINES.append(f"[WARN]  [{_ts()}] No AngelOne contracts found for expiry {expiry_code}")
            return None
        if atm is not None:
            contracts = [r for r in contracts if abs(r["strike"] - atm) <= 500]
        if not contracts:
            return None

        pcr_val = None
        _now = datetime.now().time()
        _market_open = _time(9, 15)
        _market_close = _time(15, 30)
        if _market_open <= _now <= _market_close:
            pcr_resp = _angel_get(
                "api.putCallRatio",
                "/rest/secure/angelbroking/marketData/v1/putCallRatio"
            ) or {}
        else:
            pcr_resp = {}
        if pcr_resp and pcr_resp.get("status"):
            for row in pcr_resp.get("data") or []:
                tsym = str(row.get("tradingSymbol", "")).upper()
                if tsym.startswith("NIFTY") and "FUT" in tsym:
                    pcr_val = _to_float(row.get("pcr"))
                    if pcr_val:
                        break

        tokens = [c["symboltoken"] for c in contracts if c["symboltoken"]]
        quote_rows = []
        if tokens and hasattr(angel_obj, "getMarketData"):
            try:
                quote_resp = angel_obj.getMarketData("FULL", {"NFO": tokens})
                quote_rows = _extract_quote_rows(quote_resp)
            except Exception as e:
                LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne market quote fetch failed: {e}")
        quote_map = {}
        for row in quote_rows:
            token = str(_pick(row, "symbolToken", "symboltoken", "token") or "")
            tsym = str(_pick(row, "tradingSymbol", "tradingsymbol") or "").upper()
            if token:
                quote_map[token] = row
            if tsym:
                quote_map[tsym] = row

        records_map = {}
        t_years = _time_to_expiry_years(expiry_label)
        for item in contracts:
            strike = item["strike"]
            opt_type = item["option_type"]
            tsym = item["tradingsymbol"]
            token = item["symboltoken"]
            q = quote_map.get(token) or quote_map.get(tsym) or {}
            ltp = _to_float(_pick(q, "ltp", "lastPrice", "lastprice", "close")) or None
            oi = _to_float(_pick(q, "openInterest", "opnInterest", "openinterest", "oi"))
            iv = _to_float(_pick(q, "impliedVolatility", "iv", "impliedvolatility")) or None
            delta = _to_float(_pick(q, "delta")) or None
            if iv is None and ltp and spot and t_years:
                iv = _implied_volatility_from_price(float(spot), float(strike), float(ltp), t_years, opt_type == "CE")
            if delta is None and iv is not None and spot and t_years:
                delta = _black_scholes_delta(float(spot), float(strike), iv, t_years, opt_type == "CE")
            rec = records_map.setdefault(int(strike), {
                "expiryDate": expiry_label,
                "strikePrice": int(strike),
            })
            rec[opt_type] = {
                "lastPrice": ltp,
                "impliedVolatility": iv,
                "openInterest": oi,
                "delta": delta,
                "tradingsymbol": tsym,
                "symboltoken": token,
            }

        data = {
            "source": "ANGELONE",
            "pcr": pcr_val,
            "records": {
                "expiryDates": [expiry_label],
                "data": list(sorted(records_map.values(), key=lambda r: r["strikePrice"]))
            },
            "filtered": {
                "data": list(sorted(records_map.values(), key=lambda r: r["strikePrice"]))
            }
        }
        if data["records"]["data"]:
            _chain_cache["data"] = data
            _chain_cache["ts"] = now
            return data
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne option chain error: {e}")
    return None


def _fetch_pcr():
    """Compute PCR from cached option chain."""
    data = _fetch_option_chain()
    if not data:
        return None
    if data.get("pcr") is not None:
        return round(float(data["pcr"]), 3)
    records     = data.get("filtered", {}).get("data", [])
    total_ce_oi = sum(rec.get("CE", {}).get("openInterest", 0) for rec in records if rec.get("CE"))
    total_pe_oi = sum(rec.get("PE", {}).get("openInterest", 0) for rec in records if rec.get("PE"))
    if total_ce_oi > 0:
        return round(total_pe_oi / total_ce_oi, 3)
    return None


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black_scholes_delta(spot, strike, iv_pct, t_years, is_call):
    """Approximate option delta using Black-Scholes with zero rates."""
    if not all(v and v > 0 for v in (spot, strike, iv_pct, t_years)):
        return None
    sigma = float(iv_pct) / 100.0
    try:
        d1 = (math.log(float(spot) / float(strike)) + 0.5 * sigma * sigma * t_years) / (sigma * math.sqrt(t_years))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    call_delta = _norm_cdf(d1)
    return call_delta if is_call else (call_delta - 1.0)


def _black_scholes_price(spot, strike, iv_pct, t_years, is_call):
    if not all(v and v > 0 for v in (spot, strike, iv_pct, t_years)):
        return None
    sigma = float(iv_pct) / 100.0
    try:
        d1 = (math.log(float(spot) / float(strike)) + 0.5 * sigma * sigma * t_years) / (sigma * math.sqrt(t_years))
        d2 = d1 - sigma * math.sqrt(t_years)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if is_call:
        return (spot * _norm_cdf(d1)) - (strike * _norm_cdf(d2))
    return (strike * _norm_cdf(-d2)) - (spot * _norm_cdf(-d1))


def _implied_volatility_from_price(spot, strike, option_price, t_years, is_call):
    if not all(v and v > 0 for v in (spot, strike, option_price, t_years)):
        return None
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    target = max(float(option_price), intrinsic + 0.01)
    low, high = 1.0, 300.0
    for _ in range(40):
        mid = (low + high) / 2.0
        px = _black_scholes_price(spot, strike, mid, t_years, is_call)
        if px is None:
            return None
        if px > target:
            high = mid
        else:
            low = mid
    return round((low + high) / 2.0, 2)


def _parse_expiry(expiry_str):
    for fmt in ("%d-%b-%Y", "%d%b%y", "%d-%b-%y"):
        try:
            return datetime.strptime(expiry_str, fmt)
        except Exception:
            pass
    return None


def _time_to_expiry_years(expiry_str):
    dt = _parse_expiry(expiry_str)
    if not dt:
        return None
    expiry_dt = dt.replace(hour=15, minute=30, second=0, microsecond=0)
    secs = max((expiry_dt - datetime.now()).total_seconds(), 3600)
    return secs / (365.0 * 24.0 * 3600.0)


def _record_iv_sample(iv_atm):
    if iv_atm is None:
        return None
    today = _date.today()
    if _iv_history["date"] != today:
        _iv_history["date"] = today
        _iv_history["values"] = []
    vals = _iv_history["values"]
    vals.append(float(iv_atm))
    if len(vals) > 240:
        del vals[:-240]
    if len(vals) == 1:
        return 50.0
    below = sum(1 for v in vals if v <= iv_atm)
    return round((below / len(vals)) * 100.0, 1)


def _calc_atr(candles, period=14):
    if not candles or len(candles) < period + 1:
        if not candles or len(candles) < 2:
            return None
    trs = []
    prev_close = None
    for row in candles:
        try:
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
        except Exception:
            continue
        tr = (high - low) if prev_close is None else max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    if not trs:
        return None
    use_period = min(period, len(trs))
    return round(sum(trs[-use_period:]) / use_period, 2)


def _ema_series(values, period):
    if len(values) < period:
        return []
    alpha = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    out = [ema]
    for val in values[period:]:
        ema = (val * alpha) + (ema * (1.0 - alpha))
        out.append(ema)
    return out


def _fetch_nifty_candles(interval="FIFTEEN_MINUTE", lookback_days=5):
    if not angel_obj:
        return []
    cache_key = (interval, lookback_days)
    cache_ttl = 900 if interval == "FIFTEEN_MINUTE" else 180
    now_ts = time.time()
    cached = _candle_cache.get(cache_key)
    if cached and now_ts - cached["ts"] < cache_ttl:
        return cached["data"]
    next_try = _candle_backoff.get(cache_key, 0)
    if now_ts < next_try:
        return cached["data"] if cached else []
    now = datetime.now()
    params = {
        "exchange": "NSE",
        "symboltoken": "26000",
        "interval": interval,
        "fromdate": (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M"),
        "todate": now.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        resp = angel_obj.getCandleData(params)
        if resp and resp.get("status") and resp.get("data"):
            _candle_backoff.pop(cache_key, None)
            _candle_cache[cache_key] = {"ts": now_ts, "data": resp["data"]}
            return resp["data"]
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Candle fetch error ({interval}): {e}")
        _candle_backoff[cache_key] = now_ts + cache_ttl
    if cached:
        return cached["data"]
    return []


def _synthetic_candles_from_spot(interval_minutes=15):
    """Build approximate OHLC candles from live NIFTY spot samples when broker candles are unavailable."""
    if len(_spot_history) < 4:
        return []
    bucket_secs = interval_minutes * 60
    buckets = {}
    for ts, price in _spot_history:
        epoch = int(ts.timestamp())
        bucket = epoch - (epoch % bucket_secs)
        buckets.setdefault(bucket, []).append(float(price))
    rows = []
    for bucket in sorted(buckets):
        vals = buckets[bucket]
        if not vals:
            continue
        dt = datetime.fromtimestamp(bucket).strftime("%Y-%m-%d %H:%M")
        rows.append([dt, vals[0], max(vals), min(vals), vals[-1], 0])
    return rows


def _compute_ema_trend_flat(candles, atr_15m):
    if not candles or atr_15m in (None, 0):
        return None
    closes = []
    for row in candles:
        try:
            closes.append(float(row[4]))
        except Exception:
            continue
    if len(closes) < 4:
        if len(closes) < 2:
            return None
        return abs(closes[-1] - closes[0]) <= atr_15m * 0.50
    fast_period = 9 if len(closes) >= 9 else max(3, len(closes) // 2)
    slow_period = 21 if len(closes) >= 21 else min(len(closes), max(fast_period + 1, 4))
    ema9 = _ema_series(closes, fast_period)
    ema21 = _ema_series(closes, slow_period)
    if len(ema9) < 2 or len(ema21) < 2:
        return None
    ema9_now, ema9_prev = ema9[-1], ema9[-2]
    ema21_now, ema21_prev = ema21[-1], ema21[-2]
    spread = abs(ema9_now - ema21_now)
    slope_gap = abs((ema9_now - ema9_prev) - (ema21_now - ema21_prev))
    return (spread <= atr_15m * 0.20) and (slope_gap <= atr_15m * 0.08)


def _compute_option_metrics(spot):
    data = _fetch_option_chain()
    empty = {"pcr": None, "iv_atm": None, "iv_percentile": None, "net_delta": None}
    if not data or not spot:
        return empty

    records = data.get("records", {}).get("data", [])
    expiry_dates = data.get("records", {}).get("expiryDates", [])
    if not records or not expiry_dates:
        return empty

    nearest_expiry = expiry_dates[0]
    recs = [r for r in records if r.get("expiryDate") == nearest_expiry]
    if not recs:
        return empty

    total_ce_oi = sum(rec.get("CE", {}).get("openInterest", 0) for rec in recs if rec.get("CE"))
    total_pe_oi = sum(rec.get("PE", {}).get("openInterest", 0) for rec in recs if rec.get("PE"))
    pcr = data.get("pcr")
    if pcr is None:
        pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else None

    atm = round(float(spot) / 50.0) * 50
    atm_rec = min(recs, key=lambda r: abs(float(r.get("strikePrice", 0)) - atm))
    ce_iv = atm_rec.get("CE", {}).get("impliedVolatility")
    pe_iv = atm_rec.get("PE", {}).get("impliedVolatility")
    iv_vals = [float(v) for v in (ce_iv, pe_iv) if v not in (None, "", 0)]
    iv_atm = round(sum(iv_vals) / len(iv_vals), 2) if iv_vals else None
    iv_percentile = _record_iv_sample(iv_atm) if iv_atm is not None else None

    t_years = _time_to_expiry_years(nearest_expiry)
    exposure_sum = 0.0
    exposure_abs = 0.0
    for rec in recs:
        strike = float(rec.get("strikePrice", 0))
        if abs(strike - atm) > 250:
            continue
        ce = rec.get("CE") or {}
        pe = rec.get("PE") or {}
        ce_delta = ce.get("delta")
        pe_delta = pe.get("delta")
        if ce_delta in (None, ""):
            ce_delta = _black_scholes_delta(spot, strike, ce.get("impliedVolatility"), t_years, True)
        if pe_delta in (None, ""):
            pe_delta = _black_scholes_delta(spot, strike, pe.get("impliedVolatility"), t_years, False)
        ce_oi = float(ce.get("openInterest") or 0)
        pe_oi = float(pe.get("openInterest") or 0)
        if ce_delta is not None and ce_oi > 0:
            val = ce_delta * ce_oi
            exposure_sum += val
            exposure_abs += abs(val)
        elif ce_delta is not None:
            exposure_sum += ce_delta
            exposure_abs += abs(ce_delta)
        if pe_delta is not None and pe_oi > 0:
            val = pe_delta * pe_oi
            exposure_sum += val
            exposure_abs += abs(val)
        elif pe_delta is not None:
            exposure_sum += pe_delta
            exposure_abs += abs(pe_delta)
    net_delta = round(exposure_sum / exposure_abs, 3) if exposure_abs > 0 else None

    return {
        "pcr": pcr,
        "iv_atm": iv_atm,
        "iv_percentile": iv_percentile,
        "net_delta": net_delta,
    }


def _refresh_market_metrics():
    metrics = _compute_option_metrics(state["market"].get("nifty_spot"))
    candles_15m = _fetch_nifty_candles("FIFTEEN_MINUTE", 5)
    if not candles_15m:
        candles_15m = _synthetic_candles_from_spot(15)
    if len(candles_15m) < 2:
        candles_15m = _synthetic_candles_from_spot(1)
    atr_15m = _calc_atr(candles_15m, 14)
    ema_flat = _compute_ema_trend_flat(candles_15m, atr_15m)

    state["market"]["pcr"] = metrics["pcr"]
    state["market"]["iv_atm"] = metrics["iv_atm"]
    state["market"]["iv_percentile"] = metrics["iv_percentile"]
    state["market"]["atr_15m"] = atr_15m
    state["market"]["net_delta"] = metrics["net_delta"]
    state["market"]["ema_trend_flat"] = ema_flat


# ── Strike selection ──

def _next_thursday():
    """Return the nearest NIFTY weekly expiry (Thursday). If today is Thursday and
    market is still open, return today; otherwise next Thursday."""
    today = _date.today()
    days_ahead = (3 - today.weekday()) % 7   # Thursday = weekday 3
    if days_ahead == 0 and datetime.now().time() >= _time(15, 30):
        days_ahead = 7   # today's expiry has passed, use next week
    return today + timedelta(days=days_ahead)


def _candidate_expiries(weeks=4):
    """Return upcoming Tuesday/Thursday expiries, nearest first, to handle holiday shifts."""
    today = _date.today()
    now_t = datetime.now().time()
    out = []
    for day_offset in range(0, weeks * 7 + 7):
        dt = today + timedelta(days=day_offset)
        if dt.weekday() not in (1, 3):   # Tuesday / Thursday
            continue
        if dt == today and now_t >= _time(15, 30):
            continue
        out.append(dt)
    return out


def _fetch_nifty_option_contracts():
    """Cache AngelOne NIFTY option contracts discovered via searchScrip."""
    now = time.time()
    if _angel_contract_cache["rows"] and now - _angel_contract_cache["ts"] < 1800:
        return _angel_contract_cache["rows"]
    if not angel_obj or not hasattr(angel_obj, "searchScrip"):
        return []
    try:
        resp = angel_obj.searchScrip("NFO", "NIFTY")
        rows = (resp or {}).get("data") or []
        out = []
        import re
        pat = re.compile(r"^NIFTY(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$")
        for row in rows:
            tsym = str(row.get("tradingsymbol") or "").upper()
            m = pat.match(tsym)
            if not m:
                continue
            expiry_code, strike, opt_type = m.groups()
            expiry_dt = _parse_expiry(expiry_code)
            if not expiry_dt:
                continue
            out.append({
                "tradingsymbol": tsym,
                "symboltoken": str(row.get("symboltoken") or ""),
                "expiry_code": expiry_code,
                "expiry_dt": expiry_dt,
                "strike": int(strike),
                "option_type": opt_type,
            })
        out.sort(key=lambda r: (r["expiry_dt"], r["strike"], r["option_type"]))
        _angel_contract_cache["rows"] = out
        _angel_contract_cache["ts"] = now
        return out
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne contract search failed: {e}")
        return _angel_contract_cache["rows"]


def _find_live_nifty_expiry():
    """Find the nearest live expiry from AngelOne contract discovery."""
    rows = _fetch_nifty_option_contracts()
    if rows:
        today = datetime.now()
        future_expiries = sorted({r["expiry_dt"] for r in rows if r["expiry_dt"] >= today.replace(hour=0, minute=0, second=0, microsecond=0)})
        if future_expiries:
            return future_expiries[0]
    return _next_thursday()


def _select_strikes_angelone(spot):
    """Fallback: select strikes directly via AngelOne LTP (no NSE session needed).
    Finds nearest Thursday expiry, walks ATM±50 until premium ≥ min_premium."""
    if not angel_obj:
        return None

    expiry_date = _find_live_nifty_expiry()
    expiry_code = expiry_date.strftime("%d%b%y").upper()   # e.g. 27MAR26
    expiry_str  = expiry_date.strftime("%d-%b-%Y")          # e.g. 27-Mar-2026
    atm         = round(spot / 50) * 50
    min_premium = config.get("min_premium", 40)

    def _ltp_for(symbol):
        try:
            result = angel_obj.searchScrip("NFO", symbol)
            if not (result and result.get("status")):
                return None, None
            for item in result.get("data", []):
                if item.get("tradingsymbol") == symbol:
                    token = item.get("symboltoken")
                    r = angel_obj.ltpData("NFO", symbol, token)
                    if r.get("status"):
                        return r["data"]["ltp"], token
        except Exception:
            pass
        return None, None

    ce_strike = ce_ltp = ce_token = None
    for offset in range(50, 500, 50):
        s   = int(atm + offset)
        sym = f"NIFTY{expiry_code}{s}CE"
        ltp, tok = _ltp_for(sym)
        if ltp is not None and ltp >= min_premium:
            ce_strike, ce_ltp, ce_token = s, ltp, tok
            break

    pe_strike = pe_ltp = pe_token = None
    for offset in range(50, 500, 50):
        s   = int(atm - offset)
        sym = f"NIFTY{expiry_code}{s}PE"
        ltp, tok = _ltp_for(sym)
        if ltp is not None and ltp >= min_premium:
            pe_strike, pe_ltp, pe_token = s, ltp, tok
            break

    if not ce_strike or not pe_strike:
        return None

    combined = round(ce_ltp + pe_ltp, 2)
    min_combined = config.get("min_combined_premium", 150)
    if combined < min_combined:
        LOG_LINES.append(f"[INFO]  [{_ts()}] AngelOne strike scan: combined premium ₹{combined} < min ₹{min_combined}, skipping")
        return None

    LOG_LINES.append(f"[INFO]  [{_ts()}] Strikes via AngelOne: CE {ce_strike} @ ₹{ce_ltp:.0f} | PE {pe_strike} @ ₹{pe_ltp:.0f}")
    return {
        "expiry":    expiry_str,
        "atm":       atm,
        "ce_strike": ce_strike,
        "pe_strike": pe_strike,
        "ce_ltp":    ce_ltp,
        "pe_ltp":    pe_ltp,
        "ce_token":  ce_token,
        "pe_token":  pe_token,
        "premium":   combined,
    }


def _select_strikes(spot):
    """Select SHORT STRANGLE strikes.
    Primary: AngelOne option chain. Fallback: AngelOne direct LTP lookup."""
    data = _fetch_option_chain()
    if data:
        expiry_dates = data.get("records", {}).get("expiryDates", [])
        all_records  = data.get("records", {}).get("data", [])
        if expiry_dates and all_records:
            nearest_expiry = expiry_dates[0]
            recs        = [r for r in all_records if r.get("expiryDate") == nearest_expiry]
            atm         = round(spot / 50) * 50
            min_premium = config.get("min_premium", 40)

            ce_strike = ce_ltp = None
            for offset in range(50, 500, 50):
                s   = atm + offset
                rec = next((r for r in recs if r.get("strikePrice") == s and r.get("CE")), None)
                if rec:
                    ltp = rec["CE"].get("lastPrice", 0)
                    if not ltp:
                        sym = rec["CE"].get("tradingsymbol")
                        tok = rec["CE"].get("symboltoken")
                        if sym and tok:
                            q = angel_obj.ltpData("NFO", sym, tok)
                            if q.get("status"):
                                ltp = q["data"]["ltp"]
                    if ltp >= min_premium:
                        ce_strike, ce_ltp = s, ltp
                        break

            pe_strike = pe_ltp = None
            for offset in range(50, 500, 50):
                s   = atm - offset
                rec = next((r for r in recs if r.get("strikePrice") == s and r.get("PE")), None)
                if rec:
                    ltp = rec["PE"].get("lastPrice", 0)
                    if not ltp:
                        sym = rec["PE"].get("tradingsymbol")
                        tok = rec["PE"].get("symboltoken")
                        if sym and tok:
                            q = angel_obj.ltpData("NFO", sym, tok)
                            if q.get("status"):
                                ltp = q["data"]["ltp"]
                    if ltp >= min_premium:
                        pe_strike, pe_ltp = s, ltp
                        break

            if ce_strike and pe_strike:
                combined = round(ce_ltp + pe_ltp, 2)
                min_combined = config.get("min_combined_premium", 150)
                if combined < min_combined:
                    LOG_LINES.append(f"[INFO]  [{_ts()}] Strike scan: combined premium ₹{combined} < min ₹{min_combined}, skipping")
                    return None
                return {
                    "expiry":    nearest_expiry,
                    "atm":       atm,
                    "ce_strike": ce_strike,
                    "pe_strike": pe_strike,
                    "ce_ltp":    ce_ltp,
                    "pe_ltp":    pe_ltp,
                    "premium":   combined,
                }

    LOG_LINES.append(f"[INFO]  [{_ts()}] AngelOne option chain unavailable — using direct LTP lookup")
    return _select_strikes_angelone(spot)


# ── AngelOne order execution ──

def _build_angel_symbol(strike, option_type, expiry_str):
    """Convert NSE expiry ('26-Mar-2026') → AngelOne symbol ('NIFTY26MAR2624200CE')."""
    dt          = datetime.strptime(expiry_str, "%d-%b-%Y")
    expiry_code = dt.strftime("%d%b%y").upper()   # e.g. 26MAR26
    return f"NIFTY{expiry_code}{int(strike)}{option_type}"


def _get_nfo_token(symbol):
    """Get AngelOne NFO token for a given trading symbol."""
    if not angel_obj:
        return None
    # 1. Try direct searchScrip for exact symbol
    try:
        result = angel_obj.searchScrip("NFO", symbol)
        if result and result.get("status"):
            for item in result.get("data", []):
                if item.get("tradingsymbol") == symbol:
                    tok = item.get("symboltoken")
                    if tok:
                        return str(tok)
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Token lookup (direct) failed for {symbol}: {e}")
    # 2. Fall back to full NIFTY contract cache
    try:
        contracts = _fetch_nifty_option_contracts()
        for row in contracts:
            if row.get("tradingsymbol") == symbol:
                tok = row.get("symboltoken")
                if tok:
                    return str(tok)
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Token lookup (cache) failed for {symbol}: {e}")
    LOG_LINES.append(f"[WARN]  [{_ts()}] Token not found for {symbol}")
    return None


def _place_order(symbol, token, qty, txn_type="SELL"):
    """Place a MARKET order on NFO. Returns order_id string or None."""
    # Paper trade mode — simulate orders without touching the broker
    if config.get("paper_trade"):
        fake_id = f"PAPER-{int(time.time())}"
        LOG_LINES.append(f"[PAPER] [{_ts()}] [SIMULATED] {txn_type} {qty}x {symbol} → #{fake_id}")
        return fake_id

    if not angel_obj:
        LOG_LINES.append(f"[ERROR] [{_ts()}] Order skipped: AngelOne not connected")
        return None
    try:
        params = {
            "variety":         "NORMAL",
            "tradingsymbol":   symbol,
            "symboltoken":     token,
            "transactiontype": txn_type,
            "exchange":        "NFO",
            "ordertype":       "MARKET",
            "producttype":     "INTRADAY",
            "duration":        "DAY",
            "price":           "0",
            "quantity":        qty,
        }
        result = angel_obj.placeOrder(params)

        LOG_LINES.append(f"[DEBUG] [{_ts()}] placeOrder raw response ({type(result).__name__}): {str(result)[:200]}")

        # AngelOne SDK can return: dict, str (order ID), int (order ID), or list
        if isinstance(result, dict):
            if result.get("status"):
                oid = str(result.get("data", {}).get("orderid") or result.get("data") or result)
                LOG_LINES.append(f"[TRADE] [{_ts()}] Order OK: {txn_type} {qty}x {symbol} → #{oid}")
                return oid
            else:
                msg = result.get("message", str(result))
                LOG_LINES.append(f"[ERROR] [{_ts()}] Order FAILED: {symbol} — {msg}")
                return None
        elif isinstance(result, (str, int)):
            oid = str(result).strip()
            if oid and oid not in ("None", "0", ""):
                LOG_LINES.append(f"[TRADE] [{_ts()}] Order OK: {txn_type} {qty}x {symbol} → #{oid}")
                return oid
            else:
                LOG_LINES.append(f"[ERROR] [{_ts()}] Order FAILED: {symbol} — empty/zero order ID: {oid!r}")
                return None
        elif isinstance(result, list) and result:
            # Some SDK versions return a list with the order ID as first element
            oid = str(result[0]).strip()
            if oid and oid not in ("None", "0", ""):
                LOG_LINES.append(f"[TRADE] [{_ts()}] Order OK (list): {txn_type} {qty}x {symbol} → #{oid}")
                return oid
            else:
                LOG_LINES.append(f"[ERROR] [{_ts()}] Order FAILED: {symbol} — list response: {result}")
                return None
        else:
            LOG_LINES.append(f"[ERROR] [{_ts()}] Order FAILED: {symbol} — unexpected response: {result!r}")
    except Exception as e:
        LOG_LINES.append(f"[ERROR] [{_ts()}] Order exception ({symbol}): {e}")
    return None


def _calc_target_pct_by_dte(expiry_str):
    """Return tiered profit target % based on Days-To-Expiry.
    Expiry date comes directly from AngelOne contract discovery.

    DTE tiers (intraday theta-realistic):
      0 DTE (expiry day)  : 45% — massive theta, grab fast
      1 DTE (day before)  : 28% — good theta, achievable
      2 DTE               : 22% — moderate theta
      3+ DTE              : 18% — low theta, don't be greedy
    """
    try:
        expiry_dt = datetime.strptime(expiry_str, "%d-%b-%Y").date()
        today     = _date.today()
        dte       = (expiry_dt - today).days
        if dte <= 0:
            pct = 0.45   # expiry day
        elif dte == 1:
            pct = 0.28
        elif dte == 2:
            pct = 0.22
        else:
            pct = 0.18   # 3+ DTE
        LOG_LINES.append(
            f"[INFO]  [{_ts()}] DTE={dte} (expiry {expiry_str}) → Target set to {int(pct*100)}%"
        )
        return pct
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] DTE calc failed ({e}), using default 25%")
        return 0.25   # safe fallback


def _calc_sl_mult_by_dte(expiry_str):
    """Return DTE-tiered SL multiplier to maintain balanced risk-reward.

    DTE tiers:
      0 DTE (expiry day)  : 1.5x — wider ok, theta kills premium fast
      1 DTE               : 1.4x — moderate gamma risk
      2 DTE               : 1.3x — lower gamma, smaller loss if SL hit
      3+ DTE              : 1.25x— lowest gamma, keep loss small vs low target

    Risk-reward with paired targets:
      0 DTE : 45% target / 50% risk  → RR 0.9:1 (win rate ~53%)
      1 DTE : 28% target / 40% risk  → RR 0.7:1 (win rate ~59%)
      2 DTE : 22% target / 30% risk  → RR 0.73:1 (win rate ~58%)
      3+DTE : 18% target / 25% risk  → RR 0.72:1 (win rate ~58%)
    """
    try:
        expiry_dt = datetime.strptime(expiry_str, "%d-%b-%Y").date()
        today     = _date.today()
        dte       = (expiry_dt - today).days
        if dte <= 0:
            mult = 1.50
        elif dte == 1:
            mult = 1.40
        elif dte == 2:
            mult = 1.30
        else:
            mult = 1.25   # 3+ DTE
        LOG_LINES.append(
            f"[INFO]  [{_ts()}] DTE={dte} → SL multiplier set to {mult}x"
        )
        return mult
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] SL DTE calc failed ({e}), using default 1.5x")
        return 1.50   # safe fallback


def _execute_trade(signal):
    """Execute a SHORT STRANGLE — SELL CE + SELL PE via AngelOne."""
    lot_size = state.get("lot_size") or config.get("lot_size", 75)
    qty = config.get("base_lots", 1) * lot_size

    ce_symbol = signal["ce_symbol"]
    pe_symbol = signal["pe_symbol"]
    ce_token  = signal.get("ce_token") or _get_nfo_token(ce_symbol)
    pe_token  = signal.get("pe_token") or _get_nfo_token(pe_symbol)

    if not ce_token or not pe_token:
        LOG_LINES.append(f"[ERROR] [{_ts()}] Execute failed: token not found. CE={ce_symbol} PE={pe_symbol}")
        state["signal_pending"] = False
        return False

    LOG_LINES.append(f"[TRADE] [{_ts()}] Executing SHORT STRANGLE — Qty {qty}")
    LOG_LINES.append(f"[TRADE] [{_ts()}] SELL {ce_symbol} @ ₹{signal['ce_ltp']:.2f}")
    LOG_LINES.append(f"[TRADE] [{_ts()}] SELL {pe_symbol} @ ₹{signal['pe_ltp']:.2f}")

    ce_oid = _place_order(ce_symbol, ce_token, qty, "SELL")
    pe_oid = _place_order(pe_symbol, pe_token, qty, "SELL")

    # Partial fill recovery — CE filled but PE failed → buy back CE immediately
    if ce_oid and not pe_oid:
        LOG_LINES.append(f"[ERROR] [{_ts()}] PE leg failed! Buying back CE to avoid naked short...")
        _place_order(ce_symbol, ce_token, qty, "BUY")
        state["signal_pending"] = False
        return False

    if ce_oid and pe_oid:
        premium    = signal["ce_ltp"] + signal["pe_ltp"]
        target_pct = _calc_target_pct_by_dte(signal.get("expiry", ""))
        sl_mult    = _calc_sl_mult_by_dte(signal.get("expiry", ""))
        target     = round(premium * target_pct, 2)
        sl         = round(premium * sl_mult, 2)

        trade_id = f"T{int(time.time())}"
        entry_time = datetime.now()

        state["active_position"]  = True
        state["signal_pending"]   = False
        state["pending_signal"]   = None
        state["trades_today"]    += 1
        state["position_detail"]  = {
            "trade_id":         trade_id,
            "ce_strike":        signal["ce_strike"],
            "pe_strike":        signal["pe_strike"],
            "ce_symbol":        ce_symbol,
            "pe_symbol":        pe_symbol,
            "ce_token":         ce_token,
            "pe_token":         pe_token,
            "ce_entry_price":   signal["ce_ltp"],
            "pe_entry_price":   signal["pe_ltp"],
            "premium_received": premium,
            "target":           target,
            "sl":               sl,
            "quantity":         qty,
            "entry_time":       entry_time,
            "pnl":              0.0,
            "ce_order_id":      ce_oid,
            "pe_order_id":      pe_oid,
            "setup_type":       "Short Strangle",
            "expiry":           signal["expiry"],
            "target_pct":       target_pct,
            "sl_mult":          sl_mult,
            "initial_sl":       sl,   # preserved even if trailing SL tightens pos["sl"]
        }
        _persist_entry(state["position_detail"])
        state["last_signal"].update({
            "signal":     "ACTIVE",
            "confidence": 100,
            "reason":     f"Strangle active | ₹{premium:.0f} received | Target ₹{target:.0f} | SL ₹{sl:.0f}",
            "setup_type": "Short Strangle",
            "ce_strike":  signal["ce_strike"],
            "pe_strike":  signal["pe_strike"],
            "premium":    premium,
        })
        LOG_LINES.append(f"[TRADE] [{_ts()}] Strangle ON | Premium ₹{premium:.2f} | Target ₹{target:.2f} | SL ₹{sl:.2f}")
        _notify(
            "✅ Trade Executed — Short Strangle",
            f"CE {signal['ce_strike']} @ ₹{signal['ce_ltp']:.0f} | PE {signal['pe_strike']} @ ₹{signal['pe_ltp']:.0f}\n"
            f"Premium: ₹{premium:.0f} | Target: ₹{target:.0f} ({int(target_pct*100)}%) | SL: ₹{sl:.0f} ({sl_mult}x)\n"
            f"Qty: {qty} | Entry: {_ts()}",
            "success"
        )
        return True

    LOG_LINES.append(f"[ERROR] [{_ts()}] Strangle execution incomplete — check orders manually")
    state["signal_pending"] = False
    return False


def _place_test_live_atm_sell():
    """Place one live ATM CE sell order for manual connectivity verification."""
    if config.get("paper_trade"):
        return {"ok": False, "error": "Paper trade is enabled. Disable it before sending a live test order."}, 400
    if not angel_obj or connection.get("status") != "connected":
        return {"ok": False, "error": "AngelOne is not connected."}, 400

    spot = state["market"].get("nifty_spot")
    if not spot:
        return {"ok": False, "error": "NIFTY spot is unavailable right now."}, 400

    expiry_dt = _find_live_nifty_expiry()
    expiry_str = expiry_dt.strftime("%d-%b-%Y")
    strike = int(round(float(spot) / 50.0) * 50)
    symbol = _build_angel_symbol(strike, "CE", expiry_str)
    token = _get_nfo_token(symbol)
    if not token:
        return {"ok": False, "error": f"Could not resolve token for {symbol}."}, 400

    ltp = None
    try:
        q = angel_obj.ltpData("NFO", symbol, token)
        if q and q.get("status"):
            ltp = q["data"]["ltp"]
    except Exception:
        pass

    qty = 1
    order_id = _place_order(symbol, token, qty, "SELL")
    if not order_id:
        return {"ok": False, "error": f"Order placement failed for {symbol}."}, 500

    LOG_LINES.append(
        f"[TRADE] [{_ts()}] LIVE TEST ORDER | SELL {qty}x {symbol} | Spot {spot:.2f} | "
        f"LTP {ltp if ltp is not None else '—'} | Order #{order_id}"
    )
    _notify(
        "Live Test Order Sent",
        f"SELL {qty} x {symbol}\nSpot: ₹{spot:.2f} | Option LTP: ₹{ltp if ltp is not None else '—'}\n"
        f"Order ID: {order_id}\nThis is a manual test order. Manage/square off it from AngelOne or a dedicated exit flow.",
        "warning"
    )
    return {
        "ok": True,
        "symbol": symbol,
        "token": token,
        "qty": qty,
        "spot": round(float(spot), 2),
        "ltp": ltp,
        "order_id": order_id,
        "expiry": expiry_str,
        "strike": strike,
    }, 200


def _square_off_position():
    """Square off both legs with MARKET BUY and record trade history."""
    if not state["active_position"] or not state["position_detail"]:
        return
    if state["squaring_off"]:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Square-off already in progress — skipping duplicate call")
        return
    state["squaring_off"] = True
    pos = state["position_detail"]
    qty = pos["quantity"]
    exit_time = datetime.now()
    LOG_LINES.append(f"[TRADE] [{_ts()}] Squaring off position...")

    ce_oid = _place_order(pos["ce_symbol"], pos["ce_token"], qty, "BUY")
    pe_oid = _place_order(pos["pe_symbol"], pos["pe_token"], qty, "BUY")

    final_pnl = pos.get("pnl", 0)
    state["closed_pnl"] += final_pnl        # accumulate realized P&L for the day
    state["daily_pnl"]   = state["closed_pnl"]
    # trades_today already incremented at entry — do NOT increment again here

    # Record complete trade in history — reuse trade_id from entry
    trade_record = {
        "trade_id":        pos.get("trade_id", f"T{int(time.time())}"),
        "entry_time":      _fmt_ts(pos["entry_time"]),
        "exit_time":       _fmt_ts(exit_time),
        "setup_type":      pos.get("setup_type", "Short Strangle"),
        "ce_strike":       pos["ce_strike"],
        "pe_strike":       pos["pe_strike"],
        "ce_symbol":       pos["ce_symbol"],
        "pe_symbol":       pos["pe_symbol"],
        "quantity":        qty,
        "premium_received": pos["premium_received"],
        "target":          pos["target"],
        "stop_loss":       pos["sl"],
        "final_pnl":       final_pnl,
        "exit_reason":     pos.get("exit_reason", "UNKNOWN"),
        "expiry":          pos.get("expiry", ""),
    }
    # Upsert in memory (entry row may already exist)
    tid = trade_record["trade_id"]
    idx = next((i for i, t in enumerate(state["trade_history"]) if t.get("trade_id") == tid), None)
    if idx is not None:
        state["trade_history"][idx] = trade_record
    else:
        state["trade_history"].append(trade_record)
    _persist_trade(trade_record)
    
    LOG_LINES.append(f"[TRADE] [{_ts()}] Square-off complete | Realized ₹{final_pnl:,.0f} | Daily ₹{state['daily_pnl']:,.0f} | Trades today: {state['trades_today']}")
    _notify(
        "✅ Position Closed",
        f"CE {pos['ce_strike']} | PE {pos['pe_strike']}\n"
        f"P&L: ₹{final_pnl:,.0f}\n"
        f"Total trades today: {state['trades_today']}",
        "success" if final_pnl >= 0 else "danger"
    )

    state["active_position"] = False
    state["squaring_off"]    = False
    state["position_detail"] = None
    state["last_signal"]     = {
        "signal":       "WAIT",
        "confidence":   0,
        "reason":       "Position closed. Scanning for next setup.",
        "setup_type":   "—",
        "ce_strike":    None,
        "pe_strike":    None,
        "premium":      None,
        "approx_entry": None,
        "approx_sl":    None,
    }
    _update_checks()


# ── Signal engine ──

def signal_engine():
    """Generate signals every 5 minutes during entry window. Runs in background."""
    time.sleep(12)   # wait for login + market data
    last_signal_ts = time.time()  # enforce 5-min cooldown from startup

    while True:
        try:
            if (state["bot_running"] and
                    not state["active_position"] and
                    not state["signal_pending"] and
                    state["trades_today"] < config.get("max_trades_per_day", 3) and
                    _is_market_open() and
                    _is_entry_window() and
                    _all_checks_pass()):

                now = time.time()
                if now - last_signal_ts >= 300:   # max 1 signal per 5 min
                    spot   = state["market"]["nifty_spot"]
                    if spot:
                        strikes = _select_strikes(spot)
                        if strikes:
                            ce_sym   = _build_angel_symbol(strikes["ce_strike"], "CE", strikes["expiry"])
                            pe_sym   = _build_angel_symbol(strikes["pe_strike"], "PE", strikes["expiry"])
                            # Use pre-fetched tokens from AngelOne fallback if available
                            ce_token = strikes.get("ce_token") or _get_nfo_token(ce_sym)
                            pe_token = strikes.get("pe_token") or _get_nfo_token(pe_sym)

                            signal = {
                                "signal":       "SELL",
                                "confidence":   82,
                                "reason":       f"All checks pass | Premium ₹{strikes['premium']:.0f}",
                                "setup_type":   "Short Strangle",
                                "ce_strike":    strikes["ce_strike"],
                                "pe_strike":    strikes["pe_strike"],
                                "ce_ltp":       strikes["ce_ltp"],
                                "pe_ltp":       strikes["pe_ltp"],
                                "ce_symbol":    ce_sym,
                                "pe_symbol":    pe_sym,
                                "ce_token":     ce_token,
                                "pe_token":     pe_token,
                                "premium":      strikes["premium"],
                                "approx_entry": strikes["premium"],
                                "approx_sl":    round(strikes["premium"] * _calc_sl_mult_by_dte(strikes["expiry"]), 2),
                                "expiry":       strikes["expiry"],
                            }

                            last_signal_ts = now
                            state["pending_signal"] = signal
                            state["last_signal"].update({
                                "signal":       "SELL",
                                "confidence":   82,
                                "reason":       signal["reason"],
                                "setup_type":   "Short Strangle",
                                "ce_strike":    strikes["ce_strike"],
                                "pe_strike":    strikes["pe_strike"],
                                "premium":      strikes["premium"],
                                "approx_entry": strikes["premium"],
                                "approx_sl":    signal["approx_sl"],
                            })

                            if state["execution_mode"] == "AUTO":
                                LOG_LINES.append(f"[TRADE] [{_ts()}] Signal: SHORT STRANGLE | AUTO — executing now")
                                _notify(
                                    "📊 Signal — Auto Executing",
                                    f"SHORT STRANGLE | CE {strikes['ce_strike']} | PE {strikes['pe_strike']}\n"
                                    f"Premium ₹{strikes['premium']:.0f} | Placing orders now...",
                                    "info"
                                )
                                threading.Thread(target=_execute_trade, args=(signal,), daemon=True).start()
                            else:
                                state["signal_pending"] = True
                                LOG_LINES.append(f"[TRADE] [{_ts()}] Signal: SHORT STRANGLE | MANUAL — waiting for user")
                                _notify(
                                    "📊 Signal Ready — Action Required",
                                    f"SHORT STRANGLE | CE {strikes['ce_strike']} @ ₹{strikes['ce_ltp']:.0f} | PE {strikes['pe_strike']} @ ₹{strikes['pe_ltp']:.0f}\n"
                                    f"Premium ₹{strikes['premium']:.0f} | Click Execute Trade to confirm.",
                                    "warning"
                                )
                                LOG_LINES.append(
                                    f"[TRADE] [{_ts()}] CE {strikes['ce_strike']} @ ₹{strikes['ce_ltp']:.0f} | "
                                    f"PE {strikes['pe_strike']} @ ₹{strikes['pe_ltp']:.0f} | "
                                    f"Premium ₹{strikes['premium']:.0f}"
                                )
                        else:
                            LOG_LINES.append(f"[INFO]  [{_ts()}] Signal scan: no valid strikes (premium too low)")

            # Reset signal when outside entry window
            elif (state["bot_running"] and
                  not state["active_position"] and
                  not _is_entry_window() and
                  state["last_signal"]["signal"] not in ("WAIT", "ACTIVE")):
                reason = "Market closed" if not _is_market_open() else "Outside entry window (9:30–11:00)"
                state["last_signal"] = {
                    "signal": "WAIT", "confidence": 0, "reason": reason,
                    "setup_type": "—", "ce_strike": None, "pe_strike": None,
                    "premium": None, "approx_entry": None, "approx_sl": None,
                }
                state["signal_pending"] = False
                state["pending_signal"] = None

        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Signal engine error: {e}")

        # Sleep long when market is closed to reduce CPU/RAM usage
        if not _is_market_open():
            time.sleep(300)  # check every 5 min outside market hours
        else:
            time.sleep(60)   # check every minute during market hours


# ── Position monitor ──

def position_monitor():
    """Monitor active position SL/target every 30s."""
    time.sleep(20)

    while True:
        try:
            if state["active_position"] and state["position_detail"] and angel_obj:
                pos      = state["position_detail"]
                ce_token = pos.get("ce_token")
                pe_token = pos.get("pe_token")

                # Retry token lookup if missing (e.g. restored from disk before login)
                if not ce_token and pos.get("ce_symbol"):
                    ce_token = _get_nfo_token(pos["ce_symbol"])
                    if ce_token:
                        pos["ce_token"] = ce_token
                if not pe_token and pos.get("pe_symbol"):
                    pe_token = _get_nfo_token(pos["pe_symbol"])
                    if pe_token:
                        pos["pe_token"] = pe_token

                if ce_token and pe_token:
                    ce_r = angel_obj.ltpData("NFO", pos["ce_symbol"], ce_token)
                    pe_r = angel_obj.ltpData("NFO", pos["pe_symbol"], pe_token)

                    if ce_r.get("status") and pe_r.get("status"):
                        ce_ltp       = ce_r["data"]["ltp"]
                        pe_ltp       = pe_r["data"]["ltp"]
                        current_cost = ce_ltp + pe_ltp
                        entry_prem   = pos["premium_received"]
                        qty          = pos["quantity"]
                        open_pnl     = (entry_prem - current_cost) * qty
                        pnl          = open_pnl   # alias for SL/target log

                        pos["pnl"]              = round(open_pnl, 2)
                        pos["current_ce_ltp"]   = round(ce_ltp, 2)
                        pos["current_pe_ltp"]   = round(pe_ltp, 2)
                        state["daily_pnl"]      = round(state["closed_pnl"] + open_pnl, 2)

                        # ── Trailing SL: tighten to breakeven at 20% profit ──
                        trail_pct = config.get("trail_trigger_pct", 0.20)
                        profit_pct = (entry_prem - current_cost) / entry_prem if entry_prem > 0 else 0
                        if profit_pct >= trail_pct and not pos.get("trail_locked"):
                            # Lock SL at breakeven (entry premium = no loss)
                            old_sl = pos["sl"]
                            pos["sl"] = entry_prem
                            pos["trail_locked"] = True
                            LOG_LINES.append(
                                f"[TRADE] [{_ts()}] TRAILING SL ACTIVATED ✓ | "
                                f"Profit {profit_pct*100:.0f}% ≥ {trail_pct*100:.0f}% | "
                                f"SL tightened: ₹{old_sl:.0f} → ₹{entry_prem:.0f} (breakeven)"
                            )
                            _notify(
                                "🔒 Trailing SL — Breakeven Locked",
                                f"Profit at {profit_pct*100:.0f}% | SL moved to ₹{entry_prem:.0f} (breakeven)\n"
                                f"CE {pos['ce_strike']} | PE {pos['pe_strike']}",
                                "success"
                            )

                        # Target = 30% profit (current_cost ≤ entry - target)
                        if current_cost <= entry_prem - pos["target"]:
                            if not pos.get("exit_notified"):
                                pos["exit_notified"] = True
                                pos["exit_reason"] = "TARGET"
                                LOG_LINES.append(f"[TRADE] [{_ts()}] TARGET HIT ✓ | P&L ₹{pnl:,.0f} | Squaring off")
                                _notify(
                                    "🎯 Target Hit!",
                                    f"CE {pos['ce_strike']} | PE {pos['pe_strike']}\n"
                                    f"P&L: +₹{pnl:,.0f} | Entry ₹{entry_prem:.0f} → Now ₹{current_cost:.0f}\nSquaring off...",
                                    "success"
                                )
                                _square_off_position()

                        elif current_cost > pos["sl"]:
                            if not pos.get("exit_notified"):
                                pos["exit_notified"] = True
                                pos["exit_reason"] = "STOP_LOSS"
                                LOG_LINES.append(f"[TRADE] [{_ts()}] SL HIT ✗ | P&L ₹{pnl:,.0f} | Squaring off")
                                _notify(
                                    "🛑 Stop Loss Hit",
                                    f"CE {pos['ce_strike']} | PE {pos['pe_strike']}\n"
                                    f"P&L: ₹{pnl:,.0f} | Entry ₹{entry_prem:.0f} → Now ₹{current_cost:.0f}\nSquaring off...",
                                    "danger"
                                )
                                _square_off_position()

                        else:
                            now_t = datetime.now()
                            # Expiry cut-time: exit on expiry day before 1 PM
                            try:
                                expiry_dt = datetime.strptime(pos.get("expiry", ""), "%d-%b-%Y").date()
                                h, m = map(int, config.get("expiry_cut_time", "13:00").split(":"))
                                if now_t.date() == expiry_dt and now_t.time() >= _time(h, m):
                                    if not pos.get("exit_notified"):
                                        pos["exit_notified"] = True
                                        pos["exit_reason"] = "EXPIRY_CUT"
                                        LOG_LINES.append(f"[TRADE] [{_ts()}] EXPIRY CUT-TIME {config.get('expiry_cut_time','13:00')} on expiry day | P&L ₹{pnl:,.0f} | Squaring off")
                                        _notify("⏰ Expiry Cut-Time Exit", f"CE {pos['ce_strike']} | PE {pos['pe_strike']}\nP&L: ₹{pnl:,.0f}\nExiting before expiry.", "warning")
                                        _square_off_position()
                            except Exception:
                                pass

                            # Dead zone: force exit after 14:30 if still in position
                            if state["active_position"] and not pos.get("exit_notified"):
                                dh, dm = map(int, config.get("dead_zone_start", "14:30").split(":"))
                                if now_t.time() >= _time(dh, dm):
                                    pos["exit_notified"] = True
                                    pos["exit_reason"] = "DEAD_ZONE"
                                    LOG_LINES.append(f"[TRADE] [{_ts()}] DEAD ZONE {config.get('dead_zone_start','14:30')} reached | P&L ₹{pnl:,.0f} | Squaring off")
                                    _notify("⏰ Dead Zone Exit", f"CE {pos['ce_strike']} | PE {pos['pe_strike']}\nP&L: ₹{pnl:,.0f}\nExiting — past 14:30 dead zone.", "warning")
                                    _square_off_position()

                            # EOD exit at 3:20 PM
                            if state["active_position"] and not pos.get("exit_notified") and now_t.time() >= _time(15, 20):
                                pos["exit_notified"] = True
                                pos["exit_reason"] = "EOD_EXIT"
                                LOG_LINES.append(f"[TRADE] [{_ts()}] EOD auto-exit at 3:20 PM | P&L ₹{pnl:,.0f} | Squaring off")
                                _notify("⏰ EOD Auto-Exit — 3:20 PM", f"CE {pos['ce_strike']} | PE {pos['pe_strike']}\nP&L: ₹{pnl:,.0f} | Squaring off before market close.", "warning")
                                _square_off_position()

        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Position monitor error: {e}")

        time.sleep(30)


# ── Market data thread ──

def fetch_market_data():
    """Fetch Nifty/VIX/margin every 15s and refresh derived market metrics every minute."""
    time.sleep(5)
    metrics_counter   = 3   # first derived refresh happens on the first live cycle
    _last_date        = _date.today()
    _holidays_fetched = False   # retry holiday fetch once market opens

    while True:
        # ── Daily reset at start of each new trading day ──
        today = _date.today()
        if today != _last_date:
            _last_date = today
            state["daily_pnl"]    = 0.0
            state["closed_pnl"]   = 0.0
            state["trades_today"] = 0
            state["market"].update({
                "pcr": None,
                "iv_atm": None,
                "iv_percentile": None,
                "atr_15m": None,
                "net_delta": None,
                "ema_trend_flat": None,
            })
            LOG_LINES.append(f"[INFO]  [{_ts()}] New day {today} — daily stats reset.")
            # Refresh lot size + holidays for the new trading day
            threading.Thread(target=_fetch_nifty_lot_size, daemon=True).start()
            threading.Thread(target=_fetch_nse_holidays,   daemon=True).start()

        if angel_obj and connection["status"] == "connected":
            try:
                t0    = time.time()
                nifty = angel_obj.ltpData("NSE", "Nifty 50", "26000")
                vix   = angel_obj.ltpData("NSE", "India VIX", "99926017")
                ping  = int((time.time() - t0) * 1000)

                if nifty.get("status"):
                    state["market"]["nifty_spot"] = nifty["data"]["ltp"]
                    _spot_history.append((datetime.now(), nifty["data"]["ltp"]))
                    if len(_spot_history) > 2400:
                        del _spot_history[:-2400]
                else:
                    LOG_LINES.append(f"[WARN]  [{_ts()}] NIFTY LTP failed: {nifty.get('message','no data')}")
                if vix.get("status"):
                    state["market"]["vix"] = vix["data"]["ltp"]
                else:
                    LOG_LINES.append(f"[WARN]  [{_ts()}] VIX LTP failed: {vix.get('message','no data')} — token 99926017")

                connection["last_ping"] = _ts()
                connection["ping_ms"]   = ping

                spot = state["market"]["nifty_spot"]
                vix_ = state["market"]["vix"]
                if spot and vix_:
                    LOG_LINES.append(f"[INFO]  [{_ts()}] NIFTY {spot:.2f} | VIX {vix_:.2f}")

                _fetch_margin()

                metrics_counter += 1
                if metrics_counter >= 4:   # every ~60s
                    metrics_counter = 0
                    _refresh_market_metrics()
                    if state["market"]["pcr"] is not None:
                        LOG_LINES.append(
                            f"[INFO]  [{_ts()}] Metrics | PCR {state['market']['pcr']:.3f} | "
                            f"IV {state['market']['iv_atm'] if state['market']['iv_atm'] is not None else '—'} | "
                            f"ATR15 {state['market']['atr_15m'] if state['market']['atr_15m'] is not None else '—'} | "
                            f"Delta {state['market']['net_delta'] if state['market']['net_delta'] is not None else '—'}"
                        )
                    else:
                        LOG_LINES.append(f"[INFO]  [{_ts()}] Market metrics unavailable (option chain/candles not loaded)")

                _update_checks()

                # Retry holiday fetch once after market opens (in case startup fetch failed)
                if not _holidays_fetched and _is_market_open():
                    _holidays_fetched = True
                    threading.Thread(target=_fetch_nse_holidays, daemon=True).start()

            except Exception as e:
                LOG_LINES.append(f"[WARN]  [{_ts()}] Market fetch error: {e}")

        if len(LOG_LINES) > 200:
            del LOG_LINES[:-200]
        time.sleep(15)


# ── Routes ──

@app.route("/")
def index():
    from flask import make_response
    resp = make_response(send_from_directory(BASE, "nifty_dashboard.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.route("/fifto_logo.png")
def logo():
    p = os.path.join(BASE, "fifto_logo.png")
    return send_from_directory(BASE, "fifto_logo.png") if os.path.exists(p) else ("", 404)

@app.route("/api/state")
def api_state():
    # Inject live DTE on every poll — cheap calculation
    try:
        exp_dt   = _find_live_nifty_expiry()
        exp_str  = exp_dt.strftime("%d-%b-%Y")
        dte_val  = (exp_dt.date() - _date.today()).days
        state["dte"]         = max(dte_val, 0)
        state["expiry_date"] = exp_str
    except Exception:
        pass   # keep whatever was there before
    # Compute overall P&L from all closed trades in history
    overall = sum(
        float(t.get("final_pnl") or 0)
        for t in state.get("trade_history", [])
        if t.get("exit_time") and t.get("final_pnl") is not None
    )
    capital = float(config.get("capital", 1500000))
    state["overall_pnl"] = round(overall, 2)
    state["overall_pnl_pct"] = round((overall / capital * 100), 2) if capital else 0
    return jsonify(state)

@app.route("/api/connection")
def api_connection():
    return jsonify(connection)

@app.route("/api/logs")
def api_logs():
    return jsonify({"lines": LOG_LINES[-60:]})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["bot_running"]    = False
    state["signal_pending"] = False
    state["pending_signal"] = None
    state["button_states"].update({"stop_agent": True, "start_agent": False,
                                   "emergency_exit": False, "approve_buy": False})
    state["last_signal"] = {
        "signal": "WAIT", "confidence": 0, "reason": "Agent stopped.",
        "setup_type": "—", "ce_strike": None, "pe_strike": None,
        "premium": None, "approx_entry": None, "approx_sl": None,
    }
    LOG_LINES.append(f"[WARN]  [{_ts()}] Agent stopped by user.")
    def _reset(): time.sleep(2); state["button_states"]["stop_agent"] = False
    threading.Thread(target=_reset, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/start", methods=["POST"])
def api_start():
    state["bot_running"] = True
    state["button_states"].update({"start_agent": True, "stop_agent": False,
                                   "emergency_exit": False, "approve_buy": False})
    state["last_signal"]["reason"] = "Agent running. Scanning for setup..."
    LOG_LINES.append(f"[INFO]  [{_ts()}] Agent started by user.")
    def _reset(): time.sleep(2); state["button_states"]["start_agent"] = False
    threading.Thread(target=_reset, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/emergency_exit", methods=["POST"])
def api_emergency_exit():
    LOG_LINES.append(f"[ERROR] [{_ts()}] EMERGENCY EXIT triggered by user.")
    _notify("🚨 Emergency Exit", "Manual emergency exit triggered. All positions being squared off.", "danger")
    state["signal_pending"] = False
    state["pending_signal"] = None
    state["button_states"].update({"emergency_exit": True, "stop_agent": False,
                                   "start_agent": False, "approve_buy": False})
    if state["active_position"]:
        threading.Thread(target=_square_off_position, daemon=True).start()
    else:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Emergency exit: no active position.")
    def _reset(): time.sleep(3); state["button_states"]["emergency_exit"] = False
    threading.Thread(target=_reset, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/set_mode", methods=["POST"])
def api_set_mode():
    data = request.get_json() or {}
    mode = data.get("mode", "MANUAL").upper()
    if mode not in ("AUTO", "MANUAL"):
        return jsonify({"error": "Invalid mode. Use AUTO or MANUAL"}), 400
    state["execution_mode"] = mode
    config["execution_mode"] = mode
    LOG_LINES.append(f"[INFO]  [{_ts()}] Execution mode → {mode}")
    return jsonify({"ok": True, "mode": mode})

@app.route("/api/execute", methods=["POST"])
def api_execute():
    """User manually triggers execution of the pending signal."""
    if not state.get("signal_pending") or not state.get("pending_signal"):
        return jsonify({"error": "No pending signal to execute"}), 400
    signal = state["pending_signal"]
    LOG_LINES.append(f"[TRADE] [{_ts()}] Manual execute triggered by user.")
    threading.Thread(target=_execute_trade, args=(signal,), daemon=True).start()
    return jsonify({"ok": True, "msg": "Executing trade..."})


@app.route("/api/test_live_trade", methods=["POST"])
def api_test_live_trade():
    payload, status = _place_test_live_atm_sell()
    return jsonify(payload), status

@app.route("/api/approve_buy", methods=["POST"])
def api_approve_buy():
    """Legacy endpoint — manual execution now handled by /api/execute."""
    if state.get("signal_pending") and state.get("pending_signal"):
        signal = state["pending_signal"]
        LOG_LINES.append(f"[TRADE] [{_ts()}] Approve Buy → executing pending signal.")
        threading.Thread(target=_execute_trade, args=(signal,), daemon=True).start()
        return jsonify({"ok": True, "msg": "Executing trade..."})
    return jsonify({"ok": False, "msg": "No pending signal to execute."})

@app.route("/api/reconnect", methods=["POST"])
def api_reconnect():
    LOG_LINES.append(f"[INFO]  [{_ts()}] Manual reconnect triggered...")
    threading.Thread(target=angel_login, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(config)

@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    for k, v in data.items():
        if k in config:
            config[k] = v
    # Apply execution_mode immediately if changed
    if "execution_mode" in data:
        state["execution_mode"] = data["execution_mode"].upper()
    LOG_LINES.append(f"[INFO]  [{_ts()}] Configuration updated.")
    return jsonify({"ok": True})

@app.route("/api/notifications")
def api_notifications():
    """Return and clear pending notifications for the dashboard."""
    pending = list(_NOTIF)
    _NOTIF.clear()
    return jsonify({"notifications": pending})

@app.route("/api/trades")
def api_trades():
    return jsonify({"trades": state["trade_history"]})

@app.route("/api/trade/<trade_id>", methods=["DELETE"])
def api_delete_trade(trade_id):
    # Remove from memory
    before = len(state["trade_history"])
    state["trade_history"] = [t for t in state["trade_history"] if t.get("trade_id") != trade_id]
    if len(state["trade_history"]) == before:
        return jsonify({"ok": False, "msg": "Trade not found"}), 404

    # Remove from local JSON
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump(state["trade_history"], f, indent=2, default=str)
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Local trade delete failed: {e}")

    # Remove from Google Sheets in background
    def _delete_from_sheets():
        creds_file = os.path.join(BASE, "gsheet_creds.json")
        sheet_id   = os.getenv("GSHEET_ID", "")
        if not sheet_id or not os.path.exists(creds_file):
            return
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(creds_file, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            gc    = gspread.authorize(creds)
            ws    = gc.open_by_key(sheet_id).worksheet("Trades")
            cell  = ws.find(trade_id, in_column=1)
            if cell:
                ws.delete_rows(cell.row)
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Sheets delete failed: {e}")

    threading.Thread(target=_delete_from_sheets, daemon=True).start()
    LOG_LINES.append(f"[INFO]  [{_ts()}] Trade {trade_id} deleted")
    return jsonify({"ok": True})

@app.route("/api/test_telegram", methods=["POST"])
def api_test_telegram():
    token   = config.get("telegram_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        return jsonify({"ok": False, "msg": "Telegram token or chat ID not configured"}), 400
    try:
        _send_telegram("*FIFTO AI Trading* — Telegram test OK ✅")
        LOG_LINES.append(f"[INFO]  [{_ts()}] Telegram test sent.")
        return jsonify({"ok": True, "msg": "Test message sent. Check your Telegram."})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


def _startup_nse_fetch():
    """Fetch lot size + holidays once at startup (runs in background)."""
    time.sleep(20)   # let NSE session & cookies establish via option chain warm-up
    _fetch_nifty_lot_size()
    _fetch_nse_holidays()


if __name__ == "__main__":
    threading.Thread(target=angel_login,        daemon=True).start()
    threading.Thread(target=fetch_market_data,  daemon=True).start()
    threading.Thread(target=signal_engine,      daemon=True).start()
    threading.Thread(target=position_monitor,   daemon=True).start()
    threading.Thread(target=_startup_nse_fetch, daemon=True).start()
    print("FIFTO AI Trading server → http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
