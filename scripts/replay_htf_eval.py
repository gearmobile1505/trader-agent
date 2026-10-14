#!/usr/bin/env python3
"""
HTF Trend-Alignment Filter Replay Evaluation
=============================================

Replays bot trade history through two filter variants:

- Variant A (direction gate): reject entries that oppose the 4H swing-structure trend
- Variant B (retracement + continuation): Variant A + require entry near 4H EMA21

Data sources:
- alerts_log.jsonl: bot alert history (entry time, symbol, direction, qty, order_id)
- TradeLocker order history: actual entry/exit prices and P&L
- TradeLocker 4H price history: for HTF trend computation

No live trading-logic changes. Evaluation only.

Fixes vs. the previous revision (see PR comment for details):
 1. match_trade_to_position(): TradeLocker's order-level `isOpen` flag means
    "this order OPENS a position", not "this position is still open". The old
    `if any(isOpen): return None` therefore rejected every position. A position
    is now treated as closed iff it has at least one filled closing order.
 2. `tl` was referenced as an undefined name in match_trade_to_position()
    (JPY pairs) -> NameError. It is now passed explicitly.
 3. EMA_SPAN_50 was never defined -> NameError in compute_swing_trend_at().
    EMA50 is now a real EMA, computed on the full closed-bar history rather than
    on a 25-bar slice (where a 50-period average could never be populated).
 4. Look-ahead bias: the 4H bar still forming at entry time was included in the
    trend/EMA calculation. Only fully closed bars are used now.
 5. NaN-safe price extraction (NaN is truthy, so `avgPrice or price` never fell back).
 6. "Insufficient 4H bars" is now counted as an HTF data failure (it was silently
    reported as a successful fetch, understating the fail-open rate).
 7. Misc: lazy tradelocker import, direction taken from the actual entry fill,
    failed 4H fetches are no longer cached, simplified weekly breakdown.

Review corrections (Zon, 2026-10-13): the owner's two manual trades below were
SELLS, not buys, and the entry timestamps are the real ones from TradeLocker
history (platform times EET = UTC+3 in October) - not placeholders.
"""

import os
import json
import time
import re
import argparse
from pathlib import Path
from collections import defaultdict, Counter

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOP_SYMBOLS = {
    "LVMH": {"point_value": 100.0, "currency": "EUR", "max_lot": 1.0},
    "XPDUSD.R": {"point_value": 100.0, "currency": "USD", "max_lot": 0.20},
    "ALPHABET-C": {"point_value": 100.0, "currency": "USD", "max_lot": 2.0},
    "UKOIL.R": {"point_value": 1000.0, "currency": "USD", "max_lot": 0.30},
    "SIEMENS": {"point_value": 100.0, "currency": "EUR", "max_lot": 1.0},
    "GE": {"point_value": 100.0, "currency": "USD", "max_lot": 1.0},
    "US30.R": {"point_value": 100.0, "currency": "USD", "max_lot": 0.10},
    "NAS100.R": {"point_value": 100.0, "currency": "USD", "max_lot": 0.10},
    "SPX500.R": {"point_value": 100.0, "currency": "USD", "max_lot": 0.30},
    "XAUUSD.R": {"point_value": 100.0, "currency": "USD", "max_lot": 0.20},
    "GBPJPY.R": {"point_value": 100000.0, "currency": "JPY", "max_lot": 0.40},
    "USDJPY.R": {"point_value": 100000.0, "currency": "JPY", "max_lot": 0.40},
}

JPY_CONTRACT_SIZE = 100_000.0
EUR_USD_RATE = 1.08

# HTF parameters
HTF_LOOKBACK_BARS = 20
EMA_SPAN_21 = 21
EMA_SPAN_50 = 50
RETRACEMENT_TOLERANCE = 0.02  # 2% of price
HTF_BAR_MS = 4 * 60 * 60 * 1000  # one 4H bar, in ms

_last_request = 0.0


def _throttle():
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < 0.25:
        time.sleep(0.25 - elapsed)
    _last_request = time.monotonic()


def _build_client():
    # Imported lazily so the pure-logic functions can be imported/tested
    # without the broker SDK installed.
    from tradelocker import TLAPI
    from dotenv import load_dotenv

    load_dotenv()
    return TLAPI(
        environment=os.environ.get("TL_ENV", "https://demo.tradelocker.com"),
        username=os.environ.get("TL_USER"),
        password=os.environ.get("TL_PASS"),
        server=os.environ.get("TL_SERVER"),
        log_level="WARNING",
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

ORDER_ID_RE = re.compile(r"\d{10,}")


def broker_order_id(response: str) -> str | None:
    match = ORDER_ID_RE.search(str(response or ""))
    return match.group(0) if match else None


def load_bot_trades(alerts_path: Path) -> list[dict]:
    """Load successful bot trades from alerts_log.jsonl."""
    trades = []
    if not alerts_path.exists():
        return trades

    for line in alerts_path.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        result = entry.get("result", {})
        if result.get("status") != "success":
            continue

        oid = broker_order_id(result.get("broker_response", ""))
        if not oid:
            continue

        alert = entry.get("alert", {})
        ticker = result.get("tl_symbol") or alert.get("ticker", "")
        if ticker not in TOP_SYMBOLS:
            ticker_mapped = {
                "US100": "NAS100.R", "US500": "SPX500.R", "US30": "US30.R",
                "XAUUSD": "XAUUSD.R", "XPDUSD": "XPDUSD.R", "UKOIL": "UKOIL.R",
                "NAS100": "NAS100.R", "SPX500": "SPX500.R",
            }.get(ticker.replace(".R", "").replace("!", ""), ticker)
            if ticker_mapped not in TOP_SYMBOLS:
                continue
            ticker = ticker_mapped

        raw_action = alert.get("action", "")
        action = raw_action.strip().lower() if isinstance(raw_action, str) else ""
        if action not in ("buy", "sell"):
            alert_name = str(alert.get("alert_name") or "").lower()
            trend = str(alert.get("trend") or "").lower()
            if "buy" in alert_name or "bullish" in alert_name:
                action = "buy"
            elif "sell" in alert_name or "bearish" in alert_name:
                action = "sell"
            elif "buy" in trend:
                action = "buy"
            elif "sell" in trend:
                action = "sell"
        if action not in ("buy", "sell"):
            continue

        qty = float(result.get("executed_quantity", 0) or 0)
        entry_price = float(alert.get("indicator_value", 0) or 0)
        timestamp = entry.get("timestamp", "")
        try:
            entry_time = pd.to_datetime(timestamp, utc=True)
        except Exception:
            continue

        max_lot = TOP_SYMBOLS[ticker].get("max_lot", 1.0)
        if qty > max_lot:
            continue  # skip likely manual trades

        trades.append({
            "order_id": oid,
            "symbol": ticker,
            "direction": action,
            "qty": qty,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "tech_summary_5m": result.get("technical_summary", "Neutral"),
            "tp_levels": result.get("tp_levels", ""),
        })

    return trades


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    """Numeric column, or all-NaN if the column is absent."""
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def fetch_order_map(tl, lookback="90D") -> pd.DataFrame:
    """Fetch filled orders and add normalised helper columns."""
    orders = tl.get_all_orders(lookback_period=lookback, history=True)
    filled = orders[orders["status"].astype(str).str.lower() == "filled"].copy()

    filled["_order_id"] = filled["id"].astype(str)
    filled["_fill_time"] = pd.to_datetime(_num(filled, "lastModified"), unit="ms", utc=True)
    filled["_entry_time"] = pd.to_datetime(_num(filled, "createdDate"), unit="ms", utc=True)
    filled["_qty"] = _num(filled, "qty")
    filled["_price"] = _num(filled, "price")
    filled["_avg_price"] = _num(filled, "avgPrice")
    filled["_instrument_id"] = _num(filled, "tradableInstrumentId").astype("Int64")
    filled["_stop_loss"] = _num(filled, "stopLoss")
    filled["_take_profit"] = _num(filled, "takeProfit")
    # NOTE: on a TradeLocker *order*, isOpen == "this order opens a position"
    # (entry order). It is NOT a "position still open" flag. Closing orders
    # have isOpen == False.
    filled["_is_opening"] = filled["isOpen"].astype(str).str.lower().eq("true")
    return filled


# ---------------------------------------------------------------------------
# Position matching + P&L
# ---------------------------------------------------------------------------

# Why each unmatched trade failed - printed in main() to make debugging easy.
match_failures: Counter = Counter()


def _first_valid_price(*vals) -> float:
    """First finite, positive value (NaN is truthy, so `a or b` is not safe)."""
    for v in vals:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(f) and f > 0:
            return f
    return 0.0


def _finite_or_zero(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if np.isfinite(f) else 0.0


def match_trade_to_position(trade: dict, orders: pd.DataFrame,
                            instruments: pd.DataFrame | None = None,
                            tl=None) -> dict | None:
    """Find the TradeLocker position for a bot trade and determine exit + P&L."""
    order_id = str(trade["order_id"])

    hit = orders[orders["_order_id"] == order_id]
    if hit.empty:
        match_failures["order_id_not_in_history"] += 1
        return None

    position_id = hit["positionId"].iloc[0]
    if pd.isna(position_id):
        match_failures["no_position_id"] += 1
        return None

    pos_orders = orders[orders["positionId"] == position_id].sort_values("_fill_time")
    opening = pos_orders[pos_orders["_is_opening"]]
    closing = pos_orders[~pos_orders["_is_opening"]]

    # A position is closed iff a closing order has filled.
    if closing.empty:
        match_failures["position_still_open"] += 1
        return None

    entry_row = opening.iloc[0] if not opening.empty else pos_orders.iloc[0]
    exit_row = closing.iloc[-1]

    symbol = trade["symbol"]
    cfg = TOP_SYMBOLS[symbol]

    # Prefer the side actually filled at the broker over the alert's wording.
    side = str(entry_row.get("side", "")).strip().lower()
    direction_str = side if side in ("buy", "sell") else trade["direction"]
    direction = 1.0 if direction_str == "buy" else -1.0
    if direction_str != trade["direction"]:
        match_failures["direction_mismatch_alert_vs_fill"] += 1

    entry_price = _first_valid_price(entry_row["_avg_price"], entry_row["_price"])
    exit_price = _first_valid_price(exit_row["_avg_price"], exit_row["_price"])
    qty = trade["qty"]
    if entry_price <= 0 or exit_price <= 0 or qty <= 0:
        match_failures["bad_price_or_qty"] += 1
        return None

    if cfg["currency"] == "JPY":
        # Convert JPY P&L to USD using USDJPY rate at entry time
        usd_jpy_rate = _get_usd_jpy_rate(tl, entry_row["_entry_time"])
        if usd_jpy_rate <= 0:
            usd_jpy_rate = 150.0  # fallback
            match_failures["usdjpy_rate_fallback_used"] += 1
        pnl = (exit_price - entry_price) * direction * JPY_CONTRACT_SIZE * qty / usd_jpy_rate
    elif cfg["currency"] == "EUR":
        pnl = (exit_price - entry_price) * direction * cfg["point_value"] * qty * EUR_USD_RATE
    else:
        pnl = (exit_price - entry_price) * direction * cfg["point_value"] * qty

    exit_order_type = str(exit_row.get("type", "")).lower()
    exit_type = ("tp" if exit_order_type == "limit"
                 else "sl" if exit_order_type == "stop"
                 else "market")

    return {
        "trade_id": f"{trade['order_id']}",
        "position_id": str(position_id),
        "symbol": symbol,
        "direction": direction_str,
        "entry_time": entry_row["_entry_time"],
        "exit_time": exit_row["_fill_time"],
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": qty,
        "stop_loss": _finite_or_zero(entry_row.get("_stop_loss", 0)),
        "take_profit": _finite_or_zero(entry_row.get("_take_profit", 0)),
        "net_pnl": round(pnl, 2),
        "exit_type": exit_type,
        "tech_summary_5m": trade.get("tech_summary_5m", "Neutral"),
        "tp_levels": trade.get("tp_levels", ""),
    }


USDJPY_ID_GLOBAL = None
_usd_jpy_cache: dict = {}


def _get_usd_jpy_rate(tl, ts: pd.Timestamp) -> float:
    if tl is None or USDJPY_ID_GLOBAL is None:
        return 0.0
    cache_key = ts.strftime("%Y-%m-%d")
    if cache_key in _usd_jpy_cache:
        return _usd_jpy_cache[cache_key]

    end_ms = int(ts.timestamp() * 1000)
    start_ms = int((ts - pd.Timedelta(hours=24)).timestamp() * 1000)
    try:
        _throttle()
        bars = tl.get_price_history(
            instrument_id=int(USDJPY_ID_GLOBAL),
            resolution="1H",
            start_timestamp=start_ms,
            end_timestamp=end_ms,
        )
        if bars is not None and not bars.empty:
            rate = float(bars["c"].iloc[-1])
            _usd_jpy_cache[cache_key] = rate
            return rate
    except Exception:
        pass
    # Not cached on failure, so a later trade on the same day can retry.
    return 0.0


# ---------------------------------------------------------------------------
# HTF trend computation
# ---------------------------------------------------------------------------

_htf_cache: dict = {}


def fetch_htf_4h(tl, instrument_id: int, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch 4H bars for a symbol (cached per symbol on success)."""
    cache_key = (symbol, "4H")
    if cache_key in _htf_cache:
        return _htf_cache[cache_key]

    _throttle()
    try:
        bars = tl.get_price_history(
            instrument_id=int(instrument_id),
            resolution="4H",
            start_timestamp=start_ms,
            end_timestamp=end_ms,
        )
    except Exception:
        bars = None

    if bars is None or bars.empty:
        return pd.DataFrame(columns=["time", "o", "h", "l", "c", "v"])  # don't cache failures

    df = bars.rename(columns={"t": "time"}).copy()
    for col in ("time", "o", "h", "l", "c"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["time", "h", "l", "c"])
    # Normalise bar-open timestamps to milliseconds (seconds -> ms if needed).
    if not df.empty and df["time"].median() < 1e11:
        df["time"] = df["time"] * 1000
    df = df.sort_values("time").reset_index(drop=True)
    _htf_cache[cache_key] = df
    return df


def _unavailable(message: str) -> dict:
    return {"trend": "no_clear_trend", "fetched": False, "message": message,
            "ema21": None, "ema50": None, "last_close": None}


def compute_swing_trend_at(bars_4h: pd.DataFrame, entry_time: pd.Timestamp,
                           lookback_bars: int = HTF_LOOKBACK_BARS) -> dict:
    """Compute swing-structure trend from 4H bars at entry_time.

    Only fully CLOSED 4H bars are used (bar_open + 4h <= entry_time). Including
    the still-forming bar would leak post-entry highs/lows/closes into the signal.
    """
    if bars_4h is None or bars_4h.empty or "time" not in bars_4h.columns:
        return _unavailable("4H history unavailable")

    entry_ms = entry_time.timestamp() * 1000
    closed = bars_4h[bars_4h["time"] + HTF_BAR_MS <= entry_ms]

    needed = max(lookback_bars, EMA_SPAN_21)
    if len(closed) < needed:
        return _unavailable(f"insufficient closed 4H bars ({len(closed)} < {needed})")

    closes_all = closed["c"].astype(float)
    ema21 = float(closes_all.ewm(span=EMA_SPAN_21, adjust=False).mean().iloc[-1])
    ema50 = (float(closes_all.ewm(span=EMA_SPAN_50, adjust=False).mean().iloc[-1])
             if len(closed) >= EMA_SPAN_50 else None)
    last_close = float(closes_all.iloc[-1])

    # Swing structure: compare first half vs second half of the lookback window
    window = closed.iloc[-lookback_bars:]
    highs = window["h"].to_numpy(dtype=float)
    lows = window["l"].to_numpy(dtype=float)
    mid = len(window) // 2

    higher_highs = highs[mid:].max() > highs[:mid].max()
    higher_lows = lows[mid:].min() > lows[:mid].min()
    lower_highs = highs[mid:].max() < highs[:mid].max()
    lower_lows = lows[mid:].min() < lows[:mid].min()

    if higher_highs and higher_lows:
        trend = "uptrend"
    elif lower_highs and lower_lows:
        trend = "downtrend"
    else:
        trend = "no_clear_trend"

    return {
        "trend": trend, "fetched": True,
        "message": (f"bars={len(window)}, hh={higher_highs}, hl={higher_lows}, "
                    f"lh={lower_highs}, ll={lower_lows}"),
        "ema21": ema21, "ema50": ema50, "last_close": last_close,
    }


# ---------------------------------------------------------------------------
# Filter variants
# ---------------------------------------------------------------------------

def variant_a_direction_gate(trade: dict, htf: dict) -> tuple[bool, str]:
    """Variant A: reject if trade direction opposes 4H trend.
    No clear trend => allow (do not filter range days)."""
    trend = htf.get("trend", "no_clear_trend")
    action = trade["direction"]

    if not htf.get("fetched", False):
        return True, f"fail-open (HTF fetch: {htf.get('message', '')})"
    if trend == "no_clear_trend":
        return True, "no clear 4H trend — allowed (Variant A)"
    if action == "buy" and trend == "downtrend":
        return False, "Variant A: BUY rejected — 4H downtrend"
    if action == "sell" and trend == "uptrend":
        return False, "Variant A: SELL rejected — 4H uptrend"
    return True, f"Variant A: {action.upper()} aligned with 4H {trend}"


def variant_b_retracement_gate(trade: dict, htf: dict) -> tuple[bool, str]:
    """Variant B: Variant A + require entry near 4H EMA21 (pullback)."""
    allowed, reason = variant_a_direction_gate(trade, htf)
    if not allowed:
        return False, reason

    if not htf.get("fetched", False) or htf.get("ema21") is None:
        return True, f"fail-open (EMA21 unavailable: {htf.get('message', '')})"

    ema21 = htf["ema21"]
    entry_price = trade["entry_price"]
    if ema21 <= 0:
        return True, "fail-open (EMA21 <= 0)"

    pct_diff = abs(entry_price - ema21) / ema21
    if pct_diff <= RETRACEMENT_TOLERANCE:
        return True, f"Variant B: entry within {pct_diff:.2%} of 4H EMA21 ({ema21:.4f}) — pullback"
    return False, f"Variant B: entry {entry_price:.4f} too far from 4H EMA21 {ema21:.4f} ({pct_diff:.2%})"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarize(trades: list[dict], label: str) -> dict:
    total = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]
    net_pnl = sum(t["net_pnl"] for t in trades)
    win_rate = len(wins) / total * 100 if total else 0
    avg_win = np.mean([t["net_pnl"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["net_pnl"] for t in losses]) if losses else 0
    expectancy = net_pnl / total if total else 0
    return {
        "label": label, "trades": total, "wins": len(wins), "losses": len(losses),
        "net_pnl": round(net_pnl, 2), "win_rate": round(win_rate, 1),
        "avg_win": round(float(avg_win), 2), "avg_loss": round(float(avg_loss), 2),
        "expectancy": round(expectancy, 2),
    }


def print_summary_table(baseline, a, b):
    print("\n" + "=" * 120)
    print("HTF TREND-ALIGNMENT FILTER REPLAY RESULTS")
    print("=" * 120)
    rows = []
    for name, s in [("Baseline (all bot trades)", baseline),
                    ("Variant A (direction gate)", a),
                    ("Variant B (retracement)", b)]:
        rows.append({
            "Variant": name, "Trades": s["trades"], "Wins": s["wins"], "Losses": s["losses"],
            "Net P&L": f"${s['net_pnl']:.2f}", "Win Rate": f"{s['win_rate']:.1f}%",
            "Expectancy": f"${s['expectancy']:.2f}",
        })
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== DELTAS vs Baseline ===")
    for name, s in [("Variant A", a), ("Variant B", b)]:
        print(f"{name}: trades={s['trades'] - baseline['trades']:+d} | "
              f"net_pnl={s['net_pnl'] - baseline['net_pnl']:+.2f} | "
              f"win_rate={s['win_rate'] - baseline['win_rate']:+.1f}pp | "
              f"expectancy={s['expectancy'] - baseline['expectancy']:+.2f}")


def print_per_symbol(baseline_trades, a_taken, b_taken):
    print("\n=== PER-SYMBOL BREAKDOWN ===")
    symbols = sorted(set(t["symbol"] for t in baseline_trades))
    rows = []
    for sym in symbols:
        for label, trades in [("Baseline", baseline_trades), ("A", a_taken), ("B", b_taken)]:
            syms = [t for t in trades if t["symbol"] == sym]
            pnl = sum(t["net_pnl"] for t in syms)
            wr = len([t for t in syms if t["net_pnl"] > 0]) / len(syms) * 100 if syms else 0
            rows.append({"Symbol": sym, "Variant": label, "Trades": len(syms),
                         "Net P&L": f"${pnl:.2f}", "Win Rate": f"{wr:.1f}%"})
    print(pd.DataFrame(rows).to_string(index=False))


# Owner's manual trades — facts verified from TradeLocker history (2026-10-12):
# both were SELLS using the 4H price-action BOS-retracement strategy, entered
# 10/6 and held overnight into 10/7. Platform times are EET = UTC+3 in October.
MANUAL_TRADES = [
    {"symbol": "EURUSD.R", "direction": "sell", "pnl": 493.00,
     "entry_time": "2026-10-06 15:25:00Z"},  # entered 10/6 18:25 EET
    {"symbol": "EURJPY.R", "direction": "sell", "pnl": 369.24,
     "entry_time": "2026-10-06 12:12:00Z"},  # entered 10/6 15:12 EET
]


def check_manual_trades_survival(tl, baseline_trades, htf_results):
    """Check whether owner's manual EURUSD.R and EURJPY.R trades would survive filters."""
    print("\n=== MANUAL TRADE SURVIVAL CHECK ===")
    print("Owner's manual trades (4H BOS-retracement strategy):")
    for m in MANUAL_TRADES:
        print(f"  {m['symbol']}: +${m['pnl']:.2f} ({m['direction']})")
    print("  (These were NOT bot trades — excluded from baseline.)")

    manual_period_start = pd.Timestamp("2026-10-05 12:00:00Z")
    manual_period_end = pd.Timestamp("2026-10-09 12:00:00Z")
    manual_candidates = [
        t for t in baseline_trades
        if t["symbol"] in ("EURUSD.R", "EURJPY.R")
        and manual_period_start <= t["entry_time"] <= manual_period_end
    ]
    if manual_candidates:
        print(f"\n  Found {len(manual_candidates)} EUR bot trades in Oct 5-9 period:")
        for t in manual_candidates:
            htf = htf_results.get(t["trade_id"], {})
            a_ok, _ = variant_a_direction_gate(t, htf)
            b_ok, _ = variant_b_retracement_gate(t, htf)
            print(f"    {t['entry_time'].strftime('%Y-%m-%d %H:%M')} {t['symbol']} {t['direction']} "
                  f"P&L=${t['net_pnl']:.2f} 4H_trend={htf.get('trend', '?')} "
                  f"VarA={'PASS' if a_ok else 'BLOCK'} VarB={'PASS' if b_ok else 'BLOCK'}")

    for m in MANUAL_TRADES:
        sym = m["symbol"]
        try:
            iid = tl.get_instrument_id_from_symbol_name(sym)
            if not iid:
                print(f"\n  {sym}: instrument id not found")
                continue
            ts = pd.Timestamp(m["entry_time"])
            start_ts = int((ts - pd.Timedelta(hours=4 * 80)).timestamp() * 1000)
            end_ts = int(ts.timestamp() * 1000)
            bars = fetch_htf_4h(tl, iid, sym, start_ts, end_ts)
            htf = compute_swing_trend_at(bars, ts)

            # Manual trades were sells: build a pseudo-trade to run the real gates.
            probe = {"direction": m["direction"], "entry_price": htf.get("last_close") or 0.0}
            a_ok, a_reason = variant_a_direction_gate(probe, htf)
            b_ok, b_reason = variant_b_retracement_gate(probe, htf)
            print(f"\n  {sym} (manual {m['direction']}, +${m['pnl']:.2f}) at {ts}:")
            print(f"    4H trend: {htf.get('trend', '?')}  EMA21: {htf.get('ema21')}  "
                  f"last closed 4H close: {htf.get('last_close')}")
            print(f"    Variant A: {'PASS' if a_ok else 'BLOCK'} — {a_reason}")
            print(f"    Variant B (using last 4H close as entry proxy): "
                  f"{'PASS' if b_ok else 'BLOCK'} — {b_reason}")
        except Exception as e:
            print(f"\n  {sym}: error checking — {e}")


def check_tp_transition(trades):
    """Note the TP change from $150 to $100 on Oct 5 (PR #8)."""
    print("\n=== TP TRANSITION NOTE (Sep 30 -> Oct 5) ===")
    cutoff = pd.Timestamp("2026-10-05 00:00:00Z")
    pre = [t for t in trades if t["entry_time"] < cutoff]
    post = [t for t in trades if t["entry_time"] >= cutoff]
    print(f"  Pre-Oct-5 (TP=$150): {len(pre)} trades")
    print(f"  Post-Oct-5 (TP=$100): {len(post)} trades")
    print("  (TP=$100 is the current frozen risk param. Filter analysis covers both periods.)")


def print_weekly_breakdown(trades):
    """Show weekly P&L by entry date (weeks start Monday, New York time)."""
    print("\n=== WEEKLY P&L BY ENTRY DATE ===")
    if not trades:
        print("  No trades")
        return

    weekly = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
    for t in trades:
        ny = t["entry_time"].tz_convert("America/New_York")
        week_start = (ny - pd.Timedelta(days=ny.weekday())).normalize()
        key = week_start.strftime("%Y-%m-%d")
        weekly[key]["trades"] += 1
        weekly[key]["pnl"] += t["net_pnl"]

    for week, data in sorted(weekly.items()):
        print(f"  Week of {week}: {data['trades']} trades, net=${data['pnl']:.2f}")

    ref_start = pd.Timestamp("2026-10-05 00:00:00Z")
    ref_end = pd.Timestamp("2026-10-10 00:00:00Z")
    ref_trades = [t for t in trades if ref_start <= t["entry_time"] < ref_end]
    ref_pnl = sum(t["net_pnl"] for t in ref_trades)
    print(f"\n  Reference week (10/5->10/9): {len(ref_trades)} trades, net=${ref_pnl:.2f}")
    print("  (Brief cites 38-trade bot baseline for this week — compare against alert success count.)")


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------

_instrument_id_cache = {}


def _instrument_id(tl, symbol: str) -> int:
    if symbol in _instrument_id_cache:
        return _instrument_id_cache[symbol]
    iid = tl.get_instrument_id_from_symbol_name(symbol)
    _instrument_id_cache[symbol] = iid
    return iid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global USDJPY_ID_GLOBAL

    parser = argparse.ArgumentParser(description="HTF trend-alignment filter replay evaluation")
    parser.add_argument("--alerts-log", default="/opt/trader-agent/scripts/alerts_log.jsonl",
                        help="Path to alerts_log.jsonl")
    parser.add_argument("--lookback", default="90D", help="TradeLocker order lookback period")
    parser.add_argument("--out-dir", default="/tmp", help="Where to write CSV/JSON output")
    args = parser.parse_args()

    print("=" * 80)
    print("HTF TREND-ALIGNMENT FILTER — REPLAY EVALUATION")
    print("Evaluation only — no live trading logic changes")
    print("=" * 80)

    tl = _build_client()
    USDJPY_ID_GLOBAL = tl.get_instrument_id_from_symbol_name("USDJPY.R")
    print(f"USDJPY instrument id: {USDJPY_ID_GLOBAL}")

    # Step 1: Load bot trades from alerts log
    print("\n[1/5] Loading bot trades from alerts_log.jsonl...")
    bot_trades = load_bot_trades(Path(args.alerts_log))
    print(f"  Loaded {len(bot_trades)} successful bot trades")
    check_tp_transition(bot_trades)

    # Step 2: Fetch orders and match to positions
    print("\n[2/5] Fetching TradeLocker order history and matching to trades...")
    instruments = tl.get_all_instruments()
    orders = fetch_order_map(tl, lookback=args.lookback)
    print(f"  Filled orders: {len(orders)}")

    completed_trades = []
    unmatched = 0
    for trade in bot_trades:
        matched = match_trade_to_position(trade, orders, instruments, tl=tl)
        if matched:
            completed_trades.append(matched)
        else:
            unmatched += 1
    print(f"  Matched: {len(completed_trades)}, Unmatched: {unmatched}")
    if match_failures:
        print("  Match diagnostics:")
        for reason, n in match_failures.most_common():
            print(f"    {reason}: {n}")

    if not completed_trades:
        print("ERROR: No trades matched. Check data.")
        return

    date_range_start = min(t["entry_time"] for t in completed_trades)
    date_range_end = max(t["entry_time"] for t in completed_trades)
    print(f"  Date range: {date_range_start} to {date_range_end}")
    date_range_days = (date_range_end - date_range_start).days + 1
    print_weekly_breakdown(completed_trades)

    # Step 3: Fetch 4H history per symbol
    print("\n[3/5] Fetching 4H candle history per symbol (batch)...")
    start_ms = int((date_range_start - pd.Timedelta(hours=4 * 60)).timestamp() * 1000)
    end_ms = int(date_range_end.timestamp() * 1000)
    print(f"  Window: {pd.to_datetime(start_ms, unit='ms', utc=True)} to "
          f"{pd.to_datetime(end_ms, unit='ms', utc=True)}")

    htf_data = {}
    for sym in sorted(set(t["symbol"] for t in completed_trades)):
        iid = _instrument_id(tl, sym)
        bars = fetch_htf_4h(tl, int(iid), sym, start_ms, end_ms)
        htf_data[sym] = bars
        print(f"  {sym}: {len(bars)} 4H bars")

    # Step 4: Compute HTF trend at each entry
    print("\n[4/5] Computing 4H swing-structure trend at each entry...")
    htf_results = {}
    fetch_failures = 0
    for trade in completed_trades:
        bars = htf_data.get(trade["symbol"], pd.DataFrame())
        htf = compute_swing_trend_at(bars, trade["entry_time"])
        htf_results[trade["trade_id"]] = htf
        if not htf.get("fetched", False):
            fetch_failures += 1
    fail_pct = fetch_failures / len(completed_trades) * 100
    print(f"  HTF data failures (fetch or insufficient bars): "
          f"{fetch_failures} / {len(completed_trades)} ({fail_pct:.1f}%)")

    # Step 5: Apply filters and report
    print("\n[5/5] Applying filter variants and generating reports...")
    a_taken, a_blocked = [], []
    b_taken, b_blocked = [], []
    htf_trend_dist = defaultdict(int)
    direction_alignment = defaultdict(int)

    for trade in completed_trades:
        htf = htf_results.get(trade["trade_id"], {})
        trend = htf.get("trend", "no_clear_trend")
        htf_trend_dist[trend if htf.get("fetched") else "fetch_failed"] += 1

        if htf.get("fetched") and trend == "uptrend":
            direction_alignment["aligned" if trade["direction"] == "buy" else "opposed"] += 1
        elif htf.get("fetched") and trend == "downtrend":
            direction_alignment["aligned" if trade["direction"] == "sell" else "opposed"] += 1
        else:
            direction_alignment["no_trend"] += 1

        a_ok, a_reason = variant_a_direction_gate(trade, htf)
        (a_taken if a_ok else a_blocked).append(trade if a_ok else (trade, a_reason))

        b_ok, b_reason = variant_b_retracement_gate(trade, htf)
        (b_taken if b_ok else b_blocked).append(trade if b_ok else (trade, b_reason))

    baseline_summary = summarize(completed_trades, "Baseline")
    a_summary = summarize(a_taken, "Variant A")
    b_summary = summarize(b_taken, "Variant B")

    print_summary_table(baseline_summary, a_summary, b_summary)
    print_per_symbol(completed_trades, a_taken, b_taken)

    n = len(completed_trades)
    print("\n=== HTF TREND DISTRIBUTION AT ENTRY ===")
    for k, v in sorted(htf_trend_dist.items()):
        print(f"  {k}: {v} ({v / n * 100:.1f}%)")

    print("\n=== DIRECTION ALIGNMENT (bot trades vs 4H trend) ===")
    for k, v in sorted(direction_alignment.items()):
        print(f"  {k}: {v} ({v / n * 100:.1f}%)")

    # Blocks are never fail-open by construction (fail-open => allowed).
    print("\n=== BLOCKING SUMMARY ===")
    print(f"Variant A blocks: {len(a_blocked)} of {n} trades")
    for sym in sorted(set(t["symbol"] for t, _ in a_blocked)):
        sym_blocks = [t for t, _ in a_blocked if t["symbol"] == sym]
        print(f"  {sym}: {len(sym_blocks)} blocked, P&L=${sum(t['net_pnl'] for t in sym_blocks):.2f}")

    a_blocked_ids = {t["trade_id"] for t, _ in a_blocked}
    b_additional = [(t, r) for t, r in b_blocked if t["trade_id"] not in a_blocked_ids]
    print(f"Variant B additional blocks (beyond A): {len(b_additional)}")

    print("\n=== TRADE FREQUENCY ===")
    per_week = lambda k: k / date_range_days * 7
    print(f"  Baseline: {n} trades over {date_range_days} days = {per_week(n):.1f}/week")
    print(f"  Variant A: {len(a_taken)} trades = {per_week(len(a_taken)):.1f}/week")
    print(f"  Variant B: {len(b_taken)} trades = {per_week(len(b_taken)):.1f}/week")

    print("\n=== HTF DATA AVAILABILITY ===")
    print(f"  Data failure rate: {fail_pct:.1f}%")
    print("  Choice: FAIL-OPEN (when 4H candles can't be fetched, the trade is allowed)")
    print("  Rationale: A fail-closed HTF filter would silently block live alerts when the")
    print("  broker history API hiccups. For evaluation, we fail-open to measure the")
    print("  filter's signal power, not its data-availability penalty.")

    check_manual_trades_survival(tl, completed_trades, htf_results)

    # Save results
    all_results = []
    for trade in completed_trades:
        htf = htf_results.get(trade["trade_id"], {})
        a_ok, a_reason = variant_a_direction_gate(trade, htf)
        b_ok, b_reason = variant_b_retracement_gate(trade, htf)
        all_results.append({
            "trade_id": trade["trade_id"],
            "position_id": trade["position_id"],
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "entry_time": trade["entry_time"].isoformat(),
            "entry_price": trade["entry_price"],
            "exit_price": trade["exit_price"],
            "qty": trade["qty"],
            "net_pnl": trade["net_pnl"],
            "exit_type": trade["exit_type"],
            "tech_summary_5m": trade.get("tech_summary_5m", ""),
            "tp_levels": trade.get("tp_levels", ""),
            "htf_trend": htf.get("trend"),
            "htf_fetched": htf.get("fetched"),
            "htf_ema21": round(htf["ema21"], 4) if htf.get("ema21") else None,
            "htf_ema50": round(htf["ema50"], 4) if htf.get("ema50") else None,
            "variant_a_allowed": a_ok,
            "variant_a_reason": a_reason,
            "variant_b_allowed": b_ok,
            "variant_b_reason": b_reason,
        })

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "htf_replay_results.csv"
    pd.DataFrame(all_results).to_csv(csv_path, index=False)
    print(f"\nDetailed results saved to {csv_path}")

    summary = {
        "baseline": baseline_summary,
        "variant_a": a_summary,
        "variant_b": b_summary,
        "htf_trend_distribution": dict(htf_trend_dist),
        "direction_alignment": dict(direction_alignment),
        "blocks_a": len(a_blocked),
        "blocks_b": len(b_blocked),
        "date_range": {
            "start": date_range_start.isoformat(),
            "end": date_range_end.isoformat(),
            "days": date_range_days,
        },
        "trade_frequency": {
            "baseline_per_week": round(per_week(n), 1),
            "variant_a_per_week": round(per_week(len(a_taken)), 1),
            "variant_b_per_week": round(per_week(len(b_taken)), 1),
        },
        "htf_data_failure_rate_pct": round(fail_pct, 1),
        "match_diagnostics": dict(match_failures),
    }
    json_path = out_dir / "htf_replay_summary.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Summary saved to {json_path}")

    print("\n=== VARIANT A BLOCKED TRADES ===")
    for trade, reason in sorted(a_blocked, key=lambda x: x[0]["entry_time"]):
        print(f"  {trade['entry_time'].strftime('%Y-%m-%d %H:%M')} {trade['symbol']} "
              f"{trade['direction']} P&L=${trade['net_pnl']:.2f} -> BLOCKED: {reason}")

    print("\n=== VARIANT B ADDITIONAL BLOCKS (beyond A) ===")
    for trade, reason in sorted(b_additional, key=lambda x: x[0]["entry_time"]):
        print(f"  {trade['entry_time'].strftime('%Y-%m-%d %H:%M')} {trade['symbol']} "
              f"{trade['direction']} P&L=${trade['net_pnl']:.2f} -> BLOCKED: {reason}")

    print("\n" + "=" * 80)
    print("REPLAY COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
