#!/usr/bin/env python3
"""Fetch and display all available tickers from TradeLocker broker."""

import os
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

print("Fetching all available instruments from TradeLocker...")
instruments = tl.get_all_instruments()

if instruments.empty:
    print("No instruments found.")
else:
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)
    pd.set_option('display.max_colwidth', 50)
    
    print(f"\nTotal instruments: {len(instruments)}")
    print("\n" + "=" * 100)
    
    # Display key columns
    display_cols = ['name', 'description', 'type', 'tradingExchange', 'marketDataExchange', 'country', 'tradableInstrumentId']
    print(instruments[display_cols].to_string(index=False))
    
    # Also show unique types and exchanges
    print("\n" + "=" * 100)
    print("\nUnique Instrument Types:")
    print(instruments['type'].unique())
    
    print("\nUnique Trading Exchanges:")
    print(instruments['tradingExchange'].unique())
    
    print("\nUnique Market Data Exchanges:")
    print(instruments['marketDataExchange'].unique())
    
    # Filter for common futures tickers
    print("\n" + "=" * 100)
    print("\nFutures-like instruments (common patterns):")
    futures_patterns = ['MNQ', 'MES', 'MYM', 'M2K', 'NQ', 'ES', 'YM', 'RTY', 'CL', 'GC', 'SI', 'HG', 'ZB', 'ZN', 'ZF', 'ZT']
    for pattern in futures_patterns:
        matches = instruments[instruments['name'].str.contains(pattern, case=False, na=False)]
        if not matches.empty:
            print(f"\n{pattern}:")
            print(matches[['name', 'description', 'type', 'tradingExchange']].to_string(index=False))