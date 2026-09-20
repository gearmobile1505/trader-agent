#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from tradelocker import TLAPI
import warnings
warnings.filterwarnings('ignore')

load_dotenv()

TL_ENV = os.getenv("TL_ENV", "https://demo.tradelocker.com")
TL_USER = os.getenv("TL_USER")
TL_PASS = os.getenv("TL_PASS")
TL_SERVER = os.getenv("TL_SERVER")

tl = TLAPI(environment=TL_ENV, username=TL_USER, password=TL_PASS, server=TL_SERVER)

from phantom_scanner import (
    calculate_phantom_shift, calculate_phantom_oscillator,
    calculate_combo_signals, run_backtest
)

TARGET_SYMBOLS = ["GBPJPY.R", "USDJPY.R"]

def run_backtest_for_symbols(lookback_days=90, resolution="1H"):
    print(f"Phantom Flow Backtest — GBPJPY.R & USDJPY.R")
    print(f"Lookback: {lookback_days} days, Resolution: {resolution}")
    print()

    instruments = tl.get_all_instruments()
    forex_instruments = instruments[instruments['type'] == 'FOREX']
    target = forex_instruments[forex_instruments['name'].isin(TARGET_SYMBOLS)]
    print(f"Found {len(target)} target instruments:")
    for _, row in target.iterrows():
        print(f"  {row['name']} (id={row['tradableInstrumentId']}, desc={row['description']})")
    print()

    end_ts = int(datetime.now().timestamp() * 1000)
    start_ts = int((datetime.now() - timedelta(days=lookback_days)).timestamp() * 1000)

    all_results = []

    for idx, row in target.iterrows():
        symbol = row['name']
        instrument_id = int(row['tradableInstrumentId'])
        print(f"\n[{idx+1}/{len(target)}] Testing {symbol}...")

        try:
            hist = tl.get_price_history(
                instrument_id=instrument_id,
                resolution=resolution,
                start_timestamp=start_ts,
                end_timestamp=end_ts
            )

            if hist.empty or len(hist) < 50:
                print(f"  Insufficient data ({len(hist)} bars), skipping")
                continue

            df = hist.rename(columns={'t': 'time', 'o': 'o', 'h': 'h', 'l': 'l', 'c': 'c', 'v': 'v'})
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df = df.set_index('time')
            print(f"  Data: {len(df)} bars, {df.index[0]} to {df.index[-1]}")

            shift_df = calculate_phantom_shift(df, atr_period=10, multiplier=3.0)
            osc_df = calculate_phantom_oscillator(df, ma_length=40, osc_length=15, ma_type='SMA', threshold=0.5)
            combo_df = calculate_combo_signals(shift_df, osc_df)

            for sig_name, sig_df in [("Shift", shift_df), ("Osc", osc_df), ("Combo", combo_df)]:
                result = run_backtest(df, sig_df, f"{symbol}_{sig_name}",
                                      initial_capital=10000, risk_per_trade=0.02, sl_atr_mult=2.0)
                result['symbol'] = symbol
                result['description'] = row['description']
                result['type'] = row['type']
                result['resolution'] = resolution
                result['bars'] = len(df)
                all_results.append(result)

                print(f"  {sig_name:6s}: {result['trades']:>4d} trades, WR: {result['win_rate']:>5.1f}%, PnL: ${result['total_pnl']:>9.0f}, PF: {result['profit_factor']:.2f}, MaxDD: {result['max_dd']:.1f}%")

        except Exception as e:
            print(f"  Error: {e}")
            import traceback; traceback.print_exc()
            continue

    if all_results:
        results_df = pd.DataFrame(all_results)

        print("\n" + "="*120)
        print(f"BACKTEST RESULTS: {', '.join(TARGET_SYMBOLS)} — Resolution: {resolution}")
        print("="*120)

        for strategy in ['Shift', 'Osc', 'Combo']:
            strat_results = results_df[results_df['strategy'].str.endswith(f'_{strategy}')].copy()
            if strat_results.empty:
                continue
            strat_results = strat_results[strat_results['trades'] >= 3].sort_values('total_pnl', ascending=False)
            print(f"\n--- {strategy} Strategy ---")
            print(f"{'Symbol':<12} {'Trades':>6} {'Win%':>6} {'PnL':>10} {'AvgWin':>10} {'AvgLoss':>10} {'PF':>6} {'MaxDD':>7} {'Sharpe':>7}")
            print("-"*90)
            for _, r in strat_results.iterrows():
                print(f"{r['symbol']:<12} {r['trades']:>6} {r['win_rate']:>6.1f} ${r['total_pnl']:>9.0f} ${r['avg_win']:>9.0f} ${r['avg_loss']:>9.0f} {r['profit_factor']:>6.2f} {r['max_dd']:>7.1f}% {r['sharpe']:>7.2f}")

        combo_results = results_df[
            (results_df['strategy'].str.endswith('_Combo')) &
            (results_df['trades'] >= 3)
        ].sort_values('total_pnl', ascending=False)

        if not combo_results.empty:
            print("\n" + "="*120)
            print("OVERALL RANKING (Combo Strategy)")
            print("="*120)
            print(f"{'Rank':>4} {'Symbol':<12} {'Trades':>6} {'Win%':>6} {'PnL':>10} {'PF':>6} {'MaxDD':>7} {'Sharpe':>7}")
            print("-"*70)
            for i, (_, r) in enumerate(combo_results.head(10).iterrows(), 1):
                print(f"{i:>4} {r['symbol']:<12} {r['trades']:>6} {r['win_rate']:>6.1f} ${r['total_pnl']:>9.0f} {r['profit_factor']:>6.2f} {r['max_dd']:>7.1f}% {r['sharpe']:>7.2f}")

        output_file = f"backtest_{TARGET_SYMBOLS[0].replace('.R','')}_{TARGET_SYMBOLS[1].replace('.R','')}_{resolution.lower()}.csv"
        results_df.to_csv(output_file, index=False)
        print(f"\nResults saved to {output_file}")

        return results_df
    else:
        print("No results generated.")
        return None

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--resolution', type=str, default='1H', choices=['1m', '5m', '15m', '30m', '1H', '4H', '1D'])
    args = parser.parse_args()
    run_backtest_for_symbols(lookback_days=args.days, resolution=args.resolution)
