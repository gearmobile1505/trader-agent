#!/usr/bin/env python3
"""Replay-evaluate the three guards from the server-parity brief (2026-10-11).

(a) BE stop-limit evaluation: find every breakeven stop-out in broker history
    (exit stop order whose trigger price sits at the position's entry price),
    measure the slippage leak, and simulate stop-limit exits at 1x and 2x the
    symbol's max_spread against the 5m price path.
(b) US30.R NY_EARLY replay: bucket US30.R round trips by entry session (ET)
    and compute the counterfactual P&L of blocking NY_EARLY or its first
    30 minutes.
(c) Direction-aware cooldown replay: for every stop-out, find opposite-
    direction entries on the same symbol within 60-240 minutes and compute
    the counterfactual P&L of blocking the opposite direction for 3h/4h.

Runs against the live TradeLocker history. Usage (on the server):
    python3 scripts/replay_guard_eval.py [--lookback-days 60]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from tradelocker import TLAPI

ET = ZoneInfo("America/New_York")

# Frozen config mirrored from scripts/main_cfd_5m.py TOP_SYMBOLS / SESSIONS_ET.
SYMBOLS = {
    "US30.R":     {"id": 13638, "pv": 100.0, "max_spread": 2.0,  "tick": 0.01},
    "NAS100.R":   {"id": 13645, "pv": 100.0, "max_spread": 1.5,  "tick": 0.01},
    "SPX500.R":   {"id": 13644, "pv": 100.0, "max_spread": 1.0,  "tick": 0.01},
    "XAUUSD.R":   {"id": 13676, "pv": 100.0, "max_spread": 0.5,  "tick": 0.001},
    "XPDUSD.R":   {"id": 13674, "pv": 100.0, "max_spread": 5.0,  "tick": 0.001},
    "UKOIL.R":    {"id": 13119, "pv": 1000.0, "max_spread": 0.05, "tick": 0.001},
    "USOIL.R":    {"id": 13118, "pv": 1000.0, "max_spread": 0.05, "tick": 0.001},
    "LVMH":       {"id": 13255, "pv": 100.0, "max_spread": 0.50, "tick": 0.01, "eur": True},
    "SIEMENS":    {"id": 13250, "pv": 100.0, "max_spread": 0.40, "tick": 0.01, "eur": True},
    "ALPHABET-C": {"id": 13698, "pv": 100.0, "max_spread": 0.30, "tick": 0.01},
    "GE":         {"id": 13702, "pv": 100.0, "max_spread": 0.20, "tick": 0.01},
    "GBPJPY.R":   {"id": 13452, "pv": 100000.0, "max_spread": 2.0, "tick": 0.01, "jpy": True},
    "USDJPY.R":   {"id": 13440, "pv": 100000.0, "max_spread": 1.5, "tick": 0.01, "jpy": True},
}
ID_TO_SYMBOL = {cfg["id"]: sym for sym, cfg in SYMBOLS.items()}
EUR_USD_RATE = 1.08
STOP_EXIT_TYPES = ("stop", "stop_limit")
BE_TICK_TOLERANCE = 2          # ticks of slack when matching a BE stop trigger
BE_PCT_TOLERANCE = 0.0005      # 0.05% relative slack
SIM_HORIZON_HOURS = 4          # how long a left-open position is tracked
BAR_FETCH_PAUSE = 1.2          # seconds between broker bar requests


def build_client() -> TLAPI:
    load_dotenv(os.getenv("ENV_PATH", ".env"))
    return TLAPI(
        environment=os.getenv("TL_ENV", "https://demo.tradelocker.com"),
        username=os.getenv("TL_USER"),
        password=os.getenv("TL_PASS"),
        server=os.getenv("TL_SERVER"),
        log_level="error",
    )


def usd_jpy_rates(tl: TLAPI, start_ms: int, end_ms: int) -> pd.Series:
    """USDJPY 5m closes indexed by UTC millis, for JPY P&L conversion."""
    bars = tl.get_price_history(
        instrument_id=SYMBOLS["USDJPY.R"]["id"],
        resolution="5m",
        start_timestamp=start_ms,
        end_timestamp=end_ms,
    )
    if bars is None or bars.empty:
        return pd.Series(dtype=float)
    df = bars.rename(columns={"t": "time", "c": "close"})
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna().set_index("time")["close"].sort_index()


def point_value(symbol: str, usd_jpy_series: pd.Series, at_ms: int) -> float:
    cfg = SYMBOLS[symbol]
    pv = cfg["pv"]
    if cfg.get("jpy"):
        if not usd_jpy_series.empty:
            idx = usd_jpy_series.index.searchsorted(at_ms)
            idx = min(max(idx, 0), len(usd_jpy_series) - 1)
            rate = float(usd_jpy_series.iloc[idx])
            if rate > 0:
                pv = pv / rate
        else:
            pv = pv / 158.0
    if cfg.get("eur"):
        pv *= EUR_USD_RATE
    return pv


def pnl(symbol: str, side: str, entry: float, exit_: float, qty: float,
        usd_jpy_series: pd.Series, at_ms: int) -> float:
    pv = point_value(symbol, usd_jpy_series, at_ms)
    diff = exit_ - entry if side == "buy" else entry - exit_
    return diff * qty * pv


def reconstruct_round_trips(orders: pd.DataFrame) -> list[dict]:
    """One round trip per positionId: first fill = entry, first opposite stop = exit."""
    filled = orders[orders["status"].astype(str).str.lower() == "filled"].copy()
    filled["_lm"] = pd.to_numeric(filled["lastModified"], errors="coerce")
    # positionId is int64 and 18 digits long: converting it through
    # float64 would round distinct ids onto the same value (the float64
    # ulp at 2e17 is 32), merging unrelated positions into one group.
    filled["_pos"] = filled["positionId"].map(
        lambda v: str(int(v)) if pd.notna(v) else "")
    trips = []
    for pos_id, group in filled.groupby("_pos"):
        if not pos_id:
            continue
        group = group.sort_values("_lm")
        entry = None
        for _, row in group.iterrows():
            rtype = str(row.get("type", "")).lower()
            side = str(row.get("side", "")).lower()
            if entry is None:
                if rtype in ("market", "limit") and side in ("buy", "sell"):
                    entry = {
                        "position_id": pos_id,
                        "symbol": ID_TO_SYMBOL.get(int(row["tradableInstrumentId"])),
                        "side": side,
                        "entry_price": float(row["avgPrice"]),
                        "entry_ms": int(row["_lm"]),
                        "qty": float(row["qty"]),
                        "order_id": str(int(row["id"])),
                    }
                continue
            if rtype in STOP_EXIT_TYPES and side != entry["side"]:
                stop_price = float(row.get("price") or row["avgPrice"])
                exit_ms = int(row["_lm"])
                trips.append({**entry, "exit_price": float(row["avgPrice"]),
                            "stop_price": stop_price, "exit_ms": exit_ms,
                            "exit_order_id": str(int(row["id"])),
                            "exit_type": rtype})
                break
    return [t for t in trips if t["symbol"]]


def be_tolerance(symbol: str, price: float) -> float:
    cfg = SYMBOLS[symbol]
    return max(BE_TICK_TOLERANCE * cfg["tick"], price * BE_PCT_TOLERANCE)


def fetch_bars(tl: TLAPI, symbol: str, center_ms: int, before_min: int = 15,
               after_hours: int = SIM_HORIZON_HOURS) -> pd.DataFrame:
    cfg = SYMBOLS[symbol]
    start = center_ms - before_min * 60_000
    end = center_ms + int(after_hours * 3_600_000)
    bars = tl.get_price_history(
        instrument_id=cfg["id"], resolution="5m",
        start_timestamp=start, end_timestamp=end,
    )
    if bars is None or bars.empty:
        return pd.DataFrame()
    df = bars.rename(columns={"t": "time", "o": "open", "h": "high",
                              "l": "low", "c": "close"})
    for col in ("time", "open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["time", "open", "high", "low", "close"]).sort_values("time")


def spread_offset_price(symbol: str, multiple: float) -> float:
    """Limit offset in price units for ``multiple`` x the symbol's max_spread.

    JPY pairs (GBPJPY.R, USDJPY.R) configure max_spread in pips (0.01 yen),
    like the quoting convention for those pairs; every other symbol configures
    it in price units. Convert so the offset is always in price units.
    """
    cfg = SYMBOLS[symbol]
    offset = multiple * cfg["max_spread"]
    if cfg.get("jpy"):
        offset *= cfg["tick"]
    return offset


def simulate_stop_limit(symbol: str, side: str, stop_price: float, qty: float,
                        exit_ms: int, bars: pd.DataFrame, offset: float,
                        usd_jpy: pd.Series) -> dict:
    """Simulate a stop-limit at ``stop_price`` with a limit ``offset`` away.

    Outcome classes (only bars at/after the exit are considered, so pre-exit
    dips to the stop level cannot be mistaken for the trigger):
      - continuous: the trigger bar never trades at/beyond the limit. The
        limit is marketable the moment the stop triggers (market at ~the
        stop), so it fills at ~the stop -> loss 0.
      - through_band: the trigger bar trades through the limit band. The fill
        lands somewhere in [stop, limit]; bounds reported as [0, offset].
      - gap_recovery: the trigger bar opens beyond the limit (true gap), and
        price trades back to the limit within the horizon -> fills at the
        limit, loss = offset.
      - left_open: true gap with no recovery within the horizon -> the
        position is left open, marked to market at the horizon end.
    """
    cfg = SYMBOLS[symbol]
    pv = point_value(symbol, usd_jpy, exit_ms)
    if side == "buy":
        limit = stop_price - offset
        trig = bars["low"] <= stop_price
        beyond_limit = bars["low"] <= limit
        opened_beyond = bars["open"] <= limit
        recovery = bars["high"] >= limit
        mtm = lambda close: (stop_price - close) * qty * pv
    else:
        limit = stop_price + offset
        trig = bars["high"] >= stop_price
        beyond_limit = bars["high"] >= limit
        opened_beyond = bars["open"] >= limit
        recovery = bars["low"] <= limit
        mtm = lambda close: (close - stop_price) * qty * pv

    out = {"outcome": "no_trigger", "best": None, "worst": None, "fills": False}
    candidates = bars[bars["time"] >= exit_ms - 5 * 60_000]
    idx = np.where(trig.reindex(candidates.index, fill_value=False).values)[0]
    if len(idx) == 0:
        return out
    first = candidates.index[idx[0]]
    pos = list(bars.index).index(first)

    if not beyond_limit.loc[first]:
        out.update(outcome="continuous", best=0.0, worst=0.0, fills=True)
        return out
    if opened_beyond.loc[first]:
        later = bars.iloc[pos + 1:]
        rec = np.where(recovery.values)[0]
        rec = rec[rec > pos]
        if len(rec):
            out.update(outcome="gap_recovery",
                       best=offset * qty * pv, worst=offset * qty * pv, fills=True)
        else:
            out.update(outcome="left_open",
                       best=float(mtm(bars.iloc[-1]["close"])),
                       worst=float(mtm(bars.iloc[-1]["close"])), fills=False)
        return out
    out.update(outcome="through_band", best=0.0,
               worst=offset * qty * pv, fills=True)
    return out


def section_a(tl: TLAPI, trips: list[dict], usd_jpy: pd.Series) -> dict:
    print("\n" + "=" * 72)
    print("PART A — breakeven stop-out slippage + stop-limit simulation")
    print("=" * 72)

    be_events = []
    for trip in trips:
        if abs(trip["stop_price"] - trip["entry_price"]) <= be_tolerance(trip["symbol"], trip["entry_price"]):
            leak_pts = (trip["entry_price"] - trip["exit_price"]) if trip["side"] == "buy" \
                else (trip["exit_price"] - trip["entry_price"])
            pv = point_value(trip["symbol"], usd_jpy, trip["exit_ms"])
            be_events.append({**trip, "leak_pts": leak_pts,
                              "leak_usd": leak_pts * trip["qty"] * pv})

    print(f"\nBE stop-outs found (stop trigger == entry price): {len(be_events)}")
    if not be_events:
        return {"be_events": 0}

    leaks = sorted(e["leak_usd"] for e in be_events)
    worse_than_10 = [e for e in be_events if e["leak_usd"] > 10.0]
    print(f"Leak distribution (USD beyond the breakeven stop):")
    print(f"  count={len(leaks)} median=${np.median(leaks):.2f} "
          f"worst=${max(leaks):.2f} total=${sum(leaks):.2f}")
    print(f"  scratches flipped worse than -$10: {len(worse_than_10)}")
    for e in sorted(be_events, key=lambda x: -x["leak_usd"]):
        ts = datetime.fromtimestamp(e["exit_ms"] / 1000, ET)
        print(f"    {ts:%Y-%m-%d %H:%M} ET {e['symbol']:10s} {e['side'].upper():4s} "
              f"stop={e['stop_price']:.5g} fill={e['exit_price']:.5g} "
              f"leak={e['leak_pts']:.5g} pts = ${e['leak_usd']:.2f}")

    print(f"\nStop-limit simulation (limit offset = 1x and 2x max_spread, "
          f"{SIM_HORIZON_HOURS}h horizon):")
    results = {k: {"n": 0, "fills": 0, "left_open": 0, "actual_loss": 0.0,
                   "best": 0.0, "worst": 0.0, "by_outcome": {}}
               for k in ("1x", "2x")}
    for e in sorted(be_events, key=lambda x: x["exit_ms"]):
        bars = fetch_bars(tl, e["symbol"], e["exit_ms"])
        time.sleep(BAR_FETCH_PAUSE)
        if bars.empty:
            print(f"  {e['symbol']}: no bars around event, skipped")
            continue
        ts = datetime.fromtimestamp(e["exit_ms"] / 1000, ET)
        actual_loss = e["leak_usd"]
        line = f"  {ts:%m-%d %H:%M} ET {e['symbol']:10s} actual=${actual_loss:7.2f}"
        for multiple, key in ((1.0, "1x"), (2.0, "2x")):
            offset = spread_offset_price(e["symbol"], multiple)
            sim = simulate_stop_limit(e["symbol"], e["side"], e["stop_price"],
                                      e["qty"], e["exit_ms"], bars, offset, usd_jpy)
            res = results[key]
            res["n"] += 1
            res["actual_loss"] += actual_loss
            if sim["best"] is None:
                line += f" | {key}: n/a"
                continue
            res["best"] += sim["best"]
            res["worst"] += sim["worst"]
            res["by_outcome"][sim["outcome"]] = res["by_outcome"].get(sim["outcome"], 0) + 1
            if sim["fills"]:
                res["fills"] += 1
            else:
                res["left_open"] += 1
            line += (f" | {key}: {sim['outcome']} "
                     f"[${sim['best']:.2f}..${sim['worst']:.2f}]")
        print(line)

    print(f"\nStop-limit summary:")
    for key, res in results.items():
        if res["n"] == 0:
            continue
        delta_best = res["best"] - res["actual_loss"]
        delta_worst = res["worst"] - res["actual_loss"]
        print(f"  offset {key} max_spread: n={res['n']}, fills {res['fills']} "
              f"({res['fills']/res['n']:.0%}), left-open {res['left_open']}, "
              f"outcomes={res['by_outcome']}")
        print(f"    actual stop-market total=${res['actual_loss']:.2f} -> "
              f"stop-limit total ${res['best']:.2f}..${res['worst']:.2f} "
              f"(delta {delta_best:+.2f}..{delta_worst:+.2f})")
    return {"be_events": len(be_events), "results": results}


def session_bucket(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, ET)
    hour = dt.hour + dt.minute / 60
    if 6 <= hour < 9:
        return "NY_EARLY"
    if 9 <= hour < 19:
        return "NY"
    if 3 <= hour < 11:
        return "EU"
    return "ASIA"


def bucket_stats(trips: list[dict], usd_jpy: pd.Series) -> dict:
    stats = {}
    for trip in trips:
        bucket = session_bucket(trip["entry_ms"])
        p = pnl(trip["symbol"], trip["side"], trip["entry_price"], trip["exit_price"],
                trip["qty"], usd_jpy, trip["exit_ms"])
        stats.setdefault(bucket, {"n": 0, "net": 0.0, "wins": 0.0, "losses": 0.0,
                                "trips": []})
        stats[bucket]["n"] += 1
        stats[bucket]["net"] += p
        stats[bucket]["trips"].append((trip, p))
        if p >= 0:
            stats[bucket]["wins"] += p
        else:
            stats[bucket]["losses"] += abs(p)
    return stats


def pf(stats: dict) -> float:
    return stats["wins"] / stats["losses"] if stats["losses"] > 0 else float("inf")


def section_b(trips: list[dict], usd_jpy: pd.Series) -> dict:
    print("\n" + "=" * 72)
    print("PART B — US30.R pre-market (NY_EARLY) replay")
    print("=" * 72)
    us30 = [t for t in trips if t["symbol"] == "US30.R"]
    print(f"\nUS30.R round trips: {len(us30)}")
    if not us30:
        return {}

    stats = bucket_stats(us30, usd_jpy)
    for bucket in ("NY_EARLY", "NY", "EU", "ASIA"):
        if bucket in stats:
            s = stats[bucket]
            print(f"  {bucket:9s}: n={s['n']:2d} net=${s['net']:8.2f} "
                  f"PF={pf(s):.2f}")

    early = stats.get("NY_EARLY", {"n": 0, "net": 0.0, "trips": []})
    print(f"\nCounterfactual — block all NY_EARLY entries:")
    print(f"  blocked {early['n']} trades, net P&L of blocked = ${early['net']:+.2f}")
    if early["n"]:
        kept = [p for _, p in early["trips"]]
        losers = sum(p for p in kept if p < 0)
        winners = sum(p for p in kept if p >= 0)
        print(f"    blocked losers ${losers:.2f} / blocked winners ${winners:.2f}")

    first30 = []
    rest = []
    for trip in us30:
        dt = datetime.fromtimestamp(trip["entry_ms"] / 1000, ET)
        hour = dt.hour + dt.minute / 60
        (first30 if 6 <= hour < 6.5 else rest).append(trip)
    f30_pnl = sum(pnl(t["symbol"], t["side"], t["entry_price"], t["exit_price"],
                      t["qty"], usd_jpy, t["exit_ms"]) for t in first30)
    print(f"\nCounterfactual — block first 30 min of NY_EARLY (06:00-06:30 ET):")
    print(f"  blocked {len(first30)} trades, net P&L of blocked = ${f30_pnl:+.2f}")
    for t in first30:
        dt = datetime.fromtimestamp(t["entry_ms"] / 1000, ET)
        p = pnl(t["symbol"], t["side"], t["entry_price"], t["exit_price"],
                t["qty"], usd_jpy, t["exit_ms"])
        print(f"    {dt:%Y-%m-%d %H:%M} ET {t['side'].upper():4s} "
              f"entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} P&L=${p:+.2f}")
    return {"us30_trips": len(us30)}


def section_c(trips: list[dict], usd_jpy: pd.Series) -> dict:
    print("\n" + "=" * 72)
    print("PART C — direction-aware whipsaw cooldown replay")
    print("=" * 72)

    stop_outs = [t for t in trips if "exit_type" in t]
    print(f"\nStop-outs in window: {len(stop_outs)}")
    entries = sorted(trips, key=lambda t: t["entry_ms"])

    for hours in (3, 4):
        window_ms = hours * 3_600_000
        blocked = []
        for so in stop_outs:
            for ent in entries:
                if (ent["symbol"] != so["symbol"]
                        or ent["side"] == so["side"]
                        or not (so["exit_ms"] < ent["entry_ms"] <= so["exit_ms"] + window_ms)):
                    continue
                p = pnl(ent["symbol"], ent["side"], ent["entry_price"], ent["exit_price"],
                        ent["qty"], usd_jpy, ent["exit_ms"])
                blocked.append({**ent, "after_stopout_ms": ent["entry_ms"] - so["exit_ms"],
                                "pnl": p})
                break  # one blocking stop-out per entry is enough to count it
        net = sum(b["pnl"] for b in blocked)
        losers = [b for b in blocked if b["pnl"] < 0]
        print(f"\nBlock opposite-direction entries for {hours}h after a stop-out:")
        print(f"  blocked {len(blocked)} entries, net P&L of blocked = ${net:+.2f}")
        print(f"  losers: {len(losers)} totalling ${sum(b['pnl'] for b in losers):+.2f}, "
              f"winners: {len(blocked)-len(losers)} totalling "
              f"${sum(b['pnl'] for b in blocked if b['pnl'] >= 0):+.2f}")
        for b in sorted(blocked, key=lambda x: x["entry_ms"]):
            ts = datetime.fromtimestamp(b["entry_ms"] / 1000, ET)
            print(f"    {ts:%m-%d %H:%M} ET {b['symbol']:10s} {b['side'].upper():4s} "
                  f"({b['after_stopout_ms']/60000:.0f} min after stop-out) "
                  f"P&L=${b['pnl']:+.2f}")

    # Context: same-direction entries within the existing 60-minute cooldown.
    same_dir = []
    for so in stop_outs:
        for ent in entries:
            if (ent["symbol"] == so["symbol"]
                    and ent["side"] == so["side"]
                    and so["exit_ms"] < ent["entry_ms"] <= so["exit_ms"] + 3_600_000):
                p = pnl(ent["symbol"], ent["side"], ent["entry_price"], ent["exit_price"],
                        ent["qty"], usd_jpy, ent["exit_ms"])
                same_dir.append(p)
                break
    print(f"\nContext — same-direction entries within the existing 60-min cooldown: "
          f"{len(same_dir)} blocked, net P&L of blocked = ${sum(same_dir):+.2f}")
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=60)
    args = parser.parse_args()

    tl = build_client()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - args.lookback_days * 86_400_000

    orders = tl.get_all_orders(
        lookback_period=f"{args.lookback_days}D", history=True)
    print(f"Orders fetched: {len(orders)} rows, "
          f"{args.lookback_days}D lookback from {datetime.fromtimestamp(start_ms/1000, ET):%Y-%m-%d}")

    usd_jpy = usd_jpy_rates(tl, start_ms, now_ms)
    print(f"USDJPY 5m bars for JPY conversion: {len(usd_jpy)}")

    trips = reconstruct_round_trips(orders)
    print(f"Round trips reconstructed: {len(trips)}")
    by_symbol = {}
    for t in trips:
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0) + 1
    print("  per symbol:", dict(sorted(by_symbol.items())))

    section_a(tl, trips, usd_jpy)
    section_b(trips, usd_jpy)
    section_c(trips, usd_jpy)


if __name__ == "__main__":
    main()
