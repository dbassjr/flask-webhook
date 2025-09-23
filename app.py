from flask import Flask, request
from ib_insync import *
import os

app = Flask(__name__)

# Environment variables set in Coolify
IB_HOST = os.getenv("IB_HOST", "ibgateway")
IB_PORT = int(os.getenv("IBC_PORT", "7497"))  # 7497=paper, 7496=live
ACCOUNT_ID = os.getenv("TRADING_ACCOUNT")

@app.route('/tv', methods=['POST'])
def tradingview_webhook():
    data = request.json
    print("TradingView webhook:", data)

    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=1)

    if data.get("action") in ["BUY", "SELL"]:
        contract = Stock(data["symbol"], 'SMART', 'USD')
        order = MarketOrder(data["action"], int(data["qty"]))
        trade = ib.placeOrder(contract, order, account=ACCOUNT_ID)
        ib.sleep(1)
        ib.disconnect()
        return {"status": "order sent", "details": data}

    return {"status": "ignored", "details": data}