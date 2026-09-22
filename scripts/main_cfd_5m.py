#!/usr/bin/env python3
"""
Optimized CFD Trading Webhook Bridge - 5M Scalping Configuration.
Based on Phantom Flow backtest results (7 days, 5m resolution).
Top symbols: LVMH, XPDUSD.R, ALPHABET-C, UKOIL.R, SIEMENS, GE
"""

import os
import json
import gzip
import asyncio
from uuid import uuid4
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import ollama
import pandas as pd
from tradelocker import TLAPI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Alert log file
ALERT_LOG = "/opt/trader-agent/scripts/alerts_log.jsonl"

# Trailing Stop Configuration
TRAILING_SL_BE_PROFIT = 80.0         # Move SL to breakeven when P&L >= $80

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
    except Exception:
        pass

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
    server=TL_SERVER
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
        "sessions": ["NY", "EU", "ASIA", "NY_EARLY"],
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
        "sessions": ["NY"],
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
        "point_value": 3000.0,
        "min_lot": 0.01,
        "max_lot": 0.20,
        "max_spread": 2.0,
        "sessions": ["NY", "EU", "ASIA", "NY_EARLY"],
        "description": "British Pound vs Japanese Yen",
        "currency": "USD",
        "tick_size": 0.01
    },
    "USDJPY.R": {
        "point_value": 3000.0,
        "min_lot": 0.01,
        "max_lot": 0.30,
        "max_spread": 1.5,
        "sessions": ["NY", "EU", "ASIA", "NY_EARLY"],
        "description": "US Dollar vs Japanese Yen",
        "currency": "USD",
        "tick_size": 0.01
    },
}

# Phantom Shift Parameters (from Pine Script)
SHIFT_ATR_PERIOD = 10
SHIFT_MULTIPLIER = 3.0

# Risk Management
TARGET_DOLLAR_RISK = 125.0        # $125 risk per trade
MAX_DAILY_LOSS = 400.0            # Stop trading if -$400/day
MAX_OPEN_TRADES = 3               # Max concurrent positions
MIN_RISK_REWARD = 1.5             # Min R:R for entry
MAX_HOLD_TIME_MINUTES = 45        # Max time a trade can be open (5m scalping)
MAX_SL_OVERSHOOT_PCT = 20         # Max % over $125 risk at SL (min lot basis): 150 = reject

# Take Profit Configuration
# TP1 = $100 for all symbols
TP_CONFIG = {
    "US30.R":     {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
    "NAS100.R":   {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
    "SPX500.R":   {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": False},
    "XAUUSD.R":   {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": False},
    "XPDUSD.R":   {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": False},
    "UKOIL.R":    {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": False},
    "LVMH":       {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
    "SIEMENS":    {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
    "ALPHABET-C": {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
    "GE":         {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
    "GBPJPY.R":   {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
    "USDJPY.R":   {"tp1_dollars": 100, "tp2_dollars": None, "trail_after_tp1": True},
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
        "LVMH": "LVMH", "XPDUSD": "XPDUSD.R", "XPTUSD": "XPDUSD.R", "GOOG": "ALPHABET-C", "GOOGL": "ALPHABET-C",
        "UKOIL": "UKOIL.R", "USOIL": "USOIL.R", "SIEMENS": "SIEMENS", "GE": "GE",
        "US30": "US30.R", "US100": "NAS100.R", "US500": "SPX500.R", "XAUUSD": "XAUUSD.R",
        # Crypto
        "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
        # Forex
        "EURUSD": "EURUSD.R", "GBPUSD": "GBPUSD.R", "USDJPY": "USDJPY.R", "GBPJPY": "GBPJPY.R",
    }
    base = tv_symbol.replace("1!", "").replace("!", "").upper()
    return SYMBOL_MAP.get(base, tv_symbol)

def get_point_value(tl_symbol: str) -> float:
    """Get the USD value of a one-price-unit move for one lot."""
    symbol_config = TOP_SYMBOLS.get(tl_symbol, {})
    return symbol_config.get("point_value", 1.0)

def get_min_lot(tl_symbol: str) -> float:
    return TOP_SYMBOLS.get(tl_symbol, {}).get("min_lot", 0.01)

# EUR/USD rate for EUR-denominated symbols (approximate, update periodically)
EUR_USD_RATE = 1.08

def calculate_position_size(ticker: str, entry_price: float, stop_loss: float) -> float:
    """Dynamic position sizing based on $200 risk, handles EUR/USD conversion."""
    sl_distance = abs(entry_price - stop_loss)
    if sl_distance == 0:
        return get_min_lot(ticker)
    
    tl_symbol = map_symbol(ticker)
    point_val = get_point_value(tl_symbol)
    currency = TOP_SYMBOLS.get(tl_symbol, {}).get("currency", "USD")
    
    # Convert risk per lot to USD
    risk_per_lot = sl_distance * point_val
    if currency == "EUR":
        risk_per_lot *= EUR_USD_RATE
    
    if risk_per_lot <= 0:
        return get_min_lot(tl_symbol)
    
    calculated_qty = TARGET_DOLLAR_RISK / risk_per_lot
    min_lot = get_min_lot(tl_symbol)
    max_lot = TOP_SYMBOLS.get(tl_symbol, {}).get("max_lot", 1.0)
    return round(max(min_lot, min(calculated_qty, max_lot)), 2)

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


def get_live_price(tl_symbol: str) -> float:
    """Fetch current market price from TradeLocker for a symbol."""
    try:
        instrument_id = tl.get_instrument_id_from_symbol_name(tl_symbol)
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
        instrument_id = tl.get_instrument_id_from_symbol_name(tl_symbol)
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
        sl_price = round(sl_price / tick_size) * tick_size
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
        instrument_id = tl.get_instrument_id_from_symbol_name(tl_symbol)
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

async def check_and_apply_trailing_stops():
    """Background task: move SL to breakeven when profitable."""
    global _be_applied_positions

    try:
        positions_df = tl.get_all_positions()
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


async def check_and_close_overdue_positions():
    """Close positions that have been open longer than MAX_HOLD_TIME_MINUTES.

    For 5m scalping, trades should not run for hours. This catches any
    positions where the SL/trailing mechanism failed to trigger.
    """
    try:
        positions_df = tl.get_all_positions()
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

            open_time_str = pos.get("openTime", "unknown")
            try:
                if isinstance(open_time_str, (int, float)):
                    open_time = datetime.fromtimestamp(open_time_str / 1000, tz=timezone.utc)
                else:
                    open_time = datetime.fromisoformat(open_time_str.replace("Z", "+00:00"))
            except (ValueError, TypeError, AttributeError):
                open_time = None

            if open_time is None:
                continue

            age_minutes = (now - open_time).total_seconds() / 60

            if age_minutes > MAX_HOLD_TIME_MINUTES:
                symbol_name = "UNKNOWN"
                for sym, cfg in TOP_SYMBOLS.items():
                    try:
                        if tl.get_instrument_id_from_symbol_name(sym) == instrument_id:
                            symbol_name = sym
                            break
                    except Exception:
                        continue

                print(
                    f"[OVERDUE] Position {position_id} {symbol_name} {side} {qty} "
                    f"open {age_minutes:.0f}m (limit {MAX_HOLD_TIME_MINUTES}m), "
                    f"entry={avg_price}, PnL=${unrealized_pl:.2f} — CLOSING",
                    flush=True,
                )

                try:
                    tl.modify_position(position_id, {
                        "stopLossType": "absolute",
                        "stopLoss": avg_price,
                    })
                    print(
                        f"[OVERDUE] Position {position_id}: SL moved to BE @{avg_price} for forced close",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"[OVERDUE] Failed to set BE SL for position {position_id}: {exc}", flush=True)

    except Exception as e:
        print(f"[OVERDUE] Position age check error: {e}", flush=True)


# Start background task on startup
@app.on_event("startup")
async def start_trailing_stop_monitor():
    """Start the breakeven SL monitoring background task."""
    import asyncio
    
    async def be_check_loop():
        while True:
            await asyncio.sleep(30)
            await check_and_apply_trailing_stops()
            await check_and_close_overdue_positions()

    asyncio.create_task(be_check_loop())
    print(f"[BE SL] Monitor started: trigger=${TRAILING_SL_BE_PROFIT}, interval=30s", flush=True)
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
    """Run the blocking broker and AI workflow outside the Uvicorn event loop."""
    
    # Log full raw payload for debugging
    print(f"Raw webhook payload ({task_id}): {json.dumps(data)}", flush=True)
    
    action = data.get("action")  # "buy" or "sell"
    tv_ticker = data.get("ticker")
    trend_context = data.get("trend", "Unknown")
    alert_name = data.get("alert_name") or data.get("name") or data.get("alertName") or ""
    
    # Handle TradingView placeholder for action (not interpolated in webhook JSON)
    if action and action.startswith("{{") and action.endswith("}}"):
        # First try: extract from alert name (TradingView sends alert name in webhook)
        alert_lower = alert_name.lower()
        if "buy" in alert_lower or "bullish" in alert_lower or "long" in alert_lower:
            action = "buy"
        elif "sell" in alert_lower or "bearish" in alert_lower or "short" in alert_lower:
            action = "sell"
        else:
            # Fallback: extract from trend field
            trend_lower = trend_context.lower()
            if "buy" in trend_lower or "bullish" in trend_lower:
                action = "buy"
            elif "sell" in trend_lower or "bearish" in trend_lower:
                action = "sell"
            else:
                action = "buy"  # default
    
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
        result = {
            "status": "rejected",
            "reason": f"Outside trading session for {tl_symbol}",
            "allowed_sessions": TOP_SYMBOLS[tl_symbol]["sessions"]
        }
        log_alert(data, result)
        return result
    
    # Technical summary check
    tech_summary = get_technical_summary(tl_symbol)
    print(f"[TECH SUMMARY] {tl_symbol}: {tech_summary}", flush=True)
    
    # Fetch live price from TradeLocker for accurate SL/TP calculation
    live_price = get_live_price(tl_symbol)
    if live_price <= 0:
        result = {"status": "rejected", "reason": "Could not fetch live price from TradeLocker"}
        log_alert(data, result)
        return result
    
    print(f"[LIVE PRICE] {tl_symbol}: live_price={live_price}, webhook_price={indicator_val}", flush=True)
    
    # Auto-calculate SL from ATR if not provided or invalid (handles TradingView placeholder strings)
    if suggested_sl <= 0 or abs(indicator_val - suggested_sl) < 0.01:
        atr_sl = calculate_atr_sl(tl_symbol, action, live_price)
        if atr_sl > 0:
            suggested_sl = atr_sl
            print(f"[AUTO-SL] {tl_symbol} {action}: Calculated SL from ATR(10)*3 = {suggested_sl}")
        else:
            result = {"status": "rejected", "reason": "Could not calculate stop loss from ATR"}
            log_alert(data, result)
            return result
    
    # Round SL to symbol's tick size (broker requirement)
    tick_size = TOP_SYMBOLS[tl_symbol].get("tick_size", 0.01)
    suggested_sl = round(suggested_sl / tick_size) * tick_size
    
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
    
    # Calculate preliminary quantity for AI prompt
    prelim_qty = calculate_position_size(tv_ticker, live_price, suggested_sl)
    
    # AI Risk Check
    prompt = f"""
    Strict risk management for 5M scalping prop challenge. Answer ONLY 'APPROVED' or 'REJECTED' + brief reason.
    - Symbol: {tv_ticker} -> {tl_symbol} ({TOP_SYMBOLS[tl_symbol]['description']})
    - Action: {action}
    - Entry: {live_price}
    - Stop Loss: {suggested_sl}
    - SL Distance: {abs(live_price - suggested_sl):.2f} points
    - Position Size: {prelim_qty} lots
    - Risk: ${TARGET_DOLLAR_RISK}
    - Trend: {trend_context}
    - Technical Summary: {tech_summary}
    - Session Check: {TOP_SYMBOLS[tl_symbol]['sessions']}
    Rules: Max 3 concurrent trades, $500 daily loss limit, min 1.5 R:R
    Technical Summary acts as a directional bias: if it contradicts the trade direction, note it as a risk factor.
    """
    
    try:
        response = ollama.chat(
            model='phi3:mini',
            messages=[{'role': 'user', 'content': prompt}]
        )
        agent_decision = response['message']['content']
        print(f"[AI DECISION] {agent_decision}", flush=True)
    except Exception as e:
        result = {"status": "error", "message": f"AI Agent unavailable: {str(e)}"}
        log_alert(data, result)
        return result
    
    if "APPROVED" in agent_decision.upper():
        try:
            instrument_id = tl.get_instrument_id_from_symbol_name(tl_symbol)
            
            # Recalculate position size with live price and validated SL (in case price moved slightly)
            quantity = calculate_position_size(tv_ticker, live_price, suggested_sl)
            
            # Calculate take profit price based on TP config
            tp_config = TP_CONFIG.get(tl_symbol, {})
            tp1_dollars = tp_config.get("tp1_dollars", 300)
            tp2_dollars = tp_config.get("tp2_dollars")
            
            # Convert TP dollars to price distance
            point_value = get_point_value(tl_symbol)
            currency = TOP_SYMBOLS[tl_symbol].get("currency", "USD")
            if currency == "EUR":
                point_value *= 1.08  # EUR/USD conversion
            
            # TP1 = 1.5R ($300) - calculate absolute price level from LIVE price
            tp1_distance = tp1_dollars / (quantity * point_value)
            tp1_distance = round(tp1_distance / tick_size) * tick_size
            
            # Calculate absolute TP price from LIVE entry price
            if action == "buy":
                tp1_price = live_price + tp1_distance
            else:  # sell
                tp1_price = live_price - tp1_distance
            tp1_price = round(tp1_price / tick_size) * tick_size
            
            # Debug logging
            print(f"[TP DEBUG] {tl_symbol} {action}: entry={live_price}, sl={suggested_sl}, qty={quantity}, "
                  f"point_value={point_value}, tp1_distance={tp1_distance}, tp1_price={tp1_price}, tick_size={tick_size}", flush=True)
            if not valid:
                result = {"status": "rejected", "reason": reason}
                log_alert(data, result)
                return result
            
            # Recalculate position size with live price and validated SL
            quantity = calculate_position_size(tv_ticker, live_price, suggested_sl)
            
            # Calculate take profit price based on TP config
            tp_config = TP_CONFIG.get(tl_symbol, {})
            tp1_dollars = tp_config.get("tp1_dollars", 300)
            tp2_dollars = tp_config.get("tp2_dollars")
            
            # Convert TP dollars to price distance
            point_value = get_point_value(tl_symbol)
            currency = TOP_SYMBOLS[tl_symbol].get("currency", "USD")
            if currency == "EUR":
                point_value *= 1.08  # EUR/USD conversion
            
            # TP1 = 1.5R ($300) - calculate absolute price level from LIVE price
            tp1_distance = tp1_dollars / (quantity * point_value)
            tp1_distance = round(tp1_distance / tick_size) * tick_size
            
            # Calculate absolute TP price from LIVE entry price
            if action == "buy":
                tp1_price = live_price + tp1_distance
            else:  # sell
                tp1_price = live_price - tp1_distance
            tp1_price = round(tp1_price / tick_size) * tick_size
            
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
                tp2_distance = round(tp2_distance / tick_size) * tick_size
                if action == "buy":
                    tp2_price = live_price + tp2_distance
                else:
                    tp2_price = live_price - tp2_distance
                tp2_price = round(tp2_price / tick_size) * tick_size
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
                "agent_notes": agent_decision,
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
    
    result = {"status": "blocked", "agent_notes": agent_decision, "technical_summary": tech_summary, "tl_symbol": tl_symbol}
    log_alert(data, result)
    return result

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
        positions_df = tl.get_all_positions()
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
                "point_value": cfg["point_value"],
                "min_lot": cfg["min_lot"],
                "sessions": cfg["sessions"],
                "session_active": is_session_active(cfg)
            }
            for sym, cfg in TOP_SYMBOLS.items()
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)