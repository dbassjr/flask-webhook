import os
import logging
import time
import asyncio
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
    """Connect to IB Gateway with retry logic and event loop handling"""
    # Create event loop for this thread if it doesn't exist
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
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
    """Handle TradingView webhook for placing trades with multiple order support"""
    try:
        data = request.json or {}
        logger.info(f"Received webhook data: {data}")
        
        # Expect orders array format only
        orders = data.get("orders", [])
        if not orders:
            return jsonify({
                "status": "error",
                "message": "Missing 'orders' array. Format: {'orders': [{'action': 'SELL', 'symbol': 'VX-Oct-25', 'qty': 2, 'order_type': 'MKT'}]}"
            }), 400
        
        # Validate for duplicate contracts with conflicting order types
        contract_orders = {}
        for i, order_data in enumerate(orders):
            symbol = order_data.get("symbol")
            order_type = order_data.get("order_type", "MKT").upper()
            
            if symbol in contract_orders:
                existing_order_type = contract_orders[symbol]
                
                # Allow multiple orders only if one is a stop/stop-limit order
                allowed_combinations = [
                    {"MKT", "STP"}, {"MKT", "STP_LMT"}, {"LMT", "STP"}, {"LMT", "STP_LMT"},
                    {"MARKET", "STOP"}, {"MARKET", "STOP_LIMIT"}, {"LIMIT", "STOP"}, {"LIMIT", "STOP_LIMIT"}
                ]
                
                current_combination = {existing_order_type, order_type}
                
                if current_combination not in allowed_combinations:
                    return jsonify({
                        "status": "error",
                        "message": f"Multiple orders for {symbol} with incompatible order types: {existing_order_type} and {order_type}. Only market/limit + stop combinations are allowed."
                    }), 400
            
            contract_orders[symbol] = order_type
        
        results = []
        ib = None
        
        try:
            ib = connect_to_ib()
            
            for i, order_data in enumerate(orders):
                try:
                    # Extract fields
                    action = order_data.get("action")
                    symbol = order_data.get("symbol") 
                    qty = order_data.get("qty")
                    order_type = order_data.get("order_type", "MKT")  # Default to market
                    price = order_data.get("price")
                    aux_price = order_data.get("aux_price")
                    target_position = order_data.get("position")
                    
                    # Validate order_type and default to MKT if invalid
                    valid_order_types = ["MKT", "MARKET", "LMT", "LIMIT", "STP", "STOP", "STP_LMT", "STOP_LIMIT"]
                    if order_type.upper() not in valid_order_types:
                        logger.warning(f"Order {i+1}: Invalid order_type '{order_type}', defaulting to MKT")
                        order_type = "MKT"
                    
                    # Handle conflicting instructions (position vs action/qty)
                    if target_position is not None and (action or qty):
                        logger.warning(f"Order {i+1}: Both 'position' and 'action/qty' provided. Using position-based logic, ignoring action/qty")
                        action = None  # Clear action/qty to use position logic
                        qty = None
                    
                    # Validate required fields
                    if not symbol:
                        error_msg = f"Order {i+1}: Missing required field: symbol"
                        logger.error(error_msg)
                        results.append({"order": i+1, "status": "error", "message": error_msg})
                        continue
                    
                    if target_position is None and (not action or not qty):
                        error_msg = f"Order {i+1}: Must provide either 'position' OR both 'action' and 'qty'"
                        logger.error(error_msg)
                        results.append({"order": i+1, "status": "error", "message": error_msg})
                        continue
                    
                    # Parse contract from TradingView format
                    if symbol.upper().startswith('VX'):
                        # Parse hyphenated VIX futures symbol like "VX-Oct-25" or "VXM-Nov-25"
                        parts = symbol.upper().split('-')
                        if len(parts) != 3:
                            error_msg = f"Order {i+1}: Invalid VIX symbol format: {symbol}. Use format like VX-Oct-25 or VXM-Oct-25"
                            logger.error(error_msg)
                            results.append({"order": i+1, "status": "error", "message": error_msg})
                            continue
                        
                        ticker = parts[0]  # VX, VXM, etc.
                        month_name = parts[1].upper()
                        year_short = parts[2]
                        
                        # Convert month name to number
                        month_map = {
                            'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                            'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
                        }
                        
                        if month_name not in month_map:
                            error_msg = f"Order {i+1}: Invalid month in symbol: {month_name}"
                            logger.error(error_msg)
                            results.append({"order": i+1, "status": "error", "message": error_msg})
                            continue
                        
                        contract_month = f"20{year_short}{month_map[month_name]}"
                        
                        # Create VIX futures contract - use the actual ticker provided
                        contract = Future(
                            symbol=ticker,  # Use VX, VXM, etc. as provided
                            lastTradeDateOrContractMonth=contract_month,
                            exchange='CFE',
                            currency='USD'
                        )
                    else:
                        # Regular stock
                        contract = Stock(symbol.upper(), 'SMART', 'USD')
                    
                    # Qualify the contract
                    qualified_contracts = ib.qualifyContracts(contract)
                    if not qualified_contracts:
                        error_msg = f"Order {i+1}: Could not find contract for symbol {symbol}"
                        logger.error(error_msg)
                        results.append({"order": i+1, "status": "error", "message": error_msg})
                        continue
                    
                    qualified_contract = qualified_contracts[0]
                    
                    # Position-based logic: calculate required action/qty
                    if target_position is not None:
                        # Get current position for this contract
                        current_positions = ib.positions()
                        current_position = 0
                        
                        for pos in current_positions:
                            if (pos.contract.symbol == qualified_contract.symbol and 
                                pos.contract.lastTradeDateOrContractMonth == qualified_contract.lastTradeDateOrContractMonth):
                                current_position = pos.position
                                break
                        
                        # Get pending orders for this contract
                        open_orders = ib.openOrders()
                        pending_position_change = 0
                        
                        for trade in open_orders:
                            if (trade.contract.symbol == qualified_contract.symbol and 
                                trade.contract.lastTradeDateOrContractMonth == qualified_contract.lastTradeDateOrContractMonth):
                                # Calculate position change from this pending order
                                if trade.order.action == 'BUY':
                                    pending_position_change += trade.order.totalQuantity
                                elif trade.order.action == 'SELL':
                                    pending_position_change -= trade.order.totalQuantity
                        
                        # Calculate effective current position (settled + pending)
                        effective_current_position = current_position + pending_position_change
                        
                        # Calculate required trade
                        position_difference = target_position - effective_current_position
                        
                        if position_difference == 0:
                            logger.info(f"Order {i+1}: Already at target position {target_position} (current: {current_position}, pending: {pending_position_change}), skipping")
                            results.append({
                                "order": i+1,
                                "status": "skipped",
                                "message": f"Already at target position {target_position}",
                                "details": {
                                    "symbol": symbol,
                                    "current_position": current_position,
                                    "pending_position_change": pending_position_change,
                                    "effective_position": effective_current_position,
                                    "target_position": target_position,
                                    "difference": 0
                                }
                            })
                            continue
                        
                        # Set action and quantity based on difference
                        if position_difference > 0:
                            action = "BUY"
                            qty = abs(position_difference)
                        else:
                            action = "SELL" 
                            qty = abs(position_difference)
                        
                        logger.info(f"Order {i+1}: Position calculation - Current: {current_position}, Pending: {pending_position_change}, Effective: {effective_current_position}, Target: {target_position}, Action: {action} {qty}")
                    
                    # Validate we have action and qty (either provided or calculated)
                    if not action or not qty:
                        error_msg = f"Order {i+1}: Could not determine action/qty"
                        logger.error(error_msg)
                        results.append({"order": i+1, "status": "error", "message": error_msg})
                        continue
                    
                    # Create order based on order_type
                    order_type = order_type.upper()
                    quantity = int(qty)
                    action = action.upper()
                    
                    if order_type in ["MKT", "MARKET"]:
                        order = MarketOrder(action, quantity)
                    
                    elif order_type in ["LMT", "LIMIT"]:
                        if not price:
                            error_msg = f"Order {i+1}: Price required for limit order"
                            logger.error(error_msg)
                            results.append({"order": i+1, "status": "error", "message": error_msg})
                            continue
                        order = LimitOrder(action, quantity, float(price))
                    
                    elif order_type in ["STP", "STOP"]:
                        stop_price = aux_price or price
                        if not stop_price:
                            error_msg = f"Order {i+1}: Stop price required for stop order"
                            logger.error(error_msg)
                            results.append({"order": i+1, "status": "error", "message": error_msg})
                            continue
                        order = StopOrder(action, quantity, float(stop_price))
                    
                    elif order_type in ["STP_LMT", "STOP_LIMIT"]:
                        if not price or not aux_price:
                            error_msg = f"Order {i+1}: Both limit price and stop price required for stop-limit order"
                            logger.error(error_msg)
                            results.append({"order": i+1, "status": "error", "message": error_msg})
                            continue
                        order = StopLimitOrder(action, quantity, float(price), float(aux_price))
                    
                    else:
                        error_msg = f"Order {i+1}: Unsupported order type: {order_type}. Use MKT, LMT, STP, or STP_LMT"
                        logger.error(error_msg)
                        results.append({"order": i+1, "status": "error", "message": error_msg})
                        continue
                    
                    # Place the order (removed account parameter)
                    trade = ib.placeOrder(qualified_contract, order)
                    logger.info(f"Order {i+1} placed: {trade}")
                    
                    # Wait for order confirmation
                    ib.sleep(1)
                    
                    # Get order status
                    order_status = trade.orderStatus.status if trade.orderStatus else "Submitted"
                    
                    results.append({
                        "order": i+1,
                        "status": "success", 
                        "message": "Order placed successfully",
                        "details": {
                            "symbol": symbol,
                            "action": action,
                            "quantity": quantity,
                            "order_type": order_type,
                            "price": price if price else None,
                            "aux_price": aux_price if aux_price else None,
                            "trade_id": trade.order.orderId if trade else None,
                            "order_status": order_status,
                            "contract_symbol": qualified_contract.symbol,
                            "local_symbol": getattr(qualified_contract, 'localSymbol', symbol),
                            "position_based": target_position is not None,
                            "current_position": current_position if target_position is not None else "N/A",
                            "pending_position_change": pending_position_change if target_position is not None else "N/A",
                            "effective_position": effective_current_position if target_position is not None else "N/A",
                            "target_position": target_position if target_position is not None else "N/A"
                        }
                    })
                    
                except Exception as e:
                    error_msg = f"Order {i+1} error: {str(e)}"
                    logger.error(error_msg)
                    results.append({"order": i+1, "status": "error", "message": error_msg})
            
            # Prepare final response
            successful_orders = len([r for r in results if r["status"] == "success"])
            failed_orders = len(results) - successful_orders
            
            overall_status = "success" if successful_orders > 0 else "error"
            
            return jsonify({
                "status": overall_status,
                "message": f"Processed {len(results)} orders: {successful_orders} successful, {failed_orders} failed",
                "total_orders": len(results),
                "successful_orders": successful_orders,
                "failed_orders": failed_orders,
                "results": results
            }), 200 if successful_orders > 0 else 400
            
        except Exception as e:
            error_msg = f"Trading connection error: {str(e)}"
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
    return jsonify({"status": "ok", "message": "VIX futures webhook service is healthy"}), 200

@app.route('/ib-status', methods=['GET'])
def ib_status():
    """Detailed IB Gateway connection status"""
    try:
        import socket
        # Socket check
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((IB_HOST, IB_PORT))
        sock.close()
        
        if result == 0:
            # Additional connection test with ib_insync
            try:
                # Handle event loop for threading
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                ib = IB()
                ib.connect(IB_HOST, IB_PORT, clientId=999, timeout=5)
                account_summary = ib.accountSummary() if ib.isConnected() else []
                ib.disconnect()
                
                return jsonify({
                    "status": "ok", 
                    "ib_gateway": "connected",
                    "port": IB_PORT,
                    "accounts_available": len(account_summary) > 0,
                    "timestamp": time.time()
                }), 200
            except Exception as e:
                return jsonify({
                    "status": "ok", 
                    "ib_gateway": "port_open_but_api_failed",
                    "error": str(e),
                    "timestamp": time.time()
                }), 200
        else:
            return jsonify({
                "status": "ok", 
                "ib_gateway": "disconnected", 
                "reason": "port_not_open",
                "port": IB_PORT,
                "timestamp": time.time()
            }), 200
    except Exception as e:
        return jsonify({
            "status": "ok", 
            "ib_gateway": "error", 
            "error": str(e),
            "timestamp": time.time()
        }), 200

@app.route('/test', methods=['GET'])
def test():
    """Test endpoint for debugging"""
    return jsonify({
        "status": "ok",
        "service": "VIX Futures Trading Webhook",
        "config": {
            "IB_HOST": IB_HOST,
            "IB_PORT": IB_PORT,
            "ACCOUNT_ID": ACCOUNT_ID[:4] + "***" if ACCOUNT_ID else None
        },
        "endpoints": {
            "trading": "POST /bgf (with 'orders' array)",
            "health": "GET /health",
            "ib_status": "GET /ib-status"
        },
        "examples": {
            "position_based": '{"orders": [{"symbol": "VX-Oct-25", "position": -2, "order_type": "MKT"}]}',
            "action_based": '{"orders": [{"action": "SELL", "symbol": "VX-Oct-25", "qty": 2, "order_type": "MKT"}]}'
        },
        "message": "VIX futures webhook service is running"
    }), 200

if __name__ == "__main__":
    logger.info("Starting VIX futures trading webhook service...")
    app.run(host="0.0.0.0", port=5000, debug=False)
