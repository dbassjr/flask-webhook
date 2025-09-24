import os
import logging
import time
from flask import Flask, request, jsonify
from ib_insync import *

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
IB_HOST = os.getenv("IB_HOST", "ibgateway")
IB_PORT = int(os.getenv("IBC_PORT", "4002"))
ACCOUNT_ID = os.getenv("TRADING_ACCOUNT")

logger.info(f"Configuration: IB_HOST={IB_HOST}, IB_PORT={IB_PORT}, ACCOUNT={ACCOUNT_ID}")

def connect_to_ib(retries=3, delay=2):
    """Connect to IB Gateway with retry logic"""
    ib = IB()
    for attempt in range(retries):
        try:
            logger.info(f"Attempting to connect to IB Gateway at {IB_HOST}:{IB_PORT} (attempt {attempt + 1}/{retries})")
            ib.connect(IB_HOST, IB_PORT, clientId=1, timeout=10)
            logger.info("Successfully connected to IB Gateway")
            return ib
        except Exception as e:
            logger.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                logger.error("All connection attempts failed")
                raise
    return None

@app.route('/bgf', methods=['POST'])
def tradingview_webhook():
    """Handle TradingView webhook for placing trades"""
    try:
        data = request.json or {}
        logger.info(f"Received webhook data: {data}")
        
        # Validate required fields
        action = data.get("action")
        symbol = data.get("symbol")
        qty = data.get("qty")
        
        if not action or not symbol or not qty:
            error_msg = "Missing required fields: action, symbol, qty"
            logger.error(error_msg)
            return jsonify({"status": "error", "message": error_msg}), 400
        
        # Connect to IB Gateway
        ib = None
        try:
            ib = connect_to_ib()
            
            # Create contract and order
            contract = Stock(symbol.upper(), 'SMART', 'USD')
            ib.qualifyContracts(contract)  # Validate contract
            
            order = MarketOrder(action.upper(), int(qty))
            
            # Place order
            trade = ib.placeOrder(contract, order, account=ACCOUNT_ID)
            logger.info(f"Order placed: {trade}")
            
            # Wait for order confirmation
            ib.sleep(2)
            
            return jsonify({
                "status": "success",
                "message": "Order placed successfully",
                "details": {
                    "action": action,
                    "symbol": symbol,
                    "quantity": qty,
                    "trade_id": trade.order.orderId if trade else None
                }
            }), 200
            
        except Exception as e:
            error_msg = f"Trading error: {str(e)}"
            logger.error(error_msg)
            return jsonify({"status": "error", "message": error_msg}), 500
            
        finally:
            if ib and ib.isConnected():
                ib.disconnect()
                logger.info("Disconnected from IB Gateway")
                
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    try:
        # Test IB Gateway connection
        ib = IB()
        ib.connect(IB_HOST, IB_PORT, clientId=999, timeout=5)
        ib.disconnect()
        return jsonify({"status": "ok", "ib_gateway": "connected"}), 200
    except Exception as e:
        logger.warning(f"Health check - IB Gateway connection failed: {str(e)}")
        return jsonify({"status": "ok", "ib_gateway": "disconnected", "warning": str(e)}), 200

@app.route('/test', methods=['GET'])
def test():
    """Test endpoint for debugging"""
    return jsonify({
        "status": "ok",
        "config": {
            "IB_HOST": IB_HOST,
            "IB_PORT": IB_PORT,
            "ACCOUNT_ID": ACCOUNT_ID[:4] + "***" if ACCOUNT_ID else None
        },
        "message": "Webhook service is running"
    }), 200

if __name__ == "__main__":
    logger.info("Starting Flask webhook service...")
    app.run(host="0.0.0.0", port=5000, debug=False)
