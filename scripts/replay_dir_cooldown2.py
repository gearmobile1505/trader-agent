#!/usr/bin/env python3
"""Replay-evaluate direction-aware whipsaw cooldown (Part C).

Uses order history with careful FIFO matching per positionId to reconstruct
round trips. Then checks: after a stop-out, how many opposite-direction
entries occur within 3-4h, and what's their P&L.
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
    """Build round trips from order history with careful FIFO per position."""
    orders = tl.get_all_orders(lookback_period=f"{lookback_days}D", history=True)
    if orders is None or orders.empty:
        return []

    filled = orders[orders["status"].astype(str).str.lower() == "filled"].copy()
    filled["_lm"] = pd.to_numeric(filled["lastModified"], errors="coerce")
    filled["_pos"] = filled["positionId"].astype(str)
    filled["_inst"] = pd.to_numeric(filled["tradableInstrumentId"], errors="coerce")
    filled["_price"] = pd.to_numeric(filled["avgPrice"], errors="coerce")
    filled["_qty"] = pd.to_numeric(filled["qty"], errors="coerce")
    filled["_side"] = filled["side"].astype(str).str.lower()
    filled["_type"] = filled["type"].astype(str).str.lower()
    filled["_stop_price"] = pd.to_numeric(filled.get("price", 0), errors="coerce")
    filled = filled.dropna(subset=["_lm", "_pos", "_inst", "_price", "_qty", "_side", "_type"])
    filled = filled[filled["_pos"] != "0"]
    filled["symbol"] = filled["_inst"].map(ID_TO_SYMBOL)
    filled = filled[filled["symbol"].notna()]
    filled = filled.sort_values(["_pos", "_lm"])

    trips = []
    for pos_id, group in filled.groupby("_pos"):
        symbol = group["symbol"].iloc[0]
        if symbol not in SYMBOLS:
            continue
        # FIFO matching within this position
        open_lots = []  # (side, qty, price)
        for _, row in group.iterrows():
            side = row["_side"]
            qty = row["_qty"]
            price = row["_price"]
            rtype = row["_type"]
            if side not in ("buy", "sell") or qty <= 1e-9:
                continue

            # Match against open opposite lots
            while qty > 1e-9 and open_lots:
                opp_side, opp_qty, opp_price = open_lots[0]
                if opp_side == side:
                    break
                m = min(qty, opp_qty)
                is_stop = rtype in STOP_EXIT_TYPES and side != opp_side
                stop_price = row["_stop_price"] if is_stop else None
                trips.append({
                    "symbol": symbol,
                    "side": opp_side,
                    "entry_price": opp_price,
                    "exit_price": price,
                    "qty": m,
                    "entry_ms": int(row["_lm"]),  # approx
                    "exit_ms": int(row["_lm"]),
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
    print(f"Round trips from orders (FIFO per position): {len(trips)}")
    by_symbol = {}
    for t in trips:
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0) + 1
    print("  per symbol:", dict(sorted(by_symbol.items())))

    total = sum(pnl(t["symbol"], t["side"], t["entry_price"], t["exit_price"],
                    t["qty"], usd_jpy, t["exit_ms"]) for t in trips)
    print(f"Total P&L: ${total:.2f}")

    stop_outs = [t for t in trips if t["is_stop_out"]]
    entries = sorted(trips, key=lambda t: t["entry_ms"])

    print(f"\nStop-outs: {len(stop_outs)}")
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
        print(f"\nBlock opposite-direction for {hours}h after stop-out:")
        print(f"  blocked {len(blocked)}, net=${net:+.2f}, "
              f"losers={len(losers)} (${sum(b['pnl'] for b in losers):+.2f})")
        for b in sorted(blocked, key=lambda x: x["entry_ms"]):
            ts = datetime.fromtimestamp(b["entry_ms"] / 1000, ET)
            print(f"    {ts:%m-%d %H:%M} ET {b['symbol']:10s} {b['side'].upper():4s} "
                  f"({b['after_stopout_ms']/60000:.0f} min) P&L=${b['pnl']:+.2f}")

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
    print(f"\nSame-direction within 60-min cooldown: {len(same_dir)} blocked, net=${sum(same_dir):+.2f}")


if __name__ == "__main__":
    main()