#!/usr/bin/env python3
"""
US30.R 125-point stop cap replay (PR #12, work item 1).

Re-simulates US30.R alerts vetoed by validate_sl_distance since 2026-10-02
with the stop capped at 125 points (TP $100, BE +$75, profit lock +$80 -> +$50,
NY session only). Reports vetoed count, simulated net P&L, win rate,
expectancy/trade, and winners vs losers.

Reuses scripts/loss_study.py (_build_client, normalize_bars) for broker price
history, fetched once as a continuous US30.R window then sliced per signal to
minimize API calls (and avoid Cloudflare 1015).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.loss_study import _build_client, normalize_bars  # noqa: E402

CUTOFF = datetime(2026, 10, 2, tzinfo=timezone.utc)
ALERT_LOG = "/opt/trader-agent/scripts/alerts_log.jsonl"

# US30.R: min lot 0.01, point value 100 -> $1/pt at min lot
SL_CAP_POINTS = 125.0      # capped stop distance
TP_POINTS = 100.0          # $100 take profit
BE_TRIGGER_POINTS = 75.0   # breakeven trigger (+$75)
LOCK_TRIGGER_POINTS = 80.0 # profit-lock trigger (+$80)
LOCK_TARGET_POINTS = 50.0  # lock to +$50
FETCH_HOURS_AFTER_ENTRY = 6  # price-history window per signal


def load_vetoed_us30_alerts(log_path: str) -> list[dict]:
    """US30.R alerts since CUTOFF vetoed by validate_sl_distance."""
    vetoed = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            alert = d.get("alert", {})
            result = d.get("result", {})
            if alert.get("ticker") != "US30":
                continue
            if result.get("status") != "rejected":
                continue
            reason = result.get("reason", "")
            if not reason.startswith("SL distance"):
                continue
            ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
            if ts < CUTOFF:
                continue
            vetoed.append({
                "timestamp": ts,
                "ts_utc": ts.astimezone(timezone.utc),
                "entry": float(alert["indicator_value"]),
                "action": alert["action"],
                "reason": reason,
            })
    return vetoed


def simulate_capped_stop(bars: pd.DataFrame, entry_time: datetime, entry_price: float,
                         action: str) -> dict:
    """Bar-by-bar simulation of the 125-pt capped stop with BE + profit lock.

    Returns dict with outcome, pnl_dollars, exit_time, exit_reason.
    """
    sign = 1.0 if action == "buy" else -1.0
    # Price levels (buy: SL below entry; sell: SL above entry)
    sl_level = entry_price - sign * SL_CAP_POINTS
    tp_level = entry_price + sign * TP_POINTS
    be_trigger = entry_price + sign * BE_TRIGGER_POINTS
    lock_trigger = entry_price + sign * LOCK_TRIGGER_POINTS
    lock_level = entry_price + sign * LOCK_TARGET_POINTS

    state = "open"          # open | be | lock
    exit_time = None
    exit_reason = None
    pnl = 0.0

    normalized = normalize_bars(bars)
    if normalized.empty:
        return {"outcome": "unresolved", "pnl_dollars": 0.0, "exit_time": None,
                "exit_reason": "no price data", "state": state}

    normalized["bar_close_time"] = normalized["time"] + pd.to_timedelta(
        5, unit="m"
    )
    entry_ts = pd.Timestamp(entry_time)
    # Only bars starting at/after entry
    normalized = normalized[normalized["time"] >= entry_ts]
    if normalized.empty:
        return {"outcome": "unresolved", "pnl_dollars": 0.0, "exit_time": None,
                "exit_reason": "no bars after entry", "state": state}

    for _, row in normalized.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        bct = row["bar_close_time"]

        if state == "open":
            if action == "buy":
                sl_hit = low <= sl_level
                tp_hit = high >= tp_level
                be_reached = high >= be_trigger
            else:
                sl_hit = high >= sl_level
                tp_hit = low <= tp_level
                be_reached = low <= be_trigger
            # Conservative: check stop before target within the same bar
            if sl_hit:
                pnl = -SL_CAP_POINTS  # -$125 at min lot ($1/pt)
                exit_time, exit_reason = bct, "sl"
                state = "closed"
            elif tp_hit:
                pnl = TP_POINTS  # +$100
                exit_time, exit_reason = bct, "tp"
                state = "closed"
            elif be_reached:
                state = "be"
        elif state == "be":
            # SL moved to entry (0 risk)
            if action == "buy":
                scratch = low <= entry_price
                tp_hit = high >= tp_level
                lock_reached = high >= lock_trigger
            else:
                scratch = high >= entry_price
                tp_hit = low <= tp_level
                lock_reached = low <= lock_trigger
            if scratch:
                pnl = 0.0
                exit_time, exit_reason = bct, "breakeven_scratch"
                state = "closed"
            elif tp_hit:
                pnl = TP_POINTS
                exit_time, exit_reason = bct, "tp"
                state = "closed"
            elif lock_reached:
                state = "lock"
        elif state == "lock":
            # SL locked at +50 pts
            if action == "buy":
                tp_hit = high >= tp_level
                lock_hit = low <= lock_level
            else:
                tp_hit = low <= tp_level
                lock_hit = high >= lock_level
            if tp_hit:
                pnl = TP_POINTS
                exit_time, exit_reason = bct, "tp"
                state = "closed"
            elif lock_hit:
                pnl = LOCK_TARGET_POINTS  # +$50
                exit_time, exit_reason = bct, "profit_lock"
                state = "closed"

    if state != "closed":
        return {"outcome": "unresolved", "pnl_dollars": 0.0, "exit_time": None,
                "exit_reason": f"no exit in {FETCH_HOURS_AFTER_ENTRY}h", "state": state}

    win = pnl > 0
    loss = pnl < 0
    scratch = pnl == 0.0
    return {
        "outcome": "win" if win else ("loss" if loss else "scratch"),
        "pnl_dollars": pnl,
        "exit_time": exit_time,
        "exit_reason": exit_reason,
        "state": state,
    }


def main() -> None:
    vetoed = load_vetoed_us30_alerts(ALERT_LOG)
    print(f"US30.R sl_distance vetoes since {CUTOFF.date()}: {len(vetoed)}")
    if not vetoed:
        print("Nothing to simulate.")
        return

    tl = _build_client()
    # Instrument id for US30.R
    instruments = tl.get_all_instruments()
    instrument_ids = dict(zip(instruments["name"].astype(str),
                              instruments["tradableInstrumentId"].astype(int)))
    us30_id = instrument_ids.get("US30.R")
    if us30_id is None:
        raise SystemExit("US30.R not found in instruments")

    # One continuous window covering all entries + fetch window (1 API call)
    start_ts = min(v["timestamp"] for v in vetoed)
    end_ts = max(v["timestamp"] for v in vetoed) + pd.Timedelta(hours=FETCH_HOURS_AFTER_ENTRY)
    print(f"Fetching US30.R price history {start_ts} -> {end_ts} (1 window)...", flush=True)

    history = tl.get_price_history(
        instrument_id=int(us30_id),
        resolution="5m",
        start_timestamp=int(start_ts.timestamp() * 1000),
        end_timestamp=int(end_ts.timestamp() * 1000),
    )
    if history is None or history.empty:
        raise SystemExit("No price history returned for US30.R")
    bars = normalize_bars(history)
    print(f"Fetched {len(bars)} 5m bars", flush=True)

    results = []
    for v in vetoed:
        # Slice bars from entry to entry + FETCH_HOURS_AFTER_ENTRY
        entry = v["ts_utc"]
        slice_end = entry + pd.Timedelta(hours=FETCH_HOURS_AFTER_ENTRY)
        window = bars[(bars["time"] >= entry)
                      & (bars["time"] < slice_end)]
        sim = simulate_capped_stop(window, entry, v["entry"], v["action"])
        results.append({**v, **sim})

    wins = [r for r in results if r["outcome"] == "win"]
    losses = [r for r in results if r["outcome"] == "loss"]
    scratches = [r for r in results if r["outcome"] == "scratch"]
    unresolved = [r for r in results if r["outcome"] == "unresolved"]
    net_pnl = sum(r["pnl_dollars"] for r in results)
    n = len(results)
    win_rate = len(wins) / n * 100 if n else 0.0
    expectancy = net_pnl / n if n else 0.0

    print("\n" + "=" * 70)
    print("US30.R 125-PT STOP CAP REPLAY (PR #12, vetoed signals since 10/02)")
    print("=" * 70)
    print(f"Vetoed signals simulated: {n}")
    print(f"  Winners: {len(wins)}  Losers: {len(losses)}  Scratches: {len(scratches)}  "
          f"Unresolved: {len(unresolved)}")
    print(f"Simulated net P&L: ${net_pnl:.2f}")
    print(f"Win rate: {win_rate:.1f}%")
    print(f"Expectancy/trade: ${expectancy:.2f}")
    print()
    print("Per-signal detail:")
    for r in results:
        print(f"  {r['timestamp'].strftime('%m-%d %H:%M')} entry={r['entry']:.1f} "
              f"{r['action']:4s} -> {r['outcome']:9s} ${r['pnl_dollars']:+.2f} "
              f"({r['exit_reason']})")


if __name__ == "__main__":
    main()
