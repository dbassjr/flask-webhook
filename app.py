import os
import logging
from flask import Flask, request, jsonify
from ib_insync import *

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = Flask(__name__)

# --- Environment variables set in Coolify ---
IB_HOST = os.getenv("IB_HOST", "ibgateway")
IB_PORT = int(os.getenv("IBC_PORT", "7497"))  # 7497=paper, 7496=live
ACCOUNT_ID = os.getenv("TRADING_ACCOUNT")

@app.route('/bgf', methods=['POST'])
def tradingview_webhook():
    data = request.json or {}
    logging.info(f"Webhook received: {data}")

    # Basic validation
    action = data.get("action")
    symbol = data.get("symbol")
    qty = data.get("qty")

    if not action or not symbol or not qty:
        logging.warning("Invalid payload received")
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    # Connect to IB Gateway
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=1)
        logging.info(f"Connected to IB Gateway at {IB_HOST}:{IB_PORT}")

        if action.upper() in ["BUY", "SELL"]:
            contract = Stock(symbol.upper(), 'SMART', 'USD')
            order = MarketOrder(action.upper(), int(qty))
            trade = ib.placeOrder(contract, order, account=ACCOUNT_ID)
            ib.sleep(1)  # allow IB to process
            logging.info(f"Order placed: {action.upper()} {qty} {symbol.upper()} into {ACCOUNT_ID}")
            return jsonify({"status": "order sent", "details": data}), 200
        else:
            logging.warning(f"Invalid action received: {action}")
            return jsonify({"status": "error", "message": "Invalid action"}), 400

    except Exception as e:
        logging.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if ib.isConnected():
            ib.disconnect()
            logging.info("Disconnected from IB Gateway")
