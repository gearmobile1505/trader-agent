#!/usr/bin/env python3
"""
Optimized CFD Trading Webhook Bridge - 5M Scalping Configuration.
Based on Phantom Flow backtest results (7 days, 5m resolution).
Top symbols: LVMH, XPDUSD.R, ALPHABET-C, UKOIL.R, SIEMENS, GE
"""

import os
import json
import gzip
import re
import sys
import asyncio
import threading
from collections import deque
from uuid import uuid4
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import numpy as np
import pandas as pd
from tradelocker import TLAPI
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_decider

app = FastAPI()

# Alert log file
ALERT_LOG = "/opt/trader-agent/scripts/alerts_log.jsonl"

# AI Evaluation Configuration
# Bound concurrent AI calls: the default asyncio thread executor allows
# cpu_count + 4 workers, and on a 1 GB droplet an alert burst must not pin
# every thread on a provider call.
AI_MAX_CONCURRENCY = int(os.getenv("AI_MAX_CONCURRENCY", "2"))
AI_QUEUE_TIMEOUT_SECONDS = float(os.getenv("AI_QUEUE_TIMEOUT_SECONDS", "30"))
# 0 = deny trades when the AI backend is unavailable (prop-account safe)
# 1 = treat a backend outage as an approval
AI_FAIL_OPEN = os.getenv("AI_FAIL_OPEN", "0") == "1"

_AI_GATE = threading.Semaphore(AI_MAX_CONCURRENCY)

_AI_STATE = {
    "decisions": 0,
    "failures": 0,
    "last_error": None,
    "last_latency_ms": None,
    "last_decision": None,
    "last_source": None,
    "last_decision_at": None,
}


def _ai_reset_stats() -> None:
    """Counters describe this process, not the previous one."""
    _AI_STATE.update({
        "decisions": 0,
        "failures": 0,
        "last_error": None,
        "last_latency_ms": None,
        "last_decision": None,
        "last_source": None,
        "last_decision_at": None,
    })


def ai_backend_label() -> str:
    """Human-readable AI backend name for log lines."""
    try:
        return ai_decider.backend()
    except Exception:
        return "unknown"

# Trailing Stop Configuration
TRAILING_SL_BE_PROFIT = 75.0        # Move SL to breakeven when P&L >= $75
POSITION_MONITOR_INTERVAL_SECONDS = 30
POSITION_MONITOR_EMPTY_INTERVAL_SECONDS = 120
POSITION_MONITOR_MAX_BACKOFF_SECONDS = 300

# Gzip decompression + JSON fix middleware
@app.middleware("http")
async def decompress_gzip_and_fix_json(request: Request, call_next):
    """Decompress gzip-encoded request bodies and fix TradingView malformed JSON."""
    body = await request.body()
    
    # Handle gzip decompression
    if request.headers.get("content-encoding") == "gzip":
        try:
            body = gzip.decompress(body)
            request.headers.__dict__["_list"] = [
                h for h in request.headers.__dict__["_list"] 
                if h[0] != b"content-encoding"
            ]
        except Exception:
            pass
    
    # Fix TradingView malformed JSON: quote unquoted {{...}} placeholders
    if body:
        try:
            body_str = body.decode('utf-8')
            # Fix unquoted {{placeholder}} in JSON values
            import re
            # Match {{...}} not inside quotes
            def quote_placeholders(match):
                return f'"{match.group(0)}"'
            
            # Pattern: : {{...}} (colon space then placeholder not in quotes)
            fixed = re.sub(r':\s*(\{\{[^}]+\}\})', r': "\1"', body_str)
            # Also fix: {{...}} at start of value (after { or ,)
            fixed = re.sub(r'([{,])\s*(\{\{[^}]+\}\})', r'\1 "\2"', fixed)
            
            body = fixed.encode('utf-8')
            request.headers.__dict__["_list"] = [
                (k, v) for k, v in request.headers.__dict__["_list"] 
                if k != b"content-length"
            ]
            request.headers.__dict__["_list"].append((b"content-length", str(len(body)).encode()))
            request.headers.__dict__["_list"].append((b"content-type", b"application/json"))
        except Exception:
            pass
    
    request._body = body
    return await call_next(request)

def log_alert(alert_data: dict, result: dict):
    """Log alert and result to JSONL file."""
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "alert": alert_data,
        "result": result
    }
    try:
        with open(ALERT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[LOG ALERT ERROR] {type(e).__name__}: {e}", flush=True)

# Load broker credentials
TL_ENV = os.getenv("TL_ENV", "https://demo.tradelocker.com")
TL_USER = os.getenv("TL_USER")
TL_PASS = os.getenv("TL_PASS")
TL_SERVER = os.getenv("TL_SERVER")

# Initialize TradeLocker client wrapper
tl = TLAPI(
    environment=TL_ENV,
    username=TL_USER,
    password=TL_PASS,
    server=TL_SERVER,
    log_level="warning"
)

# =============================================================================
# OPTIMIZED 5M SCALPING CONFIGURATION
# =============================================================================

# Top performing symbols from 5m backtest (7 days)
# Format: {trade_locker_symbol: {point_value, min_lot, max_spread_pips, session_hours, tick_size}}
TOP_SYMBOLS = {
    "LVMH": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 0.50,
        "sessions": ["EU"],
        "description": "Louis Vuitton SE (Euronext)",
        "currency": "EUR",
        "tick_size": 0.01
    },
    "XPDUSD.R": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 5.0,
        "sessions": ["NY", "EU"],
        "description": "Palladium vs USD",
        "currency": "USD",
        "tick_size": 0.001
    },
    "ALPHABET-C": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 0.30,
        "sessions": ["NY"],
        "description": "Alphabet Class C (Google)",
        "currency": "USD",
        "tick_size": 0.01
    },
    "UKOIL.R": {
        "point_value": 1000.0,
        "min_lot": 0.01,
        "max_spread": 0.05,
        "sessions": ["NY", "ASIA"],
        "description": "Brent Crude Oil",
        "currency": "USD",
        "tick_size": 0.001
    },
    "SIEMENS": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 0.40,
        "sessions": ["EU"],
        "description": "Siemens AG (XETRA)",
        "currency": "EUR",
        "tick_size": 0.01
    },
    "GE": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 0.20,
        "sessions": ["NY"],
        "description": "General Electric",
        "currency": "USD",
        "tick_size": 0.01
    },
    "US30.R": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 2.0,
        "sessions": ["NY_EARLY", "NY"],
        "description": "US 30 Cash (Dow Jones)",
        "currency": "USD",
        "tick_size": 0.01
    },
    "NAS100.R": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 1.5,
        # 2026-10-01: ASIA added per 60-day gated backtest.
        # ASIA 59 trades +$151.03 PF 1.08 (only qualifying gated session).
        # NY 46 trades −$424.37 PF 0.79; combined ASIA+NY on table for review.
        "sessions": ["NY", "ASIA"],
        "description": "US Tech Cash (NASDAQ 100)",
        "currency": "USD",
        "tick_size": 0.01
    },
    "SPX500.R": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 1.0,
        "sessions": ["NY_EARLY"],
        "description": "US 500 Cash (S&P 500)",
        "currency": "USD",
        "tick_size": 0.01
    },
    "XAUUSD.R": {
        "point_value": 100.0,
        "min_lot": 0.01,
        "max_spread": 0.5,
        "sessions": ["NY", "EU", "ASIA"],
        "description": "Gold Ounce vs US Dollar",
        "currency": "USD",
        "tick_size": 0.001
    },
    "GBPJPY.R": {
        "point_value": 100000.0,
        "point_value_currency": "JPY",
        "fallback_point_value": 650.0,
        "min_lot": 0.01,
        "max_lot": 0.30,
        "max_spread": 2.0,
        # 2026-10-01: NY removed per 60-day gated backtest.
        # NY 50 trades −$1,164.88 PF 0.19 (zero TP exits); combined net −$5.94.
        # EU 56 trades +$389.92 PF 1.37; ASIA 57 trades +$641.70 PF 1.69; NY_EARLY 20 trades +$98.92 PF 1.35.
        "sessions": ["EU", "ASIA", "NY_EARLY"],
        "description": "British Pound vs Japanese Yen",
        "currency": "USD",
        "tick_size": 0.01
    },
    "USDJPY.R": {
        "point_value": 100000.0,
        "point_value_currency": "JPY",
        "fallback_point_value": 650.0,
        "min_lot": 0.01,
        # Sizing ceiling, not a target. The bot places
        # min(TARGET_DOLLAR_RISK / risk_per_lot, max_lot), so risk is pinned to
        # $100 whenever the calculated size fits under this cap and strictly
        # under $100 when it does not. Raising this from 0.30 to 0.40 admits
        # tighter stops (the ATR*3 stop on this pair is routinely 0.2-0.6 pts,
        # which needs 0.26-0.77 lots at $100 risk) without raising risk.
        # At 0.30 every such setup was rejected outright.
        "max_lot": 0.40,
        "max_spread": 1.5,
        # 2026-10-01: EU removed per 60-day gated backtest.
        # ASIA 49 trades +$113.57 PF 1.10; EU 39 trades −$351.57 PF 0.64.
        # Combined config netted −$57.09; edge is Asia-only.
        "sessions": ["ASIA"],
        "description": "US Dollar vs Japanese Yen",
        "currency": "USD",
        "tick_size": 0.01
    },
}

# Phantom Shift Parameters (from Pine Script)
SHIFT_ATR_PERIOD = 10
SHIFT_MULTIPLIER = 3.0

# Risk Management
TARGET_DOLLAR_RISK = 100.0        # $100 risk per trade
MAX_DAILY_LOSS = 400.0            # Stop trading if -$400/day
MAX_OPEN_TRADES = 3               # Max concurrent positions
MIN_RISK_REWARD = 1.25            # Min R:R for entry
MAX_HOLD_TIME_MINUTES = 0         # 0 = no max hold limit (rely on session-end flattening)
MAX_SL_OVERSHOOT_PCT = 100        # Max % over target risk at SL (min lot basis)

# Entry Vetting Gates (all default ON)
ENABLE_TREND_GATE = True                # Reject counter-trend vs tech summary
ENABLE_COOLDOWN_GATE = True             # Block re-entry after stop-out
COOLDOWN_AFTER_STOP_MINUTES = 60        # Cooldown period after SL hit
ENABLE_ONE_PER_SYMBOL_GATE = True       # Block same-symbol re-entry (same direction)
ENABLE_ALERT_FRESHNESS_GATE = True      # Reject stale/future alerts
ALERT_MAX_AGE_MINUTES = 10              # Max alert age before rejection
ENABLE_DAILY_LOSS_GATE = True           # Enforce daily loss limit before AI
ENABLE_SLIPPAGE_GUARD = True            # XPDUSD.R stop-limit / spread guard
XPDUSD_SLIPPAGE_MULTIPLE = 2.0          # Alert if fill > 2x stop distance
XPDUSD_MAX_SPREAD_PCT = 0.005           # Block entry if spread > 0.5% of price

# Session exit protection
# A position is flattened this long before its trading session ends. Holding
# into a close exposes the position to the illiquid gap afterwards, where the
# spread widens far past the stop and the stop can fill well beyond the
# intended risk. A trade that was green before the close can close red.
SESSION_CLOSE_BUFFER_MINUTES = 15
# Friday: flatten the whole book by this hour ET, regardless of symbol session,
# so nothing is carried into the weekend gap.
WEEKEND_FLATTEN_HOUR_ET = 18
# CFD markets are closed at the weekend. ASIA (20:00-06:00 ET) spans the
# weekend, so a naive session check would still allow Saturday entries.
NO_ENTRY_ON_WEEKEND = True
# Never hold both directions on the same instrument at once. Two opposing
# positions on one CFD net out at the broker but still pay spread twice and
# can leave a residual position through a session close.
BLOCK_OPPOSING_SAME_SYMBOL = True

# Take Profit Configuration
# TP1 = $125 minimum (1.25x R:R on $100 risk)
TP_CONFIG = {
    "US30.R":     {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
    "NAS100.R":   {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
    "SPX500.R":   {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": False},
    "XAUUSD.R":   {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": False},
    "XPDUSD.R":   {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": False},
    "UKOIL.R":    {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": False},
    "LVMH":       {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
    "SIEMENS":    {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
    "ALPHABET-C": {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
    "GE":         {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
    "GBPJPY.R":   {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
    "USDJPY.R":   {"tp1_dollars": 125, "tp2_dollars": None, "trail_after_tp1": True},
}

# Session time ranges (ET)
SESSIONS_ET = {
    "ASIA": (20, 6),     # 8 PM - 6 AM ET (previous day 8 PM to 6 AM)
    "EU":   (3, 11),     # 3 AM - 11 AM ET
    "NY":   (9, 19),     # 9 AM - 7 PM ET (23h total coverage with ASIA)
    "NY_EARLY": (6, 9),  # 6 AM - 9 AM ET (pre-market for indices)
    "NY_MORNING": (9, 13),  # 9 AM - 1 PM ET (high liquidity, tight spreads for metals)
}

def is_session_active(symbol_config: dict) -> bool:
    """Check if current time is in allowed trading session for symbol."""
    from datetime import datetime
    import pytz
    
    et = pytz.timezone('US/Eastern')
    now_et = datetime.now(et)
    current_hour = now_et.hour + now_et.minute / 60
    weekday = now_et.weekday()  # 0=Mon, 4=Fri
    weekend = weekday >= 5  # Sat/Sun
    
    for session in symbol_config.get("sessions", []):
        if weekend and session in ("NY", "NY_EARLY", "NY_MORNING"):
            continue
        start, end = SESSIONS_ET.get(session, (0, 24))
        if start < end:
            if start <= current_hour < end:
                return True
        else:  # Overnight session (ASIA)
            if current_hour >= start or current_hour < end:
                return True
    return False

def is_weekend_et() -> bool:
    """True Saturday/Sunday in ET. CFD markets are closed; ASIA spans the weekend."""
    import pytz
    return datetime.now(pytz.timezone('US/Eastern')).weekday() >= 5


def session_exit_info(symbol_config: dict) -> tuple[bool, float | None]:
    """
    Return (in_session, minutes_until_latest_session_end).

    When a symbol has overlapping sessions the position may legitimately stay
    open until the last of them ends, so the latest end wins. minutes is None
    when no configured session is currently active.
    """
    import pytz

    now_et = datetime.now(pytz.timezone('US/Eastern'))
    current = now_et.hour + now_et.minute / 60 + now_et.second / 3600
    weekend = now_et.weekday() >= 5

    latest = None
    for session in symbol_config.get("sessions", []):
        if weekend and session in ("NY", "NY_EARLY", "NY_MORNING"):
            continue
        start, end = SESSIONS_ET.get(session, (0, 24))

        if start < end:
            if not (start <= current < end):
                continue
            minutes = (end - current) * 60
        else:
            # Overnight window, e.g. ASIA 20:00 -> 06:00
            if current >= start:
                end_dt = (now_et + timedelta(days=1)).replace(
                    hour=int(end), minute=int((end % 1) * 60), second=0, microsecond=0
                )
            elif current < end:
                end_dt = now_et.replace(
                    hour=int(end), minute=int((end % 1) * 60), second=0, microsecond=0
                )
            else:
                continue
            minutes = (end_dt - now_et).total_seconds() / 60

        latest = minutes if latest is None else max(latest, minutes)

    return (latest is not None, latest)


def flatten_reason(symbol_config: dict) -> str | None:
    """
    Return why this symbol must be flattened right now, or None to hold.

    Covers three ways a position can be stranded past its session: the session
    already closed, the session closes inside the buffer window, or it is
    Friday and the weekend gap is approaching.
    """
    import pytz

    now_et = datetime.now(pytz.timezone('US/Eastern'))

    if now_et.weekday() >= 5:
        return "weekend: markets closed"

    # Friday: flatten the whole book before the weekend gap.
    if now_et.weekday() == 4 and now_et.hour >= WEEKEND_FLATTEN_HOUR_ET:
        return f"Friday {WEEKEND_FLATTEN_HOUR_ET:02d}:00 ET weekend flatten window"

    in_session, minutes = session_exit_info(symbol_config)
    if not in_session:
        return "trading session closed"

    if minutes is not None and minutes <= SESSION_CLOSE_BUFFER_MINUTES:
        return f"{minutes:.0f}m until session close"

    return None


def map_symbol(tv_symbol: str) -> str:
    """Map TradingView symbol to TradeLocker CFD symbol."""
    SYMBOL_MAP = {
        # Futures -> CFD
        "MNQ": "NAS100.R", "MES": "SPX500.R", "MYM": "US30.R", "M2K": "US30.R",
        "NQ": "NAS100.R", "ES": "SPX500.R", "YM": "US30.R", "RTY": "US30.R",
        "CL": "USOIL.R", "GC": "XAUUSD.R", "SI": "XAGUSD.R", "HG": "XAUUSD.R",
        "ZB": "XAUUSD.R", "ZN": "XAUUSD.R", "ZF": "XAUUSD.R", "ZT": "XAUUSD.R",
        "6E": "EURUSD.R", "6J": "USDJPY.R", "6B": "GBPUSD.R", "6A": "AUDUSD.R",
        "6C": "USDCAD.R", "6N": "NZDUSD.R",
        # Direct CFD symbols (5m optimized)
        # NOTE: XPTUSD is deliberately absent. It used to alias to XPDUSD.R,
        # which meant a platinum (XPTUSD) alert silently traded palladium
        # (XPDUSD.R) — different instruments, different prices, different risk.
        # Platinum does exist at the broker as XPTUSD.R (id 13678) but is not in
        # TOP_SYMBOLS, so an XPTUSD alert is now rejected as not-approved rather
        # than executed against the wrong metal. Add it to TOP_SYMBOLS with its
        # own point_value, tick_size and sessions before trading it.
        "LVMH": "LVMH", "XPDUSD": "XPDUSD.R", "GOOG": "ALPHABET-C", "GOOGL": "ALPHABET-C",
        "UKOIL": "UKOIL.R", "USOIL": "USOIL.R", "SIEMENS": "SIEMENS", "GE": "GE",
        "US30": "US30.R", "US100": "NAS100.R", "US500": "SPX500.R", "XAUUSD": "XAUUSD.R",
        # Crypto
        "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
        # Forex
        "EURUSD": "EURUSD.R", "GBPUSD": "GBPUSD.R", "USDJPY": "USDJPY.R", "GBPJPY": "GBPJPY.R",
    }
    base = tv_symbol.replace("1!", "").replace("!", "").upper()
    return SYMBOL_MAP.get(base, tv_symbol)

# Cache for get_all_positions to prevent TradeLocker API hammering
_POSITIONS_CACHE: dict = {"data": None, "expires_at": 0.0, "last_error": None}
_POSITIONS_CACHE_TTL = 120.0


_INSTRUMENT_ID_CACHE: dict = {}
_INSTRUMENT_ID_TTL = 3600.0


def get_instrument_id(tl_symbol: str) -> int:
    """
    Cached instrument id lookup.

    The bare tradelocker call hits the API on every invocation, and the
    per-position symbol resolution loops below call it once per symbol per
    open position. That is what produced the 429 rate limits.
    """
    now = datetime.now().timestamp()
    cached = _INSTRUMENT_ID_CACHE.get(tl_symbol)
    if cached is not None and now < cached[1]:
        return cached[0]
    value = tl.get_instrument_id_from_symbol_name(tl_symbol)
    _INSTRUMENT_ID_CACHE[tl_symbol] = (value, now + _INSTRUMENT_ID_TTL)
    return value


def resolve_symbol_for_instrument(instrument_id: int) -> str | None:
    """Map a broker instrument id back to our configured symbol, or None."""
    for sym in TOP_SYMBOLS:
        try:
            if get_instrument_id(sym) == instrument_id:
                return sym
        except Exception:
            continue
    return None


def get_cached_positions():
    """Return cached positions if fresh, otherwise fetch from broker."""
    now = datetime.now().timestamp()
    cached = _POSITIONS_CACHE.get("data")
    expires = _POSITIONS_CACHE.get("expires_at", 0.0)
    if cached is not None and now < expires:
        return cached
    try:
        positions = tl.get_all_positions()
        _POSITIONS_CACHE["data"] = positions
        _POSITIONS_CACHE["expires_at"] = now + _POSITIONS_CACHE_TTL
        _POSITIONS_CACHE["last_error"] = None
        return positions
    except Exception as exc:
        _POSITIONS_CACHE["last_error"] = str(exc)
        print(f"[POS CACHE] Fetch failed: {exc}", flush=True)
        return cached if cached is not None else pd.DataFrame()

# Cooldown tracking for stop-outs (per symbol)
_STOP_OUT_COOLDOWN: dict[str, float] = {}  # tl_symbol -> expiry timestamp

def record_stop_out(tl_symbol: str):
    """Record a stop-out event for cooldown enforcement."""
    if ENABLE_COOLDOWN_GATE:
        expiry = datetime.now().timestamp() + COOLDOWN_AFTER_STOP_MINUTES * 60
        _STOP_OUT_COOLDOWN[tl_symbol] = expiry
        _save_cooldown_state()
        print(f"[COOLDOWN] {tl_symbol}: stop-out recorded, blocked until {datetime.fromtimestamp(expiry).isoformat()}", flush=True)

def is_in_cooldown(tl_symbol: str) -> tuple[bool, float | None]:
    """Check if symbol is in post-stop-out cooldown. Returns (in_cooldown, seconds_remaining)."""
    if not ENABLE_COOLDOWN_GATE:
        return False, None
    now = datetime.now().timestamp()
    if _prune_stop_out_cooldowns(now):
        _save_cooldown_state()
    expiry = _STOP_OUT_COOLDOWN.get(tl_symbol)
    if expiry is None:
        return False, None
    if now < expiry:
        return True, expiry - now
    return False, None


# State file for persisting cooldown and daily realized P&L across restarts
STATE_FILE = "/opt/trader-agent/scripts/bot_state.json"

def _load_state() -> dict:
    """Load persistent state from JSON file."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_state(state: dict):
    """Save persistent state to JSON file."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as exc:
        print(f"[STATE] Could not save state: {exc}", flush=True)

def _init_cooldown_from_state():
    """Load cooldown expiries from state file on startup."""
    global _STOP_OUT_COOLDOWN
    state = _load_state()
    cooldowns = state.get("stop_out_cooldowns", {})
    now = datetime.now().timestamp()
    restored = {}
    for symbol, expiry in cooldowns.items():
        try:
            expiry = float(expiry)
        except (TypeError, ValueError):
            continue
        if expiry > now:
            restored[symbol] = expiry
            print(f"[COOLDOWN] Restored {symbol}: blocked until {datetime.fromtimestamp(expiry).isoformat()}", flush=True)
    _STOP_OUT_COOLDOWN = restored
    if restored != cooldowns:
        _save_cooldown_state()


def _prune_stop_out_cooldowns(now: float | None = None) -> bool:
    """Remove expired cooldowns and report whether persistent state changed."""
    now = datetime.now().timestamp() if now is None else now
    active = {symbol: expiry for symbol, expiry in _STOP_OUT_COOLDOWN.items() if expiry > now}
    changed = active != _STOP_OUT_COOLDOWN
    if changed:
        _STOP_OUT_COOLDOWN.clear()
        _STOP_OUT_COOLDOWN.update(active)
    return changed

def _save_cooldown_state():
    """Save current cooldown expiries to state file."""
    _prune_stop_out_cooldowns()
    state = _load_state()
    state["stop_out_cooldowns"] = _STOP_OUT_COOLDOWN
    _save_state(state)

def _get_daily_realized_pnl() -> float:
    """Get realized P&L for current ET day from state file."""
    import pytz
    et = pytz.timezone('US/Eastern')
    now_et = datetime.now(et)
    today_key = now_et.strftime("%Y-%m-%d")
    state = _load_state()
    daily = state.get("daily_realized_pnl", {})
    return float(daily.get(today_key, 0.0))

def _add_daily_realized_pnl(pnl: float):
    """Add realized P&L for current ET day to state file."""
    import pytz
    et = pytz.timezone('US/Eastern')
    now_et = datetime.now(et)
    today_key = now_et.strftime("%Y-%m-%d")
    state = _load_state()
    daily = state.get("daily_realized_pnl", {})
    daily[today_key] = daily.get(today_key, 0.0) + pnl
    state["daily_realized_pnl"] = daily
    # Also prune old days (keep last 30)
    cutoff = (now_et - timedelta(days=30)).strftime("%Y-%m-%d")
    state["daily_realized_pnl"] = {k: v for k, v in daily.items() if k >= cutoff}
    _save_state(state)

# Cache for JPY point value (USDJPY rate)
_JPY_PV_CACHE: dict = {"value": None, "expires_at": 0.0}
_JPY_PV_TTL = 60.0


def get_point_value(tl_symbol: str) -> float:
    """Get the USD value of a one-price-unit move for one lot.

    For JPY pairs (GBPJPY.R, USDJPY.R), dynamically calculates
    100000 / USDJPY_rate using a cached live USDJPY bid price.
    Falls back to a static value if the price can't be fetched.
    """
    symbol_config = TOP_SYMBOLS.get(tl_symbol, {})

    if symbol_config.get("point_value_currency") == "JPY":
        now = datetime.now().timestamp()
        if _JPY_PV_CACHE["value"] is not None and now < _JPY_PV_CACHE["expires_at"]:
            return _JPY_PV_CACHE["value"]

        try:
            usd_jpy_id = tl.get_instrument_id_from_symbol_name("USDJPY.R")
            usd_jpy_price = tl.get_latest_bid_price(usd_jpy_id)
            if usd_jpy_price > 0:
                value = 100000.0 / usd_jpy_price
                _JPY_PV_CACHE["value"] = value
                _JPY_PV_CACHE["expires_at"] = now + _JPY_PV_TTL
                return value
        except Exception as exc:
            print(f"[POINT VALUE] Could not fetch USDJPY for JPY conversion: {exc}", flush=True)

        return symbol_config.get("fallback_point_value", 650.0)

    return symbol_config.get("point_value", 1.0)

def get_min_lot(tl_symbol: str) -> float:
    return TOP_SYMBOLS.get(tl_symbol, {}).get("min_lot", 0.01)

def quantize_price(price: float, tick_size: float) -> float:
    return round(round(price / tick_size) * tick_size, 6)

def calculate_position_size(ticker: str, entry_price: float, stop_loss: float) -> float | None:
    """Dynamic position sizing based on $100 risk. Returns None if max_lot exceeded."""
    sl_distance = abs(entry_price - stop_loss)
    if sl_distance == 0:
        return get_min_lot(ticker)

    tl_symbol = map_symbol(ticker)
    point_val = get_point_value(tl_symbol)
    currency = TOP_SYMBOLS.get(tl_symbol, {}).get("currency", "USD")

    risk_per_lot = sl_distance * point_val
    if currency == "EUR":
        risk_per_lot *= EUR_USD_RATE

    if risk_per_lot <= 0:
        return get_min_lot(tl_symbol)

    calculated_qty = TARGET_DOLLAR_RISK / risk_per_lot
    min_lot = get_min_lot(tl_symbol)
    max_lot = TOP_SYMBOLS.get(tl_symbol, {}).get("max_lot", 1.0)

    if calculated_qty > max_lot and max_lot > 0:
        return None

    return round(max(min_lot, min(calculated_qty, max_lot)), 2)

EUR_USD_RATE = 1.08

def validate_entry(tl_symbol: str, action: str, entry: float, sl: float) -> tuple[bool, str]:
    """Validate trade entry against risk rules."""
    if tl_symbol not in TOP_SYMBOLS:
        return False, f"Symbol {tl_symbol} not in approved list"
    
    if not is_session_active(TOP_SYMBOLS[tl_symbol]):
        return False, f"Outside trading session for {tl_symbol}"
    
    # Check R:R
    # We'd need TP from signal; for now just validate SL distance reasonable
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return False, "Invalid stop loss"

    return True, "OK"


def validate_sl_distance(tl_symbol: str, action: str, entry: float, sl: float) -> tuple[bool, str]:
    """Validate that even at minimum lot, SL risk doesn't exceed MAX_SL_OVERSHOOT_PCT % of TARGET_DOLLAR_RISK.

    This prevents trades where the SL is so wide that min lot exposure still breaches the risk limit.
    """
    if tl_symbol not in TOP_SYMBOLS:
        return False, f"Symbol {tl_symbol} not in approved list"

    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return False, "Invalid stop loss"

    min_lot = get_min_lot(tl_symbol)
    point_val = get_point_value(tl_symbol)
    min_lot_risk = sl_dist * min_lot * point_val
    max_acceptable_risk = TARGET_DOLLAR_RISK * (1 + MAX_SL_OVERSHOOT_PCT / 100)

    if min_lot_risk > max_acceptable_risk:
        max_sl_dist = max_acceptable_risk / (min_lot * point_val)
        return False, (
            f"SL distance {sl_dist:.2f} too wide: min lot risk=${min_lot_risk:.2f} "
            f"exceeds ${max_acceptable_risk:.2f}. Max SL distance={max_sl_dist:.2f} pts"
        )

    return True, "OK"


def validate_spread(tl_symbol: str) -> tuple[bool, str]:
    """Validate current spread is within symbol's max_spread tolerance."""
    if tl_symbol not in TOP_SYMBOLS:
        return False, f"Symbol {tl_symbol} not in approved list"

    max_spread = TOP_SYMBOLS[tl_symbol].get("max_spread", 5.0)
    try:
        instrument_id = get_instrument_id(tl_symbol)
        bid = tl.get_latest_bid_price(instrument_id)
        ask = tl.get_latest_asking_price(instrument_id)
        if bid <= 0 or ask <= 0:
            return True, "Spread data unavailable, skipping check"

        spread = abs(ask - bid)
        if spread > max_spread:
            return False, (
                f"Spread {spread:.4f} exceeds max {max_spread} for {tl_symbol}"
            )
        return True, "OK"
    except Exception as exc:
        print(f"[SPREAD] Could not check spread for {tl_symbol}: {exc}", flush=True)
        return True, "Spread check skipped"


def _realized_pnl_from_executions(executions_df, et_date) -> float:
    """Match execution lots and total realized P&L for closes on an ET date."""
    if executions_df is None:
        raise RuntimeError("TradeLocker returned no execution history")
    if executions_df.empty:
        return 0.0

    required = {"positionId", "tradableInstrumentId", "createdDate", "side", "qty", "price"}
    missing = required.difference(executions_df.columns)
    if missing:
        raise ValueError(f"execution history missing columns: {sorted(missing)}")

    symbol_by_instrument = {
        int(get_instrument_id(symbol)): symbol for symbol in TOP_SYMBOLS
    }
    executions = executions_df[
        executions_df["tradableInstrumentId"].isin(symbol_by_instrument)
    ].copy()
    if executions.empty:
        return 0.0
    if executions["positionId"].isna().any():
        raise ValueError("execution history contains rows without positionId")

    executions["createdDate"] = pd.to_numeric(executions["createdDate"], errors="coerce")
    if executions["createdDate"].isna().any():
        raise ValueError("execution history contains invalid createdDate values")
    executions["time"] = pd.to_datetime(executions["createdDate"], unit="ms", utc=True).dt.tz_convert("US/Eastern")
    executions = executions.sort_values(["positionId", "time"])

    realized = 0.0
    for (_, instrument_id), group in executions.groupby(
        ["positionId", "tradableInstrumentId"], dropna=False
    ):
        symbol = symbol_by_instrument[int(instrument_id)]
        value_per_unit = get_point_value(symbol)
        if TOP_SYMBOLS[symbol].get("currency") == "EUR":
            value_per_unit *= EUR_USD_RATE
        open_lots = {"buy": deque(), "sell": deque()}

        for row in group.itertuples(index=False):
            side = str(row.side).lower()
            if side not in open_lots:
                raise ValueError(f"unexpected execution side {row.side!r}")
            quantity = float(row.qty)
            price = float(row.price)
            if not np.isfinite(quantity) or quantity <= 0 or not np.isfinite(price):
                raise ValueError("execution history contains invalid qty or price")

            opposite = "sell" if side == "buy" else "buy"
            while quantity > 1e-9 and open_lots[opposite]:
                entry_quantity, entry_price = open_lots[opposite][0]
                matched_quantity = min(quantity, entry_quantity)
                if opposite == "buy":
                    pnl = (price - entry_price) * matched_quantity * value_per_unit
                else:
                    pnl = (entry_price - price) * matched_quantity * value_per_unit
                if row.time.date() == et_date:
                    realized += pnl
                quantity -= matched_quantity
                entry_quantity -= matched_quantity
                if entry_quantity <= 1e-9:
                    open_lots[opposite].popleft()
                else:
                    open_lots[opposite][0] = (entry_quantity, entry_price)

            if quantity > 1e-9:
                open_lots[side].append((quantity, price))

    return realized


def compute_daily_pnl() -> float:
    """Compute realized + unrealized P&L for the current America/New_York day.

    Returns total P&L in USD. Returns negative infinity when broker data cannot
    be verified so the daily-loss gate rejects new entries.
    """
    realized = None
    unrealized = None
    try:
        import pytz

        now_et = datetime.now(pytz.timezone("America/New_York"))
        positions_df = get_cached_positions()
        if _POSITIONS_CACHE.get("last_error"):
            raise RuntimeError(f"open-position fetch failed: {_POSITIONS_CACHE['last_error']}")
        unrealized = 0.0
        if positions_df is not None and not positions_df.empty:
            if "unrealizedPl" not in positions_df.columns:
                raise ValueError("open positions missing unrealizedPl")
            unrealized = float(pd.to_numeric(positions_df["unrealizedPl"], errors="raise").sum())

        executions_df = tl.get_all_executions()
        realized = _realized_pnl_from_executions(executions_df, now_et.date())
        total = realized + unrealized
        print(
            f"[DAILY P&L] realized=${realized:.2f} unrealized=${unrealized:.2f} total=${total:.2f}",
            flush=True,
        )
        return total
    except Exception as exc:
        realized_log = f"${realized:.2f}" if realized is not None else "unavailable"
        unrealized_log = f"${unrealized:.2f}" if unrealized is not None else "unavailable"
        print(
            f"[DAILY P&L] realized={realized_log} unrealized={unrealized_log} total=unverified",
            flush=True,
        )
        print(f"[DAILY P&L] CRITICAL: could not verify realized/unrealized P&L; failing closed: {exc}", flush=True)
        return float("-inf")


def validate_alert_freshness(bar_time_str: str | None, tl_symbol: str) -> tuple[bool, str]:
    """Validate alert is fresh (not stale, not from future).
    
    Args:
        bar_time_str: ISO-8601 timestamp in ET from TradingView (e.g., "2026-09-30T03:15:00-05:00")
        tl_symbol: Symbol for logging
    
    Returns:
        (is_valid, reason)
    """
    if not ENABLE_ALERT_FRESHNESS_GATE:
        return True, "freshness gate disabled"
    
    if not bar_time_str:
        return False, f"Missing required 'bar_time' field for {tl_symbol} (alert freshness gate)"
    
    try:
        import pytz
        et = pytz.timezone('US/Eastern')
        now_et = datetime.now(et)
        
        # Parse ISO-8601 with timezone
        bar_time = datetime.fromisoformat(bar_time_str.replace("Z", "+00:00"))
        if bar_time.tzinfo is None:
            bar_time = et.localize(bar_time)
        else:
            bar_time = bar_time.astimezone(et)
        
        age_minutes = (now_et - bar_time).total_seconds() / 60
        
        if age_minutes < -1:  # Allow 1 min clock skew
            return False, f"Alert from future: {bar_time_str} ({abs(age_minutes):.0f} min ahead)"
        
        if age_minutes > ALERT_MAX_AGE_MINUTES:
            return False, f"Alert too old: {age_minutes:.0f} min > {ALERT_MAX_AGE_MINUTES} max"
        
        return True, "OK"
    except Exception as exc:
        return False, f"Invalid bar_time format '{bar_time_str}': {exc}"


def validate_trend_gate(action: str, tech_summary: str, tl_symbol: str) -> tuple[bool, str]:
    """Hard trend gate: reject counter-trend entries before AI call.
    
    SELL + tech in {"Buy", "Strong Buy"} → reject
    BUY + tech in {"Sell", "Strong Sell"} → reject
    Neutral / fetch failure → pass to AI
    
    Returns:
        (is_valid, reason)
    """
    if not ENABLE_TREND_GATE:
        return True, "trend gate disabled"
    
    tech = (tech_summary or "").strip().lower()
    action_lower = action.lower()
    
    if action_lower == "sell" and tech in ("buy", "strong buy"):
        return False, f"Trend gate: SELL rejected — tech summary is '{tech_summary}' (bullish)"
    
    if action_lower == "buy" and tech in ("sell", "strong sell"):
        return False, f"Trend gate: BUY rejected — tech summary is '{tech_summary}' (bearish)"
    
    return True, "OK"


def validate_slippage_guard(tl_symbol: str, action: str, entry: float, sl: float) -> tuple[bool, str]:
    """XPDUSD.R stop-fill slippage guard.
    
    - Blocks entry if spread > XPDUSD_MAX_SPREAD_PCT of price
    - Logs warning if SL distance * XPDUSD_SLIPPAGE_MULTIPLE would exceed reasonable fill
    - For production: could use stop-limit order with offset
    - FAILS CLOSED for XPDUSD.R if spread data unavailable
    
    Returns:
        (is_valid, reason)
    """
    if not ENABLE_SLIPPAGE_GUARD or tl_symbol != "XPDUSD.R":
        return True, "slippage guard disabled or not XPDUSD.R"
    
    try:
        instrument_id = get_instrument_id(tl_symbol)
        bid = tl.get_latest_bid_price(instrument_id)
        ask = tl.get_latest_asking_price(instrument_id)
        if bid <= 0 or ask <= 0:
            return False, "Slippage guard: spread data unavailable for XPDUSD.R (fail closed)"
        
        spread = abs(ask - bid)
        spread_pct = spread / bid
        
        if spread_pct > XPDUSD_MAX_SPREAD_PCT:
            return False, (
                f"Slippage guard: spread {spread_pct:.4%} > {XPDUSD_MAX_SPREAD_PCT:.2%} max for {tl_symbol}"
            )
        
        sl_dist = abs(entry - sl)
        if sl_dist > 0:
            max_fill_dist = sl_dist * XPDUSD_SLIPPAGE_MULTIPLE
            print(f"[SLIPPAGE GUARD] {tl_symbol}: spread={spread_pct:.4%}, SL_dist={sl_dist:.2f}, "
                  f"max_fill_dist={max_fill_dist:.2f} ({XPDUSD_SLIPPAGE_MULTIPLE}x SL)", flush=True)
        
        return True, "OK"
    except Exception as exc:
        print(f"[SLIPPAGE GUARD] Error: {exc}", flush=True)
        return False, f"Slippage guard: error fetching spread data for XPDUSD.R (fail closed): {exc}"


def get_live_price(tl_symbol: str) -> float:
    """Fetch current market price from TradeLocker for a symbol."""
    try:
        instrument_id = get_instrument_id(tl_symbol)
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(minutes=10)).timestamp() * 1000)
        hist = tl.get_price_history(
            instrument_id=int(instrument_id),
            resolution="5m",
            start_timestamp=start_ts,
            end_timestamp=end_ts
        )
        if hist.empty or len(hist) < 1:
            return 0.0
        df = hist.rename(columns={'t': 'time', 'o': 'o', 'h': 'h', 'l': 'l', 'c': 'c', 'v': 'v'})
        current_price = df['c'].iloc[-1]
        return current_price
    except Exception:
        return 0.0


def calculate_atr_sl(tl_symbol: str, action: str, current_price: float) -> float:
    """Calculate stop loss using ATR(10) * 3.0 from recent price data (Phantom Shift params).
    Uses current market price for SL calculation, not stale alert price."""
    try:
        instrument_id = get_instrument_id(tl_symbol)
        # Fetch last 20 5m bars for ATR calculation
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(hours=2)).timestamp() * 1000)
        hist = tl.get_price_history(
            instrument_id=int(instrument_id),
            resolution="5m",
            start_timestamp=start_ts,
            end_timestamp=end_ts
        )
        if hist.empty or len(hist) < 14:
            print(f"[ATR DEBUG] {tl_symbol}: No price history (len={len(hist) if not hist.empty else 0})", flush=True)
            return 0.0
        
        df = hist.rename(columns={'t': 'time', 'o': 'o', 'h': 'h', 'l': 'l', 'c': 'c', 'v': 'v'})
        high, low, close = df['h'], df['l'], df['c']
        
        print(f"[ATR DEBUG] {tl_symbol}: instrument_id={instrument_id}, bars={len(df)}, current_price={current_price}, high_range={high.min()}-{high.max()}, low_range={low.min()}-{low.max()}", flush=True)
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(10).mean().iloc[-1]
        
        if pd.isna(atr) or atr == 0:
            print(f"[ATR DEBUG] {tl_symbol}: ATR is NaN or 0", flush=True)
            return 0.0
        
        sl_distance = atr * 3.0
        print(f"[ATR DEBUG] {tl_symbol}: atr={atr}, sl_distance={sl_distance}, action={action}, current_price={current_price}", flush=True)
        if action == "buy":
            sl_price = current_price - sl_distance
        else:
            sl_price = current_price + sl_distance
        
        # Round to symbol's tick size
        tick_size = TOP_SYMBOLS.get(tl_symbol, {}).get("tick_size", 0.01)
        sl_price = quantize_price(sl_price, tick_size)
        return sl_price
    except Exception as e:
        print(f"[ATR DEBUG] {tl_symbol}: Exception: {e}", flush=True)
        return 0.0


def _ewm_avg(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _macd(series, fast=12, slow=26, signal=9):
    ema_fast = _ewm_avg(series, fast)
    ema_slow = _ewm_avg(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _adx(high, low, close, period=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    tr_mean = tr.rolling(period).mean()
    dx = 100 * (tr_mean / (tr_mean + 1e-10))
    return dx.rolling(period).mean()


def _adx_di(high, low, close, period=14):
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_di = 100 * up_move.clip(lower=0).rolling(period).mean() / (close.diff().abs().rolling(period).mean() + 1e-10)
    minus_di = 100 * down_move.clip(lower=0).rolling(period).mean() / (close.diff().abs().rolling(period).mean() + 1e-10)
    return plus_di, minus_di


def _williams_r(high, low, close, period=14):
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    return -100 * (highest_high - close) / (highest_high - lowest_low + 1e-10)


def get_technical_summary(tl_symbol: str) -> str:
    """Compute a TradingView-style technical summary for a symbol.
    
    Uses EMA(9/21), SMA(50), RSI(14), MACD, ADX, Williams %R to produce
    a rating: "Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell".
    """
    try:
        instrument_id = get_instrument_id(tl_symbol)
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(hours=18)).timestamp() * 1000)
        hist = tl.get_price_history(
            instrument_id=int(instrument_id),
            resolution="5m",
            start_timestamp=start_ts,
            end_timestamp=end_ts,
        )
        if hist is None or hist.empty or len(hist) < 50:
            return "Neutral"

        df = hist.rename(columns={"t": "time", "o": "o", "h": "h", "l": "l", "c": "c", "v": "v"})
        close = df["c"]
        high = df["h"]
        low = df["l"]

        buy_score = 0
        sell_score = 0

        ema9 = _ewm_avg(close, 9)
        ema21 = _ewm_avg(close, 21)
        sma50 = close.rolling(50).mean()
        rsi = _rsi(close, 14)
        macd_line, signal_line = _macd(close, 12, 26, 9)
        adx = _adx(high, low, close, 14)
        plus_di, minus_di = _adx_di(high, low, close, 14)
        williams = _williams_r(high, low, close, 14)

        last = -1
        if pd.isna(ema9.iloc[last]) or pd.isna(ema21.iloc[last]) or pd.isna(sma50.iloc[last]):
            return "Neutral"

        # 1. EMA trend: price > EMA9 > EMA21 = buy; reverse = sell
        if close.iloc[last] > ema9.iloc[last] > ema21.iloc[last]:
            buy_score += 1
        elif close.iloc[last] < ema9.iloc[last] < ema21.iloc[last]:
            sell_score += 1

        # 2. SMA50 direction: price > SMA50 = buy, price < SMA50 = sell
        if close.iloc[last] > sma50.iloc[last]:
            buy_score += 1
        else:
            sell_score += 1

        # 3. RSI(14)
        if rsi.iloc[last] > 60:
            buy_score += 1
        elif rsi.iloc[last] < 40:
            sell_score += 1

        # 4. MACD
        if macd_line.iloc[last] > signal_line.iloc[last]:
            buy_score += 1
        else:
            sell_score += 1

        # 5. ADX + DI/-DI (only count if ADX > 25 = trending)
        if adx.iloc[last] > 25:
            if plus_di.iloc[last] > minus_di.iloc[last]:
                buy_score += 1
            else:
                sell_score += 1

        # 6. Williams %R: oversold (< -80) = buy, overbought (> -20) = sell
        if williams.iloc[last] < -80:
            buy_score += 1
        elif williams.iloc[last] > -20:
            sell_score += 1

        if buy_score >= 5:
            return "Strong Buy"
        elif buy_score >= 3 and buy_score > sell_score:
            return "Buy"
        elif sell_score >= 5:
            return "Strong Sell"
        elif sell_score >= 3 and sell_score > buy_score:
            return "Sell"
        else:
            return "Neutral"
    except Exception as e:
        print(f"[TECH DEBUG] {tl_symbol}: Exception: {e}", flush=True)
        return "Neutral"


# =============================================================================
# TRAILING STOP BACKGROUND TASK
# =============================================================================

# Track positions that already have breakeven SL applied
_be_applied_positions: set[int] = set()

# Track position info for realized P&L calculation on close
# Maps position_id -> {"symbol": str, "side": str, "entry_price": float, "qty": float, "unrealized_pl": float}
_position_tracker: dict[int, dict] = {}

async def check_and_apply_trailing_stops(positions_df=None):
    """Background task: move SL to breakeven when profitable."""
    global _be_applied_positions

    if positions_df is None:
        positions_df = get_cached_positions()
    try:
        if positions_df is None or positions_df.empty:
            return

        for _, pos in positions_df.iterrows():
            position_id = int(pos.get("id", 0))
            unrealized_pl = float(pos.get("unrealizedPl", 0.0))
            avg_price = float(pos.get("avgPrice", 0.0))
            instrument_id = int(pos.get("tradableInstrumentId", 0))

            if unrealized_pl >= TRAILING_SL_BE_PROFIT:
                if position_id in _be_applied_positions:
                    continue

                try:
                    modification_params = {
                        "stopLossType": "absolute",
                        "stopLoss": avg_price
                    }
                    success = tl.modify_position(position_id, modification_params)
                    if success:
                        _be_applied_positions.add(position_id)
                        print(f"[BE SL] Position {position_id}: SL moved to BE @{avg_price}, P&L=${unrealized_pl:.2f}", flush=True)
                    else:
                        print(f"[BE SL] Failed to move SL to BE for position {position_id}", flush=True)
                except Exception as e:
                    print(f"[BE SL] Error for position {position_id}: {e}", flush=True)

    except Exception as e:
        print(f"[BE SL] Background task error: {e}", flush=True)


def find_opposing_position(tl_symbol: str, action: str, positions_df=None):
    """
    Return the open position that blocks this entry, or None.

    Same instrument, opposite side. Blocks a buy when a sell is already open on
    that CFD and vice versa.
    """
    if not BLOCK_OPPOSING_SAME_SYMBOL:
        return None
    if positions_df is None:
        positions_df = get_cached_positions()
    if positions_df is None or positions_df.empty:
        return None

    try:
        instrument_id = get_instrument_id(tl_symbol)
    except Exception as exc:
        print(f"[OPPOSING] Could not resolve {tl_symbol} instrument id: {exc}", flush=True)
        return None

    opposite = "sell" if str(action).lower() == "buy" else "buy"
    for _, pos in positions_df.iterrows():
        if int(pos.get("tradableInstrumentId", 0)) != instrument_id:
            continue
        if str(pos.get("side", "")).lower() == opposite:
            return pos
    return None


def flatten_position(position_id: int, instrument_id: int, tl_symbol: str,
                     reason: str, avg_price: float, unrealized_pl: float) -> bool:
    """
    Force a position closed: breakeven stop first, then a close order.

    close_position() is documented as not guaranteed to fill immediately (it
    tries IOC then GTC), so the stop is moved to entry first. That way the
    worst case is closing flat at entry rather than riding the gap.
    """
    print(
        f"[SESSION] Position {position_id} {tl_symbol} {reason} "
        f"entry={avg_price} PnL=${unrealized_pl:.2f} — FLATTENING",
        flush=True,
    )

    try:
        tl.modify_position(position_id, {
            "stopLossType": "absolute",
            "stopLoss": avg_price,
        })
        print(f"[SESSION] Position {position_id}: SL pinned to entry {avg_price}", flush=True)
    except Exception as exc:
        print(f"[SESSION] Position {position_id}: breakeven stop failed: {exc}", flush=True)

    try:
        tl.close_position(position_id=position_id)
        print(f"[SESSION] Position {position_id}: close order placed", flush=True)
        return True
    except Exception as exc:
        print(f"[SESSION] Position {position_id}: close order FAILED: {exc}", flush=True)
        return False


def check_and_flatten_before_session_end(positions_df=None) -> int:
    """
    Close every position whose trading session is closing, already closed, or
    about to hit the weekend gap.

    This is the guarantee that no trade is held past its session: is_session_active()
    only gates entry, so without this a position opened near the close rides the
    illiquid gap and can fill far beyond its stop.
    """
    if positions_df is None:
        positions_df = get_cached_positions()

    flattened = 0
    try:
        if positions_df is None or positions_df.empty:
            return 0

        for _, pos in positions_df.iterrows():
            position_id = int(pos.get("id", 0))
            if not position_id:
                continue
            instrument_id = int(pos.get("tradableInstrumentId", 0))
            qty = float(pos.get("qty", 0.0))
            side = str(pos.get("side", ""))
            avg_price = float(pos.get("avgPrice", 0.0))
            unrealized_pl = float(pos.get("unrealizedPl", 0.0))

            if qty == 0:
                continue

            tl_symbol = resolve_symbol_for_instrument(instrument_id)
            if tl_symbol is None:
                print(
                    f"[SESSION] Position {position_id}: instrument {instrument_id} is not in "
                    f"the configured symbol list, cannot evaluate session — leaving open",
                    flush=True,
                )
                continue

            reason = flatten_reason(TOP_SYMBOLS[tl_symbol])
            if reason is None:
                continue

            if flatten_position(position_id, instrument_id, tl_symbol, reason,
                                avg_price, unrealized_pl):
                flattened += 1
                # Drop the snapshot: it still lists this position, and the next
                # 30s tick would otherwise re-issue a close order for it.
                _POSITIONS_CACHE["expires_at"] = 0.0
    except Exception as exc:
        print(f"[SESSION] Flatten sweep error: {exc}", flush=True)

    if flattened:
        print(f"[SESSION] Flattened {flattened} position(s) ahead of a session close", flush=True)
    return flattened


def parse_position_open_time(pos) -> "datetime | None":
    """Extract the position open time from a TradeLocker positions row.

    The positions endpoint returns the column as ``openDate`` in epoch
    milliseconds. An earlier version of this code read ``openTime``, which the
    API never sends, so the max-hold rule silently matched nothing and no
    position was ever closed for being overdue.

    Accepts ``openDate`` and the legacy ``openTime`` spelling, numeric epoch
    values in either seconds or milliseconds, and ISO-8601 strings. Returns
    None when no usable timestamp is present so callers can report it instead
    of skipping quietly.
    """
    from datetime import datetime, timezone

    raw = pos.get("openDate", None)
    if raw is None or (isinstance(raw, float) and not np.isfinite(raw)):
        raw = pos.get("openTime", None)
    if raw is None:
        return None
    if isinstance(raw, (float, np.floating)) and not np.isfinite(raw):
        return None

    if isinstance(raw, pd.Timestamp):
        if raw.tzinfo is None:
            return raw.tz_localize("UTC")
        return raw.tz_convert("UTC")

    # A pandas Series yields numpy scalars, and numpy.int64 is NOT a subclass of
    # int, so a plain isinstance(raw, (int, float)) check silently rejects every
    # real epoch value. Accept any real number instead.
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, np.integer, np.floating)):
        value = float(raw)
        if not np.isfinite(value) or value <= 0:
            return None
        # Epoch seconds vs milliseconds. Current time is ~1.8e9 in seconds and
        # ~1.8e12 in milliseconds, so 1e11 cleanly separates the two scales and
        # stays valid for roughly the next 11,000 years.
        if value > 1e11:
            value = value / 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() in ("unknown", "none", "nat", "nan"):
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    return None


async def check_and_close_overdue_positions(positions_df=None):
    """Close positions that have been open longer than MAX_HOLD_TIME_MINUTES.

    For 5m scalping, trades should not run for hours. This catches any
    positions where the SL/trailing mechanism failed to trigger.
    Set MAX_HOLD_TIME_MINUTES = 0 to disable (rely on session-end flattening).
    """
    if MAX_HOLD_TIME_MINUTES <= 0:
        return
    if positions_df is None:
        positions_df = get_cached_positions()
    try:
        if positions_df is None or positions_df.empty:
            return

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        for _, pos in positions_df.iterrows():
            position_id = int(pos.get("id", 0))
            instrument_id = int(pos.get("tradableInstrumentId", 0))
            qty = float(pos.get("qty", 0.0))
            side = pos.get("side", "")
            avg_price = float(pos.get("avgPrice", 0.0))
            unrealized_pl = float(pos.get("unrealizedPl", 0.0))

            open_time = parse_position_open_time(pos)

            if open_time is None:
                # Fail loudly. Skipping silently here makes the whole max-hold
                # rule a silent no-op, which is how a position ran 4h36m
                # against a 45m limit without a single line in the log.
                print(
                    f"[OVERDUE] Position {position_id}: cannot determine open time "
                    f"(row keys: {sorted(pos.keys())}) — max-hold check SKIPPED, "
                    f"position may be held indefinitely",
                    flush=True,
                )
                continue

            age_minutes = (now - open_time).total_seconds() / 60

            if age_minutes > MAX_HOLD_TIME_MINUTES:
                symbol_name = resolve_symbol_for_instrument(instrument_id) or "UNKNOWN"

                print(
                    f"[OVERDUE] Position {position_id} {symbol_name} {side} {qty} "
                    f"open {age_minutes:.0f}m (limit {MAX_HOLD_TIME_MINUTES}m), "
                    f"entry={avg_price}, PnL=${unrealized_pl:.2f} — CLOSING",
                    flush=True,
                )

                # Pin the stop to entry first, then actually close. Relying on
                # the stop alone is not enough: a thin-session gap can fill far
                # beyond it, so the position is closed outright.
                try:
                    tl.modify_position(position_id, {
                        "stopLossType": "absolute",
                        "stopLoss": avg_price,
                    })
                    print(
                        f"[OVERDUE] Position {position_id}: SL moved to BE @{avg_price}",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"[OVERDUE] Failed to set BE SL for position {position_id}: {exc}", flush=True)

                try:
                    tl.close_position(position_id=position_id)
                    print(f"[OVERDUE] Position {position_id}: close order placed", flush=True)
                    _POSITIONS_CACHE["expires_at"] = 0.0
                except Exception as exc:
                    print(f"[OVERDUE] Close order FAILED for position {position_id}: {exc}", flush=True)

    except Exception as e:
        print(f"[OVERDUE] Position age check error: {e}", flush=True)


# A stop is a trigger, not a fill price. On a thin CFD the market can gap
# through it and fill far beyond the stop, so a position sized for a $100 loss
# can realize several hundred. This is how XPDUSD.R stopped at 1231.81 and
# filled at 1282.29: 50.48 points of slippage, 5.2x the stop distance, turning
# $86.58 of risk into $540.90. Detect it after the fact so the symbol's spread
# and sizing can be corrected.
SLIPPAGE_ALERT_MULTIPLE = 2.0  # flag a fill this many stop-distances past the stop
# Fallback threshold for the order-history audit, which has no entry price and
# so cannot compute a stop-distance multiple. 1% of the fill is far above
# ordinary spread-induced slippage but well under the 4.1% seen on XPDUSD.R.
STOP_FILL_OVERSHOOT_PCT = 0.01


def classify_stop_fill_slippage(entry: float, stop: float, fill: float) -> dict | None:
    """Compare a stop's fill against the stop distance it was meant to bound.

    Returns None when the fill is ordinary, otherwise a dict describing the
    overshoot so it can be logged and alerted on.
    """
    if not all(isinstance(v, (int, float)) for v in (entry, stop, fill)):
        return None

    stop_dist = abs(stop - entry)
    if stop_dist <= 0:
        return None

    overshoot = abs(fill - stop)
    if overshoot <= 0:
        return None

    multiple = overshoot / stop_dist
    if multiple < SLIPPAGE_ALERT_MULTIPLE:
        return None

    return {
        "stop_distance": stop_dist,
        "overshoot": overshoot,
        "multiple": multiple,
        "entry": entry,
        "stop": stop,
        "fill": fill,
    }


def audit_recent_stop_fills(orders_df) -> int:
    """Scan recent order history for stop orders that filled well past their stop.

    Purely observational: it does not place or modify orders. Returns the number
    of oversized fills found so callers can surface it.
    """
    if orders_df is None or len(orders_df) == 0:
        return 0

    flagged = 0
    try:
        stop_rows = orders_df[
            (orders_df.get("type", pd.Series(dtype=object)).astype(str).str.lower() == "stop")
            & (orders_df.get("status", pd.Series(dtype=object)).astype(str).str.lower() == "filled")
        ]
    except Exception as exc:
        print(f"[SLIPPAGE] Could not scan order history: {exc}", flush=True)
        return 0

    for _, order in stop_rows.iterrows():
        stop = _safe_float(order.get("price"))
        fill = _safe_float(order.get("avgPrice"))
        if stop is None or fill is None or stop <= 0 or fill <= 0:
            continue

        # A stop order is filed on the side opposite the position, so its
        # trigger is the stop level and the fill is what the broker gave us.
        # Order history carries no entry price, so measure the overshoot as a
        # fraction of the fill, which scales across instruments whose price
        # levels differ by orders of magnitude.
        overshoot = abs(fill - stop)
        if overshoot <= 0 or overshoot < fill * STOP_FILL_OVERSHOOT_PCT:
            continue

        flagged += 1
        print(
            f"[SLIPPAGE] Order {order.get('id')} stop@{stop} filled@{fill} "
            f"({overshoot:.2f} pts, {overshoot / fill * 100:.2f}% past the trigger) "
            f"— realized loss may exceed configured risk; review "
            f"{resolve_symbol_for_instrument(int(order.get('tradableInstrumentId', 0))) or 'unknown'} "
            f"spread and sizing",
            flush=True,
        )

    return flagged


def _safe_float(value):
    """Best-effort float conversion that never raises."""
    try:
        if value is None:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


# Start background task on startup
@app.on_event("startup")
async def start_trailing_stop_monitor():
    """Start the breakeven SL monitoring background task."""
    import asyncio

    _ai_reset_stats()
    _init_cooldown_from_state()

    # Session-map telemetry at startup (item 4 from round-3 brief)
    print("[SESSION MAP] Effective session configuration:", flush=True)
    for symbol, config in TOP_SYMBOLS.items():
        sessions = config.get("sessions", [])
        session_windows = []
        for s in sessions:
            if s in SESSIONS_ET:
                start, end = SESSIONS_ET[s]
                if start < end:
                    session_windows.append(f"{s}={start:02d}:00-{end:02d}:00 ET")
                else:
                    session_windows.append(f"{s}={start:02d}:00-{end:02d}:00 ET (overnight)")
            else:
                session_windows.append(f"{s}=unknown")
        print(f"[SESSION MAP]   {symbol}: sessions={sessions} windows={session_windows}", flush=True)
    
    async def be_check_loop():
        monitor_interval = POSITION_MONITOR_INTERVAL_SECONDS
        # Track known position IDs to detect closes
        known_position_ids: set[int] = set()
        
        while True:
            await asyncio.sleep(monitor_interval)
            try:
                positions_df = get_cached_positions()
            except Exception as exc:
                monitor_interval = min(
                    monitor_interval * 2,
                    POSITION_MONITOR_MAX_BACKOFF_SECONDS,
                )
                print(
                    f"[POSITION MONITOR] Broker check failed; retrying in {monitor_interval}s: {exc}",
                    flush=True,
                )
                continue

            global _position_tracker

            if positions_df is None or positions_df.empty:
                _be_applied_positions.clear()
                # Check for newly closed positions
                current_ids = set()
                if positions_df is not None:
                    current_ids = set(int(pos.get("id", 0)) for _, pos in positions_df.iterrows())
                closed_ids = known_position_ids - current_ids
                for pid in closed_ids:
                    # Record realized P&L for closed position
                    pos_info = _position_tracker.pop(pid, None)
                    if pos_info and pos_info.get("unrealized_pl") is not None:
                        realized_pl = float(pos_info["unrealized_pl"])
                        _add_daily_realized_pnl(realized_pl)
                        print(f"[DAILY P&L] Position {pid} ({pos_info['symbol']}) closed: realized P&L = ${realized_pl:.2f}", flush=True)
                known_position_ids = current_ids
                _position_tracker.clear()
                monitor_interval = POSITION_MONITOR_EMPTY_INTERVAL_SECONDS
                continue

            # Track position IDs for stop-out detection and realized P&L
            current_ids = set(int(pos.get("id", 0)) for _, pos in positions_df.iterrows())
            closed_ids = known_position_ids - current_ids
            if closed_ids:
                for pid in closed_ids:
                    pos_info = _position_tracker.pop(pid, None)
                    if pos_info and pos_info.get("unrealized_pl") is not None:
                        realized_pl = float(pos_info["unrealized_pl"])
                        _add_daily_realized_pnl(realized_pl)
                        print(f"[DAILY P&L] Position {pid} ({pos_info['symbol']}) closed: realized P&L = ${realized_pl:.2f}", flush=True)

            # Update position tracker with current positions
            for _, pos in positions_df.iterrows():
                pid = int(pos.get("id", 0))
                inst_id = int(pos.get("tradableInstrumentId", 0))
                sym = resolve_symbol_for_instrument(inst_id)
                if sym:
                    _position_tracker[pid] = {
                        "symbol": sym,
                        "side": str(pos.get("side", "")).lower(),
                        "entry_price": float(pos.get("avgPrice", 0.0)),
                        "qty": float(pos.get("qty", 0.0)),
                        "unrealized_pl": float(pos.get("unrealizedPl", 0.0)),
                    }

            if closed_ids and ENABLE_COOLDOWN_GATE:
                # Poll order history to detect stop-out closes
                try:
                    orders = tl.get_order_history()
                    if orders is not None and not orders.empty:
                        stop_fills = orders[
                            (orders.get("status", "").astype(str).str.lower() == "filled") &
                            (orders.get("type", "").astype(str).str.lower().isin(["stop", "stop_limit"]))
                        ]
                        for _, order in stop_fills.iterrows():
                            inst_id = int(order.get("tradableInstrumentId", 0))
                            sym = resolve_symbol_for_instrument(inst_id)
                            if sym and inst_id in [int(pos.get("tradableInstrumentId", 0)) for _, pos in positions_df.iterrows() if int(pos.get("id", 0)) in closed_ids]:
                                record_stop_out(sym)
                except Exception as exc:
                    print(f"[STOP-OUT DETECT] Could not check order history: {exc}", flush=True)
            
            known_position_ids = current_ids

            monitor_interval = POSITION_MONITOR_INTERVAL_SECONDS
            await check_and_apply_trailing_stops(positions_df)
            await check_and_close_overdue_positions(positions_df)
            # Session-exit guard must run last: it can close positions, which
            # invalidates the snapshot the two checks above just used.
            check_and_flatten_before_session_end(positions_df)

    asyncio.create_task(be_check_loop())
    print(
        f"[BE SL] Monitor started: trigger=${TRAILING_SL_BE_PROFIT}, "
        f"interval={POSITION_MONITOR_INTERVAL_SECONDS}s, empty={POSITION_MONITOR_EMPTY_INTERVAL_SECONDS}s",
        flush=True,
    )
    print(f"[OVERDUE] Position age limit={MAX_HOLD_TIME_MINUTES}min", flush=True)

@app.post("/webhook")
async def receive_tradingview_alert(request: Request):
    data = await request.json()

    task_id = uuid4().hex
    print(f"Webhook accepted for background processing: {task_id}", flush=True)
    task = asyncio.create_task(asyncio.to_thread(process_tradingview_alert, data, task_id))
    task.add_done_callback(report_background_task_failure)
    return {"status": "accepted", "task_id": task_id}

def report_background_task_failure(task):
    """Surface unexpected background failures in the service journal."""
    try:
        task.result()
    except Exception as error:
        print(f"[BACKGROUND WEBHOOK ERROR] {error}", flush=True)

def process_tradingview_alert(data: dict, task_id: str):
    """Run the alert handler and persist any unexpected crash before re-raising."""
    try:
        return _process_tradingview_alert(data, task_id)
    except Exception as exc:
        error_result = {
            "status": "error",
            "reason": f"Handler crashed: {type(exc).__name__}: {exc}",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        }
        log_alert(data, error_result)
        print(f"[HANDLER CRASH] {task_id}: {type(exc).__name__}: {exc}", flush=True)
        raise


def _process_tradingview_alert(data: dict, task_id: str):
    """Run the blocking broker and AI workflow outside the Uvicorn event loop."""

    # Log full raw payload for debugging
    print(f"Raw webhook payload ({task_id}): {json.dumps(data)}", flush=True)

    raw_action = data.get("action")
    tv_ticker = data.get("ticker")
    trend_context = data.get("trend") or "Unknown"
    alert_name = data.get("alert_name") or data.get("name") or data.get("alertName") or ""

    action = raw_action.strip().lower() if isinstance(raw_action, str) else ""

    # Handle TradingView placeholder for action (not interpolated in webhook JSON)
    if not action or (action.startswith("{{") and action.endswith("}}")):
        alert_lower = alert_name.lower()
        if "buy" in alert_lower or "bullish" in alert_lower or "long" in alert_lower:
            action = "buy"
        elif "sell" in alert_lower or "bearish" in alert_lower or "short" in alert_lower:
            action = "sell"
        else:
            trend_lower = trend_context.lower()
            if "buy" in trend_lower or "bullish" in trend_lower:
                action = "buy"
            elif "sell" in trend_lower or "bearish" in trend_lower:
                action = "sell"

    # Fail closed: an alert whose direction cannot be resolved is rejected outright. It must
    # never be defaulted to a side and carried downstream into the gates and the decider.
    if action not in ("buy", "sell"):
        result = {
            "status": "rejected",
            "reason": "Unresolvable action in alert payload",
            "gate": "action_parse",
        }
        log_alert(data, result)
        print(
            f"[GATE] action_parse: rejected — unresolvable action "
            f"(action={raw_action!r}, alert_name={alert_name!r}, trend={trend_context!r})",
            flush=True,
        )
        return result
    
    # Handle TradingView placeholder strings that weren't interpolated
    raw_sl = data.get("suggested_sl", 0.0)
    raw_entry = data.get("indicator_value", 0.0)
    
    # Try to parse as float, fallback to 0.0 if it's a placeholder string
    try:
        suggested_sl = float(raw_sl)
    except (ValueError, TypeError):
        suggested_sl = 0.0
    
    try:
        indicator_val = float(raw_entry)
    except (ValueError, TypeError):
        indicator_val = 0.0
    
    # Map to TradeLocker symbol
    if not tv_ticker:
        result = {
            "status": "rejected",
            "reason": "Missing or null 'ticker' field in alert payload"
        }
        log_alert(data, result)
        return result

    tl_symbol = map_symbol(tv_ticker)
    
    # Validate symbol is in our top list
    if tl_symbol not in TOP_SYMBOLS:
        result = {
            "status": "rejected",
            "reason": f"Symbol {tl_symbol} not in optimized 5m scalping list",
            "allowed_symbols": list(TOP_SYMBOLS.keys())
        }
        log_alert(data, result)
        return result
    
    # Session check
    if not is_session_active(TOP_SYMBOLS[tl_symbol]):
        allowed = TOP_SYMBOLS[tl_symbol]["sessions"]
        result = {
            "status": "rejected",
            "reason": f"Outside trading session for {tl_symbol} (allowed: {allowed})",
            "allowed_sessions": allowed
        }
        log_alert(data, result)
        print(f"[SESSION] {tl_symbol}: rejected — outside session (allowed: {allowed})", flush=True)
        return result
    
    # Technical summary check
    tech_summary = get_technical_summary(tl_symbol)
    print(f"[TECH SUMMARY] {tl_symbol}: {tech_summary}", flush=True)
    
    # Fetch live price from TradeLocker for accurate SL/TP calculation (needed by vetting gates)
    live_price = get_live_price(tl_symbol)
    if live_price <= 0:
        result = {"status": "rejected", "reason": "Could not fetch live price from TradeLocker"}
        log_alert(data, result)
        return result
    
    MIN_SL_PCT = {
        "GBPJPY.R": 0.003, "USDJPY.R": 0.003,
        "US30.R": 0.003, "NAS100.R": 0.003, "SPX500.R": 0.003,
        "XAUUSD.R": 0.005, "XPDUSD.R": 0.005, "UKOIL.R": 0.003,
        "LVMH": 0.005, "SIEMENS": 0.005, "ALPHABET-C": 0.005, "GE": 0.005,
    }
    # Wider percentage-based SL fallback for when ATR can't be calculated
    FALLBACK_SL_PCT = {
        "GBPJPY.R": 0.010, "USDJPY.R": 0.010,
        "XPDUSD.R": 0.020, "XAUUSD.R": 0.020, "UKOIL.R": 0.020,
    }

    print(f"[LIVE PRICE] {tl_symbol}: live_price={live_price}, webhook_price={indicator_val}", flush=True)
    
    # Auto-calculate SL from ATR if not provided or invalid (handles TradingView placeholder strings)
    if suggested_sl <= 0 or abs(indicator_val - suggested_sl) < 0.01:
        atr_sl = calculate_atr_sl(tl_symbol, action, live_price)
        if atr_sl > 0:
            suggested_sl = atr_sl
            print(f"[AUTO-SL] {tl_symbol} {action}: Calculated SL from ATR(10)*3 = {suggested_sl}")
        else:
            # Fallback: percentage-based SL when ATR data unavailable
            fallback_pct = FALLBACK_SL_PCT.get(tl_symbol, 0.015)
            if action == "buy":
                suggested_sl = live_price * (1 - fallback_pct)
            else:
                suggested_sl = live_price * (1 + fallback_pct)
            print(f"[ATR FALLBACK] {tl_symbol} {action}: ATR unavailable, using {fallback_pct:.1%} fallback SL = {suggested_sl}", flush=True)
    
    # Round SL to symbol's tick size (broker requirement)
    tick_size = TOP_SYMBOLS[tl_symbol].get("tick_size", 0.01)
    suggested_sl = quantize_price(suggested_sl, tick_size)
    
    # Enforce minimum SL distance (prevents ATR from giving too-tight stops)
    min_sl_dist = live_price * MIN_SL_PCT.get(tl_symbol, 0.005)
    actual_sl_dist = abs(live_price - suggested_sl)
    if actual_sl_dist < min_sl_dist:
        print(f"[MIN SL] {tl_symbol}: SL dist {actual_sl_dist:.2f} < min {min_sl_dist:.2f}, expanding", flush=True)
        if action == "buy":
            suggested_sl = live_price - min_sl_dist
        else:
            suggested_sl = live_price + min_sl_dist
        suggested_sl = quantize_price(suggested_sl, tick_size)
    
    # ===== ENTRY VETTING GATES (deterministic, before AI) =====
    
    # Gate 1: Alert freshness (requires bar_time in payload)
    bar_time = data.get("bar_time")
    fresh_valid, fresh_reason = validate_alert_freshness(bar_time, tl_symbol)
    if not fresh_valid:
        result = {"status": "rejected", "reason": fresh_reason, "gate": "alert_freshness"}
        log_alert(data, result)
        print(f"[GATE] {tl_symbol}: {fresh_reason}", flush=True)
        return result
    
    # Gate 2: Hard trend gate (counter-trend rejection)
    trend_valid, trend_reason = validate_trend_gate(action, tech_summary, tl_symbol)
    if not trend_valid:
        result = {"status": "rejected", "reason": trend_reason, "gate": "trend_gate"}
        log_alert(data, result)
        print(f"[GATE] {tl_symbol}: {trend_reason}", flush=True)
        return result
    
    # Gate 3: Daily P&L enforcement (before AI call)
    daily_pnl = compute_daily_pnl()
    if ENABLE_DAILY_LOSS_GATE and daily_pnl <= -MAX_DAILY_LOSS:
        reason = (
            f"Daily loss limit hit: ${daily_pnl:.2f} <= -${MAX_DAILY_LOSS:.0f}"
            if np.isfinite(daily_pnl)
            else "Daily P&L unavailable; rejecting new entry (fail closed)"
        )
        result = {"status": "rejected", "reason": reason, "gate": "daily_loss"}
        log_alert(data, result)
        print(f"[GATE] {tl_symbol}: Daily loss limit ${daily_pnl:.2f} blocks new entry", flush=True)
        return result
    
    # Gate 4: One position per symbol (same direction) + cooldown
    try:
        positions_df = get_cached_positions()
        if positions_df is not None and not positions_df.empty:
            instrument_id = get_instrument_id(tl_symbol)
            
            # Check same-direction position already open
            if ENABLE_ONE_PER_SYMBOL_GATE:
                for _, pos in positions_df.iterrows():
                    if int(pos.get("tradableInstrumentId", 0)) == instrument_id:
                        if str(pos.get("side", "")).lower() == action.lower():
                            result = {
                                "status": "rejected",
                                "reason": f"Same-direction {action.upper()} already open on {tl_symbol} (qty {pos.get('qty')}, entry {pos.get('avgPrice')})",
                                "gate": "one_per_symbol"
                            }
                            log_alert(data, result)
                            print(f"[GATE] {tl_symbol}: {result['reason']}", flush=True)
                            return result
            
            # Check opposing position (existing logic)
            blocker = find_opposing_position(tl_symbol, action, positions_df)
            if blocker is not None:
                result = {
                    "status": "rejected",
                    "reason": (
                        f"Opposing {str(blocker.get('side', '')).upper()} position already open on "
                        f"{tl_symbol} (qty {blocker.get('qty')}, entry {blocker.get('avgPrice')}, "
                        f"PnL ${float(blocker.get('unrealizedPl', 0.0)):.2f}) — {action.upper()} blocked"
                    ),
                    "gate": "opposing_position"
                }
                log_alert(data, result)
                print(f"[GATE] {tl_symbol}: {result['reason']}", flush=True)
                return result
        
        # Check cooldown after stop-out
        in_cooldown, secs = is_in_cooldown(tl_symbol)
        if in_cooldown:
            result = {
                "status": "rejected",
                "reason": f"Post-stop-out cooldown active for {tl_symbol}: {secs:.0f}s remaining",
                "gate": "cooldown"
            }
            log_alert(data, result)
            print(f"[GATE] {tl_symbol}: {result['reason']}", flush=True)
            return result
            
    except Exception as exc:
        print(f"[POSITION CHECK] Could not check open positions/cooldown: {exc}", flush=True)
    
    # Gate 5: XPDUSD.R slippage guard
    slippage_valid, slippage_reason = validate_slippage_guard(tl_symbol, action, live_price, suggested_sl)
    if not slippage_valid:
        result = {"status": "rejected", "reason": slippage_reason, "gate": "slippage_guard"}
        log_alert(data, result)
        print(f"[GATE] {tl_symbol}: {slippage_reason}", flush=True)
        return result
    
    # Validate entry against live price
    valid, reason = validate_entry(tl_symbol, action, live_price, suggested_sl)
    if not valid:
        result = {"status": "rejected", "reason": reason}
        log_alert(data, result)
        return result

    # Validate SL distance is reasonable even at minimum lot
    sl_valid, sl_reason = validate_sl_distance(tl_symbol, action, live_price, suggested_sl)
    if not sl_valid:
        result = {"status": "rejected", "reason": sl_reason}
        log_alert(data, result)
        return result

    # Check spread is within tolerance
    spread_valid, spread_reason = validate_spread(tl_symbol)
    if not spread_valid:
        result = {"status": "rejected", "reason": spread_reason}
        log_alert(data, result)
        return result

    # Weekend guard: ASIA spans Sat/Sun, so the session check alone would still
    # admit weekend entries on a market that is closed.
    if NO_ENTRY_ON_WEEKEND and is_weekend_et():
        result = {"status": "rejected", "reason": "Weekend: CFD markets are closed"}
        log_alert(data, result)
        return result

    # Check max concurrent positions (account-wide)
    try:
        positions_df = get_cached_positions()
        if positions_df is not None and len(positions_df) >= MAX_OPEN_TRADES:
            result = {"status": "rejected", "reason": f"Max {MAX_OPEN_TRADES} concurrent positions reached"}
            log_alert(data, result)
            return result
    except Exception as exc:
        print(f"[POSITION CHECK] Could not check max positions: {exc}", flush=True)

    # Calculate preliminary quantity for AI prompt
    prelim_qty = calculate_position_size(tv_ticker, live_price, suggested_sl)
    if prelim_qty is None:
        result = {"status": "rejected", "reason": f"Position size exceeds max_lot for {tl_symbol} at $100 risk"}
        log_alert(data, result)
        return result

    # AI Risk Check (structured JSON verdict, bounded by timeout and concurrency)
    # The payload is serialised with json.dumps so that a quote character in a
    # TradingView-supplied field (trend, alert name) cannot break out of the
    # JSON literal in the prompt.
    actual_rr = TP_CONFIG.get(tl_symbol, {}).get("tp1_dollars", 125) / TARGET_DOLLAR_RISK
    ai_payload = {
        "symbol": tv_ticker,
        "tl_symbol": tl_symbol,
        "action": action,
        "entry": live_price,
        "sl": suggested_sl,
        "sl_dist": round(abs(live_price - suggested_sl), 2),
        "qty": prelim_qty,
        "risk": TARGET_DOLLAR_RISK,
        "trend": trend_context,
        "tech": tech_summary,
        "sessions": TOP_SYMBOLS[tl_symbol]['sessions'],
        "rules": f"max {MAX_OPEN_TRADES} concurrent, ${MAX_DAILY_LOSS:.0f} daily loss, {actual_rr:.2f}x R:R",
    }
    prompt = (
        json.dumps(ai_payload) + "\n"
        "Evaluate this trade for a 5M scalping prop challenge. "
        "Check for trend/technical direction conflicts. "
        "Decision must be ALLOW or DENY."
    )

    if not _AI_GATE.acquire(timeout=AI_QUEUE_TIMEOUT_SECONDS):
        _AI_STATE["failures"] += 1
        _AI_STATE["last_error"] = "ai queue saturated"
        result = {
            "status": "error",
            "reason": "ai evaluation queue saturated",
            "fail_open": AI_FAIL_OPEN,
        }
        log_alert(data, result)
        print("[AI] queue saturated; trade dropped", flush=True)
        return result

    try:
        verdict = ai_decider.decide(prompt)
    except ai_decider.AiUnavailable as exc:
        _AI_STATE["failures"] += 1
        _AI_STATE["last_error"] = f"{ai_backend_label()}: {exc}"
        print(f"[AI] provider error: {_AI_STATE['last_error']}", flush=True)
        result = {
            "status": "error",
            "reason": f"ai provider unavailable: {exc}",
            "fail_open": AI_FAIL_OPEN,
        }
        log_alert(data, result)
        if not AI_FAIL_OPEN:
            return result
        decision, confidence = "ALLOW", 0.0
        reason = f"ai unavailable, fail-open: {exc}"
        source = "fail-open"
    else:
        decision = verdict["decision"]
        confidence = verdict["confidence"]
        reason = verdict["reason"]
        source = verdict["source"]
        _AI_STATE["decisions"] += 1
        _AI_STATE["last_latency_ms"] = verdict["latency_ms"]
        _AI_STATE["last_decision"] = decision
        _AI_STATE["last_source"] = source
        _AI_STATE["last_decision_at"] = datetime.utcnow().isoformat() + "Z"
        print(
            f"[AI] {source} decision={decision} confidence={confidence:.2f} "
            f"latency={verdict['latency_ms']}ms reason={reason}",
            flush=True,
        )
    finally:
        _AI_GATE.release()

    agent_notes = f"{decision} (confidence: {confidence:.2f}, source={source}): {reason}"
    print(f"[AI PARSED] {agent_notes}", flush=True)

    if decision == "ALLOW":
        try:
            instrument_id = get_instrument_id(tl_symbol)

            # Recalculate position size with live price and validated SL (in case price moved slightly)
            quantity = calculate_position_size(tv_ticker, live_price, suggested_sl)
            if quantity is None:
                result = {"status": "rejected", "reason": f"Position size exceeds max_lot for {tl_symbol}"}
                log_alert(data, result)
                return result

            # Calculate take profit price based on TP config
            tp_config = TP_CONFIG.get(tl_symbol, {})
            tp1_dollars = tp_config.get("tp1_dollars", 150)
            tp2_dollars = tp_config.get("tp2_dollars")
            
            # Convert TP dollars to price distance
            point_value = get_point_value(tl_symbol)
            currency = TOP_SYMBOLS[tl_symbol].get("currency", "USD")
            if currency == "EUR":
                point_value *= 1.08  # EUR/USD conversion
            
            # TP1 = $150 (1.5R on $100 risk)
            tp1_distance = tp1_dollars / (quantity * point_value)
            tp1_distance = quantize_price(tp1_distance, tick_size)
            
            # Calculate absolute TP price from LIVE entry price
            if action == "buy":
                tp1_price = live_price + tp1_distance
            else:  # sell
                tp1_price = live_price - tp1_distance
            tp1_price = quantize_price(tp1_price, tick_size)
            
            # Debug logging
            print(f"[TP DEBUG] {tl_symbol} {action}: entry={live_price}, sl={suggested_sl}, qty={quantity}, "
                  f"point_value={point_value}, tp1_distance={tp1_distance}, tp1_price={tp1_price}, tick_size={tick_size}", flush=True)
            
            # Prepare order params - use ABSOLUTE price for TP (like SL)
            order_params = {
                "instrument_id": instrument_id,
                "quantity": quantity,
                "side": action,
                "type_": "market",
                "stop_loss": suggested_sl,
                "stop_loss_type": "absolute",
                "take_profit": tp1_price,
                "take_profit_type": "absolute"
            }
            
            order_response = tl.create_order(**order_params)
            
            tp_info = f"TP1: ${tp1_dollars} (abs {tp1_price})"
            if tp2_dollars:
                tp2_distance = tp2_dollars / (quantity * point_value)
                tp2_distance = quantize_price(tp2_distance, tick_size)
                if action == "buy":
                    tp2_price = live_price + tp2_distance
                else:
                    tp2_price = live_price - tp2_distance
                tp2_price = quantize_price(tp2_price, tick_size)
                tp_info += f", TP2: ${tp2_dollars} (abs {tp2_price})"
            if tp_config.get("trail_after_tp1"):
                tp_info += ", Trail after TP1"
            
            result = {
                "status": "success",
                "tv_ticker": tv_ticker,
                "tl_symbol": tl_symbol,
                "description": TOP_SYMBOLS[tl_symbol]["description"],
                "executed_quantity": quantity,
                "risk_dollars": TARGET_DOLLAR_RISK,
                "agent_notes": agent_notes,
                "technical_summary": tech_summary,
                "broker_response": str(order_response),
                "tp_levels": tp_info
            }
            log_alert(data, result)
            return result
        except Exception as broker_error:
            result = {"status": "broker_error", "message": str(broker_error), "tl_symbol": tl_symbol}
            log_alert(data, result)
            return result
    
    result = {"status": "blocked", "agent_notes": agent_notes, "technical_summary": tech_summary, "tl_symbol": tl_symbol}
    log_alert(data, result)
    return result

@app.get("/health")
async def health():
    """
    Liveness plus AI backend state. Polled every 5 minutes by monitor_health.py.
    Must stay fast and must never call the AI provider on the request path.
    """
    backend = ai_backend_label()
    ai_info = {
        "backend": backend,
        "model": (ai_decider.CLOUD_API_MODEL if backend == "cloud" else ai_decider.AI_MODEL),
        "fail_open": AI_FAIL_OPEN,
        "max_concurrency": AI_MAX_CONCURRENCY,
        "decisions": _AI_STATE["decisions"],
        "failures": _AI_STATE["failures"],
        "last_error": _AI_STATE["last_error"],
        "last_latency_ms": _AI_STATE["last_latency_ms"],
        "last_decision": _AI_STATE["last_decision"],
        "last_source": _AI_STATE["last_source"],
        "last_decision_at": _AI_STATE["last_decision_at"],
    }

    if backend == "cloud":
        ai_info["key_configured"] = bool(ai_decider.CLOUD_API_KEY)
        ai_info["endpoint_configured"] = bool(ai_decider.CLOUD_API_URL)
    else:
        try:
            import httpx

            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"{ai_decider.OLLAMA_BASE_URL}/api/tags")
            ai_info["reachable"] = resp.status_code == 200
            ai_info["models_loaded"] = [m["name"] for m in resp.json().get("models", [])]
        except Exception as exc:
            ai_info["reachable"] = False
            ai_info["last_error"] = f"{type(exc).__name__}: {exc}"

    return {
        "ok": True,
        "service": "trader-agent",
        "ts": datetime.utcnow().isoformat() + "Z",
        "ai": ai_info,
    }

@app.get("/status")
async def get_status():
    """Get current trading status."""
    return {
        "active_symbols": list(TOP_SYMBOLS.keys()),
        "risk_per_trade": TARGET_DOLLAR_RISK,
        "max_daily_loss": MAX_DAILY_LOSS,
        "max_open_trades": MAX_OPEN_TRADES,
        "max_hold_time_minutes": MAX_HOLD_TIME_MINUTES,
        "max_sl_overshoot_pct": MAX_SL_OVERSHOOT_PCT,
        "shift_params": {"atr_period": SHIFT_ATR_PERIOD, "multiplier": SHIFT_MULTIPLIER},
        "sessions_et": SESSIONS_ET
    }

@app.get("/trailing-status")
async def get_trailing_status():
    """Get breakeven SL status for all open positions."""
    try:
        positions_df = get_cached_positions()
        if positions_df is None or positions_df.empty:
            return {"be_sl_active": [], "positions": []}
        
        positions = []
        for _, pos in positions_df.iterrows():
            position_id = int(pos.get("id", 0))
            unrealized_pl = float(pos.get("unrealizedPl", 0.0))
            qty = float(pos.get("qty", 0.0))
            side = pos.get("side", "")
            avg_price = float(pos.get("avgPrice", 0.0))
            instrument_id = int(pos.get("tradableInstrumentId", 0))
            
            # Find symbol name
            symbol_name = "UNKNOWN"
            for sym, cfg in TOP_SYMBOLS.items():
                if tl.get_instrument_id_from_symbol_name(sym) == instrument_id:
                    symbol_name = sym
                    break
            
            positions.append({
                "position_id": position_id,
                "symbol": symbol_name,
                "side": side,
                "qty": qty,
                "avg_price": avg_price,
                "unrealized_pl": unrealized_pl,
                "be_sl_applied": position_id in _be_applied_positions,
                "be_trigger": TRAILING_SL_BE_PROFIT
            })
        
        return {
            "be_sl_active": [p["position_id"] for p in positions if p["be_sl_applied"]],
            "positions": positions
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/symbols")
async def list_symbols():
    """List configured symbols with details."""
    return {
        "symbols": {
            sym: {
                "description": cfg["description"],
                "point_value": get_point_value(sym),
                "min_lot": cfg["min_lot"],
                "max_lot": cfg.get("max_lot", 1.0),
                "sessions": cfg["sessions"],
                "session_active": is_session_active(cfg)
            }
            for sym, cfg in TOP_SYMBOLS.items()
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)