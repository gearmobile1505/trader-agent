#!/usr/bin/env python3
"""
CFD Trading Webhook Bridge for TradeLocker Demo Account.
Works with available CFD instruments: Indices, Metals, Energies, Forex, Crypto, Stocks.
"""

import os
from fastapi import FastAPI, Request
import ollama
from tradelocker import TLAPI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Load broker credentials
TL_ENV = os.getenv("TL_ENV", "https://demo.tradelocker.com")
TL_USER = os.getenv("TL_USER")
TL_PASS = os.getenv("TL_PASS")
TL_SERVER = os.getenv("TL_SERVER")

# Initialize TradeLocker client wrapper
tl = TLAPI(
    environment=TL_ENV,
    username=TL_USER,
    password=TL_PASS,
    server=TL_SERVER
)

# CFD Point Values ($ per 1 point movement) - TradeLocker demo typical values
# These are approximate; check instrument details for exact contract specs
POINT_VALUES = {
    # Indices (Cash CFDs)
    "US30.R": 1.0,      # US 30 (Dow) - $1 per point
    "SPX500.R": 1.0,    # US 500 (S&P 500) - $1 per point
    "NAS100.R": 1.0,    # US Tech 100 (Nasdaq) - $1 per point
    "DE30.R": 1.0,      # Germany 30 (DAX) - ~$1 per point
    "UK100.R": 1.0,     # UK 100 (FTSE) - ~$1 per point
    "F40.R": 1.0,       # France 40 (CAC) - ~$1 per point
    "ES35.R": 1.0,      # Spain 35 (IBEX) - ~$1 per point
    "AUS200.R": 1.0,    # Australia 200 - ~$1 per point
    "JP225.R": 100.0,   # Japan 225 (Nikkei) - ¥100 per point (~$0.65)
    
    # Metals
    "XAUUSD.R": 1.0,    # Gold - $1 per $1 move (1 oz)
    "XAGUSD.R": 50.0,   # Silver - $50 per $1 move (5000 oz)
    "XPTUSD.R": 1.0,    # Platinum - $1 per $1 move (1 oz)
    "XPDUSD.R": 1.0,    # Palladium - $1 per $1 move (1 oz)
    "XAGEUR.R": 50.0,   # Silver/EUR
    "XAUEUR.R": 1.0,    # Gold/EUR
    
    # Energies
    "USOIL.R": 10.0,    # WTI Crude - $10 per $1 move (1000 barrels)
    "UKOIL.R": 10.0,    # Brent Crude - $10 per $1 move (1000 barrels)
    "NGAS.R": 1000.0,   # Natural Gas - $1000 per $1 move (10,000 MMBtu)
    
    # Forex (per 0.0001 pip for majors, 0.01 for JPY pairs)
    # Standard lot = 100,000 units, so $10 per pip for majors
    "EURUSD.R": 100000.0,
    "GBPUSD.R": 100000.0,
    "USDJPY.R": 100000.0,
    "USDCHF.R": 100000.0,
    "AUDUSD.R": 100000.0,
    "USDCAD.R": 100000.0,
    "NZDUSD.R": 100000.0,
    "EURGBP.R": 100000.0,
    "EURJPY.R": 100000.0,
    "GBPJPY.R": 100000.0,
    "EURCHF.R": 100000.0,
    "GBPCHF.R": 100000.0,
    "AUDJPY.R": 100000.0,
    "CADJPY.R": 100000.0,
    "CHFJPY.R": 100000.0,
    "EURAUD.R": 100000.0,
    "EURCAD.R": 100000.0,
    "GBPAUD.R": 100000.0,
    "GBPCAD.R": 100000.0,
    "NZDJPY.R": 100000.0,
    
    # Crypto (typically $1 per coin move for 1 lot = 1 coin)
    "BTCUSD": 1.0,
    "ETHUSD": 1.0,
    "XRPUSD": 1.0,
    "ADAUSD": 1.0,
    "DOGEUSD": 1.0,
    "LTCUSD": 1.0,
    "BCHUSD": 1.0,
    "LINKUSD": 1.0,
    "DOTUSD": 1.0,
    "SOLUSD": 1.0,
    
    # Stocks (typically $1 per share move for 1 lot = 1 share)
    "APPLE": 1.0,
    "MICROSOFT": 1.0,
    "AMAZON": 1.0,
    "TESLA": 1.0,
    "GOOGLE": 1.0,
    "META": 1.0,
    "NVDA": 1.0,
    "NFLX": 1.0,
}

# Target max risk per trade for prop account protection ($250 target risk)
TARGET_DOLLAR_RISK = 250.0

# Map common TradingView symbols to TradeLocker CFD symbols
SYMBOL_MAP = {
    # Futures -> CFD equivalents
    "MNQ": "NAS100.R",
    "MES": "SPX500.R", 
    "MYM": "US30.R",
    "M2K": "US30.R",  # No direct Russell 2000 CFD, use US30
    "NQ": "NAS100.R",
    "ES": "SPX500.R",
    "YM": "US30.R",
    "RTY": "US30.R",
    "CL": "USOIL.R",
    "GC": "XAUUSD.R",
    "SI": "XAGUSD.R",
    "HG": "XAUUSD.R",  # No copper CFD, use gold as proxy
    "ZB": "XAUUSD.R",  # No bond CFD
    "ZN": "XAUUSD.R",
    
    # Crypto (same symbols often work)
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
    
    # Forex (TradingView uses no .R suffix)
    "EURUSD": "EURUSD.R",
    "GBPUSD": "GBPUSD.R",
    "USDJPY": "USDJPY.R",
    "GBPJPY": "GBPJPY.R",
    "AUDUSD": "AUDUSD.R",
    "USDCAD": "USDCAD.R",
    "NZDUSD": "NZDUSD.R",
}

def map_symbol(tv_symbol: str) -> str:
    """Map TradingView symbol to TradeLocker CFD symbol."""
    # Remove common suffixes like "1!" for futures
    base = tv_symbol.replace("1!", "").replace("!", "").upper()
    return SYMBOL_MAP.get(base, tv_symbol)

def get_point_value(symbol: str) -> float:
    """Get point value for a symbol, with fallback logic."""
    mapped = map_symbol(symbol)
    return POINT_VALUES.get(mapped, POINT_VALUES.get(symbol.upper(), 1.0))

def calculate_position_size(ticker: str, entry_price: float, stop_loss: float) -> float:
    """Calculates dynamic lot/contract size based on stop loss distance for CFDs."""
    sl_distance = abs(entry_price - stop_loss)
    if sl_distance == 0:
        return 0.01  # Minimum lot size for CFDs
    
    point_val = get_point_value(ticker)
    
    # Dollar risk for 1 standard lot
    risk_per_lot = sl_distance * point_val
    if risk_per_lot <= 0:
        return 0.01
        
    # Dynamic Lot Calculation
    calculated_qty = TARGET_DOLLAR_RISK / risk_per_lot
    return round(max(0.01, calculated_qty), 2)

@app.post("/webhook")
async def receive_tradingview_alert(request: Request):
    data = await request.json()
    
    # Extract parameters from TradingView JSON payload
    action = data.get("action")  # "buy" or "sell"
    tv_ticker = data.get("ticker")  # e.g., "MNQ1!" or "NAS100.R"
    suggested_sl = float(data.get("suggested_sl", 0.0))
    indicator_val = float(data.get("indicator_value", 0.0))
    trend_context = data.get("trend", "Unknown")
    
    # Map TradingView symbol to TradeLocker CFD symbol
    tl_symbol = map_symbol(tv_ticker)
    
    # Step 1: Calculate Dynamic Position Size
    quantity = calculate_position_size(tv_ticker, indicator_val, suggested_sl)
    
    # Step 2: Prompt Local Ollama AI Agent for Trade Validation
    prompt = f"""
    You are a strict risk management agent for a prop firm challenge. Review this trade signal and determine if it adheres to risk parameters. Answer with ONLY 'APPROVED' or 'REJECTED' followed by a brief reason.
    - TradingView Ticker: {tv_ticker}
    - TradeLocker Symbol: {tl_symbol}
    - Action: {action}
    - Entry Price: {indicator_val}
    - Suggested Stop Loss: {suggested_sl}
    - Calculated Lot Size: {quantity}
    - Market Trend Context: {trend_context}
    - Risk Budget: ${TARGET_DOLLAR_RISK}
    """
    
    try:
        response = ollama.chat(
            model='llama3',
            messages=[{'role': 'user', 'content': prompt}]
        )
        agent_decision = response['message']['content']
    except Exception as e:
        return {"status": "error", "message": f"AI Agent unavailable: {str(e)}"}
        
    # Step 3: Execute Order via Broker API if Approved by Agent
    if "APPROVED" in agent_decision.upper():
        try:
            instrument_id = tl.get_instrument_id_from_symbol_name(tl_symbol)
            order_response = tl.create_order(
                instrument_id=instrument_id,
                quantity=quantity,
                side=action,
                type_="market",
                stop_loss=suggested_sl
            )
            return {
                "status": "success",
                "tv_ticker": tv_ticker,
                "tl_symbol": tl_symbol,
                "executed_quantity": quantity,
                "agent_notes": agent_decision,
                "broker_response": str(order_response)
            }
        except Exception as broker_error:
            return {"status": "broker_error", "message": str(broker_error), "tl_symbol": tl_symbol}
            
    return {"status": "blocked", "agent_notes": agent_decision, "tl_symbol": tl_symbol}

@app.get("/instruments")
async def list_instruments():
    """List all available TradeLocker instruments for this account."""
    instruments = tl.get_all_instruments()
    return {
        "total": len(instruments),
        "instruments": instruments[['name', 'description', 'type', 'tradingExchange', 'marketDataExchange']].to_dict('records')
    }

@app.get("/symbols/{asset_class}")
async def get_symbols_by_class(asset_class: str):
    """Filter instruments by asset class: CRYPTO, EQUITY_CFD, FOREX"""
    instruments = tl.get_all_instruments()
    filtered = instruments[instruments['type'] == asset_class.upper()]
    return {
        "asset_class": asset_class.upper(),
        "count": len(filtered),
        "symbols": filtered[['name', 'description', 'tradingExchange']].to_dict('records')
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)