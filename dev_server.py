"""
dev_server.py — FIFTO AI Trading
Real AngelOne + NSE data + Signal Engine + Order Execution (Auto & Manual)
Run: .venv/Scripts/python.exe dev_server.py
"""

import os, time, threading, math, json, hmac
from datetime import datetime, date as _date, time as _time, timedelta
from flask import Flask, jsonify, send_from_directory, request
from dotenv import load_dotenv
import requests as _requests

# ── Trade persistence ──
TRADES_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.json")
ACTIVE_POS_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "active_pos.json")
SETTINGS_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
EQ_TRADES_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eq_trades.json")

def _save_active_position(pos):
    """Write current open position to active_pos.json for crash-safe recovery."""
    try:
        with open(ACTIVE_POS_FILE, "w") as f:
            json.dump(pos, f, indent=2, default=str)
    except Exception:
        pass

def _clear_active_position():
    """Delete active_pos.json when position is closed."""
    try:
        if os.path.exists(ACTIVE_POS_FILE):
            os.remove(ACTIVE_POS_FILE)
    except Exception:
        pass

def _load_active_position_from_file():
    """Load position from active_pos.json if it exists (crash recovery)."""
    if os.path.exists(ACTIVE_POS_FILE):
        try:
            with open(ACTIVE_POS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None

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

_SHEET_HEADERS = [
    "Trade ID", "Entry Time", "Exit Time", "Setup",
    "CE Strike", "PE Strike", "CE Symbol", "PE Symbol",
    "Qty", "Premium", "CE Entry LTP", "PE Entry LTP",
    "CE Exit LTP", "PE Exit LTP",
    "Target", "Initial SL", "Final SL", "Trail Locked",
    "P&L", "Exit Reason", "Expiry", "Duration (min)",
    "Hedge Symbol", "Hedge Entry LTP", "Hedge Exit LTP",
]

def _get_or_create_sheet():
    """Return the Trades worksheet, creating/upgrading headers if needed."""
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
        ws = sh.worksheet("Trades")
        # Upgrade header row if sheet has fewer columns than expected
        try:
            existing_headers = ws.row_values(1)
            if len(existing_headers) < len(_SHEET_HEADERS):
                ws.resize(cols=len(_SHEET_HEADERS))
                ws.update(range_name="A1", values=[_SHEET_HEADERS])
        except Exception:
            pass
        return ws
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Trades", rows=1000, cols=len(_SHEET_HEADERS))
        ws.update(range_name="A1", values=[_SHEET_HEADERS])
        return ws

def _save_entry_sheets(trade_record):
    """Write a new entry row (P&L/exit columns blank) when trade opens."""
    try:
        ws = _get_or_create_sheet()
        if not ws:
            return
        tid = trade_record.get("trade_id", "")
        if tid and ws.find(tid, in_column=1):
            LOG_LINES.append(f"[WARN]  [{_ts()}] Sheet: trade {tid} already exists, skipping duplicate append")
            return
        ws.append_row([
            trade_record.get("trade_id", ""),
            trade_record.get("entry_time", ""),
            "",                                         # exit time — blank at entry
            trade_record.get("setup_type", ""),
            trade_record.get("ce_strike", ""),
            trade_record.get("pe_strike", ""),
            trade_record.get("ce_symbol", ""),
            trade_record.get("pe_symbol", ""),
            trade_record.get("quantity", ""),
            trade_record.get("premium_received", ""),
            trade_record.get("ce_entry_ltp", ""),
            trade_record.get("pe_entry_ltp", ""),
            "",                                         # CE exit LTP — blank at entry
            "",                                         # PE exit LTP — blank at entry
            trade_record.get("target", ""),
            trade_record.get("initial_sl", trade_record.get("stop_loss", "")),
            trade_record.get("stop_loss", ""),
            "",                                         # trail locked — blank at entry
            "",                                         # P&L — blank at entry
            "",                                         # exit reason — blank at entry
            trade_record.get("expiry", ""),
            "",                                         # duration — blank at entry
            trade_record.get("hedge_symbol", ""),
            trade_record.get("hedge_entry_ltp", ""),
            "",                                         # hedge exit LTP — blank at entry
        ])
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Sheets entry write failed: {e}")

def _update_exit_sheets(trade_record):
    """Overwrite the entire row on exit — single API call, all 22 columns filled."""
    try:
        import gspread
        ws = _get_or_create_sheet()
        if not ws:
            return
        cell = ws.find(trade_record.get("trade_id", ""), in_column=1)
        if not cell:
            # Row missing — append full completed row as fallback
            _save_entry_sheets(trade_record)
            cell = ws.find(trade_record.get("trade_id", ""), in_column=1)
            if not cell:
                return
        r = cell.row
        # Write all 25 columns in one batch update (fast, atomic)
        full_row = [
            trade_record.get("trade_id", ""),                                       # 1
            trade_record.get("entry_time", ""),                                     # 2
            trade_record.get("exit_time", ""),                                      # 3
            trade_record.get("setup_type", ""),                                     # 4
            trade_record.get("ce_strike", ""),                                      # 5
            trade_record.get("pe_strike", ""),                                      # 6
            trade_record.get("ce_symbol", ""),                                      # 7
            trade_record.get("pe_symbol", ""),                                      # 8
            trade_record.get("quantity", ""),                                       # 9
            trade_record.get("premium_received", ""),                               # 10
            trade_record.get("ce_entry_ltp", ""),                                   # 11 CE Entry LTP
            trade_record.get("pe_entry_ltp", ""),                                   # 12 PE Entry LTP
            trade_record.get("ce_exit_ltp", ""),                                    # 13 CE Exit LTP
            trade_record.get("pe_exit_ltp", ""),                                    # 14 PE Exit LTP
            trade_record.get("target", ""),                                         # 15
            trade_record.get("initial_sl", trade_record.get("stop_loss", "")),      # 16 Initial SL
            trade_record.get("stop_loss", ""),                                      # 17 Final SL
            "Yes" if trade_record.get("trail_locked") else "No",                    # 18 Trail Locked
            trade_record.get("final_pnl", ""),                                      # 19 P&L
            trade_record.get("exit_reason", ""),                                    # 20 Exit Reason
            trade_record.get("expiry", ""),                                         # 21
            trade_record.get("duration_mins", ""),                                  # 22 Duration
            trade_record.get("hedge_symbol", ""),                                   # 23 Hedge Symbol
            trade_record.get("hedge_entry_ltp", ""),                                # 24 Hedge Entry LTP
            trade_record.get("hedge_exit_ltp", ""),                                 # 25 Hedge Exit LTP
        ]
        ws.update(range_name=f"A{r}:Y{r}", values=[full_row])
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Sheets exit update failed: {e}")


def _load_open_position_from_sheet():
    """Fallback recovery from today's open trade in Google Sheets."""
    ws = _get_or_create_sheet()
    if not ws:
        return None

    def parse_number(value, default=None):
        if value is None:
            return default
        text = str(value).strip()
        if text == "":
            return default
        try:
            return float(text.replace(",", ""))
        except Exception:
            return default

    try:
        rows = ws.get_all_records()
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Sheets open-position read failed: {e}")
        return None

    today = datetime.now().date()
    latest = None
    latest_dt = None
    for row in rows:
        exit_time = str(row.get("Exit Time", "")).strip()
        if exit_time:
            continue
        entry_time = str(row.get("Entry Time", "")).strip()
        if not entry_time:
            continue
        try:
            entry_dt = datetime.fromisoformat(entry_time) if "T" in entry_time else datetime.strptime(entry_time, "%d-%m-%Y  %H:%M:%S")
        except Exception:
            continue
        if entry_dt.date() != today:
            continue
        if latest_dt is None or entry_dt > latest_dt:
            latest_dt = entry_dt
            latest = row

    if not latest:
        return None

    trade_id = str(latest.get("Trade ID", "")).strip()
    state_record = {
        "trade_id":        trade_id,
        "entry_time":      latest.get("Entry Time", ""),
        "exit_time":       None,
        "setup_type":      latest.get("Setup", "Short Strangle"),
        "ce_strike":       latest.get("CE Strike", ""),
        "pe_strike":       latest.get("PE Strike", ""),
        "ce_symbol":       latest.get("CE Symbol", ""),
        "pe_symbol":       latest.get("PE Symbol", ""),
        "quantity":        int(parse_number(latest.get("Qty", "")) or 0),
        "premium_received": parse_number(latest.get("Premium", "")) or 0,
        "target":          parse_number(latest.get("Target", "")) or 0,
        "stop_loss":       parse_number(latest.get("Final SL", "")) or 0,
        "expiry":          latest.get("Expiry", ""),
        "ce_entry_ltp":    parse_number(latest.get("CE Entry LTP", "")) or 0,
        "pe_entry_ltp":    parse_number(latest.get("PE Entry LTP", "")) or 0,
        "initial_sl":      parse_number(latest.get("Initial SL", "")) or parse_number(latest.get("Final SL", "")) or 0,
        "target_pct":      latest.get("Target", ""),
        "sl_mult":         latest.get("Final SL", ""),
    }

    pos = {
        "trade_id":         trade_id,
        "entry_time":       latest.get("Entry Time", ""),
        "ce_strike":        latest.get("CE Strike", ""),
        "pe_strike":        latest.get("PE Strike", ""),
        "ce_symbol":        latest.get("CE Symbol", ""),
        "pe_symbol":        latest.get("PE Symbol", ""),
        "ce_token":         None,
        "pe_token":         None,
        "ce_entry_price":   parse_number(latest.get("CE Entry LTP", "")) or 0,
        "pe_entry_price":   parse_number(latest.get("PE Entry LTP", "")) or 0,
        "premium_received": parse_number(latest.get("Premium", "")) or 0,
        "target":           parse_number(latest.get("Target", "")) or 0,
        "sl":               parse_number(latest.get("Final SL", "")) or 0,
        "initial_sl":       parse_number(latest.get("Initial SL", "")) or parse_number(latest.get("Final SL", "")) or 0,
        "quantity":         int(parse_number(latest.get("Qty", "")) or 0),
        "setup_type":       latest.get("Setup", "Short Strangle"),
        "expiry":           latest.get("Expiry", ""),
        "pnl":              0,
        "exit_reason":      None,
        "trail_locked":     str(latest.get("Trail Locked", "")).strip().lower().startswith("y"),
    }

    if trade_id:
        # Check disk first — if trade already closed locally, don't restore from stale sheet row
        _disk_trades = _load_trades_from_disk()
        _already_closed = any(
            t.get("trade_id") == trade_id and t.get("exit_time") not in (None, "")
            for t in _disk_trades
        )
        if _already_closed:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Sheet shows {trade_id} open but it's already closed in local trades.json — skipping ghost restore")
            return None
        if any(t.get("trade_id") == trade_id for t in state.get("trade_history", [])):
            LOG_LINES.append(f"[WARN]  [{_ts()}] Trade {trade_id} already in history, skipping duplicate from Sheet")
        else:
            _save_trade_local(state_record)
            state["trade_history"].append(state_record)

    return pos


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
        "ce_entry_ltp":    pos.get("ce_entry_ltp", pos.get("ce_entry_price", "")),
        "pe_entry_ltp":    pos.get("pe_entry_ltp", pos.get("pe_entry_price", "")),
        "initial_sl":      pos.get("initial_sl", pos.get("sl", "")),
        "target_pct":      pos.get("target_pct", ""),
        "sl_mult":         pos.get("sl_mult", ""),
        "hedge_symbol":    pos.get("hedge_ce_symbol") or pos.get("hedge_pe_symbol", ""),
        "hedge_entry_ltp": pos.get("hedge_ce_entry_ltp") or pos.get("hedge_pe_entry_ltp", ""),
    }
    _save_trade_local(entry_record)
    _save_active_position(pos)                  # write full pos dict (has tokens, ltps, etc.)
    threading.Thread(target=_save_entry_sheets, args=(entry_record,), daemon=True).start()

def _persist_trade(trade_record):
    """Called at trade exit — update local record + update Sheets row, remove active_pos."""
    _save_trade_local(trade_record)
    _clear_active_position()                    # position closed — remove active_pos.json
    threading.Thread(target=_update_exit_sheets, args=(trade_record,), daemon=True).start()

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

app  = Flask(__name__)

# ── Upstox fallback ──────────────────────────────────────────────────────────
_UPSTOX_API_KEY    = os.getenv("UPSTOX_API_KEY", "")
_UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET", "")
_UPSTOX_REDIRECT   = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8080/")
_upstox_token      = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

# Upstox instrument keys for common symbols
_UPSTOX_KEYS = {
    "NIFTY_SPOT": "NSE_INDEX|Nifty 50",
    "VIX":        "NSE_INDEX|India VIX",
}

def _upstox_headers():
    return {
        "Authorization": f"Bearer {_upstox_token}",
        "Accept":        "application/json",
    }

def _upstox_available():
    return bool(_upstox_token)

def _upstox_ltp(instrument_key: str) -> dict:
    """Fetch LTP via Upstox market-quotes API.
    Returns {status: True, data: {ltp: float}} or {status: False}."""
    if not _upstox_available():
        return {"status": False, "message": "Upstox token not set"}
    try:
        url = "https://api.upstox.com/v2/market-quote/ltp"
        r = _requests.get(url, headers=_upstox_headers(),
                          params={"instrument_key": instrument_key}, timeout=5)
        if r.status_code == 200:
            data = r.json().get("data", {})
            # data is keyed by instrument_key (colon-encoded)
            key = list(data.keys())[0] if data else None
            if key:
                ltp = data[key].get("last_price")
                if ltp is not None:
                    return {"status": True, "data": {"ltp": float(ltp)}}
        LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox LTP failed: {r.status_code} {r.text[:120]}")
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox LTP error: {e}")
    return {"status": False, "message": "Upstox LTP fetch failed"}

def _upstox_nfo_key(symbol: str) -> str:
    """Convert AngelOne NFO symbol (e.g. NIFTY25APR22500CE) to Upstox instrument key."""
    # Upstox uses NSE_FO|<symbol> for NFO options
    return f"NSE_FO|{symbol}"

def _upstox_ltp_nfo(symbol: str) -> dict:
    """Fetch LTP for an NFO options symbol via Upstox."""
    return _upstox_ltp(_upstox_nfo_key(symbol))

def _upstox_candles(interval: str = "30minute", days: int = 5) -> list:
    """Fetch NIFTY 50 historical candles from Upstox.
    Returns list of [datetime_str, open, high, low, close, volume] rows."""
    if not _upstox_available():
        return []
    # Map AngelOne interval names → Upstox interval names
    interval_map = {
        "FIFTEEN_MINUTE": "30minute",   # closest available free tier
        "ONE_MINUTE":     "1minute",
        "FIVE_MINUTE":    "5minute",
        "ONE_DAY":        "day",
    }
    upstox_interval = interval_map.get(interval, "30minute")
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    to_date   = datetime.now().strftime("%Y-%m-%d")
    try:
        url = (f"https://api.upstox.com/v2/historical-candle/NSE_INDEX%7CNifty%2050"
               f"/{upstox_interval}/{to_date}/{from_date}")
        r = _requests.get(url, headers=_upstox_headers(), timeout=10)
        if r.status_code == 200:
            candles = r.json().get("data", {}).get("candles", [])
            # Upstox format: [timestamp, open, high, low, close, volume, oi]
            # AngelOne format: [datetime_str, open, high, low, close, volume]
            rows = []
            for c in candles:
                try:
                    dt_str = c[0][:16].replace("T", " ")
                    rows.append([dt_str, c[1], c[2], c[3], c[4], c[5]])
                except Exception:
                    pass
            if rows:
                LOG_LINES.append(f"[INFO]  [{_ts()}] Upstox candles fetched: {len(rows)} rows ({upstox_interval})")
            return rows
        LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox candles failed: {r.status_code} {r.text[:120]}")
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox candles error: {e}")
    return []

def _upstox_save_token(token: str):
    """Persist Upstox access token to .env file."""
    global _upstox_token
    _upstox_token = token
    env_path = os.path.join(BASE, ".env")
    try:
        with open(env_path, "r") as f:
            content = f.read()
        if "UPSTOX_ACCESS_TOKEN=" in content:
            lines = content.splitlines()
            new_lines = []
            for line in lines:
                if line.startswith("UPSTOX_ACCESS_TOKEN="):
                    new_lines.append(f"UPSTOX_ACCESS_TOKEN={token}")
                else:
                    new_lines.append(line)
            content = "\n".join(new_lines) + "\n"
        else:
            content += f"\nUPSTOX_ACCESS_TOKEN={token}\n"
        with open(env_path, "w") as f:
            f.write(content)
        LOG_LINES.append(f"[INFO]  [{_ts()}] Upstox access token saved to .env")
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Could not save Upstox token: {e}")
# ── End Upstox ───────────────────────────────────────────────────────────────

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

    if not _is_market_open():
        LOG_LINES.append(f"[INFO]  [{_ts()}] AngelOne login deferred — market is closed (holiday/weekend/outside hours)")

    api_key  = os.getenv("ANGEL_API_KEY", "")
    client   = os.getenv("ANGEL_CLIENT_ID", "")
    password = os.getenv("ANGEL_PASSWORD", "")
    secret   = os.getenv("ANGEL_TOTP_SECRET", "")

    # Debug: log what credentials are loaded
    LOG_LINES.append(f"[DEBUG] [{_ts()}] API Key: {'(set)' if api_key else '(missing)'}, Client: {client}")
    LOG_LINES.append(f"[DEBUG] [{_ts()}] Password: {'(set)' if password else '(missing)'}, TOTP: {'(set)' if secret else '(missing)'}")

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
    "min_strike_oi":        300000, # minimum OI at CE/PE strike (3 lakh contracts)
    "paper_trade":          os.getenv("PAPER_TRADE", "false").lower() == "true",
    "margin_override":      1500000,  # app-side margin bypass for test mode; broker may still reject live orders
    "daily_loss_limit":     45000,    # 3% of ₹15L capital
    "weekly_loss_limit":    90000,    # 6% of ₹15L capital  (~2 bad days before lockout)
    "monthly_loss_limit":   150000,   # 10% of ₹15L capital
    "risk_per_trade_pct":   0.02,     # 2% capital risk per trade for position sizing
    "stop_after_sl":        True,     # halt bot for rest of day after any SL hit
    "weekly_loss_lockout":  True,     # halt bot for rest of week if weekly limit breached
    "max_trades_per_day":   3,
    "brokerage_per_lot":    120,    # ₹/lot round-trip (flat ₹20×4 orders + STT + charges estimate)
    "vix_min":           13,
    "vix_max":           28,
    "entry_start":       "09:30",
    "entry_end":         "11:00",
    "dead_zone_start":   "14:30",
    "expiry_cut_time":   "13:00",
    "login_pin":         os.getenv("LOGIN_PIN", "5599"),
    "execution_mode":    "AUTO",
    "gsheet_id":         os.getenv("GSHEET_ID", ""),
}

# ── Keys that persist to settings.json (non-sensitive trading params) ──
_PERSISTENT_KEYS = {
    "capital", "base_lots", "lot_size", "min_premium", "min_combined_premium",
    "spot_momentum_limit", "min_strike_oi", "paper_trade", "margin_override",
    "daily_loss_limit", "weekly_loss_limit", "monthly_loss_limit",
    "risk_per_trade_pct", "stop_after_sl", "weekly_loss_lockout",
    "max_trades_per_day", "brokerage_per_lot", "vix_min", "vix_max", "entry_start", "entry_end",
    "dead_zone_start", "expiry_cut_time", "execution_mode",
}

# ── Defaults snapshot (for reset) ──
_CONFIG_DEFAULTS = {k: config[k] for k in _PERSISTENT_KEYS if k in config}

def _save_settings():
    """Persist non-sensitive config + state settings to settings.json."""
    try:
        data = {k: config[k] for k in _PERSISTENT_KEYS if k in config}
        data["ltp_source"]    = state.get("ltp_source", "auto")
        data["fetch_interval"] = state.get("fetch_interval", 5)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Failed to save settings.json: {e}")

def _load_settings():
    """Load persisted settings from settings.json and apply to config/state."""
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
        for k, v in data.items():
            if k in _PERSISTENT_KEYS and k in config:
                config[k] = v
        if "ltp_source" in data:
            state["ltp_source"]    = data["ltp_source"]
        if "fetch_interval" in data:
            state["fetch_interval"] = int(data["fetch_interval"])
        LOG_LINES.append(f"[INFO]  [{_ts()}] Settings loaded from settings.json ({len(data)} keys)")
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Failed to load settings.json: {e}")


# ── Agent state ──
state = {
    "regime":           None,
    "trades_today":     0,
    "daily_pnl":        0.0,
    "closed_pnl":       0.0,   # cumulative realized P&L for today
    "bot_running":      False,
    "lot_size":         65,          # updated daily from NSE
    "holidays_count":   0,          # updated after _NSE_HOLIDAYS_2026 is loaded
    "active_position":  False,
    "squaring_off":     False,      # True = square-off in progress (prevents double exit)
    "execution_mode":   config["execution_mode"],   # "AUTO" or "MANUAL" from config
    "signal_pending":      False,    # True = signal ready, awaiting manual execute
    "pending_signal":      None,    # Full signal dict stored here for execution
    "signal_generated_at": None,    # Unix timestamp when pending signal was set
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
        "trend":          "FLAT",
    },
    "checks": {
        "vix": False, "iv": None, "ema": None,
        "pcr": False, "margin": False, "gate": False,
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
    "ltp_source":          "auto",   # "auto" | "angel" | "upstox" | "diversified"
    "ltp_last_used":       None,     # which broker actually served last LTP
    "ltp_last_updated":    None,     # ISO timestamp of last successful LTP fetch
    "fetch_interval":      5,        # seconds between fast LTP refreshes (1–30)
    "_diversify_turn":     0,        # internal: 0=angel-first, 1=upstox-first (round-robin)
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
        state["closed_pnl"]   = round(total, 2)
        state["daily_pnl"]    = round(total, 2)
        state["trades_today"] = count
        LOG_LINES.append(f"[INFO]  [{_ts()}] Restored daily P&L from disk: ₹{total:,.0f} ({count} closed trade{'s' if count>1 else ''})")

# ── Restore open position from active_pos.json or trades.json on startup ──
def _restore_open_position():
    """Restore active position: prefer active_pos.json (full detail), fall back to trades.json."""
    today = datetime.now().date()

    # ── Priority 1: active_pos.json (written on every entry + trailing SL update) ──
    saved = _load_active_position_from_file()
    if saved:
        try:
            raw_et = saved.get("entry_time", "")
            entry_dt = datetime.fromisoformat(raw_et) if "T" in str(raw_et) else datetime.strptime(str(raw_et).strip(), "%d-%m-%Y  %H:%M:%S")
            if entry_dt.date() == today:
                # Null out tokens — re-resolved after AngelOne login
                saved["ce_token"] = None
                saved["pe_token"] = None
                saved["pnl"] = 0
                if "initial_sl" not in saved and "sl" in saved:
                    saved["initial_sl"] = saved["sl"]
                state["active_position"] = True
                state["trades_today"]   += 1
                state["position_detail"] = saved
                state["last_signal"]["signal"] = "ACTIVE"
                LOG_LINES.append(f"[INFO]  [{_ts()}] Restored full position from active_pos.json: {saved.get('ce_symbol')} / {saved.get('pe_symbol')}")
                return
        except Exception:
            pass  # fall through to trades.json

    # ── Priority 2: trades.json open record (exit_time = None) ──
    trades = _load_trades_from_disk()
    for t in reversed(trades):
        if t.get("exit_time") is None and t.get("entry_time"):
            try:
                raw_et = t["entry_time"]
                entry_dt = datetime.fromisoformat(raw_et) if "T" in str(raw_et) else datetime.strptime(str(raw_et).strip(), "%d-%m-%Y  %H:%M:%S")
                if entry_dt.date() != today:
                    continue
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
                "ce_token":         None,
                "pe_token":         None,
                "ce_entry_price":   t.get("ce_entry_ltp"),
                "pe_entry_price":   t.get("pe_entry_ltp"),
                "premium_received": t.get("premium_received"),
                "target":           t.get("target"),
                "sl":               t.get("stop_loss"),
                "initial_sl":       t.get("initial_sl", t.get("stop_loss")),
                "quantity":         t.get("quantity"),
                "setup_type":       t.get("setup_type", "Short Strangle"),
                "expiry":           t.get("expiry", ""),
                "pnl":              0,
                "exit_reason":      None,
                "trail_locked":     False,
            }
            state["last_signal"]["signal"] = "ACTIVE"
            LOG_LINES.append(f"[INFO]  [{_ts()}] Restored position from trades.json: {t.get('ce_symbol')} / {t.get('pe_symbol')}")
            break

    if not state["active_position"]:
        restored = _load_open_position_from_sheet()
        if restored:
            state["active_position"] = True
            state["trades_today"]  += 1
            state["position_detail"] = restored
            state["last_signal"]["signal"] = "ACTIVE"
            LOG_LINES.append(f"[INFO]  [{_ts()}] Restored position from Google Sheets: {restored.get('ce_symbol')} / {restored.get('pe_symbol')}")

LOG_LINES = [
    f"[INFO]  [{_ts()}] FIFTO AI Trading server starting...",
    f"[INFO]  [{_ts()}] Agent auto-started in AUTO mode.",
    f"[INFO]  [{_ts()}] Connecting to AngelOne...",
]

_restore_daily_pnl()
_restore_open_position()
_load_settings()

# ── Notification queue (consumed by dashboard poll) ──
_NOTIF = []

def _send_telegram(text):
    """Send plain-text message to Telegram."""
    token   = config.get("telegram_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Telegram send skipped: missing token/chat_id")
        return False
    try:
        resp = _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=8
        )
        if resp.ok:
            return True
        LOG_LINES.append(f"[WARN]  [{_ts()}] Telegram send failed: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Telegram error: {e}")
        return False


def _notify(title, body, level="info"):
    """Push notification to dashboard + Telegram. Levels: info / success / warning / danger."""
    _NOTIF.append({"title": title, "body": body, "level": level, "ts": _ts()})
    if len(_NOTIF) > 30:
        del _NOTIF[:-30]
    emoji = {"danger": "🚨", "warning": "⚠️", "success": "✅"}.get(level, "ℹ️")
    msg   = f"{emoji} *FIFTO* — *{title}*\n{body}"
    # Send danger/critical alerts immediately in background; others are best-effort
    threading.Thread(target=_send_telegram, args=(msg,), daemon=True).start()


# ── Market timing helpers ──

def _is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    today = now.date()
    if today.isoformat() in _nse_holidays:
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


def _market_closed_reason():
    """Return a human-readable reason why the market is closed, or None if open."""
    now = datetime.now()
    if now.weekday() == 5:
        return "Weekend (Saturday)"
    if now.weekday() == 6:
        return "Weekend (Sunday)"
    today = now.date().isoformat()
    if today in _nse_holidays:
        name = _NSE_HOLIDAYS_2026.get(today, "NSE Holiday")
        return f"NSE Holiday — {name}"
    t = now.time()
    if t < _time(9, 15):
        return "Before market hours (opens 9:15 AM)"
    if t > _time(15, 30):
        return "After market hours (closed 3:30 PM)"
    return None


# ── Setup checks ──

def _update_checks():
    state["checks"].pop("event", None)   # removed — market closure handles holidays
    vix   = state["market"]["vix"]
    avail = state["funds"]["available_cash"]
    pnl   = state["daily_pnl"]
    pcr   = state["market"]["pcr"]
    ivp   = state["market"].get("iv_percentile")
    emaf  = state["market"].get("ema_trend_flat")

    mkt_open = _is_market_open()
    state["checks"]["vix"]    = (vix is not None) and (config["vix_min"] <= vix <= config["vix_max"])
    # IV check: use 30-day historical percentile when available (>= 10 days history);
    # fall back to raw iv_atm > 10 when history is thin; None = skip if no data at all.
    iv_atm = state["market"].get("iv_atm")
    if ivp is not None:
        state["checks"]["iv"] = (ivp > 40)          # historical percentile path
    elif iv_atm is not None:
        state["checks"]["iv"] = (iv_atm > 10.0)     # fallback: raw ATM IV > 10%
    else:
        state["checks"]["iv"] = None                 # no data — skip
    state["checks"]["ema"]    = emaf
    state["checks"]["pcr"]    = None if pcr is None else (0.8 <= pcr <= 1.4)
    # Margin: True/False during market hours; None (waiting) before market opens
    state["checks"]["margin"] = (avail > 0) if (mkt_open or avail > 0) else None
    state["checks"]["gate"]   = pnl > -config["daily_loss_limit"]
    # ── Weekly loss gate ──
    week_pnl  = _calc_weekly_pnl()
    state["checks"]["weekly_gate"] = week_pnl > -config.get("weekly_loss_limit", 60000)
    # ── Monthly loss gate ──
    month_pnl = _calc_monthly_pnl()
    state["checks"]["monthly_gate"] = month_pnl > -config.get("monthly_loss_limit", 150000)
    # ── Stop-after-SL circuit breaker ──
    if config.get("stop_after_sl") and state.get("_sl_hit_today"):
        state["checks"]["gate"] = False


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


def _calc_weekly_pnl():
    """Sum P&L of all closed trades this Monday–Sunday week."""
    trades = state.get("trade_history", [])
    today  = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())  # Monday
    total = 0.0
    for t in trades:
        exit_t = t.get("exit_time")
        pnl    = t.get("final_pnl")
        if not exit_t or pnl is None:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_t) if "T" in str(exit_t) else datetime.strptime(str(exit_t).strip(), "%d-%m-%Y  %H:%M:%S")
            if exit_dt.date() >= week_start:
                total += float(pnl)
        except Exception:
            continue
    return round(total, 2)


def _calc_monthly_pnl():
    """Sum P&L of all closed trades this calendar month."""
    trades = state.get("trade_history", [])
    now    = datetime.now()
    total  = 0.0
    for t in trades:
        exit_t = t.get("exit_time")
        pnl    = t.get("final_pnl")
        if not exit_t or pnl is None:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_t) if "T" in str(exit_t) else datetime.strptime(str(exit_t).strip(), "%d-%m-%Y  %H:%M:%S")
            if exit_dt.year == now.year and exit_dt.month == now.month:
                total += float(pnl)
        except Exception:
            continue
    return round(total, 2)


def _all_checks_pass():
    """All mandatory checks must be True. Optional checks (pcr/iv/ema) pass when None (data unavailable).
    When market is trending (UP/DOWN), EMA-flat check is skipped — trend setups are valid in trending markets.
    Margin check is intentionally excluded — it is shown on the dashboard for information only.
    """
    c = state["checks"]
    # Mandatory: VIX range, daily/weekly/monthly loss gates
    # NOTE: margin is NOT included here — it is display-only on the dashboard
    mandatory = (
        c.get("vix")          is True and
        c.get("gate")         is True and
        c.get("weekly_gate")  is not False and
        c.get("monthly_gate") is not False
    )
    if not mandatory:
        return False
    # Mandatory: spot must not be in sharp momentum
    if not _spot_stable():
        return False
    # Optional: PCR / IV — if data is available it must pass; None = skip
    for key in ("pcr", "iv"):
        val = c.get(key)
        if val is False:
            return False
    # EMA flat check: only enforce for Short Strangle (FLAT trend).
    # If trend is UP or DOWN, skip EMA flat — spread setups thrive in trending markets.
    trend = state["market"].get("trend", "FLAT")
    if trend == "FLAT":
        ema_val = c.get("ema")
        if ema_val is False:   # explicitly not flat — skip strangle in trending market
            return False
    return True


# ── Trade execution lock — prevents concurrent orders from signal engine + Telegram ──
_trade_exec_lock = threading.Lock()

# ── NSE session + caches ──

_nse_session   = None
_nse_lock      = threading.Lock()
_chain_cache   = {"data": None, "ts": 0}
# NSE F&O trading holidays 2026 — hardcoded fallback so bot works even if API fetch fails.
# The live _fetch_nse_holidays() call overwrites this with the official list when it succeeds.
_NSE_HOLIDAYS_2026 = {
    "2026-01-26": "Republic Day",
    "2026-02-19": "Chhatrapati Shivaji Maharaj Jayanti",
    "2026-03-14": "Holi",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. B.R. Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-08-15": "Independence Day",
    "2026-08-27": "Ganesh Chaturthi",
    "2026-10-02": "Gandhi Jayanti",
    "2026-10-23": "Dussehra",
    "2026-11-13": "Diwali Laxmi Puja",
    "2026-11-14": "Diwali Balipratipada",
    "2026-11-25": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}
_nse_holidays  = set(_NSE_HOLIDAYS_2026.keys())   # pre-loaded; refreshed daily from NSE API
state["holidays_count"] = len(_nse_holidays)   # reflect hardcoded count immediately
_nifty_lotsize = 75             # updated daily from NSE CSV
_iv_history    = {"date": None, "values": []}   # legacy intraday (unused)
_angel_contract_cache = {"rows": [], "ts": 0}
_candle_cache = {}
_candle_backoff = {}
_spot_history = []

# ── Straddle premium history (for 20-day average filter) ──
_STRADDLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "straddle_history.json")

def _load_straddle_history():
    try:
        if os.path.exists(_STRADDLE_FILE):
            with open(_STRADDLE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _save_straddle_history(history):
    try:
        with open(_STRADDLE_FILE, "w") as f:
            json.dump(history[-30:], f)   # keep last 30 days
    except Exception:
        pass

def _record_straddle_premium(premium):
    """Record today's ATM straddle premium once per day, tagged with current DTE."""
    today = _date.today().isoformat()
    dte   = state.get("dte", 99)
    history = _load_straddle_history()
    if history and history[-1].get("date") == today:
        history[-1]["premium"] = round(premium, 2)
        history[-1]["dte"]     = dte
    else:
        history.append({"date": today, "premium": round(premium, 2), "dte": dte})
    _save_straddle_history(history)

def _straddle_premium_ok(current_premium):
    """Return True if current ATM straddle premium >= 80% of recent average.
    Compares against same-DTE records (±1) for apples-to-apples accuracy.
    Falls back to all history if < 3 same-DTE records exist."""
    history = _load_straddle_history()
    if len(history) < 5:
        return True   # not enough history yet — allow
    current_dte = state.get("dte", 99)
    same_dte = [d for d in history[:-1] if abs(d.get("dte", 99) - current_dte) <= 1]
    if len(same_dte) >= 3:
        reference = same_dte[-10:]   # last 10 same-DTE records
        label = f"DTE≈{current_dte} avg (n={len(reference)})"
    else:
        reference = history[-20:]    # fall back to all history
        label = f"20d avg (n={len(reference)})"
    avg = sum(d["premium"] for d in reference) / len(reference)
    threshold = avg * 0.80
    ok = current_premium >= threshold
    if not ok:
        LOG_LINES.append(
            f"[INFO]  [{_ts()}] Straddle filter [{label}]: ₹{current_premium:.0f} < 80% of "
            f"₹{avg:.0f} (threshold ₹{threshold:.0f}) — skip"
        )
    return ok

# ── Persistent IV ATM history (30-day percentile) ──
_IV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iv_history.json")

def _load_iv_history():
    try:
        if os.path.exists(_IV_FILE):
            with open(_IV_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _save_iv_history(history):
    try:
        with open(_IV_FILE, "w") as f:
            json.dump(history[-60:], f)   # keep last 60 days
    except Exception:
        pass

def _record_iv_daily(iv_atm):
    """Record today's IV ATM once per day (first good sample). Returns 30-day percentile,
    or None if < 10 days of history (fallback: use raw iv_atm > 10 check instead)."""
    if iv_atm is None:
        return None
    today = _date.today().isoformat()
    history = _load_iv_history()
    # Update or append today's entry
    if history and history[-1].get("date") == today:
        history[-1]["iv"] = round(iv_atm, 2)
    else:
        history.append({"date": today, "iv": round(iv_atm, 2)})
    _save_iv_history(history)
    # Need at least 10 days to compute a meaningful percentile
    ivs = [d["iv"] for d in history if d.get("iv") is not None]
    if len(ivs) < 10:
        return None   # not enough history — caller will use raw fallback
    below = sum(1 for v in ivs if v <= iv_atm)
    return round((below / len(ivs)) * 100.0, 1)


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
    # Skip API call on holidays/weekends — NSE website is down, hardcoded list already covers it
    today = _date.today()
    if today.weekday() >= 5 or today.isoformat() in _nse_holidays:
        LOG_LINES.append(f"[INFO]  [{_ts()}] Holiday fetch skipped (today is holiday/weekend — hardcoded list in use)")
        return _nse_holidays
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
                _nse_holidays = dates | set(_NSE_HOLIDAYS_2026.keys())   # merge API list with hardcoded fallback
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


def _calc_atr(candles, period=14):
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
    _src = state.get("ltp_source", "auto")

    # AngelOne path — skip if source is set to upstox-only
    if _src != "upstox" and angel_obj:
        try:
            resp = angel_obj.getCandleData(params)
            if resp and resp.get("status") and resp.get("data"):
                _candle_backoff.pop(cache_key, None)
                _candle_cache[cache_key] = {"ts": now_ts, "data": resp["data"]}
                return resp["data"]
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne candle fetch error ({interval}): {e}")
            _candle_backoff[cache_key] = now_ts + cache_ttl

    # Upstox path — primary when source=upstox, fallback otherwise
    if _src == "upstox" or _upstox_available():
        ux_candles = _upstox_candles(interval, lookback_days)
        if ux_candles:
            _candle_cache[cache_key] = {"ts": now_ts, "data": ux_candles}
            return ux_candles

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
    if len(closes) < 21:
        return None
    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    if len(ema9) < 2 or len(ema21) < 2:
        return None
    ema9_now, ema9_prev = ema9[-1], ema9[-2]
    ema21_now, ema21_prev = ema21[-1], ema21[-2]
    spread = abs(ema9_now - ema21_now)
    slope_gap = abs((ema9_now - ema9_prev) - (ema21_now - ema21_prev))
    return (spread <= atr_15m * 0.20) and (slope_gap <= atr_15m * 0.08)


def _detect_trend(candles, atr_15m):
    """Detect market trend direction from 15m candles.
    Returns: 'UP', 'DOWN', or 'FLAT'
    UP   = EMA9 > EMA21, both sloping up, spread > 0.3x ATR
    DOWN = EMA9 < EMA21, both sloping down, spread > 0.3x ATR
    FLAT = otherwise (use for Short Strangle)
    """
    if not candles or atr_15m in (None, 0):
        return "FLAT"
    closes = []
    for row in candles:
        try:
            closes.append(float(row[4]))
        except Exception:
            continue
    if len(closes) < 21:
        return "FLAT"
    ema9  = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    if len(ema9) < 3 or len(ema21) < 3:
        return "FLAT"
    e9, e9p  = ema9[-1], ema9[-3]
    e21, e21p = ema21[-1], ema21[-3]
    spread   = e9 - e21
    slope9   = e9 - e9p
    slope21  = e21 - e21p
    min_spread = atr_15m * 0.30
    if spread > min_spread and slope9 > 0 and slope21 > 0:
        return "UP"
    if spread < -min_spread and slope9 < 0 and slope21 < 0:
        return "DOWN"
    return "FLAT"


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
    iv_percentile = _record_iv_daily(iv_atm) if iv_atm is not None else None

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


def _safe_ltp_data(exchange, symbol, token, source=None):
    """Fetch LTP for a single instrument.
    source: "auto" (default) tries AngelOne first then Upstox,
            "angel" = AngelOne only, "upstox" = Upstox only.
    Returns {status: bool, data: {ltp: float}, broker: str} or {status: False}."""
    if source is None:
        source = state.get("ltp_source", "auto")

    def _try_angel():
        if not angel_obj:
            return {"status": False, "message": "angel_obj not ready"}
        try:
            resp = angel_obj.getMarketData("LTP", {exchange: [str(token)]})
            if resp and resp.get("status"):
                raw  = resp.get("data") or {}
                rows = raw if isinstance(raw, list) else raw.get("fetched", [])
                if rows:
                    row = rows[0]
                    ltp = row.get("ltp") or row.get("lastPrice") or row.get("close")
                    if ltp is not None:
                        return {"status": True, "data": {"ltp": float(ltp)}, "broker": "angel"}
            return {"status": False, "message": resp.get("message", "no data") if resp else "no response"}
        except Exception as e:
            return {"status": False, "message": str(e)}

    def _try_upstox():
        if not _upstox_available():
            return {"status": False, "message": "Upstox token not set"}
        if exchange == "NSE" and str(token) == "26000":
            ux = _upstox_ltp("NSE_INDEX|Nifty 50")
        elif exchange == "NSE" and str(token) == "99926017":
            ux = _upstox_ltp("NSE_INDEX|India VIX")
        elif exchange == "NFO" and symbol:
            ux = _upstox_ltp_nfo(symbol)
        else:
            ux = {"status": False}
        if ux.get("status"):
            ux["broker"] = "upstox"
        return ux

    if source == "upstox":
        result = _try_upstox()
        if not result.get("status"):
            LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox LTP failed for {symbol}, no fallback (source=upstox)")
        return result

    if source == "angel":
        return _try_angel()

    if source == "diversified":
        # Round-robin: alternate primary source each call for true load distribution
        turn = state.get("_diversify_turn", 0)
        state["_diversify_turn"] = 1 - turn  # flip for next call
        if turn == 0:
            result = _try_angel()
            if result.get("status"):
                return result
            ux = _try_upstox()
            if ux.get("status"):
                LOG_LINES.append(f"[INFO]  [{_ts()}] Diversified: Angel failed, Upstox served {symbol}")
                return ux
            return result
        else:
            result = _try_upstox()
            if result.get("status"):
                return result
            ang = _try_angel()
            if ang.get("status"):
                LOG_LINES.append(f"[INFO]  [{_ts()}] Diversified: Upstox failed, Angel served {symbol}")
                return ang
            return result

    # auto: AngelOne first, Upstox fallback
    result = _try_angel()
    if result.get("status"):
        return result
    ux = _try_upstox()
    if ux.get("status"):
        LOG_LINES.append(f"[INFO]  [{_ts()}] Upstox fallback LTP OK: {symbol} ₹{ux['data']['ltp']}")
        return ux
    return result


def _refresh_market_metrics():
    metrics = _compute_option_metrics(state["market"].get("nifty_spot"))
    candles_15m = _fetch_nifty_candles("FIFTEEN_MINUTE", 10)
    if not candles_15m:
        candles_15m = _synthetic_candles_from_spot(15)
    if len(candles_15m) < 2:
        candles_15m = _synthetic_candles_from_spot(1)
    atr_15m = _calc_atr(candles_15m, 14)
    ema_flat = _compute_ema_trend_flat(candles_15m, atr_15m)

    trend = _detect_trend(candles_15m, atr_15m)
    state["market"]["pcr"] = metrics["pcr"]
    state["market"]["iv_atm"] = metrics["iv_atm"]
    state["market"]["iv_percentile"] = metrics["iv_percentile"]
    state["market"]["atr_15m"] = atr_15m
    state["market"]["net_delta"] = metrics["net_delta"]
    state["market"]["ema_trend_flat"] = ema_flat
    state["market"]["trend"] = trend


# ── Strike selection ──

def _next_thursday():
    """Return the nearest NIFTY weekly expiry (Tuesday since SEBI change).
    If today is Tuesday and market has closed, return next Tuesday."""
    today = _date.today()
    days_ahead = (1 - today.weekday()) % 7   # Tuesday = weekday 1
    if days_ahead == 0 and datetime.now().time() >= _time(15, 30):
        days_ahead = 7   # today's expiry has passed, use next week
    return today + timedelta(days=days_ahead)


def _candidate_expiries(weeks=4):
    """Return upcoming Tuesday expiries (SEBI changed from Thursday to Tuesday),
    also include Monday as holiday-shift fallback, nearest first."""
    today = _date.today()
    now_t = datetime.now().time()
    out = []
    for day_offset in range(0, weeks * 7 + 7):
        dt = today + timedelta(days=day_offset)
        if dt.weekday() not in (0, 1):   # Monday (holiday shift) / Tuesday
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
                    r = _safe_ltp_data("NFO", symbol, token)
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

    # OI data is unavailable in this fallback path (direct LTP lookup, no option chain).
    # Warn prominently so the operator knows liquidity has not been verified.
    LOG_LINES.append(f"[WARN]  [{_ts()}] AngelOne fallback: OI data NOT available — liquidity unverified for CE {ce_strike} / PE {pe_strike}")
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


def _target_delta_by_vix():
    """Return target OTM delta for strike selection based on current VIX.
    Lower VIX → closer ATM (more premium).  Higher VIX → wider (more cushion)."""
    vix = state["market"].get("vix") or 16
    if vix < 14:  return 0.20   # calm: sell closer, collect more premium
    if vix < 20:  return 0.16   # normal regime
    return 0.12                  # volatile: go wider for safety


def _select_strikes(spot, setup_type="strangle"):
    """Select strikes for given setup_type: 'strangle', 'bull_put_spread', 'bear_call_spread'.
    Primary: AngelOne option chain. Fallback: AngelOne direct LTP lookup."""
    data = _fetch_option_chain()
    if data:
        expiry_dates = data.get("records", {}).get("expiryDates", [])
        all_records  = data.get("records", {}).get("data", [])
        if expiry_dates and all_records:
            # Trust AngelOne's expiry_dates[0] — it already accounts for holiday shifts
            nearest_expiry = expiry_dates[0]
            recs        = [r for r in all_records if r.get("expiryDate") == nearest_expiry]
            atm         = round(spot / 50) * 50
            min_premium = config.get("min_premium", 40)
            min_oi      = config.get("min_strike_oi", 300000)   # 3 lakh contracts

            # ── ATM straddle premium for 20-day filter (strangle only) ──
            if setup_type == "strangle":
                atm_rec = next((r for r in recs if r.get("strikePrice") == atm), None)
                if atm_rec:
                    atm_ce_ltp = atm_rec.get("CE", {}).get("lastPrice", 0) or 0
                    atm_pe_ltp = atm_rec.get("PE", {}).get("lastPrice", 0) or 0
                    atm_straddle = atm_ce_ltp + atm_pe_ltp
                    if atm_straddle > 0:
                        _record_straddle_premium(atm_straddle)
                        if not _straddle_premium_ok(atm_straddle):
                            LOG_LINES.append(f"[INFO]  [{_ts()}] Straddle avg filter blocked entry — premium too cheap")
                            return None

            # ── Delta-based strike selection ──
            # Target delta from VIX: 0.12 (wide/volatile) → 0.20 (calm/tight).
            # Pick the OTM strike whose delta is closest to target — higher
            # probability of expiring worthless vs premium-first walk.
            t_years      = _time_to_expiry_years(nearest_expiry)
            target_delta = _target_delta_by_vix()
            iv_fallback  = state["market"].get("iv_atm") or 15.0

            def _strike_delta(rec, is_call):
                """Compute delta for a chain record; fall back to BS with ATM IV."""
                side = rec.get("CE" if is_call else "PE") or {}
                iv   = side.get("impliedVolatility") or iv_fallback
                return _black_scholes_delta(spot, float(rec.get("strikePrice", 0)), iv, t_years, is_call)

            # ── CE side ──
            ce_strike = ce_ltp = ce_oi = None
            ce_candidates = []
            for rec in recs:
                s = float(rec.get("strikePrice", 0))
                if s <= atm or not rec.get("CE"):
                    continue
                ltp = rec["CE"].get("lastPrice", 0)
                if not ltp:
                    sym = rec["CE"].get("tradingsymbol")
                    tok = rec["CE"].get("symboltoken")
                    if sym and tok:
                        q = _safe_ltp_data("NFO", sym, tok)
                        if q.get("status"):
                            ltp = q["data"]["ltp"]
                if not ltp or ltp < min_premium:
                    continue
                delta = _strike_delta(rec, True)
                if delta is not None and 0.04 < delta < (target_delta + 0.06):
                    ce_candidates.append((s, ltp, float(rec["CE"].get("openInterest") or 0), delta))
            if ce_candidates:
                # Pick strike whose delta is closest to target (from the OTM side)
                ce_candidates.sort(key=lambda x: abs(x[3] - target_delta))
                best = ce_candidates[0]
                ce_strike, ce_ltp, ce_oi = int(best[0]), best[1], best[2]
                LOG_LINES.append(f"[INFO]  [{_ts()}] CE δ-select: {ce_strike} δ={best[3]:.3f} ₹{ce_ltp:.0f} (target δ={target_delta})")
            else:
                # Fallback: original premium-walk
                for offset in range(50, 500, 50):
                    s   = atm + offset
                    rec = next((r for r in recs if r.get("strikePrice") == s and r.get("CE")), None)
                    if rec:
                        ltp = rec["CE"].get("lastPrice", 0)
                        if not ltp:
                            sym = rec["CE"].get("tradingsymbol")
                            tok = rec["CE"].get("symboltoken")
                            if sym and tok:
                                q = _safe_ltp_data("NFO", sym, tok)
                                if q.get("status"):
                                    ltp = q["data"]["ltp"]
                        oi = float(rec["CE"].get("openInterest") or 0)
                        if ltp >= min_premium:
                            ce_strike, ce_ltp, ce_oi = s, ltp, oi
                            break

            # ── PE side ──
            pe_strike = pe_ltp = pe_oi = None
            pe_candidates = []
            for rec in recs:
                s = float(rec.get("strikePrice", 0))
                if s >= atm or not rec.get("PE"):
                    continue
                ltp = rec["PE"].get("lastPrice", 0)
                if not ltp:
                    sym = rec["PE"].get("tradingsymbol")
                    tok = rec["PE"].get("symboltoken")
                    if sym and tok:
                        q = _safe_ltp_data("NFO", sym, tok)
                        if q.get("status"):
                            ltp = q["data"]["ltp"]
                if not ltp or ltp < min_premium:
                    continue
                delta = _strike_delta(rec, False)   # put delta is negative
                if delta is not None and -(target_delta + 0.06) < delta < -0.04:
                    pe_candidates.append((s, ltp, float(rec["PE"].get("openInterest") or 0), delta))
            if pe_candidates:
                pe_candidates.sort(key=lambda x: abs(abs(x[3]) - target_delta))
                best = pe_candidates[0]
                pe_strike, pe_ltp, pe_oi = int(best[0]), best[1], best[2]
                LOG_LINES.append(f"[INFO]  [{_ts()}] PE δ-select: {pe_strike} δ={best[3]:.3f} ₹{pe_ltp:.0f} (target δ={target_delta})")
            else:
                # Fallback: original premium-walk
                for offset in range(50, 500, 50):
                    s   = atm - offset
                    rec = next((r for r in recs if r.get("strikePrice") == s and r.get("PE")), None)
                    if rec:
                        ltp = rec["PE"].get("lastPrice", 0)
                        if not ltp:
                            sym = rec["PE"].get("tradingsymbol")
                            tok = rec["PE"].get("symboltoken")
                            if sym and tok:
                                q = _safe_ltp_data("NFO", sym, tok)
                                if q.get("status"):
                                    ltp = q["data"]["ltp"]
                        oi = float(rec["PE"].get("openInterest") or 0)
                        if ltp >= min_premium:
                            pe_strike, pe_ltp, pe_oi = s, ltp, oi
                            break

            # ── Validation: setup-type-aware checks ──
            combined = round((ce_ltp or 0) + (pe_ltp or 0), 2)
            if setup_type == "bull_put_spread":
                # Only PE leg is sold — only check PE premium and OI
                if not pe_strike:
                    LOG_LINES.append(f"[INFO]  [{_ts()}] Bull Put Spread: no PE strike with premium ≥ ₹{min_premium}")
                    return None
                pe_oi_ok = (pe_oi or 0) >= min_oi
                if not pe_oi_ok:
                    LOG_LINES.append(f"[INFO]  [{_ts()}] OI filter (BPS): PE {pe_strike} OI={int(pe_oi or 0):,} < min {min_oi:,} — skipping")
                    return None
                LOG_LINES.append(f"[INFO]  [{_ts()}] OI OK (BPS): PE {pe_strike} OI={int(pe_oi):,}")
            elif setup_type == "bear_call_spread":
                # Only CE leg is sold — only check CE premium and OI
                if not ce_strike:
                    LOG_LINES.append(f"[INFO]  [{_ts()}] Bear Call Spread: no CE strike with premium ≥ ₹{min_premium}")
                    return None
                ce_oi_ok = (ce_oi or 0) >= min_oi
                if not ce_oi_ok:
                    LOG_LINES.append(f"[INFO]  [{_ts()}] OI filter (BCS): CE {ce_strike} OI={int(ce_oi or 0):,} < min {min_oi:,} — skipping")
                    return None
                LOG_LINES.append(f"[INFO]  [{_ts()}] OI OK (BCS): CE {ce_strike} OI={int(ce_oi):,}")
            else:
                # Short Strangle — both legs required, combined premium + both OI checks
                if not ce_strike or not pe_strike:
                    return None
                min_combined = config.get("min_combined_premium", 150)
                if combined < min_combined:
                    LOG_LINES.append(f"[INFO]  [{_ts()}] Strike scan: combined ₹{combined} < min ₹{min_combined}, skipping")
                    return None
                ce_oi_ok = (ce_oi or 0) >= min_oi
                pe_oi_ok = (pe_oi or 0) >= min_oi
                if not ce_oi_ok or not pe_oi_ok:
                    LOG_LINES.append(
                        f"[INFO]  [{_ts()}] OI filter: CE {ce_strike} OI={int(ce_oi or 0):,} "
                        f"PE {pe_strike} OI={int(pe_oi or 0):,} (min {min_oi:,}) — skipping"
                    )
                    return None
                LOG_LINES.append(
                    f"[INFO]  [{_ts()}] OI OK: CE {ce_strike} OI={int(ce_oi):,} | PE {pe_strike} OI={int(pe_oi):,}"
                )

            return {
                "expiry":    nearest_expiry,
                "atm":       atm,
                "ce_strike": ce_strike,
                "pe_strike": pe_strike,
                "ce_ltp":    ce_ltp,
                "pe_ltp":    pe_ltp,
                "premium":   combined,
                "ce_oi":     int(ce_oi or 0),
                "pe_oi":     int(pe_oi or 0),
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
    """Execute trade — SHORT STRANGLE, BULL PUT SPREAD, or BEAR CALL SPREAD."""
    if not _trade_exec_lock.acquire(blocking=False):
        LOG_LINES.append(f"[WARN]  [{_ts()}] _execute_trade: concurrent call blocked by lock — already executing")
        state["signal_pending"] = False
        return False
    try:
        return _execute_trade_inner(signal)
    finally:
        _trade_exec_lock.release()


def _execute_trade_inner(signal):
    """Inner trade execution logic (called under _trade_exec_lock)."""
    if state.get("active_position"):
        LOG_LINES.append(f"[WARN]  [{_ts()}] _execute_trade_inner: position already active — duplicate execution blocked")
        state["signal_pending"] = False
        return False
    lot_size   = state.get("lot_size") or config.get("lot_size", 75)
    # ── Risk-aware position sizing ──
    # If risk_per_trade_pct > 0, cap lots so max loss ≤ capital × risk_pct
    base_lots  = config.get("base_lots", 1)
    risk_pct   = float(config.get("risk_per_trade_pct", 0.02))
    capital    = float(config.get("capital", 1500000))
    premium    = float(signal.get("premium", 0) or 0)
    sl_mult    = _calc_sl_mult_by_dte(signal.get("expiry", ""))
    if risk_pct > 0 and premium > 0 and sl_mult > 1:
        # Loss at SL = (sl_price - entry_price) × qty = premium × (sl_mult - 1) × lot_size
        max_loss_per_lot = premium * (sl_mult - 1) * lot_size
        risk_budget      = capital * risk_pct
        risk_capped_lots = max(1, int(risk_budget / max_loss_per_lot))
        qty = min(base_lots, risk_capped_lots) * lot_size
        if risk_capped_lots < base_lots:
            LOG_LINES.append(f"[INFO]  [{_ts()}] Risk sizing: capped {base_lots}→{risk_capped_lots} lots (risk_budget=₹{risk_budget:,.0f}, max_loss/lot=₹{max_loss_per_lot:,.0f})")
    else:
        qty = base_lots * lot_size
    setup_type = signal.get("setup_type", "Short Strangle")

    # ── Bull Put Spread: SELL PE + BUY lower PE ──
    if setup_type == "Bull Put Spread":
        pe_symbol = signal["pe_symbol"]
        pe_token  = signal.get("pe_token") or _get_nfo_token(pe_symbol)
        hedge_sym = signal.get("hedge_pe_symbol")
        hedge_tok = signal.get("hedge_pe_token") or (hedge_sym and _get_nfo_token(hedge_sym))
        if not pe_token:
            LOG_LINES.append(f"[ERROR] [{_ts()}] Bull Put Spread: PE token missing {pe_symbol}")
            state["signal_pending"] = False
            return False
        LOG_LINES.append(f"[TRADE] [{_ts()}] Executing BULL PUT SPREAD — Qty {qty}")
        LOG_LINES.append(f"[TRADE] [{_ts()}] SELL {pe_symbol} @ ₹{signal['pe_ltp']:.2f}")
        sell_oid = _place_order(pe_symbol, pe_token, qty, "SELL")
        if not sell_oid:
            LOG_LINES.append(f"[ERROR] [{_ts()}] Bull Put Spread: SELL PE failed")
            state["signal_pending"] = False
            return False
        hedge_oid = None
        if hedge_tok:
            LOG_LINES.append(f"[TRADE] [{_ts()}] BUY {hedge_sym} (hedge)")
            hedge_oid = _place_order(hedge_sym, hedge_tok, qty, "BUY")
            if not hedge_oid:
                LOG_LINES.append(f"[ERROR] [{_ts()}] Bull Put Spread: hedge BUY failed — buying back sold PE to avoid naked short")
                buyback = _place_order(pe_symbol, pe_token, qty, "BUY")
                if not buyback:
                    LOG_LINES.append(f"[ERROR] [{_ts()}] PE BUYBACK FAILED — NAKED SHORT! MANUAL INTERVENTION REQUIRED")
                    _notify("🚨 Spread Partial Fill Crisis", f"PE {pe_symbol} sold but hedge failed AND PE buyback failed.\nManual intervention required immediately.", "danger")
                state["signal_pending"] = False
                return False
        sell_leg_ltp = signal["pe_ltp"]
        # Fetch hedge LTP at entry to compute net spread premium received
        hedge_pe_entry_ltp = 0.0
        if hedge_tok and hedge_sym:
            _hr = _safe_ltp_data("NFO", hedge_sym, hedge_tok)
            if _hr.get("status"):
                hedge_pe_entry_ltp = round(_hr["data"]["ltp"], 2)
        premium    = round(sell_leg_ltp - hedge_pe_entry_ltp, 2)  # net spread premium
        target_pct = _calc_target_pct_by_dte(signal.get("expiry", ""))
        sl_mult    = _calc_sl_mult_by_dte(signal.get("expiry", ""))
        target     = round(premium * target_pct, 2)
        sl         = round(premium * sl_mult, 2)
        trade_id   = f"T{int(time.time())}"
        state["active_position"]  = True
        state["signal_pending"]   = False
        state["pending_signal"]   = None
        state["trades_today"]    += 1
        pos = {
            "trade_id":         trade_id,
            "ce_strike":        None,
            "pe_strike":        signal["pe_strike"],
            "ce_symbol":        None,
            "pe_symbol":        pe_symbol,
            "ce_token":         None,
            "pe_token":         pe_token,
            "ce_order_id":      None,
            "pe_order_id":      sell_oid,
            "hedge_pe_symbol":  hedge_sym,
            "hedge_pe_token":   hedge_tok,
            "hedge_pe_order_id": hedge_oid,
            "quantity":         qty,
            "premium_received": premium,
            "premium":          premium,
            "pnl":              0.0,
            "target":           target,
            "sl":               sl,
            "initial_sl":       sl,
            "trail_locked":     False,
            "setup_type":       setup_type,
            "expiry":           signal.get("expiry", ""),
            "entry_time":       datetime.now().isoformat(),
            "ce_entry_ltp":     0,
            "pe_entry_ltp":     sell_leg_ltp,
            "hedge_pe_entry_ltp": hedge_pe_entry_ltp,
            "current_hedge_pe_ltp": hedge_pe_entry_ltp,
        }
        state["position_detail"] = pos
        state["last_signal"]["signal"] = "ACTIVE"
        _persist_entry(pos)
        LOG_LINES.append(f"[TRADE] [{_ts()}] Bull Put Spread entered | PE {signal['pe_strike']} @ ₹{sell_leg_ltp:.0f} | Hedge ₹{hedge_pe_entry_ltp:.0f} | Net ₹{premium:.0f} | Target ₹{target:.0f} | SL ₹{sl:.0f}")
        _notify("✅ Bull Put Spread Entered", f"SELL PE {signal['pe_strike']} @ ₹{sell_leg_ltp:.0f} | Hedge ₹{hedge_pe_entry_ltp:.0f}\nNet Premium ₹{premium:.0f} | Target ₹{target:.0f} | SL ₹{sl:.0f}", "success")
        return True

    # ── Bear Call Spread: SELL CE + BUY higher CE ──
    if setup_type == "Bear Call Spread":
        ce_symbol = signal["ce_symbol"]
        ce_token  = signal.get("ce_token") or _get_nfo_token(ce_symbol)
        hedge_sym = signal.get("hedge_ce_symbol")
        hedge_tok = signal.get("hedge_ce_token") or (hedge_sym and _get_nfo_token(hedge_sym))
        if not ce_token:
            LOG_LINES.append(f"[ERROR] [{_ts()}] Bear Call Spread: CE token missing {ce_symbol}")
            state["signal_pending"] = False
            return False
        LOG_LINES.append(f"[TRADE] [{_ts()}] Executing BEAR CALL SPREAD — Qty {qty}")
        LOG_LINES.append(f"[TRADE] [{_ts()}] SELL {ce_symbol} @ ₹{signal['ce_ltp']:.2f}")
        sell_oid = _place_order(ce_symbol, ce_token, qty, "SELL")
        if not sell_oid:
            LOG_LINES.append(f"[ERROR] [{_ts()}] Bear Call Spread: SELL CE failed")
            state["signal_pending"] = False
            return False
        hedge_oid = None
        if hedge_tok:
            LOG_LINES.append(f"[TRADE] [{_ts()}] BUY {hedge_sym} (hedge)")
            hedge_oid = _place_order(hedge_sym, hedge_tok, qty, "BUY")
            if not hedge_oid:
                LOG_LINES.append(f"[ERROR] [{_ts()}] Bear Call Spread: hedge BUY failed — buying back sold CE to avoid naked short")
                buyback = _place_order(ce_symbol, ce_token, qty, "BUY")
                if not buyback:
                    LOG_LINES.append(f"[ERROR] [{_ts()}] CE BUYBACK FAILED — NAKED SHORT! MANUAL INTERVENTION REQUIRED")
                    _notify("🚨 Spread Partial Fill Crisis", f"CE {ce_symbol} sold but hedge failed AND CE buyback failed.\nManual intervention required immediately.", "danger")
                state["signal_pending"] = False
                return False
        sell_leg_ltp = signal["ce_ltp"]
        # Fetch hedge LTP at entry to compute net spread premium received
        hedge_ce_entry_ltp = 0.0
        if hedge_tok and hedge_sym:
            _hr = _safe_ltp_data("NFO", hedge_sym, hedge_tok)
            if _hr.get("status"):
                hedge_ce_entry_ltp = round(_hr["data"]["ltp"], 2)
        premium    = round(sell_leg_ltp - hedge_ce_entry_ltp, 2)  # net spread premium
        target_pct = _calc_target_pct_by_dte(signal.get("expiry", ""))
        sl_mult    = _calc_sl_mult_by_dte(signal.get("expiry", ""))
        target     = round(premium * target_pct, 2)
        sl         = round(premium * sl_mult, 2)
        trade_id   = f"T{int(time.time())}"
        state["active_position"]  = True
        state["signal_pending"]   = False
        state["pending_signal"]   = None
        state["trades_today"]    += 1
        pos = {
            "trade_id":         trade_id,
            "ce_strike":        signal["ce_strike"],
            "pe_strike":        None,
            "ce_symbol":        ce_symbol,
            "pe_symbol":        None,
            "ce_token":         ce_token,
            "pe_token":         None,
            "ce_order_id":      sell_oid,
            "pe_order_id":      None,
            "hedge_ce_symbol":  hedge_sym,
            "hedge_ce_token":   hedge_tok,
            "hedge_ce_order_id": hedge_oid,
            "quantity":         qty,
            "premium_received": premium,
            "premium":          premium,
            "pnl":              0.0,
            "target":           target,
            "sl":               sl,
            "initial_sl":       sl,
            "trail_locked":     False,
            "setup_type":       setup_type,
            "expiry":           signal.get("expiry", ""),
            "entry_time":       datetime.now().isoformat(),
            "ce_entry_ltp":     sell_leg_ltp,
            "pe_entry_ltp":     0,
            "hedge_ce_entry_ltp": hedge_ce_entry_ltp,
            "current_hedge_ce_ltp": hedge_ce_entry_ltp,
        }
        state["position_detail"] = pos
        state["last_signal"]["signal"] = "ACTIVE"
        _persist_entry(pos)
        LOG_LINES.append(f"[TRADE] [{_ts()}] Bear Call Spread entered | CE {signal['ce_strike']} @ ₹{sell_leg_ltp:.0f} | Hedge ₹{hedge_ce_entry_ltp:.0f} | Net ₹{premium:.0f} | Target ₹{target:.0f} | SL ₹{sl:.0f}")
        _notify("✅ Bear Call Spread Entered", f"SELL CE {signal['ce_strike']} @ ₹{sell_leg_ltp:.0f} | Hedge ₹{hedge_ce_entry_ltp:.0f}\nNet Premium ₹{premium:.0f} | Target ₹{target:.0f} | SL ₹{sl:.0f}", "success")
        return True

    # ── Default: Short Strangle ──
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

    # Partial fill recovery — one leg filled but other failed → buy back filled leg
    if ce_oid and not pe_oid:
        LOG_LINES.append(f"[ERROR] [{_ts()}] PE leg failed! Buying back CE to avoid naked short...")
        buyback = _place_order(ce_symbol, ce_token, qty, "BUY")
        if not buyback:
            LOG_LINES.append(f"[ERROR] [{_ts()}] CE BUYBACK FAILED — NAKED SHORT! MANUAL INTERVENTION REQUIRED")
            _notify("🚨 Partial Fill Crisis", f"CE {ce_symbol} sold but PE failed AND CE buyback failed.\nManual intervention required immediately.", "danger")
        state["signal_pending"] = False
        return False

    if not ce_oid and pe_oid:
        LOG_LINES.append(f"[ERROR] [{_ts()}] CE leg failed! Buying back PE to avoid naked short...")
        buyback = _place_order(pe_symbol, pe_token, qty, "BUY")
        if not buyback:
            LOG_LINES.append(f"[ERROR] [{_ts()}] PE BUYBACK FAILED — NAKED SHORT! MANUAL INTERVENTION REQUIRED")
            _notify("🚨 Partial Fill Crisis", f"PE {pe_symbol} sold but CE failed AND PE buyback failed.\nManual intervention required immediately.", "danger")
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
            "entry_time":       entry_time.strftime("%d-%m-%Y  %H:%M:%S"),
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
        q = _safe_ltp_data("NFO", symbol, token)
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


_squaring_lock = threading.Lock()

def _square_off_position():
    """Square off position — handles Short Strangle, Bull Put Spread, Bear Call Spread."""
    with _squaring_lock:
        if not state["active_position"] or not state["position_detail"]:
            return
        if state["squaring_off"]:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Square-off already in progress — skipping duplicate call")
            return
        state["squaring_off"] = True
    pos       = state["position_detail"]
    qty       = pos["quantity"]
    exit_time = datetime.now()
    setup     = pos.get("setup_type", "Short Strangle")
    LOG_LINES.append(f"[TRADE] [{_ts()}] Squaring off {setup}...")

    # Buy back only the legs that were actually sold
    _exit_failures = []
    if setup == "Bull Put Spread":
        # Buy back sold PE leg
        if pos.get("pe_symbol") and pos.get("pe_token"):
            oid = _place_order(pos["pe_symbol"], pos["pe_token"], qty, "BUY")
            if not oid:
                _exit_failures.append(f"BUY {pos['pe_symbol']} failed")
        # Sell the hedge PE leg (we bought it at entry, sell it at exit)
        if pos.get("hedge_pe_symbol") and pos.get("hedge_pe_token"):
            oid = _place_order(pos["hedge_pe_symbol"], pos["hedge_pe_token"], qty, "SELL")
            if not oid:
                _exit_failures.append(f"SELL hedge {pos['hedge_pe_symbol']} failed")
    elif setup == "Bear Call Spread":
        # Buy back sold CE leg
        if pos.get("ce_symbol") and pos.get("ce_token"):
            oid = _place_order(pos["ce_symbol"], pos["ce_token"], qty, "BUY")
            if not oid:
                _exit_failures.append(f"BUY {pos['ce_symbol']} failed")
        # Sell the hedge CE leg
        if pos.get("hedge_ce_symbol") and pos.get("hedge_ce_token"):
            oid = _place_order(pos["hedge_ce_symbol"], pos["hedge_ce_token"], qty, "SELL")
            if not oid:
                _exit_failures.append(f"SELL hedge {pos['hedge_ce_symbol']} failed")
    else:
        # Short Strangle — buy back both legs
        ce_exit_oid = None
        pe_exit_oid = None
        if pos.get("ce_symbol") and pos.get("ce_token"):
            ce_exit_oid = _place_order(pos["ce_symbol"], pos["ce_token"], qty, "BUY")
            if not ce_exit_oid:
                _exit_failures.append(f"BUY {pos['ce_symbol']} failed")
        if pos.get("pe_symbol") and pos.get("pe_token"):
            pe_exit_oid = _place_order(pos["pe_symbol"], pos["pe_token"], qty, "BUY")
            if not pe_exit_oid:
                _exit_failures.append(f"BUY {pos['pe_symbol']} failed")

    if _exit_failures:
        msg = " | ".join(_exit_failures)
        LOG_LINES.append(f"[ERROR] [{_ts()}] EXIT ORDER FAILURE — {msg} — MANUAL INTERVENTION REQUIRED")
        _notify("🚨 Exit Order Failed", f"{msg}\nVerify broker position. Manual close may be required.", "danger")

    gross_pnl  = round(pos.get("pnl", 0), 2)   # pos["pnl"] is already qty-multiplied
    lot_size   = state.get("lot_size") or config.get("lot_size", 65)
    lots       = max(1, round(qty / lot_size)) if lot_size > 0 else 1
    brokerage  = round(config.get("brokerage_per_lot", 120) * lots, 2)
    final_pnl  = round(gross_pnl - brokerage, 2)
    if brokerage > 0:
        LOG_LINES.append(f"[INFO]  [{_ts()}] Brokerage deducted: ₹{brokerage:,.0f} ({lots} lots × ₹{config.get('brokerage_per_lot',120)}) | Gross ₹{gross_pnl:,.0f} → Net ₹{final_pnl:,.0f}")
    state["closed_pnl"] += final_pnl        # accumulate realized P&L for the day
    state["daily_pnl"]   = state["closed_pnl"]
    # trades_today already incremented at entry — do NOT increment again here

    # Calculate trade duration
    try:
        _et = pos["entry_time"]
        _entry_dt = datetime.fromisoformat(str(_et)) if "T" in str(_et) else datetime.strptime(str(_et).strip(), "%d-%m-%Y  %H:%M:%S")
        _dur_mins = round((exit_time - _entry_dt).total_seconds() / 60)
    except Exception:
        _dur_mins = None

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
        # Short Strangle stores "ce_entry_price"/"pe_entry_price"; spreads store "ce_entry_ltp"/"pe_entry_ltp"
        "ce_entry_ltp":    pos.get("ce_entry_ltp") or pos.get("ce_entry_price", ""),
        "pe_entry_ltp":    pos.get("pe_entry_ltp") or pos.get("pe_entry_price", ""),
        "ce_exit_ltp":     pos.get("current_ce_ltp", ""),
        "pe_exit_ltp":     pos.get("current_pe_ltp", ""),
        "hedge_pe_exit_ltp": pos.get("current_hedge_pe_ltp", ""),
        "hedge_ce_exit_ltp": pos.get("current_hedge_ce_ltp", ""),
        "hedge_symbol":    pos.get("hedge_ce_symbol") or pos.get("hedge_pe_symbol", ""),
        "hedge_entry_ltp": pos.get("hedge_ce_entry_ltp") or pos.get("hedge_pe_entry_ltp", ""),
        "hedge_exit_ltp":  pos.get("current_hedge_ce_ltp") or pos.get("current_hedge_pe_ltp", ""),
        "target":          pos["target"],
        "initial_sl":      pos.get("initial_sl", pos.get("sl", "")),
        "stop_loss":       pos["sl"],
        "trail_locked":    pos.get("trail_locked", False),
        "gross_pnl":       gross_pnl,
        "brokerage":       brokerage,
        "final_pnl":       final_pnl,
        "exit_reason":     pos.get("exit_reason", "UNKNOWN"),
        "expiry":          pos.get("expiry", ""),
        "duration_mins":   _dur_mins,
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
    _setup_label = pos.get("setup_type", "Short Strangle")
    if _setup_label == "Bull Put Spread":
        _leg_label = f"PE {pos['pe_strike']}"
    elif _setup_label == "Bear Call Spread":
        _leg_label = f"CE {pos['ce_strike']}"
    else:
        _leg_label = f"CE {pos['ce_strike']} | PE {pos['pe_strike']}"
    _notify(
        "✅ Position Closed",
        f"{_setup_label} | {_leg_label}\n"
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

    # ── Wait for market open before doing anything ──
    if not _is_market_open():
        LOG_LINES.append(f"[INFO]  [{_ts()}] Signal engine: market closed — waiting for 9:15 AM open")
    while not _is_market_open():
        time.sleep(60)
    LOG_LINES.append(f"[INFO]  [{_ts()}] Signal engine: market open — starting scan")

    last_signal_ts = time.time()  # enforce 5-min cooldown from market open

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
                    spot  = state["market"]["nifty_spot"]
                    trend = state["market"].get("trend", "FLAT")
                    if spot:
                        # ── Determine setup type BEFORE calling _select_strikes ──
                        # FLAT  → Short Strangle (sell both sides, collect premium)
                        # UP    → Bull Put Spread (sell PE, hedge with lower PE)
                        # DOWN  → Bear Call Spread (sell CE, hedge with higher CE)
                        pcr = state["market"].get("pcr") or 1.0
                        if trend == "UP" and pcr >= 1.0:
                            setup_type_hint = "bull_put_spread"
                        elif trend == "DOWN" and pcr <= 1.0:
                            setup_type_hint = "bear_call_spread"
                        else:
                            # Spread conditions not met — fall back to strangle, but only if EMA is flat.
                            # _all_checks_pass() skips EMA for UP/DOWN trends (spread setups), so enforce
                            # it here for the strangle fallback.
                            if trend == "UP" and pcr < 1.0:
                                LOG_LINES.append(
                                    f"[INFO]  [{_ts()}] Bull Put Spread skipped — Trend UP but PCR {pcr:.2f} < 1.0 not confirming"
                                )
                            elif trend == "DOWN" and pcr > 1.0:
                                LOG_LINES.append(
                                    f"[INFO]  [{_ts()}] Bear Call Spread skipped — Trend DOWN but PCR {pcr:.2f} > 1.0 not confirming"
                                )
                            if state["checks"].get("ema") is False:
                                LOG_LINES.append(
                                    f"[INFO]  [{_ts()}] Strangle also skipped — EMA not flat (trend {trend}); no valid setup this cycle"
                                )
                                time.sleep(60)
                                continue
                            setup_type_hint = "strangle"

                        strikes = _select_strikes(spot, setup_type_hint)
                        if strikes:
                            ce_sym   = _build_angel_symbol(strikes["ce_strike"], "CE", strikes["expiry"]) if strikes.get("ce_strike") else None
                            pe_sym   = _build_angel_symbol(strikes["pe_strike"], "PE", strikes["expiry"]) if strikes.get("pe_strike") else None
                            ce_token = (strikes.get("ce_token") or (ce_sym and _get_nfo_token(ce_sym))) if ce_sym else None
                            pe_token = (strikes.get("pe_token") or (pe_sym and _get_nfo_token(pe_sym))) if pe_sym else None

                            if trend == "UP" and pcr >= 1.0:
                                setup_type = "Bull Put Spread"
                                # Sell the chosen PE, buy PE 100 points lower as hedge
                                hedge_pe_strike = strikes["pe_strike"] - 100
                                hedge_pe_sym    = _build_angel_symbol(hedge_pe_strike, "PE", strikes["expiry"])
                                hedge_pe_token  = _get_nfo_token(hedge_pe_sym)
                                confidence      = 78
                                reason = (
                                    f"Trend UP (EMA bullish) | PCR {pcr:.2f} | "
                                    f"Sell PE {strikes['pe_strike']} @ ₹{strikes['pe_ltp']:.0f} | "
                                    f"Buy PE {hedge_pe_strike} hedge"
                                )
                                LOG_LINES.append(f"[TRADE] [{_ts()}] Trend UP — Bull Put Spread selected")
                                signal = {
                                    "signal":           "SELL",
                                    "confidence":       confidence,
                                    "reason":           reason,
                                    "setup_type":       setup_type,
                                    "ce_strike":        None,
                                    "pe_strike":        strikes["pe_strike"],
                                    "ce_ltp":           0,
                                    "pe_ltp":           strikes["pe_ltp"],
                                    "ce_symbol":        None,
                                    "pe_symbol":        pe_sym,
                                    "ce_token":         None,
                                    "pe_token":         pe_token,
                                    "hedge_pe_strike":  hedge_pe_strike,
                                    "hedge_pe_symbol":  hedge_pe_sym,
                                    "hedge_pe_token":   hedge_pe_token,
                                    "premium":          strikes["pe_ltp"],
                                    "approx_entry":     strikes["pe_ltp"],
                                    "approx_sl":        round(strikes["pe_ltp"] * 1.5, 2),
                                    "expiry":           strikes["expiry"],
                                }
                            elif trend == "DOWN" and pcr <= 1.0:
                                setup_type = "Bear Call Spread"
                                # Sell chosen CE, buy CE 100 points higher as hedge
                                hedge_ce_strike = strikes["ce_strike"] + 100
                                hedge_ce_sym    = _build_angel_symbol(hedge_ce_strike, "CE", strikes["expiry"])
                                hedge_ce_token  = _get_nfo_token(hedge_ce_sym)
                                confidence      = 78
                                reason = (
                                    f"Trend DOWN (EMA bearish) | PCR {pcr:.2f} | "
                                    f"Sell CE {strikes['ce_strike']} @ ₹{strikes['ce_ltp']:.0f} | "
                                    f"Buy CE {hedge_ce_strike} hedge"
                                )
                                LOG_LINES.append(f"[TRADE] [{_ts()}] Trend DOWN — Bear Call Spread selected")
                                signal = {
                                    "signal":           "SELL",
                                    "confidence":       confidence,
                                    "reason":           reason,
                                    "setup_type":       setup_type,
                                    "ce_strike":        strikes["ce_strike"],
                                    "pe_strike":        None,
                                    "ce_ltp":           strikes["ce_ltp"],
                                    "pe_ltp":           0,
                                    "ce_symbol":        ce_sym,
                                    "pe_symbol":        None,
                                    "ce_token":         ce_token,
                                    "pe_token":         None,
                                    "hedge_ce_strike":  hedge_ce_strike,
                                    "hedge_ce_symbol":  hedge_ce_sym,
                                    "hedge_ce_token":   hedge_ce_token,
                                    "premium":          strikes["ce_ltp"],
                                    "approx_entry":     strikes["ce_ltp"],
                                    "approx_sl":        round(strikes["ce_ltp"] * 1.5, 2),
                                    "expiry":           strikes["expiry"],
                                }
                            else:
                                # Default: Short Strangle
                                setup_type = "Short Strangle"
                                confidence = 82
                                reason = (
                                    f"Trend FLAT | OI CE {strikes.get('ce_oi',0):,} PE {strikes.get('pe_oi',0):,} | "
                                    f"Premium ₹{strikes['premium']:.0f}"
                                )
                                signal = {
                                    "signal":       "SELL",
                                    "confidence":   confidence,
                                    "reason":       reason,
                                    "setup_type":   setup_type,
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
                                "confidence":   signal["confidence"],
                                "reason":       signal["reason"],
                                "setup_type":   signal["setup_type"],
                                "ce_strike":    signal["ce_strike"],
                                "pe_strike":    signal["pe_strike"],
                                "premium":      signal["premium"],
                                "approx_entry": signal["approx_entry"],
                                "approx_sl":    signal["approx_sl"],
                            })

                            if state["execution_mode"] == "AUTO":
                                state["signal_pending"] = True   # block re-entry until execution completes
                                state["signal_generated_at"] = time.time()
                                LOG_LINES.append(f"[TRADE] [{_ts()}] Signal: {setup_type} | AUTO — executing now")
                                _notify(
                                    f"📊 Signal — {setup_type} — Auto Executing",
                                    f"{signal['reason']}\nPlacing orders now...",
                                    "info"
                                )
                                threading.Thread(target=_execute_trade, args=(signal,), daemon=True).start()
                            else:
                                state["signal_pending"] = True
                                state["signal_generated_at"] = time.time()
                                LOG_LINES.append(f"[TRADE] [{_ts()}] Signal: {setup_type} | MANUAL — waiting for user")
                                _notify(
                                    f"📊 Signal Ready — {setup_type}",
                                    f"{signal['reason']}\nClick Execute Trade to confirm.",
                                    "warning"
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

def _refresh_position_ltp():
    """Fetch current LTPs for active position and update P&L. Called by fast LTP thread."""
    if not (state.get("active_position") and state.get("position_detail")):
        return
    pos        = state["position_detail"]
    setup      = pos.get("setup_type", "Short Strangle")
    ce_token   = pos.get("ce_token")
    pe_token   = pos.get("pe_token")
    qty        = pos.get("quantity", 1)
    entry_prem = pos.get("premium_received", 0)

    current_cost = None
    broker_used  = None

    if setup == "Bull Put Spread" and pe_token:
        pe_r = _safe_ltp_data("NFO", pos.get("pe_symbol"), pe_token)
        hedge_tok = pos.get("hedge_pe_token")
        hedge_r   = _safe_ltp_data("NFO", pos.get("hedge_pe_symbol"), hedge_tok) if hedge_tok else None
        if pe_r.get("status"):
            pe_ltp = pe_r["data"]["ltp"]
            pos["current_pe_ltp"] = round(pe_ltp, 2)
            pos["current_ce_ltp"] = 0
            if hedge_r and hedge_r.get("status"):
                hedge_ltp = hedge_r["data"]["ltp"]
                pos["current_hedge_pe_ltp"] = round(hedge_ltp, 2)
                current_cost = pe_ltp - hedge_ltp   # net spread cost
            else:
                current_cost = pe_ltp               # fallback: short leg only
            broker_used = pe_r.get("broker", "unknown")

    elif setup == "Bear Call Spread" and ce_token:
        ce_r = _safe_ltp_data("NFO", pos.get("ce_symbol"), ce_token)
        hedge_tok = pos.get("hedge_ce_token")
        hedge_r   = _safe_ltp_data("NFO", pos.get("hedge_ce_symbol"), hedge_tok) if hedge_tok else None
        if ce_r.get("status"):
            ce_ltp = ce_r["data"]["ltp"]
            pos["current_ce_ltp"] = round(ce_ltp, 2)
            pos["current_pe_ltp"] = 0
            if hedge_r and hedge_r.get("status"):
                hedge_ltp = hedge_r["data"]["ltp"]
                pos["current_hedge_ce_ltp"] = round(hedge_ltp, 2)
                current_cost = ce_ltp - hedge_ltp   # net spread cost
            else:
                current_cost = ce_ltp               # fallback: short leg only
            broker_used = ce_r.get("broker", "unknown")

    elif setup == "Short Strangle" and ce_token and pe_token:
        ce_r = _safe_ltp_data("NFO", pos.get("ce_symbol"), ce_token)
        pe_r = _safe_ltp_data("NFO", pos.get("pe_symbol"), pe_token)
        b1 = ce_r.get("broker"); b2 = pe_r.get("broker")
        broker_used = b1 if b1 == b2 else f"{b1}/{b2}"
        if ce_r.get("status") and pe_r.get("status"):
            ce_ltp = ce_r["data"]["ltp"]
            pe_ltp = pe_r["data"]["ltp"]
            current_cost = ce_ltp + pe_ltp
            pos["current_ce_ltp"] = round(ce_ltp, 2)
            pos["current_pe_ltp"] = round(pe_ltp, 2)
        elif ce_r.get("status"):
            pos["current_ce_ltp"] = round(ce_r["data"]["ltp"], 2)
        elif pe_r.get("status"):
            pos["current_pe_ltp"] = round(pe_r["data"]["ltp"], 2)

    if current_cost is not None:
        open_pnl = entry_prem - current_cost
        pos["pnl"]         = round(open_pnl * qty, 2)
        state["daily_pnl"] = round(state["closed_pnl"] + open_pnl * qty, 2)
        state["ltp_last_used"]    = broker_used
        state["ltp_last_updated"] = datetime.now().strftime("%H:%M:%S")


def fast_ltp_refresh():
    """Fetch position LTPs at the user-configured interval for smooth P&L updates."""
    time.sleep(25)  # let position_monitor start first
    while True:
        if not _is_market_open():
            time.sleep(60)   # idle when market is closed — no LTPs to refresh
            continue
        try:
            _refresh_position_ltp()
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Fast LTP refresh error: {e}")
        interval = max(1, min(30, int(state.get("fetch_interval", 5))))
        time.sleep(interval)


def position_monitor():
    """Monitor active position SL/target every 30s."""
    time.sleep(20)

    while True:
        try:
            if state["active_position"] and state["position_detail"]:
                pos        = state["position_detail"]
                setup      = pos.get("setup_type", "Short Strangle")
                entry_prem = pos.get("premium_received", 0)

                # Retry token lookup if missing (e.g. restored from disk before login)
                if not pos.get("ce_token") and pos.get("ce_symbol"):
                    t = _get_nfo_token(pos["ce_symbol"])
                    if t: pos["ce_token"] = t
                if not pos.get("pe_token") and pos.get("pe_symbol"):
                    t = _get_nfo_token(pos["pe_symbol"])
                    if t: pos["pe_token"] = t

                # ── LTP fetch (fast_ltp_refresh handles 5s updates; this ensures 30s fallback) ──
                _refresh_position_ltp()

                # Derive current_cost from freshly updated LTPs
                ce_ltp = pos.get("current_ce_ltp", 0) or 0
                pe_ltp = pos.get("current_pe_ltp", 0) or 0
                if setup == "Bull Put Spread":
                    hedge_pe_ltp = pos.get("current_hedge_pe_ltp", 0) or 0
                    current_cost = (pe_ltp - hedge_pe_ltp) if pe_ltp else None
                elif setup == "Bear Call Spread":
                    hedge_ce_ltp = pos.get("current_hedge_ce_ltp", 0) or 0
                    current_cost = (ce_ltp - hedge_ce_ltp) if ce_ltp else None
                else:
                    current_cost = (ce_ltp + pe_ltp) if (ce_ltp or pe_ltp) else None

                if current_cost is not None:
                    qty      = pos.get("quantity", 1)
                    open_pnl = entry_prem - current_cost

                    # Label for notifications
                    leg_label = (
                        f"PE {pos['pe_strike']}" if setup == "Bull Put Spread" else
                        f"CE {pos['ce_strike']}" if setup == "Bear Call Spread" else
                        f"CE {pos['ce_strike']} | PE {pos['pe_strike']}"
                    )

                    # ── Trail 1: lock at breakeven at 20% profit ──
                    trail_pct  = config.get("trail_trigger_pct", 0.20)
                    profit_pct = (entry_prem - current_cost) / entry_prem if entry_prem > 0 else 0
                    if profit_pct >= trail_pct and not pos.get("trail_locked"):
                        old_sl = pos["sl"]
                        pos["sl"]           = entry_prem
                        pos["trail_locked"] = True
                        LOG_LINES.append(
                            f"[TRADE] [{_ts()}] TRAIL-1 ACTIVATED ✓ | {setup} | "
                            f"Profit {profit_pct*100:.0f}% ≥ {trail_pct*100:.0f}% | "
                            f"SL ₹{old_sl:.0f} → ₹{entry_prem:.0f} (breakeven)"
                        )
                        _notify(
                            "🔒 Trail-1 — Breakeven Locked",
                            f"{setup} | {leg_label}\nProfit {profit_pct*100:.0f}% | SL → ₹{entry_prem:.0f}",
                            "success"
                        )
                        _save_active_position(pos)

                    # ── Trail 2: at 60% profit lock in 30% gain ──
                    if profit_pct >= 0.60 and not pos.get("trail2_locked"):
                        new_sl = round(entry_prem * 0.70, 2)   # allow cost to rise to 70% of entry (30% profit locked)
                        if new_sl < pos["sl"]:                  # only ever tighten, never widen
                            old_sl = pos["sl"]
                            pos["sl"]            = new_sl
                            pos["trail2_locked"] = True
                            LOG_LINES.append(
                                f"[TRADE] [{_ts()}] TRAIL-2 ACTIVATED ✓ | {setup} | "
                                f"Profit {profit_pct*100:.0f}% ≥ 60% | "
                                f"SL ₹{old_sl:.0f} → ₹{new_sl:.0f} (30% gain locked)"
                            )
                            _notify(
                                "🔒 Trail-2 — 30% Gain Locked",
                                f"{setup} | {leg_label}\nProfit {profit_pct*100:.0f}% | SL → ₹{new_sl:.0f}",
                                "success"
                            )
                            _save_active_position(pos)

                    # ── Target hit ──
                    if current_cost <= entry_prem - pos["target"]:
                        if not pos.get("exit_notified"):
                            pos["exit_notified"] = True
                            pos["exit_reason"]   = "TARGET"
                            LOG_LINES.append(f"[TRADE] [{_ts()}] TARGET HIT ✓ | {setup} | P&L ₹{open_pnl:,.0f} | Squaring off")
                            _notify(
                                "🎯 Target Hit!",
                                f"{setup} | {leg_label}\nP&L: +₹{pos['pnl']:,.0f} | Entry ₹{entry_prem:.0f} → Now ₹{current_cost:.0f}",
                                "success"
                            )
                            _square_off_position()

                    # ── Stop Loss hit ──
                    elif current_cost > pos["sl"]:
                        if not pos.get("exit_notified"):
                            pos["exit_notified"] = True
                            pos["exit_reason"]   = "STOP_LOSS"
                            state["_sl_hit_today"] = True  # circuit breaker flag
                            LOG_LINES.append(f"[TRADE] [{_ts()}] SL HIT ✗ | {setup} | P&L ₹{open_pnl:,.0f} | Squaring off")
                            _notify(
                                "🛑 Stop Loss Hit",
                                f"{setup} | {leg_label}\nP&L: ₹{pos['pnl']:,.0f} | Entry ₹{entry_prem:.0f} → Now ₹{current_cost:.0f}",
                                "danger"
                            )
                            _square_off_position()

                    else:
                        now_t = datetime.now()
                        # Expiry cut-time
                        try:
                            expiry_dt = datetime.strptime(pos.get("expiry", ""), "%d-%b-%Y").date()
                            h, m = map(int, config.get("expiry_cut_time", "13:00").split(":"))
                            if now_t.date() == expiry_dt and now_t.time() >= _time(h, m):
                                if not pos.get("exit_notified"):
                                    pos["exit_notified"] = True
                                    pos["exit_reason"]   = "EXPIRY_CUT"
                                    LOG_LINES.append(f"[TRADE] [{_ts()}] EXPIRY CUT-TIME | {setup} | P&L ₹{open_pnl:,.0f} | Squaring off")
                                    _notify("⏰ Expiry Cut-Time Exit", f"{setup} | {leg_label}\nP&L: ₹{pos['pnl']:,.0f}", "warning")
                                    _square_off_position()
                        except Exception:
                            pass

                        # Dead zone
                        if state["active_position"] and not pos.get("exit_notified"):
                            dh, dm = map(int, config.get("dead_zone_start", "14:30").split(":"))
                            if now_t.time() >= _time(dh, dm):
                                pos["exit_notified"] = True
                                pos["exit_reason"]   = "DEAD_ZONE"
                                LOG_LINES.append(f"[TRADE] [{_ts()}] DEAD ZONE | {setup} | P&L ₹{open_pnl:,.0f} | Squaring off")
                                _notify("⏰ Dead Zone Exit", f"{setup} | {leg_label}\nP&L: ₹{pos['pnl']:,.0f}", "warning")
                                _square_off_position()

                        # EOD exit
                        if state["active_position"] and not pos.get("exit_notified") and now_t.time() >= _time(15, 20):
                            pos["exit_notified"] = True
                            pos["exit_reason"]   = "EOD_EXIT"
                            LOG_LINES.append(f"[TRADE] [{_ts()}] EOD AUTO-EXIT | {setup} | P&L ₹{open_pnl:,.0f} | Squaring off")
                            _notify("⏰ EOD Auto-Exit — 3:20 PM", f"{setup} | {leg_label}\nP&L: ₹{pos['pnl']:,.0f}", "warning")
                            _square_off_position()

        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Position monitor error: {e}")

        time.sleep(30)


# ── Market data thread ──

def fetch_market_data():
    """Fetch Nifty/VIX/margin every 15s and refresh derived market metrics every minute."""
    time.sleep(5)
    metrics_counter      = 3   # first derived refresh happens on the first live cycle
    _last_date           = _date.today()
    _holidays_fetched    = False   # retry holiday fetch once market opens
    _daily_summary_sent  = False   # reset each day

    while True:
        # ── Daily summary at 3:35 PM ──
        now_t = datetime.now()
        if not _daily_summary_sent and now_t.time() >= _time(15, 35):
            _daily_summary_sent = True
            try:
                today_str    = _date.today().isoformat()
                today_trades = [t for t in state.get("trade_history", [])
                                if str(t.get("entry_time", "")).startswith(today_str)]
                n   = len(today_trades)
                wins = sum(1 for t in today_trades if (t.get("final_pnl") or 0) > 0)
                pnl  = state.get("daily_pnl", 0)
                checks_failed = [k for k, v in state.get("checks", {}).items() if v is False]
                lines = [f"Trades: {n} | Wins: {wins}/{n} | P&L: ₹{pnl:,.0f}"]
                for t in today_trades:
                    reason = t.get("exit_reason", "?")
                    tpnl   = t.get("final_pnl", 0)
                    prem   = t.get("premium_received", 0)
                    lines.append(f"  {t.get('setup_type','?')} | Entry ₹{prem:.0f} | {reason} ₹{tpnl:,.0f}")
                if checks_failed:
                    lines.append(f"Checks failed: {', '.join(checks_failed)}")
                if n == 0:
                    lines.append("No trades taken today.")
                _notify("📊 Daily Summary", "\n".join(lines), "info")
                LOG_LINES.append(f"[INFO]  [{_ts()}] Daily summary sent | {n} trades | ₹{pnl:,.0f}")
            except Exception as e:
                LOG_LINES.append(f"[WARN]  [{_ts()}] Daily summary error: {e}")

        # ── Daily reset at start of each new trading day ──
        today = _date.today()
        if today != _last_date:
            _last_date          = today
            _daily_summary_sent = False   # reset for new day
            state["daily_pnl"]      = 0.0
            state["closed_pnl"]     = 0.0
            state["trades_today"]   = 0
            state["_sl_hit_today"]  = False  # reset circuit breaker each day
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

        # ── Gate: skip all live fetching when market is closed ──
        if not _is_market_open():
            time.sleep(300)   # re-check every 5 min; do nothing until market opens
            continue

        _src = state.get("ltp_source", "auto")
        _can_fetch = (
            (angel_obj and connection["status"] == "connected") or
            (_src == "upstox" and _upstox_available())
        )
        if _can_fetch:
            try:
                t0    = time.time()
                nifty = _safe_ltp_data("NSE", "Nifty 50", "26000")
                vix   = _safe_ltp_data("NSE", "India VIX", "99926017")
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
    from flask import make_response, redirect
    # ── Upstox OAuth callback: ?code=xxx lands here ──
    code = request.args.get("code")
    if code and _UPSTOX_API_KEY:
        try:
            r = _requests.post(
                "https://api.upstox.com/v2/login/authorization/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "code":          code,
                    "client_id":     _UPSTOX_API_KEY,
                    "client_secret": _UPSTOX_API_SECRET,
                    "redirect_uri":  _UPSTOX_REDIRECT,
                    "grant_type":    "authorization_code",
                },
                timeout=10,
            )
            if r.status_code == 200:
                token = r.json().get("access_token", "")
                if token:
                    _upstox_save_token(token)
                    LOG_LINES.append(f"[INFO]  [{_ts()}] Upstox login successful ✓")
                    return redirect("/?upstox=ok")
            LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox token exchange failed: {r.text[:200]}")
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox token exchange error: {e}")
    resp = make_response(send_from_directory(BASE, "nifty_dashboard.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.route("/api/upstox/login-url")
def upstox_login_url():
    """Return the Upstox OAuth login URL for the dashboard to open."""
    try:
        if not _UPSTOX_API_KEY:
            return jsonify({"ok": False, "msg": "Upstox API key not configured"})
        import urllib.parse
        url = (
            "https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code"
            f"&client_id={_UPSTOX_API_KEY}"
            f"&redirect_uri={urllib.parse.quote(_UPSTOX_REDIRECT, safe='')}"
        )
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox login-url error: {e}")
        return jsonify({"ok": False, "msg": "Unable to get Upstox login URL"}), 500

@app.route("/api/upstox/status")
def upstox_status():
    """Return Upstox connection status."""
    try:
        return jsonify({
            "configured": bool(_UPSTOX_API_KEY),
            "connected":  _upstox_available(),
        })
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox status error: {e}")
        return jsonify({"configured": False, "connected": False, "msg": "Upstox status unavailable"}), 500

@app.route("/api/ltp-source", methods=["GET", "POST"])
def ltp_source_api():
    """GET: return current LTP source + fetch interval.
    POST {source, interval}: update either or both."""
    if request.method == "GET":
        return jsonify({
            "source":           state.get("ltp_source", "auto"),
            "fetch_interval":   state.get("fetch_interval", 5),
            "last_used":        state.get("ltp_last_used"),
            "last_updated":     state.get("ltp_last_updated"),
            "upstox_available": _upstox_available(),
            "angel_available":  bool(angel_obj),
        })
    data = request.get_json(force=True, silent=True) or {}
    if "source" in data:
        src = data["source"]
        if src not in ("auto", "angel", "upstox", "diversified"):
            return jsonify({"ok": False, "msg": "Invalid source. Use: auto | angel | upstox | diversified"}), 400
        state["ltp_source"] = src
        LOG_LINES.append(f"[INFO]  [{_ts()}] LTP source changed to: {src}")
    if "interval" in data:
        try:
            iv = max(1, min(30, int(data["interval"])))
            state["fetch_interval"] = iv
            LOG_LINES.append(f"[INFO]  [{_ts()}] Fetch interval changed to: {iv}s")
        except (ValueError, TypeError):
            return jsonify({"ok": False, "msg": "Invalid interval. Must be 1–30 seconds"}), 400
    _save_settings()
    return jsonify({"ok": True, "source": state.get("ltp_source"), "fetch_interval": state.get("fetch_interval")})


@app.route("/api/settings/reset", methods=["POST"])
def api_reset_settings():
    """Reset all trading settings to factory defaults. Requires PIN."""
    err = _require_pin()
    if err: return err
    for k, v in _CONFIG_DEFAULTS.items():
        config[k] = v
    state["ltp_source"]    = "auto"
    state["fetch_interval"] = 5
    state["execution_mode"] = config.get("execution_mode", "AUTO")
    # Delete settings.json so next restart also uses defaults
    try:
        if os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)
    except Exception:
        pass
    LOG_LINES.append(f"[INFO]  [{_ts()}] Settings reset to factory defaults.")
    return jsonify({"ok": True, "msg": "Settings reset to defaults."})


@app.route("/api/restart", methods=["POST"])
def restart_server():
    """Restart the server process. Requires PIN."""
    import sys, subprocess, threading
    data = request.get_json(force=True, silent=True) or {}
    pin  = str(data.get("pin", "")).strip()
    correct = str(config.get("login_pin", os.getenv("LOGIN_PIN", "5599")))
    if pin != correct:
        return jsonify({"ok": False, "msg": "Invalid PIN"}), 403
    LOG_LINES.append(f"[INFO]  [{_ts()}] Server restart requested from dashboard.")
    def _do_restart():
        time.sleep(1)
        subprocess.Popen([sys.executable] + sys.argv)
        os._exit(0)
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"ok": True, "msg": "Restarting..."})


@app.route("/api/fetch-capital", methods=["GET"])
def api_fetch_capital():
    """Fetch live available capital from AngelOne broker and return it."""
    _fetch_margin()
    avail = state["funds"].get("available_cash", 0)
    used  = state["funds"].get("used_margin", 0)
    total = avail + used
    return jsonify({
        "ok":        True,
        "available": avail,
        "used":      used,
        "total":     total,
        "source":    "AngelOne" if angel_obj else "unavailable",
    })


@app.route("/api/sync-position", methods=["POST"])
def api_sync_position():
    """Fetch live positions from AngelOne and attempt to recover an active position
    if the app lost state (e.g. after a crash). Requires PIN."""
    err = _require_pin()
    if err: return err

    if not angel_obj:
        return jsonify({"ok": False, "msg": "AngelOne not connected."}), 400

    try:
        # AngelOne positions endpoint
        if hasattr(angel_obj, "_routes"):
            angel_obj._routes["getPosition"] = "/rest/secure/angelbroking/order/v1/getPosition"
        r = angel_obj._getRequest("getPosition") if hasattr(angel_obj, "_getRequest") else None
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Broker API error: {e}"}), 500

    if not r or not r.get("status"):
        return jsonify({"ok": False, "msg": "No positions returned or API error."}), 400

    positions = r.get("data") or []
    nfo_pos = [p for p in positions if p.get("exchange") == "NFO" and
               abs(float(p.get("netqty", 0))) > 0]

    if not nfo_pos:
        return jsonify({"ok": True, "msg": "No open NFO positions found.", "positions": []})

    # If app already has an active position, don't overwrite
    if state.get("active_position"):
        return jsonify({
            "ok":       True,
            "msg":      "App already has an active position tracked. No sync needed.",
            "positions": nfo_pos,
        })

    # Surface broker positions for the user to review — don't auto-restore to avoid mistakes
    summary = []
    for p in nfo_pos:
        summary.append({
            "symbol":   p.get("tradingsymbol", ""),
            "qty":      p.get("netqty", 0),
            "avg_price":p.get("averageprice", 0),
            "ltp":      p.get("ltp", 0),
            "pnl":      p.get("unrealised", 0),
        })
    LOG_LINES.append(f"[INFO]  [{_ts()}] Position sync: found {len(nfo_pos)} open NFO legs from broker.")
    return jsonify({
        "ok":       True,
        "msg":      f"Found {len(nfo_pos)} open NFO position(s) on broker. App position not set — review below.",
        "positions": summary,
        "action":   "review",
    })


@app.route("/fifto_logo.png")
def logo():
    p = os.path.join(BASE, "fifto_logo.png")
    return send_from_directory(BASE, "fifto_logo.png") if os.path.exists(p) else ("", 404)

@app.route("/manifest.json")
def pwa_manifest():
    return send_from_directory(BASE, "manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js")
def pwa_sw():
    from flask import make_response
    resp = make_response(send_from_directory(BASE, "sw.js", mimetype="application/javascript"))
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/icon-192.svg")
def icon192():
    return send_from_directory(BASE, "icon-192.svg", mimetype="image/svg+xml")

@app.route("/icon-512.svg")
def icon512():
    return send_from_directory(BASE, "icon-512.svg", mimetype="image/svg+xml")

@app.route("/icon-mask.svg")
def iconmask():
    return send_from_directory(BASE, "icon-mask.svg", mimetype="image/svg+xml")

@app.route("/overview")
def overview():
    return send_from_directory(BASE, "project_overview.html")

@app.route("/eq")
def eq_market():
    from flask import make_response
    resp = make_response(send_from_directory(BASE, "eq_market.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.route("/api/eq/config")
def api_eq_config():
    """Return config needed by the EQ market page."""
    return jsonify({"openai_key": os.getenv("OPENAI_API_KEY", "")})

@app.route("/api/eq/plan", methods=["GET"])
def api_eq_plan():
    """Generate an intraday EQ trading plan via OpenAI GPT-4o.
    Returns a JSON trade plan with preMarket, setups, and watchlist."""
    oai_key = os.getenv("OPENAI_API_KEY", "")
    if not oai_key:
        return jsonify({"ok": False, "error": "OPENAI_API_KEY not set in .env"}), 500

    from datetime import datetime as _dt
    today = _dt.now().strftime("%A, %d %B %Y")

    schema = {
        "type": "object",
        "properties": {
            "preMarket": {
                "type": "object",
                "properties": {
                    "globalCues":   {"type": "string"},
                    "domesticCues": {"type": "string"},
                    "sentiment":    {"type": "string"},
                    "niftyLevels":  {"type": "string"}
                },
                "required": ["globalCues", "domesticCues", "sentiment", "niftyLevels"],
                "additionalProperties": False
            },
            "setups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol":       {"type": "string"},
                        "isFnO":        {"type": "boolean"},
                        "cmp":          {"type": "number"},
                        "tradeType":    {"type": "string"},
                        "setupType":    {"type": "string"},
                        "entryPrice":   {"type": "number"},
                        "entryTrigger": {"type": "string"},
                        "stopLoss":     {"type": "number"},
                        "target1":      {"type": "number"},
                        "target2":      {"type": "number"},
                        "confidence":   {"type": "number"},
                        "reasoning":    {"type": "string"}
                    },
                    "required": ["symbol","isFnO","cmp","tradeType","setupType","entryPrice","entryTrigger","stopLoss","target1","target2","confidence","reasoning"],
                    "additionalProperties": False
                }
            },
            "watchlist": {
                "type": "object",
                "properties": {
                    "aPlus":  {"type": "array", "items": {"type": "string"}},
                    "bSetup": {"type": "array", "items": {"type": "string"}},
                    "avoid":  {"type": "array", "items": {"type": "string"}}
                },
                "required": ["aPlus", "bSetup", "avoid"],
                "additionalProperties": False
            }
        },
        "required": ["preMarket", "setups", "watchlist"],
        "additionalProperties": False
    }

    try:
        r = _requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {oai_key}"},
            json={
                "model": "gpt-4o",
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "trading_plan", "strict": True, "schema": schema}
                },
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert intraday trader with 15+ years in Indian equity markets (NSE/BSE). "
                            "Generate realistic, high-probability intraday trade setups. "
                            "Include both F&O stocks (isFnO: true) and cash-only stocks (isFnO: false). "
                            "Use realistic current Indian stock prices. confidence is 1-5. tradeType is LONG or SHORT. "
                            "Provide 6-10 setups with diverse sectors."
                        )
                    },
                    {
                        "role": "user",
                        "content": f"Today is {today}. Generate a complete intraday trading plan for Indian equity markets covering F&O and cash segments. Include realistic CMPs, entry levels, stop-losses, and targets."
                    }
                ]
            },
            timeout=45
        )
        if r.status_code != 200:
            LOG_LINES.append(f"[WARN]  [{_ts()}] OpenAI EQ plan error: {r.status_code} {r.text[:200]}")
            return jsonify({"ok": False, "error": f"OpenAI error {r.status_code}: {r.json().get('error', {}).get('message', r.text[:200])}"}), 502
        content = r.json()["choices"][0]["message"]["content"]
        plan = json.loads(content)
        LOG_LINES.append(f"[INFO]  [{_ts()}] EQ plan generated: {len(plan.get('setups', []))} setups via GPT-4o")
        return jsonify({"ok": True, "plan": plan})
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] OpenAI EQ plan exception: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

def _eq_normalise_symbol(raw: str) -> str:
    """Normalise GPT-generated stock names to NSE trading symbols.
    Strips spaces/special chars and maps known aliases to correct NSE codes."""
    _ALIAS = {
        "HDFC BANK": "HDFCBANK", "HDFCBANK LTD": "HDFCBANK",
        "ICICI BANK": "ICICIBANK", "AXIS BANK": "AXISBANK",
        "KOTAK BANK": "KOTAKBANK", "KOTAK MAHINDRA BANK": "KOTAKBANK",
        "STATE BANK OF INDIA": "SBIN", "SBI": "SBIN",
        "TATA MOTORS": "TATAMOTORS", "TATA CONSULTANCY": "TCS",
        "TATA CONSULTANCY SERVICES": "TCS",
        "MARUTI SUZUKI": "MARUTI", "MARUTI SUZUKI INDIA": "MARUTI",
        "WIPRO LTD": "WIPRO", "HCL TECHNOLOGIES": "HCLTECH",
        "HCL TECH": "HCLTECH", "TECH MAHINDRA": "TECHM",
        "BAJAJ FINANCE": "BAJFINANCE", "BAJAJ FINSERV": "BAJAJFINSV",
        "HINDUSTAN UNILEVER": "HINDUNILVR", "HUL": "HINDUNILVR",
        "BHARAT PETROLEUM": "BPCL", "INDIAN OIL": "IOC",
        "OIL AND NATURAL GAS": "ONGC", "NTPC LTD": "NTPC",
        "POWER GRID": "POWERGRID", "ADANI PORTS": "ADANIPORTS",
        "ADANI ENTERPRISES": "ADANIENT", "ADANI ENTERPRISE": "ADANIENT",
        "DMART": "DMART", "AVENUE SUPERMARTS": "DMART",
        "EICHER MOTORS": "EICHERMOT", "HERO MOTOCORP": "HEROMOTOCO",
        "HERO MOTO": "HEROMOTOCO", "SUN PHARMA": "SUNPHARMA",
        "SUN PHARMACEUTICAL": "SUNPHARMA", "DR REDDY": "DRREDDY",
        "DR REDDYS": "DRREDDY", "CIPLA LTD": "CIPLA",
        "DIVIS LAB": "DIVISLAB", "DIVIS LABORATORIES": "DIVISLAB",
        "ASIAN PAINTS": "ASIANPAINT", "BERGER PAINTS": "BERGEPAINT",
        "ULTRATECH CEMENT": "ULTRACEMCO", "AMBUJA CEMENT": "AMBUJACEMENT",
        "BHARTI AIRTEL": "BHARTIARTL", "AIRTEL": "BHARTIARTL",
        "INDUSIND BANK": "INDUSINDBK", "FEDERAL BANK": "FEDERALBNK",
        "YES BANK": "YESBANK", "PNB": "PNB", "PUNJAB NATIONAL BANK": "PNB",
        "BANK OF BARODA": "BANKBARODA", "CANARA BANK": "CANBK",
        "TATA STEEL": "TATASTEEL", "JSPL": "JINDALSTEL",
        "JINDAL STEEL": "JINDALSTEL", "SAIL": "SAIL",
        "VEDANTA LTD": "VEDL", "HINDALCO": "HINDALCO",
        "APOLLO HOSPITALS": "APOLLOHOSP", "FORTIS": "FORTIS",
        "ZOMATO LTD": "ZOMATO", "PAYTM": "PAYTM",
        "NYKAA": "NYKAA", "FSN ECOMMERCE": "NYKAA",
        "PIDILITE": "PIDILITIND", "PIDILITE INDUSTRIES": "PIDILITIND",
        "TITAN COMPANY": "TITAN", "TITAN LTD": "TITAN",
        "NESTLE INDIA": "NESTLEIND", "ITC LTD": "ITC",
    }
    s = raw.strip().upper()
    if s in _ALIAS:
        return _ALIAS[s]
    # Remove common suffixes and spaces
    for suffix in [" LTD", " LIMITED", " INDUSTRIES", " CORP", " INDIA", " NSE"]:
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
    return s.replace(" ", "").replace(".", "").replace("&", "AND").replace("-", "")

def _eq_yahoo_ltp(symbols: list) -> dict:
    """Fetch LTP for NSE equity symbols via Yahoo Finance (no auth needed).
    Symbol format: RELIANCE → RELIANCE.NS"""
    result = {}
    for sym in symbols:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS"
            r = _requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r.status_code == 200:
                data = r.json()
                result_arr = data.get("chart", {}).get("result")
                if result_arr and len(result_arr) > 0:
                    meta = result_arr[0].get("meta", {})
                    ltp  = meta.get("regularMarketPrice") or meta.get("previousClose")
                    if ltp:
                        result[sym] = float(ltp)
        except Exception as exc:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Yahoo {sym} exception: {exc}")
    return result

@app.route("/api/eq/ltp", methods=["POST"])
def api_eq_ltp():
    """Fetch live LTP for NSE equity (cash market) symbols.
    Body: {"symbols": ["RELIANCE", "HDFC BANK", ...]}
    Returns: {"data": {"RELIANCE": 2450.5, ...}, "source": "upstox"|"yahoo"|"unavailable"}
    """
    body    = request.get_json(force=True, silent=True) or {}
    raw_syms = [s for s in body.get("symbols", []) if s][:20]
    # Normalise to NSE trading symbols
    symbols = [_eq_normalise_symbol(s) for s in raw_syms]
    sym_map = {norm: raw for norm, raw in zip(symbols, raw_syms)}  # norm→original

    if not symbols:
        return jsonify({"data": {}, "source": "none"})

    result = {}
    source = "unavailable"

    # ── 1. Try Upstox bulk LTP ──
    if _upstox_available():
        try:
            instrument_keys = ",".join(f"NSE_EQ|{s}" for s in symbols)
            r = _requests.get(
                "https://api.upstox.com/v2/market-quote/ltp",
                headers=_upstox_headers(),
                params={"instrument_key": instrument_keys},
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                for sym in symbols:
                    entry = data.get(f"NSE_EQ:{sym}") or data.get(f"NSE_EQ|{sym}")
                    if entry:
                        lp = entry.get("last_price") or entry.get("ltp")
                        if lp:
                            result[sym] = float(lp)
                if result:
                    source = "upstox"
                    LOG_LINES.append(f"[INFO]  [{_ts()}] EQ LTP via Upstox: {len(result)}/{len(symbols)}")
            else:
                LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox EQ LTP HTTP {r.status_code}")
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Upstox EQ LTP error: {e}")

    # ── 2. Fallback to Yahoo Finance for missing symbols ──
    missing = [s for s in symbols if s not in result]
    if missing:
        try:
            yahoo_data = _eq_yahoo_ltp(missing)
            for sym, ltp in yahoo_data.items():
                result[sym] = ltp
            if yahoo_data:
                source = "upstox+yahoo" if result and source == "upstox" else "yahoo"
                LOG_LINES.append(f"[INFO]  [{_ts()}] EQ LTP via Yahoo: {len(yahoo_data)} symbols")
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Yahoo EQ LTP error: {e}")

    # Map normalised keys back to the original (raw) keys the frontend sent,
    # so j.data["HDFC BANK"] finds the price even though Yahoo fetched via "HDFCBANK".
    raw_result = {sym_map.get(sym, sym): ltp for sym, ltp in result.items()}
    return jsonify({"data": raw_result, "source": source, "count": len(raw_result)})


# ── EQ Trade persistence ──────────────────────────────────────────────────────

_EQ_SHEET_HEADERS = [
    "Trade ID", "Symbol", "Trade Type", "Setup Type",
    "Entry Time", "Entry Price", "Stop Loss", "Target", "Qty",
    "Status", "Exit Time", "Exit Price", "P&L", "Source",
]

def _get_or_create_eq_sheet():
    """Return the 'EQ Trades' worksheet in the same Google Spreadsheet, creating it if missing."""
    try:
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
            ws = sh.worksheet("EQ Trades")
            existing = ws.row_values(1)
            if len(existing) < len(_EQ_SHEET_HEADERS):
                ws.resize(cols=len(_EQ_SHEET_HEADERS))
                ws.update(range_name="A1", values=[_EQ_SHEET_HEADERS])
            return ws
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title="EQ Trades", rows=2000, cols=len(_EQ_SHEET_HEADERS))
            ws.update(range_name="A1", values=[_EQ_SHEET_HEADERS])
            return ws
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] EQ Sheet init error: {e}")
        return None

def _eq_sheet_upsert(trade: dict):
    """Write or update a single trade row in the EQ Trades sheet (run in background thread)."""
    try:
        ws = _get_or_create_eq_sheet()
        if not ws:
            return
        tid = str(trade.get("id", ""))
        if not tid:
            return
        # Coerce numeric fields so Google Sheets recognises them as numbers (not strings)
        def _n(v, default=""):
            try:
                return float(v) if v not in (None, "") else default
            except (TypeError, ValueError):
                return default

        pnl = _n(trade.get("realizedPnl"))
        row = [
            tid,
            trade.get("symbol", ""),
            trade.get("tradeType", ""),
            trade.get("setupType", ""),
            trade.get("timestamp", ""),
            _n(trade.get("entryPrice")),
            _n(trade.get("stopLoss")),
            _n(trade.get("target")),
            _n(trade.get("qty")),
            trade.get("status", ""),
            trade.get("exitTime", ""),
            _n(trade.get("exitPrice")),
            pnl,
            trade.get("source", "manual"),
        ]
        cell = ws.find(tid, in_column=1)
        if cell:
            # gspread ≥ 5 uses ws.update(range, values); older uses ws.update(range, values)
            try:
                ws.update(f"A{cell.row}:N{cell.row}", [row])
            except TypeError:
                ws.update(range_name=f"A{cell.row}:N{cell.row}", values=[row])
        else:
            ws.append_row(row, value_input_option="USER_ENTERED")
        LOG_LINES.append(f"[INFO]  [{_ts()}] EQ Sheet: upserted {tid} pnl={pnl}")
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] EQ Sheet upsert failed: {e}")

def _eq_load_trades_local():
    if os.path.exists(EQ_TRADES_FILE):
        try:
            with open(EQ_TRADES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _eq_save_trade_local(trade: dict):
    try:
        trades = _eq_load_trades_local()
        tid = str(trade.get("id", ""))
        idx = next((i for i, t in enumerate(trades) if str(t.get("id", "")) == tid), None)
        if idx is not None:
            trades[idx] = trade
        else:
            trades.append(trade)
        with open(EQ_TRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2, default=str)
    except Exception as e:
        LOG_LINES.append(f"[WARN]  [{_ts()}] EQ local save failed: {e}")

@app.route("/api/eq/trades", methods=["GET"])
def api_eq_trades_get():
    """Return all persisted EQ paper trades."""
    return jsonify({"trades": _eq_load_trades_local()})

@app.route("/api/eq/trades", methods=["POST"])
def api_eq_trades_post():
    """Upsert a single EQ trade. Body: {"trade": {...}}
    Saves to eq_trades.json and async-writes to Google Sheet."""
    body  = request.get_json(force=True, silent=True) or {}
    trade = body.get("trade")
    if not trade or not trade.get("id"):
        return jsonify({"ok": False, "error": "Missing trade or id"}), 400
    _eq_save_trade_local(trade)
    import threading
    threading.Thread(target=_eq_sheet_upsert, args=(trade,), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/eq/trades/clear", methods=["POST"])
def api_eq_trades_clear():
    """Clear all EQ trades (dev/reset use). Requires PIN."""
    data = request.get_json(force=True, silent=True) or {}
    pin  = str(data.get("pin", "")).strip()
    correct = str(config.get("login_pin", os.getenv("LOGIN_PIN", "5599")))
    if pin != correct:
        return jsonify({"ok": False, "msg": "Invalid PIN"}), 403
    try:
        with open(EQ_TRADES_FILE, "w") as f:
            json.dump([], f)
    except Exception:
        pass
    return jsonify({"ok": True, "msg": "EQ trades cleared"})

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
    state["overall_pnl"]      = round(overall, 2)
    state["overall_pnl_pct"]  = round((overall / capital * 100), 2) if capital else 0
    state["weekly_pnl"]       = _calc_weekly_pnl()
    state["monthly_pnl"]      = _calc_monthly_pnl()
    state["weekly_loss_limit"]  = config.get("weekly_loss_limit", 60000)
    state["monthly_loss_limit"] = config.get("monthly_loss_limit", 150000)
    state["daily_loss_limit"]   = config.get("daily_loss_limit", 45000)
    state["vix_min"]            = config.get("vix_min", 13)
    state["vix_max"]            = config.get("vix_max", 28)
    state["max_trades"]         = config.get("max_trades_per_day", 3)
    # Signal age in seconds (so frontend can show countdown/warning)
    gen = state.get("signal_generated_at")
    state["signal_age_secs"] = round(time.time() - gen) if gen else None
    # Build response — filter checks to known keys only
    import copy
    state["checks"].pop("event", None)   # remove from live state too
    resp = copy.deepcopy(state)
    known_checks = {"vix", "iv", "ema", "pcr", "margin", "gate", "weekly_gate", "monthly_gate"}
    resp["checks"] = {k: v for k, v in resp["checks"].items() if k in known_checks}
    if "event" in state["checks"]:
        LOG_LINES.append(f"[WARN]  [{_ts()}] BUG: event key found in checks: {state['checks']}")
    resp["market_open"]          = _is_market_open()
    resp["market_closed_reason"] = _market_closed_reason()   # e.g. "NSE Holiday — Ambedkar Jayanti"

    # ── No-trade reason: which checks are blocking entry ──
    if not state.get("active_position") and _is_market_open() and _is_entry_window():
        c    = state.get("checks", {})
        trend_now = state["market"].get("trend", "FLAT")
        pcr_now   = state["market"].get("pcr") or 1.0
        failed = []
        if c.get("vix")    is False: failed.append("VIX out of range")
        if c.get("iv")     is False: failed.append("IV too low")
        if c.get("pcr")    is False: failed.append("PCR out of range")
        if c.get("margin") is False: failed.append("Margin insufficient")
        if c.get("gate")   is False: failed.append("Daily loss limit hit")
        if c.get("weekly_gate")  is False: failed.append("Weekly loss limit hit")
        if c.get("monthly_gate") is False: failed.append("Monthly loss limit hit")
        # EMA: only flag as a check failure when trend is FLAT (spread trades skip this check).
        # When trend is UP/DOWN but PCR doesn't confirm, show the real blocker instead.
        if c.get("ema") is False:
            if trend_now == "FLAT":
                failed.append("EMA not flat (trending) — strangle blocked")
            elif trend_now == "UP" and pcr_now < 1.0:
                failed.append(f"Trend UP but PCR {pcr_now:.2f} < 1.0 — Bull Put Spread blocked; EMA trending so strangle also blocked")
            elif trend_now == "DOWN" and pcr_now > 1.0:
                failed.append(f"Trend DOWN but PCR {pcr_now:.2f} > 1.0 — Bear Call Spread blocked; EMA trending so strangle also blocked")
            else:
                failed.append("EMA not flat (trending) — strangle blocked")
        resp["no_trade_reason"] = ("Checks failing: " + ", ".join(failed)) if failed else None
    else:
        resp["no_trade_reason"] = None

    return jsonify(resp)

@app.route("/api/connection")
def api_connection():
    return jsonify(connection)

@app.route("/api/logs")
def api_logs():
    return jsonify({"lines": LOG_LINES[-60:]})

_CONFIG_SENSITIVE = {"login_pin", "angel_api_key", "angel_password", "angel_totp_secret"}

# PIN brute-force protection: track failed attempts per IP
_pin_failures: dict = {}   # {ip: {"count": N, "locked_until": float}}
_PIN_MAX_ATTEMPTS = 3
_PIN_LOCKOUT_SECS = 300    # 5 minutes

def _require_pin():
    """Returns None if PIN is valid, or a (response, status) tuple to return immediately.
    After 3 wrong attempts from the same IP, locks out for 5 minutes."""
    ip = request.remote_addr or "unknown"
    now = time.time()

    rec = _pin_failures.get(ip, {"count": 0, "locked_until": 0.0})
    if rec["locked_until"] > now:
        remaining = int(rec["locked_until"] - now)
        return jsonify({"ok": False, "msg": f"Too many wrong attempts. Try again in {remaining}s.", "locked": True}), 429

    data = request.get_json(force=True, silent=True) or {}
    pin  = str(data.get("pin", "")).strip()
    if not pin:
        return jsonify({"ok": False, "msg": "PIN required."}), 401

    correct = str(config.get("login_pin", "5599"))
    if not hmac.compare_digest(pin, correct):
        rec["count"] += 1
        if rec["count"] >= _PIN_MAX_ATTEMPTS:
            rec["locked_until"] = now + _PIN_LOCKOUT_SECS
            rec["count"] = 0
            _pin_failures[ip] = rec
            LOG_LINES.append(f"[WARN]  [{_ts()}] PIN lockout triggered for {ip} ({_PIN_MAX_ATTEMPTS} failed attempts)")
            return jsonify({"ok": False, "msg": f"Too many wrong attempts. Locked for {_PIN_LOCKOUT_SECS//60} min.", "locked": True}), 429
        _pin_failures[ip] = rec
        remaining_attempts = _PIN_MAX_ATTEMPTS - rec["count"]
        return jsonify({"ok": False, "msg": f"Invalid PIN. {remaining_attempts} attempt(s) left."}), 401

    # Success — reset failure counter
    _pin_failures.pop(ip, None)
    return None


@app.route("/api/stop", methods=["POST"])
def api_stop():
    err = _require_pin()
    if err: return err
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
    err = _require_pin()
    if err: return err
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

_SIGNAL_MAX_AGE_SECS = 300   # reject manual execute if signal is older than 5 minutes

@app.route("/api/execute", methods=["POST"])
def api_execute():
    """User manually triggers execution of the pending signal."""
    if not state.get("signal_pending") or not state.get("pending_signal"):
        return jsonify({"error": "No pending signal to execute"}), 400
    # Reject stale signals
    generated_at = state.get("signal_generated_at")
    if generated_at and (time.time() - generated_at) > _SIGNAL_MAX_AGE_SECS:
        age_min = int((time.time() - generated_at) / 60)
        state["signal_pending"] = False
        state["pending_signal"] = None
        state["signal_generated_at"] = None
        LOG_LINES.append(f"[WARN]  [{_ts()}] Stale signal discarded (age {age_min} min > {_SIGNAL_MAX_AGE_SECS//60} min limit)")
        return jsonify({"error": f"Signal expired ({age_min} min old). Market conditions may have changed. A fresh signal will generate shortly."}), 400
    signal = state["pending_signal"]
    LOG_LINES.append(f"[TRADE] [{_ts()}] Manual execute triggered by user.")
    threading.Thread(target=_execute_trade, args=(signal,), daemon=True).start()
    return jsonify({"ok": True, "msg": "Executing trade..."})


@app.route("/api/test_live_trade", methods=["POST"])
def api_test_live_trade():
    payload, status = _place_test_live_atm_sell()
    return jsonify(payload), status

@app.route("/api/reconnect", methods=["POST"])
def api_reconnect():
    LOG_LINES.append(f"[INFO]  [{_ts()}] Manual reconnect triggered...")
    threading.Thread(target=angel_login, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify({k: v for k, v in config.items() if k not in _CONFIG_SENSITIVE})

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
    # ── Capital-aware risk scaling ──
    # If capital changed AND user did NOT explicitly set loss limits, scale them proportionally
    if "capital" in data:
        cap = float(config["capital"])
        if "daily_loss_limit" not in data:
            config["daily_loss_limit"]   = max(15000, round(cap * 0.03 / 1000) * 1000)   # 3% of capital
        if "weekly_loss_limit" not in data:
            config["weekly_loss_limit"]  = max(40000, round(cap * 0.06 / 1000) * 1000)   # 6% of capital (~2 bad days)
        if "monthly_loss_limit" not in data:
            config["monthly_loss_limit"] = max(50000, round(cap * 0.10 / 1000) * 1000)   # 10% of capital
        if "margin_override" not in data:
            config["margin_override"]    = int(cap)
        LOG_LINES.append(
            f"[INFO]  [{_ts()}] Capital changed to ₹{cap:,.0f} → "
            f"Daily limit ₹{config['daily_loss_limit']:,} | "
            f"Weekly ₹{config['weekly_loss_limit']:,} | "
            f"Monthly ₹{config['monthly_loss_limit']:,}"
        )
    _save_settings()
    LOG_LINES.append(f"[INFO]  [{_ts()}] Configuration updated and saved to disk.")
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
    err = _require_pin()
    if err: return err
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

    # Recalculate today's closed PnL after deletion
    today = datetime.now().date()
    daily_total = 0.0
    daily_count = 0
    for t in state["trade_history"]:
        exit_t = t.get("exit_time")
        pnl    = t.get("final_pnl")
        if not exit_t or pnl is None:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_t) if "T" in str(exit_t) else datetime.strptime(str(exit_t).strip(), "%d-%m-%Y  %H:%M:%S")
            if exit_dt.date() == today:
                daily_total += float(pnl)
                daily_count += 1
        except Exception:
            continue
    state["closed_pnl"]   = round(daily_total, 2)
    state["daily_pnl"]    = round(daily_total, 2)
    state["trades_today"] = daily_count

    threading.Thread(target=_delete_from_sheets, daemon=True).start()
    LOG_LINES.append(f"[INFO]  [{_ts()}] Trade {trade_id} deleted")
    return jsonify({"ok": True})

@app.route("/api/analytics")
def api_analytics():
    """Compute performance analytics from closed trade history."""
    from collections import defaultdict
    closed = [
        t for t in state.get("trade_history", [])
        if t.get("exit_time") and t.get("final_pnl") is not None
    ]
    if not closed:
        return jsonify({"has_data": False})

    pnls   = [float(t.get("final_pnl", 0)) for t in closed]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total       = len(pnls)
    win_count   = len(wins)
    loss_count  = len(losses)
    win_rate    = round(win_count / total * 100, 1) if total else 0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0
    avg_win   = round(gross_profit / win_count, 2)  if win_count  else 0
    avg_loss  = round(gross_loss  / loss_count, 2)  if loss_count else 0
    rr_ratio  = round(avg_win / avg_loss, 2)         if avg_loss   else 0

    # Equity curve & max drawdown
    equity = []
    running = 0.0
    for p in pnls:
        running += p
        equity.append(running)
    peak   = equity[0] if equity else 0
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    total_pnl  = round(sum(pnls), 2)
    max_dd     = round(max_dd, 2)
    capital    = float(config.get("capital", 1500000))
    max_dd_pct = round(max_dd / capital * 100, 2) if capital else 0
    calmar     = round(total_pnl / max_dd, 2) if max_dd > 0 else 0

    # Drawdown efficiency = profit factor / (1 + max_dd_pct/100)
    dd_efficiency = round(profit_factor / (1 + max_dd_pct / 100), 2) if max_dd_pct > 0 else profit_factor

    # Avg hold time
    durations = [t.get("duration_mins") for t in closed if t.get("duration_mins") is not None]
    avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

    best_trade  = round(max(pnls), 2) if pnls else 0
    worst_trade = round(min(pnls), 2) if pnls else 0

    # Current streak
    streak = 0
    streak_type = None
    for p in reversed(pnls):
        cur = "win" if p > 0 else "loss"
        if streak_type is None:
            streak_type = cur
            streak = 1
        elif cur == streak_type:
            streak += 1
        else:
            break

    # Exit reason breakdown
    reasons = {}
    for t in closed:
        r = (t.get("exit_reason") or "Unknown").strip()
        if r not in reasons:
            reasons[r] = {"count": 0, "pnl": 0.0, "wins": 0}
        reasons[r]["count"] += 1
        reasons[r]["pnl"]   += float(t.get("final_pnl", 0))
        if float(t.get("final_pnl", 0)) > 0:
            reasons[r]["wins"] += 1
    for r in reasons:
        reasons[r]["pnl"] = round(reasons[r]["pnl"], 2)

    # Equity curve points (for chart)
    eq_points = []
    running = 0.0
    for t in closed[-60:]:
        running += float(t.get("final_pnl", 0))
        eq_points.append({
            "pnl":        round(float(t.get("final_pnl", 0)), 2),
            "cumulative": round(running, 2),
            "date":       str(t.get("entry_time", ""))[:10],
        })

    # Weekly P&L (last 8 calendar weeks)
    weekly = defaultdict(float)
    for t in closed:
        raw = str(t.get("entry_time", ""))[:10]
        try:
            dt = datetime.fromisoformat(raw)
            wk = dt.strftime("%Y-W%W")
            weekly[wk] += float(t.get("final_pnl", 0))
        except Exception:
            pass
    weekly_sorted = {k: round(v, 2) for k, v in sorted(weekly.items())[-8:]}

    return jsonify({
        "has_data":        True,
        "total_trades":    total,
        "win_count":       win_count,
        "loss_count":      loss_count,
        "win_rate":        win_rate,
        "gross_profit":    round(gross_profit, 2),
        "gross_loss":      round(gross_loss, 2),
        "profit_factor":   profit_factor,
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "rr_ratio":        rr_ratio,
        "total_pnl":       total_pnl,
        "max_drawdown":    max_dd,
        "max_drawdown_pct": max_dd_pct,
        "calmar_ratio":    calmar,
        "dd_efficiency":   dd_efficiency,
        "avg_duration_mins": avg_duration,
        "best_trade":      best_trade,
        "worst_trade":     worst_trade,
        "current_streak":  streak,
        "streak_type":     streak_type,
        "exit_reasons":    reasons,
        "equity_curve":    eq_points,
        "weekly_pnl":      weekly_sorted,
    })

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

@app.route("/api/login_pin", methods=["POST"])
def api_login_pin():
    try:
        data = request.get_json(force=True, silent=True) or {}
        pin = str(data.get("pin", "")).strip()
        if not pin:
            return jsonify({"ok": False, "msg": "PIN is required."}), 400
        if pin == str(config.get("login_pin", "5599")):
            return jsonify({"ok": True, "msg": "PIN validated."})
        return jsonify({"ok": False, "msg": "Invalid PIN."}), 401
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/api/forgot_pin", methods=["POST"])
def api_forgot_pin():
    token   = config.get("telegram_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        return jsonify({"ok": False, "msg": "Telegram token or chat ID not configured"}), 400

    try:
        remote_ip = request.remote_addr or 'unknown'
        login_pin = str(config.get("login_pin", "5599"))
        message = (
            f"*FIFTO* — Forgot PIN requested from browser.\n"
            f"Source IP: {remote_ip}\n"
            f"Time: {datetime.now().strftime('%d-%b-%Y %H:%M:%S')}\n\n"
            f"Configured browser PIN: `{login_pin}`"
        )
        sent = _send_telegram(message)
        if not sent:
            return jsonify({"ok": False, "msg": "Telegram delivery failed."}), 500
        LOG_LINES.append(f"[INFO]  [{_ts()}] Forgot PIN alert sent to Telegram.")
        return jsonify({"ok": True, "msg": "Telegram recovery alert sent."})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


def _startup_nse_fetch():
    """Fetch lot size + holidays once at startup (runs in background)."""
    time.sleep(20)   # let NSE session & cookies establish via option chain warm-up
    _fetch_nifty_lot_size()
    _fetch_nse_holidays()


# ── Telegram Bot Command Polling ──────────────────────────────────────────────

_tg_offset = 0   # tracks last processed update_id

_TG_HELP = (
    "🤖 *FIFTO Commands*\n\n"
    "/status — Live NIFTY, position & P\\&L\n"
    "/run — Start the trading agent\n"
    "/stop — Stop the trading agent\n"
    "/exit — Emergency square-off\n"
    "/approve — Execute pending trade\n"
    "/pnl — Today's P\\&L summary\n"
    "/mode auto — Switch to AUTO mode\n"
    "/mode manual — Switch to MANUAL mode\n"
    "/menu — Show this list"
)

def _tg_reply(text):
    """Send a reply back to the configured chat."""
    _send_telegram(text)

def _handle_tg_command(text):
    """Parse and execute a Telegram command. Returns reply string."""
    global _tg_offset
    cmd = text.strip().lower().split("@")[0]   # strip bot username suffix

    if cmd in ("/menu", "/start_bot", "/start"):
        return _TG_HELP

    if cmd == "/status":
        spot  = state["market"].get("nifty_spot")
        vix   = state["market"].get("vix")
        mode  = state.get("execution_mode", "—")
        run   = "🟢 Running" if state.get("bot_running") else "🔴 Stopped"
        pos   = state.get("position_detail") or {}
        lines = [
            f"📡 *FIFTO Status* — {_ts()}",
            f"Bot: {run} | Mode: {mode}",
            f"NIFTY: `{spot:.2f}`  VIX: `{vix:.2f}`" if spot and vix else "NIFTY/VIX: —",
        ]
        if state.get("active_position") and pos:
            pnl = pos.get("pnl")
            lines.append(
                f"📊 Position: CE {pos.get('ce_strike')} / PE {pos.get('pe_strike')}\n"
                f"Entry ₹{pos.get('premium_received', 0):.0f} | Open P&L: "
                + (f"{'+'if pnl>=0 else ''}₹{pnl:,.0f}" if isinstance(pnl, (int, float)) else "—")
            )
        else:
            lines.append(f"No active position | Daily P&L: ₹{state.get('daily_pnl',0):,.0f}")
        return "\n".join(lines)

    if cmd == "/pnl":
        daily  = state.get("daily_pnl", 0)
        closed = state.get("closed_pnl", 0)
        trades = state.get("trades_today", 0)
        sign   = "+" if daily >= 0 else ""
        return (
            f"💰 *Today's P&L*\n"
            f"Realized: {sign}₹{daily:,.0f}\n"
            f"Trades today: {trades}"
        )

    if cmd == "/stop":
        if not state.get("bot_running"):
            return "ℹ️ Agent is already stopped."
        state["bot_running"]    = False
        state["signal_pending"] = False
        state["pending_signal"] = None
        LOG_LINES.append(f"[WARN]  [{_ts()}] Agent stopped via Telegram.")
        return "🔴 Agent stopped."

    if cmd == "/run":
        if state.get("bot_running"):
            return "ℹ️ Agent is already running."
        state["bot_running"] = True
        LOG_LINES.append(f"[INFO]  [{_ts()}] Agent started via Telegram.")
        return "🟢 Agent started."

    if cmd == "/exit":
        if not state.get("active_position"):
            return "ℹ️ No active position to exit."
        LOG_LINES.append(f"[ERROR] [{_ts()}] EMERGENCY EXIT triggered via Telegram.")
        threading.Thread(target=_square_off_position, daemon=True).start()
        return "🚨 Emergency exit triggered — squaring off positions."

    if cmd == "/approve":
        if not state.get("signal_pending") or not state.get("pending_signal"):
            return "ℹ️ No pending signal to execute."
        signal = state["pending_signal"]
        LOG_LINES.append(f"[TRADE] [{_ts()}] Trade approved via Telegram.")
        threading.Thread(target=_execute_trade, args=(signal,), daemon=True).start()
        return "✅ Trade execution started."

    if cmd.startswith("/mode"):
        parts = cmd.split()
        if len(parts) < 2 or parts[1] not in ("auto", "manual"):
            return "⚠️ Usage: /mode auto  or  /mode manual"
        mode = parts[1].upper()
        state["execution_mode"] = mode
        config["execution_mode"] = mode
        LOG_LINES.append(f"[INFO]  [{_ts()}] Execution mode → {mode} (via Telegram)")
        return f"✅ Mode set to *{mode}*."

    return f"❓ Unknown command: `{text}`\nSend /menu for the list."


def telegram_command_poll():
    """Long-poll Telegram getUpdates and dispatch commands. Runs in background thread."""
    global _tg_offset
    time.sleep(8)   # wait for config to load

    while True:
        token   = config.get("telegram_token", "")
        chat_id = str(config.get("telegram_chat_id", ""))
        if not token or not chat_id:
            time.sleep(30)
            continue
        try:
            resp = _requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": _tg_offset, "timeout": 20, "allowed_updates": ["message"]},
                timeout=25
            )
            if not resp.ok:
                time.sleep(10)
                continue
            updates = resp.json().get("result", [])
            for upd in updates:
                _tg_offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                # Only accept messages from the configured chat
                if str(msg.get("chat", {}).get("id", "")) != chat_id:
                    continue
                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                reply = _handle_tg_command(text)
                if reply:
                    _send_telegram(reply)
        except Exception as e:
            LOG_LINES.append(f"[WARN]  [{_ts()}] Telegram poll error: {e}")
            time.sleep(15)


if __name__ == "__main__":
    threading.Thread(target=angel_login,              daemon=True).start()
    threading.Thread(target=fetch_market_data,        daemon=True).start()
    threading.Thread(target=signal_engine,            daemon=True).start()
    threading.Thread(target=position_monitor,         daemon=True).start()
    threading.Thread(target=fast_ltp_refresh,         daemon=True).start()
    threading.Thread(target=_startup_nse_fetch,       daemon=True).start()
    threading.Thread(target=telegram_command_poll,    daemon=True).start()
    print("FIFTO AI Trading server → http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
