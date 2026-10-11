#!/usr/bin/env python3
"""Replay-evaluate direction-aware whipsaw cooldown (Part C).

Uses execution history (definitive) + order history (for stop prices) to
reconstruct exact round trips. Then checks: after a stop-out, how many
opposite-direction entries occur within 3-4h, and what's their P&L.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from tradelocker import TLAPI

ET = ZoneInfo("America/New_York")

SYMBOLS = {
    "US30.R":     {"id": 13638, "pv": 100.0,  "jpy": False, "eur": False, "tick": 0.01},
    "NAS100.R":   {"id": 13645, "pv": 100.0,  "jpy": False, "eur": False, "tick": 0.01},
    "SPX500.R":   {"id": 13644, "pv": 100.0,  "jpy": False, "eur": False, "tick": 0.01},
    "XAUUSD.R":   {"id": 13676, "pv": 100.0,  "jpy": False, "eur": False, "tick": 0.001},
    "XPDUSD.R":   {"id": 13674, "pv": 100.0,  "jpy": False, "eur": False, "tick": 0.001},
    "UKOIL.R":    {"id": 13119, "pv": 1000.0, "jpy": False, "eur": False, "tick": 0.001},
    "USOIL.R":    {"id": 13118, "pv": 1000.0, "jpy": False, "eur": False, "tick": 0.001},
    "LVMH":       {"id": 13255, "pv": 100.0,  "jpy": False, "eur": True,  "tick": 0.01},
    "SIEMENS":    {"id": 13250, "pv": 100.0,  "jpy": False, "eur": True,  "tick": 0.01},
    "ALPHABET-C": {"id": 13698, "pv": 100.0,  "jpy": False, "eur": False, "tick": 0.01},
    "GE":         {"id": 13702, "pv": 100.0,  "jpy": False, "eur": False, "tick": 0.01},
    "GBPJPY.R":   {"id": 13452, "pv": 100000.0, "jpy": True,  "eur": False, "tick": 0.01},
    "USDJPY.R":   {"id": 13440, "pv": 100000.0, "jpy": True,  "eur": False, "tick": 0.01},
}
ID_TO_SYMBOL = {cfg["id"]: sym for sym, cfg in SYMBOLS.items()}
EUR_USD_RATE = 1.08
STOP_EXIT_TYPES = ("stop", "stop_limit")
BAR_FETCH_PAUSE = 1.2


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


def load_round_trips(tl: TLAPI, lookback_days: int, usd_jpy: pd.Series) -> list[dict]:
    """Build round trips from execution history (definitive P&L)."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - lookback_days * 86_400_000

    # Get executions - these are the actual fills with position linkage
    execs = tl.get_all_executions()
    if execs is None or execs.empty:
        return []
    execs["_time"] = pd.to_datetime(pd.to_numeric(execs["createdDate"], errors="coerce"), unit="ms", utc=True)
    execs["_qty"] = pd.to_numeric(execs["qty"], errors="coerce")
    execs["_price"] = pd.to_numeric(execs["price"], errors="coerce")
    execs["_pos"] = execs["positionId"].astype(str)
    execs["_inst"] = pd.to_numeric(execs["tradableInstrumentId"], errors="coerce")
    execs = execs.dropna(subset=["_time", "_qty", "_price", "_inst"])
    execs["symbol"] = execs["_inst"].map(ID_TO_SYMBOL)
    execs = execs[execs["symbol"].notna()]
    execs = execs.sort_values(["_pos", "_time"])

    # Get stop prices from order history
    orders = tl.get_all_orders(lookback_period=f"{lookback_days}D", history=True)
    stop_orders = orders[
        orders["status"].astype(str).str.lower() == "filled"
    ].copy()
    stop_orders = stop_orders[
        stop_orders["type"].astype(str).str.lower().isin(STOP_EXIT_TYPES)
    ].copy()
    stop_orders["_pos"] = stop_orders["positionId"].astype(str)
    stop_orders["_stop_price"] = pd.to_numeric(stop_orders["price"], errors="coerce")
    stop_orders["_lm"] = pd.to_numeric(stop_orders["lastModified"], errors="coerce")
    stop_lookup = stop_orders.set_index("_pos")["_stop_price"].to_dict()

    trips = []
    for pos_id, group in execs.groupby("_pos"):
        if len(group) < 2:
            continue
        # Simple FIFO matching within the position
        open_lots = []  # list of (side, qty, price)
        for _, row in group.iterrows():
            side = str(row["side"]).lower()
            qty = row["_qty"]
            price = row["_price"]
            symbol = row["symbol"]
            inst = int(row["_inst"])
            cfg = SYMBOLS[symbol]
            if side not in ("buy", "sell"):
                continue
            # Match against open opposite lots
            while qty > 1e-9 and open_lots:
                opp_side, opp_qty, opp_price = open_lots[0]
                if opp_side == side:
                    break
                m = min(qty, opp_qty)
                # P&L from this match
                p = pnl(symbol, opp_side, opp_price, price, m, usd_jpy, int(row["_time"].timestamp() * 1000))
                # Determine if this is a stop-out
                is_stop = False
                stop_price = stop_lookup.get(pos_id)
                if stop_price is not None:
                    is_stop = abs(price - stop_price) / max(abs(stop_price), 1) < 0.01
                trips.append({
                    "symbol": symbol,
                    "side": opp_side,
                    "entry_price": opp_price,
                    "exit_price": price,
                    "qty": m,
                    "entry_ms": int(row["_time"].timestamp() * 1000),  # approximate
                    "exit_ms": int(row["_time"].timestamp() * 1000),
                    "is_stop_out": is_stop,
                    "stop_price": stop_price,
                    "position_id": pos_id,
                })
                qty -= m
                opp_qty -= m
                if opp_qty <= 1e-9:
                    open_lots.pop(0)
                else:
                    open_lots[0] = (opp_side, opp_qty, opp_price)
            if qty > 1e-9:
                open_lots.append((side, qty, price))
    return trips


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


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=60)
    args = parser.parse_args()

    tl = build_client()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - args.lookback_days * 86_400_000

    usd_jpy = usd_jpy_rates(tl, start_ms, now_ms)
    print(f"USDJPY 5m bars: {len(usd_jpy)}")

    trips = load_round_trips(tl, args.lookback_days, usd_jpy)
    print(f"Round trips from executions: {len(trips)}")
    by_symbol = {}
    for t in trips:
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0) + 1
    print("  per symbol:", dict(sorted(by_symbol.items())))

    # Verify P&L matches known totals
    total = sum(pnl(t["symbol"], t["side"], t["entry_price"], t["exit_price"],
                    t["qty"], usd_jpy, t["exit_ms"]) for t in trips)
    print(f"Total P&L from executions: ${total:.2f}")

    stop_outs = [t for t in trips if t["is_stop_out"]]
    entries = sorted(trips, key=lambda t: t["entry_ms"])

    print(f"\nStop-outs in window: {len(stop_outs)}")
    print(f"Total entries: {len(entries)}")

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
                blocked.append({**ent, "after_stopout_ms": ent["entry_ms"] - so["exit_ms"], "pnl": p})
                break
        net = sum(b["pnl"] for b in blocked)
        losers = [b for b in blocked if b["pnl"] < 0]
        print(f"\nBlock opposite-direction entries for {hours}h after a stop-out:")
        print(f"  blocked {len(blocked)} entries, net P&L of blocked = ${net:+.2f}")
        print(f"  losers: {len(losers)} totalling ${sum(b['pnl'] for b in losers):+.2f}, "
              f"winners: {len(blocked)-len(losers)} totalling ${sum(b['pnl'] for b in blocked if b['pnl'] >= 0):+.2f}")
        for b in sorted(blocked, key=lambda x: x["entry_ms"]):
            ts = datetime.fromtimestamp(b["entry_ms"] / 1000, ET)
            print(f"    {ts:%m-%d %H:%M} ET {b['symbol']:10s} {b['side'].upper():4s} "
                  f"({b['after_stopout_ms']/60000:.0f} min after stop-out) P&L=${b['pnl']:+.2f}")

    # Same-direction context
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
    print(f"\nContext — same-direction entries within existing 60-min cooldown: "
          f"{len(same_dir)} blocked, net P&L = ${sum(same_dir):+.2f}")


if __name__ == "__main__":
    main()