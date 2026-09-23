#!/usr/bin/env python3
"""
Daily P&L Report - Fetches TradeLocker executions and calculates P&L.
Run at end of trading day (4:30 PM ET) or anytime.
"""

import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from tradelocker import TLAPI
import pandas as pd

load_dotenv()

TL_ENV = os.getenv("TL_ENV", "https://demo.tradelocker.com")
TL_USER = os.getenv("TL_USER")
TL_PASS = os.getenv("TL_PASS")
TL_SERVER = os.getenv("TL_SERVER")

tl = TLAPI(
    environment=TL_ENV,
    username=TL_USER,
    password=TL_PASS,
    server=TL_SERVER
)


def _weighted_average(price_series, qty_series):
    """Return a qty-weighted average price, handling empty inputs safely."""
    price_series = pd.to_numeric(price_series, errors="coerce")
    qty_series = pd.to_numeric(qty_series, errors="coerce").fillna(0.0)
    total_qty = qty_series.sum()
    if total_qty == 0:
        return 0.0
    return float((price_series * qty_series).sum() / total_qty)


def summarize_position_group(group: pd.DataFrame):
    """Summarize one positionId/position-group into an entry/exit P&L record.

    This intentionally matches buy and sell quantities instead of treating the
    entire group as either fully long or fully short. That avoids the common bug
    where a partial close or mixed execution list is misclassified and produces
    bogus P&L values.
    """
    group = group.sort_values("time").copy()
    if group.empty:
        return None

    group["qty"] = pd.to_numeric(group["qty"], errors="coerce").fillna(0.0)
    group["price"] = pd.to_numeric(group["price"], errors="coerce").fillna(0.0)
    group["side"] = group["side"].astype(str).str.lower()

    total_buy_qty = group.loc[group["side"] == "buy", "qty"].sum()
    total_sell_qty = group.loc[group["side"] == "sell", "qty"].sum()

    if total_buy_qty == 0 and total_sell_qty == 0:
        return {
            "direction": "unknown",
            "qty": 0.0,
            "entry": 0.0,
            "exit": None,
            "pnl": 0.0,
            "status": "OPEN",
        }

    if total_buy_qty > 0 and total_sell_qty > 0:
        matched_qty = min(total_buy_qty, total_sell_qty)
        first_side = group.sort_values("time").iloc[0]["side"]
        if first_side == "sell":
            direction = "short"
            entry_rows = group[group["side"] == "sell"]
            exit_rows = group[group["side"] == "buy"]
            entry_price = _weighted_average(entry_rows["price"], entry_rows["qty"])
            exit_price = _weighted_average(exit_rows["price"], exit_rows["qty"])
            pnl = (entry_price - exit_price) * matched_qty
        else:
            direction = "long"
            entry_rows = group[group["side"] == "buy"]
            exit_rows = group[group["side"] == "sell"]
            entry_price = _weighted_average(entry_rows["price"], entry_rows["qty"])
            exit_price = _weighted_average(exit_rows["price"], exit_rows["qty"])
            pnl = (exit_price - entry_price) * matched_qty
        status = "CLOSED"
        return {
            "direction": direction,
            "qty": matched_qty,
            "entry": entry_price,
            "exit": exit_price,
            "pnl": pnl,
            "status": status,
        }

    if total_buy_qty > 0:
        first_side = group.sort_values("time").iloc[0]["side"]
        entry_rows = group[group["side"] == "buy"]
        entry_price = _weighted_average(entry_rows["price"], entry_rows["qty"])
        return {
            "direction": "long" if first_side == "buy" else "short",
            "qty": total_buy_qty,
            "entry": entry_price,
            "exit": None,
            "pnl": 0.0,
            "status": "OPEN",
        }

    first_side = group.sort_values("time").iloc[0]["side"]
    entry_rows = group[group["side"] == "sell"]
    entry_price = _weighted_average(entry_rows["price"], entry_rows["qty"])
    return {
        "direction": "short" if first_side == "sell" else "long",
        "qty": total_sell_qty,
        "entry": entry_price,
        "exit": None,
        "pnl": 0.0,
        "status": "OPEN",
    }

def get_daily_pnl(target_date: datetime = None):
    """Get P&L for a specific date (default: today ET)."""
    
    import pytz
    et = pytz.timezone('US/Eastern')
    utc = pytz.UTC
    
    if target_date is None:
        target_date = datetime.now(et)
    elif target_date.tzinfo is None:
        target_date = et.localize(target_date)
    
    # Convert to UTC timestamps (milliseconds)
    start_et = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_et = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    start_utc = start_et.astimezone(utc)
    end_utc = end_et.astimezone(utc)
    
    start_ts = int(start_utc.timestamp() * 1000)
    end_ts = int(end_utc.timestamp() * 1000)
    
    print(f"Fetching executions for {target_date.strftime('%Y-%m-%d')} (ET)...")
    print(f"UTC Range: {start_utc} to {end_utc}")
    
    # Get all executions
    executions = tl.get_all_executions()
    
    if executions.empty:
        print("No executions found.")
        return
    
    # Convert createdDate to datetime
    executions['time'] = pd.to_datetime(executions['createdDate'], unit='ms', utc=True)
    executions['time_et'] = executions['time'].dt.tz_convert('US/Eastern')
    
    # Filter by date
    day_executions = executions[
        (executions['time_et'].dt.date == target_date.date())
    ].copy()
    
    if day_executions.empty:
        print(f"No trades executed on {target_date.strftime('%Y-%m-%d')}")
        return
    
    # Get positions to match entry/exit
    positions = tl.get_all_positions()
    
    # Calculate P&L per trade
    # Group by positionId to match entries/exits
    trade_results = []
    
    for pos_id, group in day_executions.groupby('positionId', dropna=False):
        summary = summarize_position_group(group)
        if summary is None:
            continue

        entry_price = summary['entry']
        exit_price = summary['exit']
        qty = summary['qty']
        side = summary['direction']
        pnl = summary['pnl']
        status = summary['status']
        
        symbol_id = int(group['tradableInstrumentId'].iloc[0])
        try:
            symbol_name = tl.get_symbol_name_from_instrument_id(symbol_id)
        except:
            symbol_name = f"ID:{symbol_id}"
        
        trade_results.append({
            'symbol': symbol_name,
            'side': side,
            'qty': qty,
            'entry': entry_price,
            'exit': exit_price if status == 'CLOSED' else 'OPEN',
            'pnl': pnl,
            'status': status,
            'trades': len(group),
            'first_time': group['time_et'].min().strftime('%H:%M:%S'),
            'last_time': group['time_et'].max().strftime('%H:%M:%S'),
        })
    
    if not trade_results:
        print("No trades to report.")
        return
    
    df = pd.DataFrame(trade_results)
    
    # Summary
    closed = df[df['status'] == 'CLOSED']
    open_trades = df[df['status'] == 'OPEN']
    
    total_pnl = closed['pnl'].sum() if not closed.empty else 0
    gross_wins = closed[closed['pnl'] > 0]['pnl'].sum() if not closed.empty else 0
    gross_losses = closed[closed['pnl'] < 0]['pnl'].sum() if not closed.empty else 0
    win_count = len(closed[closed['pnl'] > 0]) if not closed.empty else 0
    loss_count = len(closed[closed['pnl'] < 0]) if not closed.empty else 0
    total_trades = len(closed)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
    profit_factor = abs(gross_wins / gross_losses) if gross_losses != 0 else float('inf')
    
    # Print Report
    print("\n" + "="*80)
    print(f"DAILY P&L REPORT - {target_date.strftime('%Y-%m-%d')} (ET)")
    print("="*80)
    
    print(f"\n{'Symbol':<12} {'Side':<6} {'Qty':>8} {'Entry':>10} {'Exit':>10} {'PnL':>10} {'Status':<8} {'Time'}")
    print("-"*80)
    
    for _, row in df.iterrows():
        pnl_str = f"${row['pnl']:.2f}" if row['status'] == 'CLOSED' else "OPEN"
        exit_str = f"{row['exit']:.2f}" if row['status'] == 'CLOSED' else "OPEN"
        print(f"{row['symbol']:<12} {row['side']:<6} {row['qty']:>8.2f} {row['entry']:>10.2f} {exit_str:>10} {pnl_str:>10} {row['status']:<8} {row['first_time']}-{row['last_time']}")
    
    print("-"*80)
    print(f"\nSUMMARY:")
    print(f"  Total Closed Trades: {total_trades}")
    print(f"  Wins: {win_count} | Losses: {loss_count} | Win Rate: {win_rate:.1f}%")
    print(f"  Gross Wins: ${gross_wins:.2f}")
    print(f"  Gross Losses: ${gross_losses:.2f}")
    print(f"  Net P&L: ${total_pnl:.2f}")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print(f"  Open Positions: {len(open_trades)}")
    
    # Daily limit check
    DAILY_LOSS_LIMIT = 400
    if total_pnl <= -DAILY_LOSS_LIMIT:
        print(f"\n⚠️  DAILY LOSS LIMIT HIT: ${total_pnl:.2f} <= -${DAILY_LOSS_LIMIT}")
        print("   Stop trading for the day!")
    elif total_pnl < 0:
        remaining = DAILY_LOSS_LIMIT + total_pnl
        print(f"\n📊 Daily P&L: ${total_pnl:.2f} | Remaining buffer: ${remaining:.2f} before ${DAILY_LOSS_LIMIT} limit")
    else:
        print(f"\n✅ Daily P&L: ${total_pnl:.2f} (Profit)")
    
    # Save to CSV
    filename = f"daily_pnl_{target_date.strftime('%Y%m%d')}.csv"
    df.to_csv(filename, index=False)
    print(f"\n📁 Saved to {filename}")
    
    return df

def get_weekly_summary(days: int = 5):
    """Get P&L summary for last N trading days."""
    import pytz
    et = pytz.timezone('US/Eastern')
    today = datetime.now(et)
    
    all_results = []
    for i in range(days):
        check_date = today - timedelta(days=i)
        if check_date.weekday() >= 5:  # Skip weekends
            continue
        try:
            df = get_daily_pnl(check_date)
            if df is not None and not df.empty:
                closed = df[df['status'] == 'CLOSED']
                if not closed.empty:
                    all_results.append({
                        'date': check_date.strftime('%Y-%m-%d'),
                        'trades': len(closed),
                        'win_rate': len(closed[closed['pnl'] > 0]) / len(closed) * 100,
                        'pnl': closed['pnl'].sum()
                    })
        except:
            pass
    
    if all_results:
        print("\n" + "="*60)
        print(f"WEEKLY SUMMARY (Last {days} Trading Days)")
        print("="*60)
        print(f"{'Date':<12} {'Trades':>6} {'Win%':>6} {'PnL':>10}")
        print("-"*60)
        total_week = 0
        for r in all_results:
            print(f"{r['date']:<12} {r['trades']:>6} {r['win_rate']:>6.1f} ${r['pnl']:>9.2f}")
            total_week += r['pnl']
        print("-"*60)
        print(f"{'WEEK TOTAL':<12} {'':>6} {'':>6} ${total_week:>9.2f}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Daily P&L Report')
    parser.add_argument('--date', type=str, help='Date (YYYY-MM-DD), default today')
    parser.add_argument('--week', action='store_true', help='Show weekly summary')
    parser.add_argument('--days', type=int, default=5, help='Days for weekly summary')
    args = parser.parse_args()
    
    if args.week:
        get_weekly_summary(args.days)
    else:
        if args.date:
            target = datetime.strptime(args.date, '%Y-%m-%d')
            import pytz
            target = pytz.timezone('US/Eastern').localize(target)
        else:
            target = None
        get_daily_pnl(target)