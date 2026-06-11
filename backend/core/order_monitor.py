import asyncio
from datetime import datetime
from core.kite import kite

class OrderMonitor:
    """Monitors pending orders and handles stuck orders"""
    
    def __init__(self, log_debug_func):
        self.log_debug = log_debug_func
        self.pending_orders = {}
        self.monitoring = False
    
    async def add_order(self, order_id, tradingsymbol, timeout_seconds=5):
        """Add order to monitoring"""
        self.pending_orders[order_id] = {
            "symbol": tradingsymbol,
            "placed_at": datetime.now(),
            "timeout": timeout_seconds
        }
    
    async def start_monitoring(self):
        """Start monitoring loop"""
        self.monitoring = True
        while self.monitoring:
            await self._check_pending_orders()
            await asyncio.sleep(1)
    
    async def stop_monitoring(self):
        """Stop monitoring"""
        self.monitoring = False
    
    async def _check_pending_orders(self):
        """Check all pending orders for timeout"""
        if not self.pending_orders:
            return
        
        orders_to_remove = []
        
        for order_id, info in self.pending_orders.items():
            elapsed = (datetime.now() - info["placed_at"]).total_seconds()
            
            if elapsed > info["timeout"]:
                await self.log_debug("OrderMonitor", 
                    f"⚠️ Order {order_id} ({info['symbol']}) stuck for {elapsed:.1f}s. Cancelling...")
                
                try:
                    def cancel_sync():
                        return kite.cancel_order(kite.VARIETY_REGULAR, order_id)
                    
                    await asyncio.to_thread(cancel_sync)
                    await self.log_debug("OrderMonitor", f"✅ Cancelled stuck order {order_id}")
                except Exception as e:
                    await self.log_debug("OrderMonitor", f"❌ Failed to cancel {order_id}: {e}")
                
                orders_to_remove.append(order_id)
        
        for order_id in orders_to_remove:
            del self.pending_orders[order_id]
    
    def remove_order(self, order_id):
        """Remove order from monitoring (filled/cancelled)"""
        self.pending_orders.pop(order_id, None)
