#!/usr/bin/env python3
"""
Swing Move Analysis for 5M Scalping - Calculate optimal TP levels per symbol.
"""
import os
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

tl = TLAPI(
    environment=TL_ENV,
    username=TL_USER,
    password=TL_PASS,
    server=TL_SERVER
)

# Top 10 symbols from config
TOP_SYMBOLS = {
    "LVMH": {"point_value": 100.0, "tick_size": 0.01, "currency": "EUR"},
    "XPDUSD.R": {"point_value": 100.0, "tick_size": 0.01, "currency": "USD"},
    "ALPHABET-C": {"point_value": 100.0, "tick_size": 0.01, "currency": "USD"},
    "UKOIL.R": {"point_value": 1000.0, "tick_size": 0.01, "currency": "USD"},
    "SIEMENS": {"point_value": 100.0, "tick_size": 0.01, "currency": "EUR"},
    "GE": {"point_value": 100.0, "tick_size": 0.01, "currency": "USD"},
    "US30.R": {"point_value": 100.0, "tick_size": 0.01, "currency": "USD"},
    "NAS100.R": {"point_value": 100.0, "tick_size": 0.01, "currency": "USD"},
    "SPX500.R": {"point_value": 100.0, "tick_size": 0.01, "currency": "USD"},
    "XAUUSD.R": {"point_value": 100.0, "tick_size": 0.001, "currency": "USD"},
}

EUR_USD = 1.08

def find_swings(prices: pd.Series, min_swing_pct: float = 0.001) -> pd.DataFrame:
    """
    Identify swing highs and lows using fractals (5-bar).
    Returns DataFrame with swing points and move sizes.
    """
    highs = prices.rolling(5, center=True).max() == prices
    lows = prices.rolling(5, center=True).min() == prices
    
    swings = []
    last_swing_price = None
    last_swing_type = None
    last_swing_idx = None
    
    for i in range(len(prices)):
        if highs.iloc[i] and (last_swing_type != 'high' or prices.iloc[i] > last_swing_price):
            if last_swing_type == 'low' and last_swing_price is not None:
                move = prices.iloc[i] - last_swing_price
                move_pct = move / last_swing_price
                if abs(move_pct) >= min_swing_pct:
                    swings.append({
                        'swing_type': 'bullish',
                        'start_idx': last_swing_idx,
                        'end_idx': i,
                        'start_price': last_swing_price,
                        'end_price': prices.iloc[i],
                        'move_points': move,
                        'move_pct': move_pct,
                        'duration_bars': i - last_swing_idx
                    })
            last_swing_price = prices.iloc[i]
            last_swing_type = 'high'
            last_swing_idx = i
            
        elif lows.iloc[i] and (last_swing_type != 'low' or prices.iloc[i] < last_swing_price):
            if last_swing_type == 'high' and last_swing_price is not None:
                move = last_swing_price - prices.iloc[i]
                move_pct = move / last_swing_price
                if abs(move_pct) >= min_swing_pct:
                    swings.append({
                        'swing_type': 'bearish',
                        'start_idx': last_swing_idx,
                        'end_idx': i,
                        'start_price': last_swing_price,
                        'end_price': prices.iloc[i],
                        'move_points': move,
                        'move_pct': move_pct,
                        'duration_bars': i - last_swing_idx
                    })
            last_swing_price = prices.iloc[i]
            last_swing_type = 'low'
            last_swing_idx = i
    
    return pd.DataFrame(swings)

def analyze_symbol(tl_symbol: str, config: dict, days: int = 14):
    """Fetch 5M data and analyze swing moves."""
    try:
        instrument_id = tl.get_instrument_id_from_symbol_name(tl_symbol)
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        
        hist = tl.get_price_history(
            instrument_id=int(instrument_id),
            resolution="5m",
            start_timestamp=start_ts,
            end_timestamp=end_ts
        )
        
        if hist.empty or len(hist) < 100:
            return None
            
        df = hist.rename(columns={'t': 'time', 'o': 'o', 'h': 'h', 'l': 'l', 'c': 'c', 'v': 'v'})
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df = df.set_index('time')
        
        # Use close prices for swing analysis
        close = df['c']
        
        # Find swings
        swings = find_swings(close, min_swing_pct=0.0005)  # 0.05% minimum
        
        if swings.empty:
            return None
            
        bullish = swings[swings['swing_type'] == 'bullish']
        bearish = swings[swings['swing_type'] == 'bearish']
        
        point_value = config['point_value']
        currency = config.get('currency', 'USD')
        
        # Convert to dollars
        if currency == 'EUR':
            point_value *= EUR_USD
        
        results = {
            'symbol': tl_symbol,
            'total_swings': len(swings),
            'bullish_count': len(bullish),
            'bearish_count': len(bearish),
            'bullish_avg_points': bullish['move_points'].mean() if len(bullish) > 0 else 0,
            'bullish_median_points': bullish['move_points'].median() if len(bullish) > 0 else 0,
            'bullish_max_points': bullish['move_points'].max() if len(bullish) > 0 else 0,
            'bullish_avg_dollars': (bullish['move_points'] * point_value).mean() if len(bullish) > 0 else 0,
            'bullish_median_dollars': (bullish['move_points'] * point_value).median() if len(bullish) > 0 else 0,
            'bearish_avg_points': bearish['move_points'].mean() if len(bearish) > 0 else 0,
            'bearish_median_points': bearish['move_points'].median() if len(bearish) > 0 else 0,
            'bearish_max_points': bearish['move_points'].max() if len(bearish) > 0 else 0,
            'bearish_avg_dollars': (bearish['move_points'] * point_value).mean() if len(bearish) > 0 else 0,
            'bearish_median_dollars': (bearish['move_points'] * point_value).median() if len(bearish) > 0 else 0,
            'avg_duration_bars': swings['duration_bars'].mean(),
            'avg_duration_minutes': swings['duration_bars'].mean() * 5,
        }
        
        # Percentiles for TP targeting
        if len(bullish) > 10:
            results['bullish_p50'] = bullish['move_points'].quantile(0.5)
            results['bullish_p60'] = bullish['move_points'].quantile(0.6)
            results['bullish_p70'] = bullish['move_points'].quantile(0.7)
            results['bullish_p80'] = bullish['move_points'].quantile(0.8)
            results['bullish_p90'] = bullish['move_points'].quantile(0.9)
            
        if len(bearish) > 10:
            results['bearish_p50'] = bearish['move_points'].quantile(0.5)
            results['bearish_p60'] = bearish['move_points'].quantile(0.6)
            results['bearish_p70'] = bearish['move_points'].quantile(0.7)
            results['bearish_p80'] = bearish['move_points'].quantile(0.8)
            results['bearish_p90'] = bearish['move_points'].quantile(0.9)
            
        return results, swings
        
    except Exception as e:
        print(f"Error analyzing {tl_symbol}: {e}")
        return None

def main():
    print("=" * 100)
    print("SWING MOVE ANALYSIS - 5M DATA (14 days)")
    print("=" * 100)
    
    all_results = []
    all_swings = {}
    
    for symbol, config in TOP_SYMBOLS.items():
        print(f"\nAnalyzing {symbol}...")
        result = analyze_symbol(symbol, config, days=14)
        if result:
            results, swings = result
            all_results.append(results)
            all_swings[symbol] = swings
        else:
            print(f"  No data or insufficient swings")
    
    # Print summary table
    print("\n" + "=" * 100)
    print("SUMMARY: AVERAGE SWING MOVES (Points & Dollars @ $200 risk sizing)")
    print("=" * 100)
    print(f"{'Symbol':<12} {'Swings':>6} {'Bull Avg':>10} {'Bull Med':>10} {'Bull P70':>10} {'Bear Avg':>10} {'Bear Med':>10} {'Bear P70':>10} {'Avg Dur(m)':>10}")
    print("-" * 100)
    
    for r in all_results:
        print(f"{r['symbol']:<12} {r['total_swings']:>6} "
              f"{r['bullish_avg_points']:>10.1f} {r['bullish_median_points']:>10.1f} {r.get('bullish_p70', 0):>10.1f} "
              f"{r['bearish_avg_points']:>10.1f} {r['bearish_median_points']:>10.1f} {r.get('bearish_p70', 0):>10.1f} "
              f"{r['avg_duration_minutes']:>10.0f}")
    
    # Detailed TP recommendations
    print("\n" + "=" * 100)
    print("RECOMMENDED TAKE PROFIT LEVELS (based on P70 percentile - 70% of swings reach this)")
    print("=" * 100)
    print(f"{'Symbol':<12} {'Bull P70 (pts)':>14} {'Bull P70 ($)':>12} {'Bear P70 (pts)':>14} {'Bear P70 ($)':>12} {'R:R @ P70':>10} {'TP1 ($)':>10} {'TP2 ($)':>10}")
    print("-" * 100)
    
    for r in all_results:
        risk = 200  # $200 risk per trade
        
        bull_p70_pts = r.get('bullish_p70', 0)
        bear_p70_pts = r.get('bearish_p70', 0)
        
        point_value = TOP_SYMBOLS[r['symbol']]['point_value']
        if TOP_SYMBOLS[r['symbol']].get('currency') == 'EUR':
            point_value *= EUR_USD
            
        bull_p70_dollars = bull_p70_pts * point_value
        bear_p70_dollars = bear_p70_pts * point_value
        
        # R:R at P70
        bull_rr = bull_p70_dollars / risk if risk > 0 else 0
        bear_rr = bear_p70_dollars / risk if risk > 0 else 0
        
        # Suggested TP tiers: TP1 at 1.5R, TP2 at P70
        tp1 = risk * 1.5
        tp2 = max(bull_p70_dollars, bear_p70_dollars)
        
        print(f"{r['symbol']:<12} {bull_p70_pts:>14.1f} {bull_p70_dollars:>12.0f} "
              f"{bear_p70_pts:>14.1f} {bear_p70_dollars:>12.0f} "
              f"{bull_rr:>9.2f}/{bear_rr:.2f} {tp1:>10.0f} {tp2:>10.0f}")
    
    # Per-symbol detailed breakdown
    print("\n" + "=" * 100)
    print("DETAILED PER-SYMBOL STATS")
    print("=" * 100)
    
    for r in all_results:
        print(f"\n{r['symbol']}:")
        print(f"  Total swings: {r['total_swings']} (Bull: {r['bullish_count']}, Bear: {r['bearish_count']})")
        print(f"  Avg duration: {r['avg_duration_minutes']:.0f} minutes ({r['avg_duration_bars']:.1f} bars)")
        print(f"  Bullish - Avg: {r['bullish_avg_points']:.1f} pts (${r['bullish_avg_dollars']:.0f}), "
              f"Median: {r['bullish_median_points']:.1f} pts (${r['bullish_median_dollars']:.0f}), "
              f"Max: {r['bullish_max_points']:.1f} pts")
        print(f"  Bearish - Avg: {r['bearish_avg_points']:.1f} pts (${r['bearish_avg_dollars']:.0f}), "
              f"Median: {r['bearish_median_points']:.1f} pts (${r['bearish_median_dollars']:.0f}), "
              f"Max: {r['bearish_max_points']:.1f} pts")
        
        # Percentile table
        for pct in [50, 60, 70, 80, 90]:
            key = f'bullish_p{pct}'
            if key in r:
                pt = r[key]
                dol = pt * point_value
                print(f"    Bullish P{pct}: {pt:.1f} pts = ${dol:.0f} (R:R {dol/200:.2f})")
            key = f'bearish_p{pct}'
            if key in r:
                pt = r[key]
                dol = pt * point_value
                print(f"    Bearish P{pct}: {pt:.1f} pts = ${dol:.0f} (R:R {dol/200:.2f})")

if __name__ == "__main__":
    main()
