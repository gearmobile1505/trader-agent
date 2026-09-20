#!/usr/bin/env python3
"""Weekly realized P&L and pair/timeframe analysis from TradeLocker executions."""

import argparse
import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta

import pandas as pd
import pytz
from dotenv import load_dotenv
from tradelocker import TLAPI

load_dotenv()
logging.disable(logging.CRITICAL)

ET = pytz.timezone("US/Eastern")
TARGET_TIMEFRAME = "5m"
EUR_USD_FALLBACK = 1.08


def build_client():
    return TLAPI(
        environment=os.getenv("TL_ENV", "https://demo.tradelocker.com"),
        username=os.getenv("TL_USER"),
        password=os.getenv("TL_PASS"),
        server=os.getenv("TL_SERVER"),
    )


def get_contracts(tl, symbols):
    instruments = tl.get_all_instruments()
    contracts = {}
    for symbol in symbols:
        rows = instruments[instruments["name"].astype(str).eq(symbol)]
        if rows.empty:
            continue
        instrument_id = int(rows.iloc[0]["tradableInstrumentId"])
        contracts[symbol] = tl.get_instrument_details(instrument_id)
    return contracts


def usd_value_per_price_unit(details, tl):
    lot_size = float(details.get("lotSize") or 1.0)
    quote_currency = details.get("quotingCurrency")
    if quote_currency == "USD":
        return lot_size
    if quote_currency == "JPY":
        usd_jpy_id = tl.get_instrument_id_from_symbol_name("USDJPY.R")
        usd_jpy = tl.get_latest_bid_price(usd_jpy_id)
        return lot_size / usd_jpy
    if quote_currency == "EUR":
        return lot_size * EUR_USD_FALLBACK
    return lot_size


def match_realized_trades(executions, symbol_by_instrument, contracts, tl, start, end):
    executions = executions.copy()
    executions["time"] = pd.to_datetime(executions["createdDate"], unit="ms", utc=True)
    executions = executions.sort_values(["positionId", "time"])
    results = []
    for position_id, group in executions.groupby("positionId"):
        symbol = symbol_by_instrument.get(int(group["tradableInstrumentId"].iloc[0]))
        if not symbol or symbol not in contracts:
            continue
        value_per_unit = usd_value_per_price_unit(contracts[symbol], tl)
        open_lots = {"buy": deque(), "sell": deque()}
        for row in group.itertuples(index=False):
            side = str(row.side).lower()
            qty_remaining = float(row.qty)
            price = float(row.price)
            opposite = "sell" if side == "buy" else "buy"
            while qty_remaining > 1e-9 and open_lots[opposite]:
                entry_qty, entry_price, entry_time = open_lots[opposite][0]
                matched_qty = min(qty_remaining, entry_qty)
                if opposite == "buy":
                    pnl = (price - entry_price) * matched_qty * value_per_unit
                    direction = "long"
                else:
                    pnl = (entry_price - price) * matched_qty * value_per_unit
                    direction = "short"
                close_time = row.time
                if start <= close_time.astimezone(ET) <= end:
                    results.append({
                        "position_id": position_id,
                        "symbol": symbol,
                        "direction": direction,
                        "qty": matched_qty,
                        "entry_price": entry_price,
                        "exit_price": price,
                        "pnl": pnl,
                        "entry_time": entry_time.astimezone(ET),
                        "exit_time": close_time.astimezone(ET),
                        "timeframe": TARGET_TIMEFRAME,
                    })
                qty_remaining -= matched_qty
                entry_qty -= matched_qty
                if entry_qty <= 1e-9:
                    open_lots[opposite].popleft()
                else:
                    open_lots[opposite][0] = (entry_qty, entry_price, entry_time)
            if qty_remaining > 1e-9:
                open_lots[side].append((qty_remaining, price, row.time))
    return pd.DataFrame(results)


def summarize(df, group_columns):
    if df.empty:
        return pd.DataFrame()
    summary = df.groupby(group_columns, dropna=False).agg(
        trades=("pnl", "size"),
        wins=("pnl", lambda values: int((values > 0).sum())),
        losses=("pnl", lambda values: int((values < 0).sum())),
        net_pnl=("pnl", "sum"),
        avg_pnl=("pnl", "mean"),
        gross_profit=("pnl", lambda values: values[values > 0].sum()),
        gross_loss=("pnl", lambda values: values[values < 0].sum()),
    ).reset_index()
    summary["win_rate_pct"] = summary["wins"] / summary["trades"] * 100
    summary["profit_factor"] = summary["gross_profit"] / summary["gross_loss"].abs().replace(0, pd.NA)
    return summary.sort_values("net_pnl", ascending=False)


def primary_session(timestamp):
    """Assign one non-overlapping ET session to a trade entry."""
    hour = timestamp.hour
    if hour >= 20 or hour < 3:
        return "ASIA"
    if hour < 6:
        return "EU"
    if hour < 9:
        return "NY_EARLY"
    if hour < 19:
        return "NY"
    return "CLOSED"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-09-15")
    parser.add_argument("--end", default="2026-09-18")
    args = parser.parse_args()
    start = ET.localize(datetime.strptime(args.start, "%Y-%m-%d"))
    end = ET.localize(datetime.strptime(args.end, "%Y-%m-%d") + timedelta(days=1)) - timedelta(microseconds=1)

    tl = build_client()
    executions = tl.get_all_executions()
    instruments = tl.get_all_instruments()
    symbol_by_instrument = dict(zip(instruments["tradableInstrumentId"].astype(int), instruments["name"]))
    symbols = sorted(set(symbol_by_instrument.values()))
    contracts = get_contracts(tl, symbols)
    trades = match_realized_trades(executions, symbol_by_instrument, contracts, tl, start, end)
    if trades.empty:
        print("No realized trades found for the requested period.")
        return

    pair_summary = summarize(trades, ["symbol"])
    timeframe_summary = summarize(trades, ["timeframe"])
    daily_summary = summarize(trades.assign(date=trades["exit_time"].dt.strftime("%Y-%m-%d")), ["date"])
    trades["session"] = trades["entry_time"].map(primary_session)
    session_summary = summarize(trades, ["symbol", "session"])

    print(f"WEEKLY TRADE ANALYSIS - {args.start} through {args.end} ET")
    print(f"Realized trades: {len(trades)} | Net P&L: ${trades['pnl'].sum():.2f}")
    print("\nPAIR PERFORMANCE")
    print(pair_summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nTIMEFRAME PERFORMANCE")
    print(timeframe_summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nDAILY PERFORMANCE")
    print(daily_summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nPAIR AND SESSION PERFORMANCE")
    print(session_summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nBEST PAIR:", pair_summary.iloc[0]["symbol"])
    print("WORST PAIR:", pair_summary.iloc[-1]["symbol"])
    print("TIMEFRAME LIMITATION: live alerts in this bot are 5m; no other live timeframe is present for this week.")

    trades.to_csv("weekly_trade_details_20260915_20260918.csv", index=False)
    pair_summary.to_csv("weekly_pair_summary_20260915_20260918.csv", index=False)
    daily_summary.to_csv("weekly_daily_summary_20260915_20260918.csv", index=False)
    session_summary.to_csv("weekly_pair_session_summary_20260915_20260918.csv", index=False)
    with open("weekly_analysis_20260915_20260918.json", "w", encoding="utf-8") as report_file:
        json.dump({"start": args.start, "end": args.end, "timeframe": TARGET_TIMEFRAME}, report_file, indent=2)
    print("\nSaved weekly_trade_details_20260915_20260918.csv")
    print("Saved weekly_pair_summary_20260915_20260918.csv")
    print("Saved weekly_daily_summary_20260915_20260918.csv")
    print("Saved weekly_pair_session_summary_20260915_20260918.csv")


if __name__ == "__main__":
    main()
