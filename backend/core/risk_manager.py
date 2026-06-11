import math
import asyncio

class RiskManager:
    """Handles position sizing and risk calculations."""
    def __init__(self, params, log_debug_func):
        self.params = params
        self.log_debug = log_debug_func
        self.pending_logs = []  # Store logs to be sent by caller

    def _queue_log(self, source, message):
        """Queue a log message to be sent by the caller (avoids duplicate asyncio.create_task issues)"""
        self.pending_logs.append((source, message))

    def calculate_trade_details(self, price, lot_size, available_cash=None, daily_pnl=0):
        """
        Hybrid Capital Logic: Combines live Zerodha capital with GUI threshold.
        
        Capital Selection Rules:
        1. Fetch live capital from Zerodha
        2. Use GUI threshold as maximum limit
        3. Use min(live_capital, gui_threshold) for position sizing
        4. If live capital drops below threshold, trade with lesser capital
        5. Apply Smart Capital rules for daily P&L adjustments
        """
        # Clear pending logs from previous calls
        self.pending_logs = []
        
        # Get GUI threshold from params (user's risk limit)
        gui_threshold = float(self.params.get("start_capital", 50000))
        
        # Hybrid Capital Logic
        if available_cash is not None:
            # Live capital fetched from Zerodha
            # Use the minimum of live capital and GUI threshold
            base_capital = min(available_cash, gui_threshold)
            capital_source = "Hybrid (Live + GUI Threshold)"
            
            # Queue log for comparison visibility (only if there's a meaningful difference)
            if available_cash < gui_threshold:
                self._queue_log("Capital", 
                    f"⚠️ Live capital (₹{available_cash:.0f}) below GUI threshold (₹{gui_threshold:.0f}). Using live capital.")
            # Removed the "sufficient" log to reduce noise
        else:
            # Fallback: Use GUI threshold only (if Zerodha fetch failed)
            base_capital = gui_threshold
            capital_source = "GUI Threshold (Zerodha fetch failed)"
            # Only log this once during fetch, not here
        
        # Apply V47.14 Smart Capital adjustments for daily P&L
        current_real_time_capital = base_capital + daily_pnl
        # Use minimum of base capital and current capital
        # - On profit: uses base capital (no compounding)
        # - On loss: uses reduced capital (de-leveraging)
        effective_capital = min(base_capital, current_real_time_capital)
        
        capital_to_use = effective_capital
        
        risk_percent = float(self.params.get("risk_per_trade_percent", 1.0))
        sl_points = float(self.params.get("trailing_sl_points", 5.0))
        sl_percent = float(self.params.get("trailing_sl_percent", 10.0))

        if price is None or price < 1.0 or lot_size is None:
            self._queue_log("Risk", f"Invalid price/lot_size: P={price}, L={lot_size}")
            return None, None

        initial_sl_price = max(price - sl_points, price * (1 - sl_percent / 100))
        risk_per_share = price - initial_sl_price

        if risk_per_share <= 0:
            self._queue_log("Risk", f"Cannot calculate quantity. Risk per share is zero or negative.")
            return None, None
        
        # Calculate lots based on effective capital
        value_per_lot = price * lot_size
        if value_per_lot <= 0:
            self._queue_log("Risk", "Trade Aborted. Invalid price or lot size.")
            return None, None
            
        max_lots_by_capital = math.floor(capital_to_use / value_per_lot)
        
        # Risk-based calculation (always use base capital for risk calculation)
        risk_amount_per_trade = base_capital * (risk_percent / 100)  # Always use base capital for risk
        risk_per_lot = risk_per_share * lot_size
        num_lots_by_risk = math.floor(risk_amount_per_trade / risk_per_lot) if risk_per_lot > 0 else 0

        if num_lots_by_risk == 0:
            if capital_to_use > price * lot_size:
                num_lots_by_risk = 1 # Default to 1 lot if capital allows but risk doesn't
            else:
                self._queue_log("Risk", f"Insufficient capital to take even 1 lot.")
                return None, None

        # The final number of lots is the minimum of what risk allows and what capital allows
        final_num_lots = min(num_lots_by_risk, max_lots_by_capital)

        if final_num_lots < num_lots_by_risk:
            capital_type = "Hybrid" if available_cash is not None else "Smart"
            self._queue_log("Risk", f"Position sizing: {final_num_lots} lots ({capital_type} Capital: ₹{capital_to_use:.0f})")
        
        if final_num_lots == 0:
            self._queue_log("Risk", "Trade Aborted. Final calculated lots is zero.")
            return None, None
            
        qty = final_num_lots * lot_size
        return qty, initial_sl_price
