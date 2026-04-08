"""
config.py — All parameters for NiftyAgent
Edit this file only. No hardcoded values anywhere else.
"""

import os

CONFIG = {
    # ── AngelOne credentials ──
    "ANGEL_API_KEY":    os.getenv("ANGEL_API_KEY", ""),
    "ANGEL_CLIENT_ID":  os.getenv("ANGEL_CLIENT_ID", ""),
    "ANGEL_PASSWORD":   os.getenv("ANGEL_PASSWORD", ""),
    "ANGEL_TOTP_SECRET":os.getenv("ANGEL_TOTP_SECRET", ""),

    # ── Telegram ──
    "TELEGRAM_TOKEN":   os.getenv("TELEGRAM_TOKEN", ""),
    "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),

    # ── OpenAI (for GoCharting vision) ──
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),

    # ── Capital & lots ──
    "CAPITAL":          1_500_000,
    "BASE_LOTS":        2,
    "lots":             2,          # runtime — changes with position sizing rules
    "LOT_SIZE":         65,         # NIFTY lot size (effective Jan 2026)

    # ── Trade parameters ──
    "DELTA_TARGET":         0.17,   # 0.15–0.20 range
    "MIN_PREMIUM_PER_SIDE": 45,     # skip if premium < ₹45
    "PROFIT_TARGET_PCT":    0.30,   # exit at 30% premium (intraday-optimized)
    "SL_MULTIPLIER":        1.5,    # SL at 1.5× premium received (keeps loss within daily limit)
    "TRAIL_TRIGGER_PCT":    0.20,   # lock breakeven at 20% profit

    # ── Risk limits ──
    "RISK_PER_TRADE_PCT":   0.02,   # 2% = ₹30,000
    "DAILY_LOSS_LIMIT":     45_000,
    "WEEKLY_LOSS_LIMIT":    60_000,
    "MONTHLY_LOSS_LIMIT":  150_000,
    "MAX_TRADES_PER_DAY":   3,

    # ── Regime filters ──
    "VIX_MIN":    13,
    "VIX_MAX":    28,
    "ATR_REGIME_B_MAX": 100,        # ATR > 100 = regime C

    # ── Entry/exit timing ──
    "ENTRY_START":      "09:30",
    "ENTRY_END":        "11:00",
    "DEAD_ZONE_START":  "14:30",
    "EXPIRY_CUT_TIME":  "13:00",    # exit on Tuesday before 1 PM

    # ── GoCharting vision ──
    "CHART_CAPTURE_INTERVAL_SEC": 60,
    "VISION_CONFIDENCE_THRESHOLD": 75,
    "CHART_REGION": {
        "top": 100, "left": 0, "width": 1400, "height": 800
    },

    # ── NSE 2026 holiday list (Tuesdays only — expiry shift days) ──
    "HOLIDAY_LIST": {
        "2026-03-31": "Mahavir Jayanti",
        "2026-04-14": "Ambedkar Jayanti",
        "2026-11-10": "Diwali Balipratipada",
    },

    # ── Paper trade mode ──
    "PAPER_TRADE": True,    # ← Set False for live trading
}
