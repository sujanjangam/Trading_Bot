import asyncio
from fastapi import HTTPException
from .strategy import Strategy
from .kite_ticker_manager import KiteTickerManager
from .websocket_manager import manager
from .kite import re_initialize_session_from_file, kite

class TradingBotService:
    _instance = None

    def __init__(self):
        self.strategy_instance: Strategy | None = None
        self.ticker_manager_instance: KiteTickerManager | None = None
        self.uoa_scanner_task: asyncio.Task | None = None
        self.liquidity_fetcher_task: asyncio.Task | None = None
        self.bot_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def uoa_scanner_worker(self):
        while True:
            try:
                if self.strategy_instance and self.strategy_instance.params.get('auto_scan_uoa'):
                    await self.strategy_instance.scan_for_unusual_activity()
                await asyncio.sleep(300)
            except asyncio.CancelledError: break
            except Exception as e: print(f"Error in UOA scanner worker: {e}"); await asyncio.sleep(60)

    async def liquidity_fetcher_worker(self):
        """Auto-fetch liquidity data for stock options mode"""
        while True:
            try:
                if self.strategy_instance and self.strategy_instance.config.get("trading_mode") == "stock_options":
                    nifty50_symbols = [
                        "RELIANCE", "TCS", "HDFCBANK", "BHARTIARTL", "ICICIBANK", "INFOSYS", "SBIN", 
                        "ITC", "HINDUNILVR", "LT", "HCLTECH", "MARUTI", "SUNPHARMA", "TITAN"
                    ]
                    
                    quotes = await asyncio.to_thread(kite.quote, [f"NSE:{s}" for s in nifty50_symbols])
                    
                    stocks_data = []
                    for symbol in nifty50_symbols:
                        key = f"NSE:{symbol}"
                        if key in quotes:
                            q = quotes[key]
                            ohlc = q.get('ohlc', {})
                            change_pct = ((q.get('last_price', 0) - ohlc.get('close', 1)) / ohlc.get('close', 1)) * 100
                            
                            stocks_data.append({
                                "stock": symbol,
                                "futPrice": f"{q.get('last_price', 0):.2f}",
                                "priceChange": f"{change_pct:+.2f}%",
                                "weightage": 1.0,
                                "atmIV": "--",
                                "open": f"{ohlc.get('open', 0):.2f}",
                                "high": f"{ohlc.get('high', 0):.2f}",
                                "low": f"{ohlc.get('low', 0):.2f}",
                                "close": f"{ohlc.get('close', 0):.2f}"
                            })
                    
                    stocks_data.sort(key=lambda x: abs(float(x["priceChange"].replace("%", "").replace("+", ""))), reverse=True)
                    top_movers = stocks_data[:10]
                    
                    await self.strategy_instance.update_liquidity_data(top_movers)
                    
                await asyncio.sleep(5)
            except asyncio.CancelledError: 
                break
            except Exception as e: 
                print(f"Error in liquidity fetcher: {e}")
                await asyncio.sleep(10)

    async def start_bot(self, params, selected_index):
        async with self.bot_lock:
            if self.ticker_manager_instance and self.ticker_manager_instance.is_connected:
                raise HTTPException(status_code=400, detail="Bot is already running.")
            
            try:
                main_loop = asyncio.get_running_loop()
                self.strategy_instance = Strategy(params=params, manager=manager, selected_index=selected_index)
                self.ticker_manager_instance = KiteTickerManager(self.strategy_instance, main_loop)
                self.strategy_instance.ticker_manager = self.ticker_manager_instance
                await self.strategy_instance.run()

                self.ticker_manager_instance.start()
                await asyncio.wait_for(self.ticker_manager_instance.connected_event.wait(), timeout=15)
                
                if not self.ticker_manager_instance.is_connected:
                     raise Exception("Ticker failed to connect after start attempt.")

                if not self.uoa_scanner_task or self.uoa_scanner_task.done():
                    self.uoa_scanner_task = asyncio.create_task(self.uoa_scanner_worker())

                # Start liquidity fetcher for stock options mode
                if self.strategy_instance.config.get("trading_mode") == "stock_options":
                    if not self.liquidity_fetcher_task or self.liquidity_fetcher_task.done():
                        self.liquidity_fetcher_task = asyncio.create_task(self.liquidity_fetcher_worker())
                        print("✅ Auto-liquidity fetcher started for Stock Options mode")

                # Load cached liquidity data if available
                from main import _liquidity_data_cache
                if _liquidity_data_cache:
                    await self.strategy_instance.update_liquidity_data(_liquidity_data_cache)
                    print(f"Loaded {len(_liquidity_data_cache)} cached liquidity stocks")

                await self.strategy_instance._update_ui_status()
                
                print("Bot started successfully and ticker is connected.")
                return {"status": "success", "message": "Bot started and connected."}

            except asyncio.TimeoutError:
                await self._cleanup_bot_state()
                raise HTTPException(status_code=504, detail="Ticker connection timed out.")
            except Exception as e:
                await self._cleanup_bot_state()
                raise HTTPException(status_code=500, detail=str(e))

    async def stop_bot(self):
        async with self.bot_lock:
            if not (self.ticker_manager_instance and self.ticker_manager_instance.is_connected):
                raise HTTPException(status_code=400, detail="Bot is not running.")

            if self.strategy_instance and self.strategy_instance.position:
                await self.strategy_instance.exit_position("Bot Stopped by User")
                await asyncio.sleep(1)
            
            await self._cleanup_bot_state()
            print("Bot stopped successfully.")
            
            # Send final disconnected status
            await manager.broadcast({"type": "status_update", "payload": {
                "connection": "DISCONNECTED", "mode": "NOT STARTED", "is_running": False,
                "is_paused": False, "indexPrice": 0, "trend": "---", "indexName": "INDEX"
            }})

            # Forcefully close the WebSocket to the frontend
            await manager.close()

            # --- NEW LINE ---
            # Proactively reload the token from the file to restore the session.
            re_initialize_session_from_file()

        return {"status": "success", "message": "Bot stopped."}

    async def pause_bot(self):
        if not self.strategy_instance:
            raise HTTPException(status_code=400, detail="Bot is not running.")
        
        self.strategy_instance.is_paused = True
        await self.strategy_instance._log_debug("System", "🚫 Bot paused. No new trades will be taken.")
        await self.strategy_instance._update_ui_status()
        return {"status": "success", "message": "Bot paused. No new trades will be taken."}

    async def unpause_bot(self):
        if not self.strategy_instance:
            raise HTTPException(status_code=400, detail="Bot is not running.")
        
        self.strategy_instance.is_paused = False
        await self.strategy_instance._log_debug("System", "✅ Bot resumed. Trading enabled.")
        await self.strategy_instance._update_ui_status()
        return {"status": "success", "message": "Bot resumed. Trading enabled."}

    async def manual_exit_trade(self):
        if not self.strategy_instance:
            raise HTTPException(status_code=400, detail="Bot is not running.")
        if not self.strategy_instance.position:
            raise HTTPException(status_code=400, detail="No active trade to exit.")
        
        await self.strategy_instance.exit_position("Manual Exit from UI")
        return {"status": "success", "message": "Manual exit signal sent."}

    async def add_to_watchlist(self, side, strike):
        if self.strategy_instance and side and strike is not None:
            await self.strategy_instance.add_to_watchlist(side, strike)

    async def _cleanup_bot_state(self):
        if self.ticker_manager_instance:
            await self.ticker_manager_instance.stop()
        if self.strategy_instance and self.strategy_instance.ui_update_task:
            self.strategy_instance.ui_update_task.cancel()
        if self.uoa_scanner_task:
            self.uoa_scanner_task.cancel()
        if self.liquidity_fetcher_task:
            self.liquidity_fetcher_task.cancel()
        
        self.ticker_manager_instance = None
        self.strategy_instance = None
        self.uoa_scanner_task = None
        self.liquidity_fetcher_task = None

async def get_bot_service():
    return await TradingBotService.get_instance()