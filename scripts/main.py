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

# Contract Point Values ($ per 1 point movement)
# MNQ: $2/pt ($0.50/tick) | MES: $5/pt ($1.25/tick) | M2K: $5/pt ($0.50/tick)
POINT_VALUES = {
    "MNQ": 2.0,
    "MES": 5.0,
    "MYM": 5.0,
    "M2K": 5.0,
}

# Target max risk per trade for prop account protection ($250 target risk)
TARGET_DOLLAR_RISK = 250.0

def calculate_position_size(ticker: str, entry_price: float, stop_loss: float) -> float:
    """Calculates dynamic lot/contract size based on stop loss distance."""
    sl_distance = abs(entry_price - stop_loss)
    if sl_distance == 0:
        return 0.1  # Fallback minimum
    
    # Extract ticker root (e.g., "MNQ1!" -> "MNQ")
    symbol_root = ticker[:3].upper()
    point_val = POINT_VALUES.get(symbol_root, 2.0)
    
    # Dollar risk for 1 contract
    risk_per_contract = sl_distance * point_val
    if risk_per_contract <= 0:
        return 0.1
        
    # Dynamic Lot Calculation
    calculated_qty = TARGET_DOLLAR_RISK / risk_per_contract
    return round(max(0.1, calculated_qty), 2)

@app.post("/webhook")
async def receive_tradingview_alert(request: Request):
    data = await request.json()
    
    # Extract parameters from TradingView JSON payload
    action = data.get("action")  # "buy" or "sell"
    ticker = data.get("ticker")  # e.g., "MNQ1!"
    suggested_sl = float(data.get("suggested_sl", 0.0))
    indicator_val = float(data.get("indicator_value", 0.0))
    trend_context = data.get("trend", "Unknown")
    
    # Step 1: Calculate Dynamic Position Size
    quantity = calculate_position_size(ticker, indicator_val, suggested_sl)
    
    # Step 2: Prompt Local Ollama AI Agent for Trade Validation
    prompt = f"""
    You are a strict risk management agent for a prop firm challenge. Review this trade signal and determine if it adheres to risk parameters. Answer with ONLY 'APPROVED' or 'REJECTED' followed by a brief reason.
    - Ticker: {ticker}
    - Action: {action}
    - Entry Price: {indicator_val}
    - Suggested Stop Loss: {suggested_sl}
    - Calculated Lot/Contract Quantity: {quantity}
    - Market Trend Context: {trend_context}
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
            instrument_id = tl.get_instrument_id_from_symbol_name(ticker)
            order_response = tl.create_order(
                instrument_id=instrument_id,
                quantity=quantity,
                side=action,
                type_="market",
                stop_loss=suggested_sl
            )
            return {
                "status": "success",
                "executed_quantity": quantity,
                "agent_notes": agent_decision,
                "broker_response": str(order_response)
            }
        except Exception as broker_error:
            return {"status": "broker_error", "message": str(broker_error)}
            
    return {"status": "blocked", "agent_notes": agent_decision}