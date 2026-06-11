# backend/core/active_stock_tracker.py

class ActiveStockTracker:
    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        self.active_stock_symbol = None
        
    async def update_from_trade(self, trigger, symbol):
        """Update active stock symbol when a new trade is taken"""
        trading_mode = self.strategy.config.get("trading_mode", "index_options")
        
        if trading_mode == "stock_options":
            # Extract stock symbol from option symbol (e.g., HEROMOTOCO25DEC5700CE -> HEROMOTOCO)
            import re
            match = re.match(r'^([A-Z&]+)', symbol)
            stock_symbol = match.group(1) if match else None
            
            if stock_symbol and stock_symbol != self.active_stock_symbol:
                self.active_stock_symbol = stock_symbol
                # Clear option chain subscription cache to force re-subscription
                if hasattr(self.strategy, '_option_chain_subscribed'):
                    self.strategy._option_chain_subscribed = None
                await self.strategy._log_debug("Active Stock", f"📈 Chart switched to {stock_symbol}")
                await self.strategy._log_debug("OptionChain", f"🔄 Cache cleared for {stock_symbol} - will re-subscribe")
                
                # 🔥 DYNAMIC SUBSCRIPTION: Subscribe to stock spot + option contracts
                await self._subscribe_to_stock_options(stock_symbol)
                
                # Broadcast active stock change to frontend with option symbol
                stock_price = self.strategy.data_manager.prices.get(f"NSE:{stock_symbol}", 0)
                await self.strategy._log_debug("Active Stock", f"📡 Broadcasting: {symbol} (stock: {stock_symbol})")
                await self.strategy.manager.broadcast({
                    "type": "active_stock_update",
                    "payload": {
                        "symbol": stock_symbol,
                        "optionSymbol": symbol,  # The actual option contract symbol
                        "price": stock_price,
                        "mode": "stock_options"
                    }
                })
        elif trading_mode == "equity":
            # For direct equity trades
            stock_symbol = symbol.replace("NSE:", "") if symbol.startswith("NSE:") else symbol
                
            if stock_symbol != self.active_stock_symbol:
                self.active_stock_symbol = stock_symbol
                await self.strategy._log_debug("Active Stock", f"📈 Chart switched to {stock_symbol}")
                
                await self.strategy.manager.broadcast({
                    "type": "active_stock_update",
                    "payload": {
                        "symbol": stock_symbol,
                        "price": self.strategy.data_manager.prices.get(f"NSE:{stock_symbol}", 0),
                        "mode": "equity"
                    }
                })
    
    async def _subscribe_to_stock_options(self, stock_symbol):
        """Subscribe to stock spot price + option contracts dynamically"""
        if not self.strategy.ticker_manager or not self.strategy.ticker_manager.is_connected:
            return
        
        tokens_to_subscribe = []
        
        # 1️⃣ Subscribe to stock spot (NSE equity token)
        from .stock_token_cache import get_stock_tokens
        from .kite import kite
        stock_tokens = get_stock_tokens(kite)
        if stock_symbol in stock_tokens:
            tokens_to_subscribe.append(stock_tokens[stock_symbol])
            await self.strategy._log_debug("WebSocket", f"✅ Subscribing to {stock_symbol} spot (NSE:{stock_symbol})")
        
        # 2️⃣ Subscribe to option contracts (ATM ± 3 strikes)
        stock_price = self.strategy.data_manager.prices.get(f"NSE:{stock_symbol}")
        if stock_price and stock_price > 0:
            strike_interval = self.strategy._get_stock_strike_interval(stock_price)
            atm_strike = round(stock_price / strike_interval) * strike_interval
            strikes = [atm_strike + (i - 3) * strike_interval for i in range(7)]
            
            for strike in strikes:
                for side in ['CE', 'PE']:
                    opt = self.strategy.get_stock_option(stock_symbol, side, strike)
                    if opt and opt.get('instrument_token'):
                        tokens_to_subscribe.append(opt['instrument_token'])
            
            await self.strategy._log_debug("WebSocket", f"✅ Subscribing to {len(tokens_to_subscribe)-1} option contracts for {stock_symbol}")
        
        # Subscribe with FULL mode for OI + depth data
        if tokens_to_subscribe:
            self.strategy.ticker_manager.subscribe(tokens_to_subscribe, mode='FULL')
            await self.strategy.map_option_tokens(tokens_to_subscribe)