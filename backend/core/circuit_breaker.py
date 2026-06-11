import asyncio
from datetime import datetime, timedelta
from collections import deque

class CircuitBreaker:
    """Global circuit breaker to prevent bot from spamming during broker glitches"""
    
    def __init__(self, log_debug_func, error_threshold=3, time_window=60):
        self.log_debug = log_debug_func
        self.error_threshold = error_threshold
        self.time_window = time_window
        self.errors = deque()
        self.is_open = False
        self.stop_callback = None
    
    def set_stop_callback(self, callback):
        """Set callback to stop bot when circuit breaker trips"""
        self.stop_callback = callback
    
    async def record_error(self, error_type, error_message):
        """Record an error and check if circuit breaker should trip"""
        now = datetime.now()
        self.errors.append({"time": now, "type": error_type, "message": error_message})
        
        # Remove old errors outside time window
        cutoff = now - timedelta(seconds=self.time_window)
        while self.errors and self.errors[0]["time"] < cutoff:
            self.errors.popleft()
        
        # Check if threshold exceeded
        if len(self.errors) >= self.error_threshold:
            await self._trip_breaker()
    
    async def _trip_breaker(self):
        """Trip the circuit breaker and stop bot"""
        if self.is_open:
            return
        
        self.is_open = True
        error_summary = ", ".join([e["type"] for e in list(self.errors)[-3:]])
        
        await self.log_debug("CircuitBreaker", 
            f"🚨 CIRCUIT BREAKER TRIPPED! {len(self.errors)} errors in {self.time_window}s: {error_summary}")
        await self.log_debug("CircuitBreaker", 
            "⛔ Auto-stopping bot to prevent order spam during broker glitch")
        
        if self.stop_callback:
            await self.stop_callback()
    
    def reset(self):
        """Reset circuit breaker"""
        self.errors.clear()
        self.is_open = False
    
    def get_status(self):
        """Get current circuit breaker status"""
        return {
            "is_open": self.is_open,
            "error_count": len(self.errors),
            "threshold": self.error_threshold,
            "time_window": self.time_window
        }
