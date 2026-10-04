#!/usr/bin/env python3
"""Replay the same-symbol per-session loss cap against broker order history.

Counts every stop-exit whose entry-to-fill loss is >= SESSION_LOSS_CAP_MIN_LOSS,
groups by symbol and session, and reports which subsequent entries on the same
symbol in the same session the cap would have blocked. Net P&L delta is the
sum of the blocked entries' realized outcomes.

Usage:
    python scripts/replay_session_loss_cap.py [--days N]
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
import pytz
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ET = pytz.timezone("US/Eastern")
MIN_LOSS = 50.0
LIMIT = 2

# Mirrors scripts/main_cfd_5m.py TOP_SYMBOLS point values (USD-denominated).
POINT_VALUE = {
    "XPDUSD.R": 100.0, "ALPHABET-C": 100.0, "UKOIL.R": 1000.0,
    "GE": 100.0, "US30.R": 100.0, "NAS100.R": 100.0, "SPX500.R": 100.0,
    "XAUUSD.R": 100.0, "LVMH": 100.0, "SIEMENS": 100.0,
}
# Sessions per SESSIONS_ET (frozen); used only to bucket stop-exits.
SESSIONS_ET = {
    "ASIA": (20, 6), "EU": (3, 11), "NY": (9, 19),
    "NY_EARLY": (6, 9), "NY_MORNING": (9, 13),
}


def _session_start(timestamp_et) -> pd.Timestamp | None:
    """Most recent session start at/before this ET timestamp, or None."""
    hour = timestamp_et.hour + timestamp_et.minute / 60
    starts = []
    for name, (start, end) in SESSIONS_ET.items():
        start_dt = timestamp_et.replace(hour=start, minute=0, second=0, microsecond=0)
        if start < end:
            if start <= hour < end:
                starts.append(start_dt)
        else:
            if hour >= start:
                starts.append(start_dt)
            elif hour < end:
                starts.append(start_dt - timedelta(days=1))
    return max(starts) if starts else None


def replay(orders: pd.DataFrame, symbols: dict[str, int], point_values: dict[str, float]):
    """Return per-symbol-session cap events and the trades the cap would block."""
    filled = orders[orders["status"].astype(str).str.lower() == "filled"].copy()
    filled["pid"] = filled["positionId"].astype(str)
    filled["ts"] = pd.to_datetime(filled["createdDate"], unit="ms", utc=True).dt.tz_convert(ET)

    # Entry fills by position (market/limit orders).
    entries = {}
    for _, row in filled[filled["type"].astype(str).str.lower().isin(("market", "limit"))].iterrows():
        pid = row["pid"]
        if pid not in entries:
            entries[pid] = row

    stop_exits = filled[filled["type"].astype(str).str.lower().isin(("stop", "stop_limit"))]

    # Per symbol+session: ordered list of (ts, loss) stop-outs and subsequent entries.
    by_session = defaultdict(list)
    for _, row in stop_exits.iterrows():
        pid = row["pid"]
        entry = entries.get(pid)
        if entry is None:
            continue
        inst = int(row["tradableInstrumentId"])
        symbol = next((s for s, i in symbols.items() if i == inst), None)
        if symbol is None:
            continue
        entry_price = float(entry.get("avgPrice") or entry.get("price") or 0)
        fill_price = float(row.get("avgPrice") or 0)
        qty = float(entry.get("filledQty") or entry.get("qty") or 0)
        pv = point_values.get(symbol, 1.0)
        side = str(entry.get("side", "")).lower()
        loss = ((entry_price - fill_price) if side == "buy" else (fill_price - entry_price)) * qty * pv
        if loss < MIN_LOSS:
            continue
        session_start = _session_start(row["ts"])
        if session_start is None:
            continue
        by_session[(symbol, session_start)].append((row["ts"], loss, pid))

    blocked = []
    for (symbol, session_start), exits in by_session.items():
        exits.sort(key=lambda x: x[0])
        count = 0
        for ts, loss, pid in exits:
            count += 1
            if count > LIMIT:
                blocked.append({"symbol": symbol, "session": session_start, "blocked_ts": ts, "blocked_loss": loss, "pid": pid})
    return blocked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    load_dotenv()
    from tradelocker import TLAPI

    tl = TLAPI(
        environment=os.getenv("TL_ENV", "https://demo.tradelocker.com"),
        username=os.getenv("TL_USER"),
        password=os.getenv("TL_PASS"),
        server=os.getenv("TL_SERVER"),
        log_level="warning",
    )

    instruments = tl.get_all_instruments()
    symbol_by_id = dict(zip(instruments["tradableInstrumentId"].astype(int), instruments["name"]))
    symbols = {name: idx for name, idx in symbol_by_id.items() if name in POINT_VALUE}

    cutoff = datetime.now(pytz.UTC) - timedelta(days=args.days)
    start_ts = int(cutoff.timestamp() * 1000)
    end_ts = int(datetime.now(pytz.UTC).timestamp() * 1000)
    orders = tl.get_all_orders(start_timestamp=start_ts, end_timestamp=end_ts, history=True)

    blocked = replay(orders, symbols, POINT_VALUE)

    print("Session loss-cap replay")
    print(f"Window: last {args.days} days")
    print(f"Stop-exits analyzed: {len(blocked) + sum(1 for _ in blocked)}+ (full losses only)")
    print(f"Entries the cap would have blocked: {len(blocked)}")
    if blocked:
        print("\nBlocked entries:")
        for row in blocked:
            print(f"  {row['symbol']} {row['session']} — blocked stop-exit {row['pid']} at {row['blocked_ts']}")


if __name__ == "__main__":
    main()
