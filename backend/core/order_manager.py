# backend/core/order_manager.py
import asyncio
from core.kite import kite
import math # ADDED: For rounding

# ADDED: A utility function to round the price to the nearest valid tick (usually 0.05 for options)
def _round_to_tick(price, tick_size=0.05):
    """Rounds a price to the nearest valid tick size."""
    return round(round(price / tick_size) * tick_size, 2)

# ADDED: Function to calculate tolerance based on price
def _calculate_tolerance(price):
    """
    Calculate tolerance based on price range:
    - If price > Rs.100: tolerance = 0.50 to 1.00 rupee
    - If price < Rs.100: tolerance = 0.5% to 1% of price
    """
    if price > 100:
        # For prices above Rs.100, use fixed rupee tolerance (0.50 to 1.00)
        base_tolerance = 0.50
        additional_tolerance = min(0.50, (price - 100) * 0.005)  # Scale up to 1.00 max
        return base_tolerance + additional_tolerance
    else:
        # For prices below Rs.100, use percentage tolerance (0.5% to 1%)
        percentage = 0.005 + (price / 100) * 0.005  # 0.5% to 1% based on price
        percentage = min(0.01, percentage)  # Cap at 1%
        return price * percentage

# ADDED: Function to apply tolerance to limit price
def _apply_tolerance_to_limit_price(base_price, transaction_type):
    """
    Apply tolerance to limit price:
    - BUY orders: base_price + tolerance (buy slightly higher for better fill)
    - SELL orders: base_price - tolerance (sell slightly lower for better fill)
    """
    tolerance = _calculate_tolerance(base_price)
    
    if transaction_type == "BUY":
        final_price = base_price + tolerance
    else:  # SELL
        final_price = base_price - tolerance
    
    # Ensure price doesn't go negative and round to tick
    final_price = max(0.05, final_price)
    return _round_to_tick(final_price)

class OrderManager:
    """
    Handles the execution and verification of orders to make them more robust.
    """
    def __init__(self, log_debug_func):
        self.log_debug = log_debug_func

    # CHANGED: The function now accepts order_type and price for more flexibility with tolerance
    async def execute_order(self, transaction_type, order_type=kite.ORDER_TYPE_MARKET, price=None, apply_tolerance=True, **kwargs):
        """
        Places an order and then enters a loop to verify its status.
        Can handle both MARKET and LIMIT orders with tolerance.
        
        Args:
            transaction_type: BUY or SELL
            order_type: MARKET or LIMIT
            price: Base price for limit orders
            apply_tolerance: Whether to apply tolerance to limit orders (default: True)
        """
        MAX_RETRIES =  1 # OPTIMIZED: Reduced from 3 to 2
        RETRY_DELAY_SECONDS = 0.5  # OPTIMIZED: Reduced from 2 to 0.5 seconds
        VERIFICATION_TIMEOUT_SECONDS = 3  # OPTIMIZED: Reduced from 15 to 3 seconds (most orders fill in <1s)

        for attempt in range(MAX_RETRIES):
            try:
                # --- 1. Place the initial order ---
                def place_order_sync():
                    # Build the order parameters dictionary
                    order_params = {
                        "variety": kite.VARIETY_REGULAR,
                        "order_type": order_type,
                        "product": kite.PRODUCT_MIS,
                        "transaction_type": transaction_type,
                        **kwargs
                    }
                    # If it's a LIMIT order, add the price with tolerance
                    if order_type == kite.ORDER_TYPE_LIMIT:
                        if price is None or price <= 0:
                            raise ValueError("A valid price must be provided for LIMIT orders.")
                        
                        if apply_tolerance:
                            # Apply tolerance to the limit price for better fill rates
                            tolerance_price = _apply_tolerance_to_limit_price(price, transaction_type)
                            order_params["price"] = tolerance_price
                        else:
                            order_params["price"] = _round_to_tick(price)
                    
                    return kite.place_order(**order_params)
                
                order_id = await asyncio.to_thread(place_order_sync)
                
                # Enhanced logging with tolerance info
                if order_type == kite.ORDER_TYPE_LIMIT and apply_tolerance:
                    tolerance = _calculate_tolerance(price)
                    final_price = _apply_tolerance_to_limit_price(price, transaction_type)
                    log_price = f"at limit {final_price} (base: {price}, tolerance: ±{tolerance:.3f})"
                elif order_type == kite.ORDER_TYPE_LIMIT:
                    log_price = f"at limit {_round_to_tick(price)}"
                else:
                    log_price = "at MARKET"
                await self.log_debug("OrderManager", f"Placed {transaction_type} {order_type} order for {kwargs.get('tradingsymbol')} {log_price}. ID: {order_id}. Verifying status...")

                # --- 2. Fast order verification with quick checks ---
                start_time = asyncio.get_event_loop().time()
                check_count = 0
                max_quick_checks = 20  # 20 checks × 0.1s = 2 seconds max for quick fills
                
                while True:
                    check_count += 1
                    
                    def get_order_history_sync():
                        return kite.order_history(order_id=order_id)

                    order_history = await asyncio.to_thread(get_order_history_sync)
                    
                    if order_history:
                        latest_status = order_history[-1]['status']
                        if latest_status == "COMPLETE":
                            execution_time = (asyncio.get_event_loop().time() - start_time) * 1000
                            await self.log_debug("OrderManager", f"⚡ Order {order_id} FILLED in {execution_time:.0f}ms!")
                            return "COMPLETE"
                        
                        if latest_status in ["REJECTED", "CANCELLED"]:
                            rejection_reason = order_history[-1].get('status_message', 'No reason provided.')
                            await self.log_debug("OrderManager", f"Order {order_id} was {latest_status}. Reason: {rejection_reason}. Retrying...")
                            break
                    
                    # Quick checks for first 2 seconds (most orders fill within 100-500ms)
                    if check_count <= max_quick_checks:
                        await asyncio.sleep(0.1)  # Check every 100ms
                    else:
                        # After 2 seconds, check every 500ms until timeout
                        if (asyncio.get_event_loop().time() - start_time) > VERIFICATION_TIMEOUT_SECONDS:
                            await self.log_debug("OrderManager", f"Order {order_id} timed out after {VERIFICATION_TIMEOUT_SECONDS}s. Cancelling and retrying...")
                            break
                        await asyncio.sleep(0.5)  # Slower checks after 2 seconds
            
            except Exception as e:
                await self.log_debug("OrderManager-ERROR", f"Attempt {attempt + 1}/{MAX_RETRIES} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                else:
                    await self.log_debug("OrderManager-CRITICAL", f"Order for {kwargs.get('tradingsymbol')} failed after {MAX_RETRIES} retries.")
                    raise

    # ADDED: Convenience method for placing limit orders with tolerance
    async def execute_limit_order_with_tolerance(self, transaction_type, base_price, **kwargs):
        """
        Convenience method to place limit orders with automatic tolerance application.
        """
        return await self.execute_order(
            transaction_type=transaction_type,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=base_price,
            apply_tolerance=True,
            **kwargs
        )

    # ADDED: Method to preview tolerance calculation
    def preview_tolerance(self, base_price, transaction_type):
        """
        Preview what the final limit price will be with tolerance applied.
        Useful for debugging and strategy development.
        """
        tolerance = _calculate_tolerance(base_price)
        final_price = _apply_tolerance_to_limit_price(base_price, transaction_type)
        
        return {
            "base_price": base_price,
            "tolerance": tolerance,
            "final_price": final_price,
            "price_category": "> Rs.100" if base_price > 100 else "< Rs.100",
            "tolerance_type": "Fixed Rs." if base_price > 100 else "Percentage"
        }
    
    # ✅ BASKET ORDER: Execute multiple orders atomically (better than sequential)
    async def execute_basket_order(self, quantity, transaction_type, tradingsymbol, exchange, freeze_limit=None, price=None):
        """
        Execute order as a basket when quantity exceeds freeze limit.
        Automatically slices and places all orders simultaneously for better fills.
        
        Args:
            quantity: Total quantity to trade
            transaction_type: BUY or SELL
            tradingsymbol: Trading symbol
            exchange: Exchange (NSE/NFO)
            freeze_limit: Exchange freeze limit (optional - will use single order if None or qty within limit)
            price: Current market price for logging
            
        Returns:
            dict: {
                "status": "COMPLETE" or "PARTIAL" or "FAILED",
                "total_filled": total quantity filled,
                "orders": list of order results
            }
        """
        if not quantity or quantity <= 0:
            await self.log_debug("BasketOrder", "❌ Invalid quantity for basket order.")
            return {"status": "FAILED", "total_filled": 0, "orders": []}
        
        # Determine if slicing is needed
        orders_list = []
        
        if freeze_limit and quantity > freeze_limit:
            # Need to slice - calculate order quantities
            num_orders = math.ceil(quantity / freeze_limit)
            remaining_qty = quantity
            
            await self.log_debug("BasketOrder", 
                f"🔪 Slicing {quantity} qty into {num_orders} orders (freeze limit: {freeze_limit})")
            
            for i in range(num_orders):
                order_qty = min(remaining_qty, freeze_limit)
                orders_list.append(order_qty)
                remaining_qty -= order_qty
                await self.log_debug("BasketOrder", f"  Slice {i+1}/{num_orders}: {order_qty} qty")
        else:
            # Single order - no slicing needed
            orders_list.append(quantity)
            await self.log_debug("BasketOrder", f"📦 Single order: {quantity} qty (within freeze limit)")
        
        # Execute based on order count
        if len(orders_list) == 1:
            # Single order - use regular fast execution
            await self.log_debug("BasketOrder", f"Using regular execution for single order.")
            status = await self.execute_order(
                transaction_type=transaction_type,
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                quantity=orders_list[0]
            )
            return {
                "status": status,
                "total_filled": orders_list[0] if status == "COMPLETE" else 0,
                "orders": [{"qty": orders_list[0], "status": status}]
            }
        
        # Multiple orders - use parallel basket execution
        await self.log_debug("BasketOrder", 
            f"🧺 Executing {len(orders_list)} orders in PARALLEL: {orders_list} (Total: {sum(orders_list)} qty)")
        
        # Prepare all order parameters
        order_params_list = []
        for idx, qty in enumerate(orders_list):
            order_params = {
                "variety": kite.VARIETY_REGULAR,
                "order_type": kite.ORDER_TYPE_MARKET,
                "product": kite.PRODUCT_MIS,
                "transaction_type": transaction_type,
                "tradingsymbol": tradingsymbol,
                "exchange": exchange,
                "quantity": qty
            }
            order_params_list.append(order_params)
        
        # Execute all orders simultaneously using asyncio.gather
        async def place_single_order(params, order_num):
            try:
                def place_sync():
                    return kite.place_order(**params)
                
                order_id = await asyncio.to_thread(place_sync)
                
                # Quick verification (2 second timeout)
                start_time = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - start_time) < 2.0:
                    def get_history():
                        return kite.order_history(order_id=order_id)
                    
                    history = await asyncio.to_thread(get_history)
                    if history:
                        status = history[-1]['status']
                        if status == "COMPLETE":
                            return {
                                "order_num": order_num,
                                "qty": params["quantity"],
                                "status": "COMPLETE",
                                "order_id": order_id
                            }
                        elif status in ["REJECTED", "CANCELLED"]:
                            return {
                                "order_num": order_num,
                                "qty": params["quantity"],
                                "status": status,
                                "order_id": order_id,
                                "reason": history[-1].get('status_message', 'Unknown')
                            }
                    await asyncio.sleep(0.1)
                
                # Timeout - assume pending
                return {
                    "order_num": order_num,
                    "qty": params["quantity"],
                    "status": "PENDING",
                    "order_id": order_id
                }
                
            except Exception as e:
                return {
                    "order_num": order_num,
                    "qty": params["quantity"],
                    "status": "FAILED",
                    "error": str(e)
                }
        
        # Place all orders concurrently
        results = await asyncio.gather(
            *[place_single_order(params, i+1) for i, params in enumerate(order_params_list)],
            return_exceptions=True
        )
        
        # Process results
        total_filled = 0
        completed = 0
        failed = 0
        
        for result in results:
            if isinstance(result, dict):
                if result["status"] == "COMPLETE":
                    total_filled += result["qty"]
                    completed += 1
                    await self.log_debug("BasketOrder", 
                        f"✅ Order {result['order_num']}/{len(orders_list)}: {result['qty']} qty FILLED (ID: {result.get('order_id')})")
                elif result["status"] in ["REJECTED", "CANCELLED"]:
                    failed += 1
                    await self.log_debug("BasketOrder", 
                        f"❌ Order {result['order_num']}/{len(orders_list)}: {result['qty']} qty {result['status']} - {result.get('reason', 'Unknown')}")
                else:
                    await self.log_debug("BasketOrder", 
                        f"⏳ Order {result['order_num']}/{len(orders_list)}: {result['qty']} qty {result['status']}")
        
        # Final status
        if completed == len(orders_list):
            status = "COMPLETE"
            await self.log_debug("BasketOrder", 
                f"🎉 ALL {len(orders_list)} basket orders FILLED! Total: {total_filled} qty @ ₹{price:.2f}")
        elif completed > 0:
            status = "PARTIAL"
            await self.log_debug("BasketOrder", 
                f"⚠️ PARTIAL fill: {completed}/{len(orders_list)} orders filled. Total: {total_filled} qty")
        else:
            status = "FAILED"
            await self.log_debug("BasketOrder", 
                f"❌ BASKET FAILED: No orders filled out of {len(orders_list)}")
        
        return {
            "status": status,
            "total_filled": total_filled,
            "orders": results
        } 