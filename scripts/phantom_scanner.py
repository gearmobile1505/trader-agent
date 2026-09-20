#!/usr/bin/env python3
"""
Phantom Flow Strategy Backtester for TradeLocker CFDs.
Tests Phantom Shift (ATR trend) + Phantom Oscillator strategies across all available symbols.
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

# =============================================================================
# PHANTOM FLOW INDICATOR LOGIC (Python Port)
# =============================================================================

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    high, low, close = df['h'], df['l'], df['c']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calculate_phantom_shift(df: pd.DataFrame, atr_period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Phantom Shift: ATR-based trailing stop / trend detection.
    Returns DataFrame with trend, signals, and bands.
    """
    src = (df['h'] + df['l']) / 2  # hl2
    atr = calculate_atr(df, atr_period)
    
    up = src - multiplier * atr
    dn = src + multiplier * atr
    
    trend = pd.Series(1, index=df.index)  # 1 = bullish, -1 = bearish
    up_band = pd.Series(index=df.index, dtype=float)
    dn_band = pd.Series(index=df.index, dtype=float)
    
    for i in range(1, len(df)):
        # Update bands
        up_band.iloc[i] = max(up.iloc[i], up_band.iloc[i-1]) if df['c'].iloc[i-1] > up_band.iloc[i-1] else up.iloc[i]
        dn_band.iloc[i] = min(dn.iloc[i], dn_band.iloc[i-1]) if df['c'].iloc[i-1] < dn_band.iloc[i-1] else dn.iloc[i]
        
        # Trend logic
        if trend.iloc[i-1] == -1 and df['c'].iloc[i] > dn_band.iloc[i-1]:
            trend.iloc[i] = 1
        elif trend.iloc[i-1] == 1 and df['c'].iloc[i] < up_band.iloc[i-1]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]
    
    # Signals: trend change
    buy_signal = (trend == 1) & (trend.shift(1) == -1)
    sell_signal = (trend == -1) & (trend.shift(1) == 1)
    
    return pd.DataFrame({
        'trend': trend,
        'up_band': up_band,
        'dn_band': dn_band,
        'buy_signal': buy_signal,
        'sell_signal': sell_signal,
        'atr': atr
    }, index=df.index)

def calculate_phantom_oscillator(df: pd.DataFrame, ma_length: int = 40, osc_length: int = 15, 
                                  ma_type: str = 'SMA', threshold: float = 0.5) -> pd.DataFrame:
    """
    Phantom Oscillator: MA distance oscillator with HMA smoothing.
    """
    src = (df['h'] + df['l']) / 2  # hl2
    
    # Moving Average
    if ma_type == 'SMA':
        ma = src.rolling(ma_length).mean()
    elif ma_type == 'EMA':
        ma = src.ewm(span=ma_length, adjust=False).mean()
    elif ma_type == 'SMMA (RMA)':
        ma = src.ewm(alpha=1/ma_length, adjust=False).mean()
    elif ma_type == 'WMA':
        weights = np.arange(1, ma_length + 1)
        ma = src.rolling(ma_length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    elif ma_type == 'VWMA':
        ma = (src * df['v']).rolling(ma_length).sum() / df['v'].rolling(ma_length).sum()
    else:
        ma = src.rolling(ma_length).mean()
    
    # Oscillator calculation
    diff = src - ma
    # Percentile normalization (approximate)
    diff_range = diff.rolling(1000).apply(lambda x: np.percentile(x, 99) if len(x) == 1000 else np.nan, raw=True)
    osc_raw = diff / diff_range
    
    # HMA smoothing (Hull Moving Average)
    def hma(series, period):
        half = int(period / 2)
        sqrt_p = int(np.sqrt(period))
        wma_half = series.rolling(half).apply(lambda x: np.dot(x, np.arange(1, half+1)) / np.arange(1, half+1).sum(), raw=True)
        wma_full = series.rolling(period).apply(lambda x: np.dot(x, np.arange(1, period+1)) / np.arange(1, period+1).sum(), raw=True)
        hma_raw = 2 * wma_half - wma_full
        return hma_raw.rolling(sqrt_p).apply(lambda x: np.dot(x, np.arange(1, sqrt_p+1)) / np.arange(1, sqrt_p+1).sum(), raw=True)
    
    osc = hma(osc_raw.diff(osc_length), 10)
    
    # Signals: cross of osc with osc[2] with threshold filter
    buy_signal = (osc > osc.shift(2)) & (osc.shift(1) <= osc.shift(3)) & (osc < -threshold)
    sell_signal = (osc < osc.shift(2)) & (osc.shift(1) >= osc.shift(3)) & (osc > threshold)
    
    return pd.DataFrame({
        'osc': osc,
        'ma': ma,
        'buy_signal': buy_signal,
        'sell_signal': sell_signal
    }, index=df.index)

def calculate_combo_signals(shift_df: pd.DataFrame, osc_df: pd.DataFrame) -> pd.DataFrame:
    """Combo: Phantom Shift + Oscillator confluence."""
    combo_buy = shift_df['buy_signal'] & osc_df['buy_signal']
    combo_sell = shift_df['sell_signal'] & osc_df['sell_signal']
    return pd.DataFrame({
        'combo_buy': combo_buy,
        'combo_sell': combo_sell
    }, index=shift_df.index)

# =============================================================================
# BACKTESTING ENGINE
# =============================================================================

def run_backtest(df: pd.DataFrame, signals: pd.DataFrame, 
                 strategy_name: str, initial_capital: float = 10000,
                 risk_per_trade: float = 0.02, sl_atr_mult: float = 2.0) -> dict:
    """
    Run backtest on signals.
    Returns performance metrics.
    """
    equity = initial_capital
    position = 0  # 1 = long, -1 = short, 0 = flat
    entry_price = 0
    stop_loss = 0
    trades = []
    equity_curve = [equity]
    
    for i in range(1, len(df)):
        # Check exit conditions first
        if position != 0:
            if position == 1:  # Long
                if df['l'].iloc[i] <= stop_loss:
                    # Stop loss hit
                    pnl = (stop_loss - entry_price) * (equity * risk_per_trade / (entry_price - stop_loss))
                    equity += pnl
                    trades.append({'type': 'long', 'entry': entry_price, 'exit': stop_loss, 'pnl': pnl, 'exit_reason': 'SL'})
                    position = 0
                elif signals['sell_signal'].iloc[i] if 'sell_signal' in signals.columns else signals.get('combo_sell', pd.Series(False)).iloc[i]:
                    # Signal exit
                    pnl = (df['c'].iloc[i] - entry_price) * (equity * risk_per_trade / (entry_price - stop_loss))
                    equity += pnl
                    trades.append({'type': 'long', 'entry': entry_price, 'exit': df['c'].iloc[i], 'pnl': pnl, 'exit_reason': 'Signal'})
                    position = 0
            elif position == -1:  # Short
                if df['h'].iloc[i] >= stop_loss:
                    pnl = (entry_price - stop_loss) * (equity * risk_per_trade / (stop_loss - entry_price))
                    equity += pnl
                    trades.append({'type': 'short', 'entry': entry_price, 'exit': stop_loss, 'pnl': pnl, 'exit_reason': 'SL'})
                    position = 0
                elif signals['buy_signal'].iloc[i] if 'buy_signal' in signals.columns else signals.get('combo_buy', pd.Series(False)).iloc[i]:
                    pnl = (entry_price - df['c'].iloc[i]) * (equity * risk_per_trade / (stop_loss - entry_price))
                    equity += pnl
                    trades.append({'type': 'short', 'entry': entry_price, 'exit': df['c'].iloc[i], 'pnl': pnl, 'exit_reason': 'Signal'})
                    position = 0
        
        # Check entry conditions
        if position == 0:
            atr_val = df.get('atr', pd.Series(1, index=df.index)).iloc[i]
            if signals['buy_signal'].iloc[i] if 'buy_signal' in signals.columns else signals.get('combo_buy', pd.Series(False)).iloc[i]:
                position = 1
                entry_price = df['c'].iloc[i]
                stop_loss = entry_price - sl_atr_mult * atr_val
            elif signals['sell_signal'].iloc[i] if 'sell_signal' in signals.columns else signals.get('combo_sell', pd.Series(False)).iloc[i]:
                position = -1
                entry_price = df['c'].iloc[i]
                stop_loss = entry_price + sl_atr_mult * atr_val
        
        equity_curve.append(equity)
    
    # Close any open position at end
    if position != 0:
        if position == 1:
            pnl = (df['c'].iloc[-1] - entry_price) * (equity * risk_per_trade / (entry_price - stop_loss))
        else:
            pnl = (entry_price - df['c'].iloc[-1]) * (equity * risk_per_trade / (stop_loss - entry_price))
        equity += pnl
        trades.append({'type': 'long' if position == 1 else 'short', 'entry': entry_price, 'exit': df['c'].iloc[-1], 'pnl': pnl, 'exit_reason': 'EOD'})
    
    # Calculate metrics
    trades_df = pd.DataFrame(trades)
    if len(trades_df) == 0:
        return {'strategy': strategy_name, 'trades': 0, 'win_rate': 0, 'total_pnl': 0, 'max_dd': 0, 'sharpe': 0, 'profit_factor': 0}
    
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] < 0]
    
    total_pnl = trades_df['pnl'].sum()
    win_rate = len(wins) / len(trades_df) * 100
    avg_win = wins['pnl'].mean() if len(wins) > 0 else 0
    avg_loss = losses['pnl'].mean() if len(losses) > 0 else 0
    profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 else float('inf')
    
    # Max drawdown
    equity_series = pd.Series(equity_curve)
    rolling_max = equity_series.expanding().max()
    drawdown = (equity_series - rolling_max) / rolling_max * 100
    max_dd = drawdown.min()
    
    # Sharpe (simplified)
    returns = equity_series.pct_change().dropna()
    sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
    
    return {
        'strategy': strategy_name,
        'trades': len(trades_df),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(win_rate, 2),
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'max_dd': round(max_dd, 2),
        'sharpe': round(sharpe, 2),
        'final_equity': round(equity, 2)
    }

# =============================================================================
# MAIN SCANNER
# =============================================================================

def scan_all_symbols(lookback_days: int = 90, resolution: str = "1H", target_symbols: list = None):
    """Fetch data for all symbols and run backtests."""
    
    print(f"Fetching all instruments from TradeLocker...")
    instruments = tl.get_all_instruments()
    print(f"Found {len(instruments)} instruments")
    
    # Filter for EQUITY_CFD (indices, metals, energies, stocks)
    cfd_instruments = instruments[instruments['type'] == 'EQUITY_CFD']
    
    # Filter by target symbols if provided
    if target_symbols:
        cfd_instruments = cfd_instruments[cfd_instruments['name'].isin(target_symbols)]
        print(f"Testing {len(cfd_instruments)} specified CFD instruments...")
    else:
        print(f"Testing {len(cfd_instruments)} CFD instruments...")
    
    end_ts = int(datetime.now().timestamp() * 1000)
    start_ts = int((datetime.now() - timedelta(days=lookback_days)).timestamp() * 1000)
    
    results = []
    
    for idx, row in cfd_instruments.iterrows():
        symbol = row['name']
        print(f"\n[{idx+1}/{len(cfd_instruments)}] Testing {symbol} ({row['description']})...")
        
        try:
            # Fetch historical data
            hist = tl.get_price_history(
                instrument_id=int(row['tradableInstrumentId']),
                resolution=resolution,
                start_timestamp=start_ts,
                end_timestamp=end_ts
            )
            
            if hist.empty or len(hist) < 100:
                print(f"  Insufficient data ({len(hist)} bars), skipping")
                continue
            
            # Rename columns
            df = hist.rename(columns={'t': 'time', 'o': 'o', 'h': 'h', 'l': 'l', 'c': 'c', 'v': 'v'})
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df = df.set_index('time')
            
            # Run Phantom Shift
            shift_df = calculate_phantom_shift(df, atr_period=10, multiplier=3.0)
            
            # Run Phantom Oscillator
            osc_df = calculate_phantom_oscillator(df, ma_length=40, osc_length=15, ma_type='SMA', threshold=0.5)
            
            # Combo signals
            combo_df = calculate_combo_signals(shift_df, osc_df)
            
            # Backtest each strategy
            shift_result = run_backtest(df, shift_df, f"{symbol}_Shift")
            osc_result = run_backtest(df, osc_df, f"{symbol}_Osc")
            combo_result = run_backtest(df, combo_df, f"{symbol}_Combo")
            
            # Add symbol info
            for r in [shift_result, osc_result, combo_result]:
                r['symbol'] = symbol
                r['description'] = row['description']
                r['type'] = row['type']
                r['exchange'] = row['tradingExchange']
            
            results.extend([shift_result, osc_result, combo_result])
            
            print(f"  Shift: {shift_result['trades']} trades, WR: {shift_result['win_rate']}%, PnL: ${shift_result['total_pnl']}")
            print(f"  Osc:   {osc_result['trades']} trades, WR: {osc_result['win_rate']}%, PnL: ${osc_result['total_pnl']}")
            print(f"  Combo: {combo_result['trades']} trades, WR: {combo_result['win_rate']}%, PnL: ${combo_result['total_pnl']}")
            
        except Exception as e:
            print(f"  Error: {e}")
            continue
    
    return pd.DataFrame(results)

def print_rankings(results_df: pd.DataFrame):
    """Print ranked results by strategy."""
    
    print("\n" + "="*100)
    print("PHANTOM FLOW BACKTEST RESULTS - RANKED BY TOTAL PnL")
    print("="*100)
    
    for strategy in ['Shift', 'Osc', 'Combo']:
        strat_results = results_df[results_df['strategy'].str.endswith(f'_{strategy}')].copy()
        if strat_results.empty:
            continue
        
        # Filter for meaningful trades
        strat_results = strat_results[strat_results['trades'] >= 5]
        if strat_results.empty:
            continue
        
        strat_results = strat_results.sort_values('total_pnl', ascending=False)
        
        print(f"\n--- {strategy} Strategy (Top 15) ---")
        print(f"{'Symbol':<12} {'Trades':>6} {'Win%':>6} {'PnL':>10} {'PF':>6} {'MaxDD':>7} {'Sharpe':>7} {'Description'}")
        print("-" * 100)
        
        for _, r in strat_results.head(15).iterrows():
            print(f"{r['symbol']:<12} {r['trades']:>6} {r['win_rate']:>6.1f} ${r['total_pnl']:>9.0f} {r['profit_factor']:>6.2f} {r['max_dd']:>7.1f}% {r['sharpe']:>7.2f} {r['description'][:40]}")
    
    # Overall best
    print("\n" + "="*100)
    print("OVERALL BEST COMBOS (Combo Strategy, min 10 trades)")
    print("="*100)
    combo_results = results_df[
        (results_df['strategy'].str.endswith('_Combo')) & 
        (results_df['trades'] >= 10)
    ].sort_values('total_pnl', ascending=False)
    
    if not combo_results.empty:
        print(f"{'Rank':>4} {'Symbol':<12} {'Trades':>6} {'Win%':>6} {'PnL':>10} {'PF':>6} {'MaxDD':>7} {'Sharpe':>7}")
        print("-" * 70)
        for i, (_, r) in enumerate(combo_results.head(20).iterrows(), 1):
            print(f"{i:>4} {r['symbol']:<12} {r['trades']:>6} {r['win_rate']:>6.1f} ${r['total_pnl']:>9.0f} {r['profit_factor']:>6.2f} {r['max_dd']:>7.1f}% {r['sharpe']:>7.2f}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Phantom Flow CFD Scanner')
    parser.add_argument('--days', type=int, default=90, help='Lookback days')
    parser.add_argument('--resolution', type=str, default='1H', choices=['1m', '5m', '15m', '30m', '1H', '4H', '1D'], help='Resolution')
    parser.add_argument('--output', type=str, help='Output CSV file')
    parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols to test')
    args = parser.parse_args()
    
    target_symbols = args.symbols.split(',') if args.symbols else None
    
    print(f"Phantom Flow CFD Scanner")
    print(f"Lookback: {args.days} days, Resolution: {args.resolution}")
    if target_symbols:
        print(f"Target symbols: {target_symbols}")
    
    results = scan_all_symbols(lookback_days=args.days, resolution=args.resolution, target_symbols=target_symbols)
    
    if not results.empty:
        print_rankings(results)
        
        if args.output:
            results.to_csv(args.output, index=False)
            print(f"\nResults saved to {args.output}")
    else:
        print("No results generated.")