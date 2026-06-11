# backend/core/strategy.py
import asyncio
import json
import pandas as pd
from datetime import datetime, date, timedelta, time
from typing import TYPE_CHECKING, Optional
import math
import numpy as np

from .kite import kite
from .websocket_manager import ConnectionManager
from .data_manager import DataManager
from .risk_manager import RiskManager
from .trade_logger import TradeLogger
from .order_manager import OrderManager, _round_to_tick
from .database import today_engine, sql_text
from .entry_strategies import (
    IntraCandlePatternStrategy,
    UoaEntryStrategy,
    TrendContinuationStrategy,
    MaCrossoverStrategy,
    CandlePatternEntryStrategy,
    EquityTrendStrategy
)

if TYPE_CHECKING:
    from .kite_ticker_manager import KiteTickerManager

def _play_sound(manager, sound):
    asyncio.create_task(manager.broadcast({"type": "play_sound", "payload": sound}))

INDEX_CONFIG = {
    "NIFTY": {"name": "NIFTY", "token": 256265, "symbol": "NSE:NIFTY 50", "strike_step": 50, "exchange": "NFO", "trading_mode": "index_options"},
    "SENSEX": {"name": "SENSEX", "token": 265, "symbol": "BSE:SENSEX", "strike_step": 100, "exchange": "BFO", "trading_mode": "index_options"},
    "BANKNIFTY": {"name": "BANKNIFTY", "token": 260105, "symbol": "NSE:NIFTY BANK", "strike_step": 100, "exchange": "NFO", "trading_mode": "index_options"},
    "Liquidity Stock Options (NIFTY & SENSEX)": {"name": "STOCK_OPTIONS", "token": 256265, "symbol": "NSE:NIFTY 50", "strike_step": 50, "exchange": "NFO", "trading_mode": "stock_options"},
    "High Volume Liquidity Stock (ON SPREAD VOLUME)": {"name": "NIFTY 50", "token": 256265, "symbol": "NSE:NIFTY 50", "strike_step": 50, "exchange": "NFO", "trading_mode": "high_volume_options"},
}

MARKET_STANDARD_PARAMS = {
    "strategy_priority": ["EQUITY_TREND"],
    'wma_period': 9, 'sma_period': 9, 'rsi_period': 9, 'rsi_signal_period': 3,
    'rsi_angle_lookback': 2, 'rsi_angle_threshold': 15.0, 'atr_period': 14,
    'min_atr_value': 4, 'ma_gap_threshold_pct': 0.05
}


class Strategy:
    def __init__(self, params, manager: ConnectionManager, selected_index="SENSEX"):
        self.params = self._sanitize_params(params)
        self.manager = manager
        self.ticker_manager: Optional["KiteTickerManager"] = None
        print(f"🔧 Strategy.__init__: selected_index = '{selected_index}'")
        print(f"🔧 Config loaded: {INDEX_CONFIG.get(selected_index, {})}")
        self.config = INDEX_CONFIG[selected_index]
        self.ui_update_task: Optional[asyncio.Task] = None
        self.position_lock = asyncio.Lock()
        self.db_lock = asyncio.Lock()

        self.is_backtest = False
        self.is_paused = False  # Pause functionality

        self.index_name, self.index_token, self.index_symbol, self.strike_step, self.exchange = \
            self.config["name"], self.config["token"], self.config["symbol"], self.config["strike_step"], self.config["exchange"]

        self.trend_candle_count = 0

        self.data_manager = DataManager(self.index_token, self.index_symbol, self.STRATEGY_PARAMS, self._log_debug, self.on_trend_update)
        self.data_manager.strategy = self  # ⚡ Link for ATM pre-fetching optimization
        self.risk_manager = RiskManager(self.params, self._log_debug)
        self.trade_logger = TradeLogger(self.db_lock)
        self.order_manager = OrderManager(self._log_debug)

        # V47.14 - Initialize coordinator
        from .v47_coordinator import V47StrategyCoordinator
        self.v47_coordinator = V47StrategyCoordinator(self)
        
        # Initialize liquidity engine
        from .liquidity_engine import LiquidityEngine
        self.liquidity_engine = LiquidityEngine(self)
        
        # Initialize active stock tracker
        from .active_stock_tracker import ActiveStockTracker
        self.active_stock_tracker = ActiveStockTracker(self)

        strategy_map = {
            "INTRA_CANDLE": IntraCandlePatternStrategy, "UOA": UoaEntryStrategy,
            "TREND_CONTINUATION": TrendContinuationStrategy,
            "MA_CROSSOVER": MaCrossoverStrategy, "CANDLE_PATTERN": CandlePatternEntryStrategy,
            "EQUITY_TREND": EquityTrendStrategy
        }
        self.entry_strategies = []
        
        # Set strategy priority based on trading mode
        trading_mode = self.config.get("trading_mode", "index_options")
        if trading_mode in ["stock_options", "high_volume_options"]:
            priority_list = []  # Empty - liquidity_engine handles stock options directly
        else:  # index_options
            priority_list = ["UOA", "TREND_CONTINUATION", "MA_CROSSOVER", "CANDLE_PATTERN", "INTRA_CANDLE"]
        
        for name in priority_list:
            if name in strategy_map:
                self.entry_strategies.append(strategy_map[name](self))

        self._reset_state()
        self.liquidity_stocks = []  # Store top liquidity movers
        self.LIQUIDITY_POOL = []  # Global shared variable for dashboard data
        self.last_liquidity_update = None  # Throttle liquidity updates
        self.option_instruments = self.load_instruments()
        self.last_used_expiry = self.get_weekly_expiry()

    async def _calculate_trade_charges(self, tradingsymbol, exchange, entry_price, exit_price, quantity):
        BROKERAGE_PER_ORDER = 20.0
        STT_RATE = 0.001
        GST_RATE = 0.18
        SEBI_RATE = 10 / 1_00_00_000
        STAMP_DUTY_RATE = 0.00003
        if exchange == "NFO":
            EXCHANGE_TXN_CHARGE_RATE = 0.00053
        elif exchange == "BFO":
            EXCHANGE_TXN_CHARGE_RATE = 0.000325
        else:
            EXCHANGE_TXN_CHARGE_RATE = 0.00053
        buy_value = entry_price * quantity
        sell_value = exit_price * quantity
        total_turnover = buy_value + sell_value
        brokerage = BROKERAGE_PER_ORDER * 2
        stt = sell_value * STT_RATE
        exchange_charges = total_turnover * EXCHANGE_TXN_CHARGE_RATE
        sebi_charges = total_turnover * SEBI_RATE
        gst = (brokerage + exchange_charges + sebi_charges) * GST_RATE
        stamp_duty = buy_value * STAMP_DUTY_RATE
        return brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty

    def _reset_state(self):
        self.position = None
        self.daily_gross_pnl = 0
        self.daily_net_pnl = 0
        self.total_charges = 0
        self.daily_profit = 0
        self.daily_loss = 0
        self.daily_trade_limit_hit = False
        self.trades_this_minute = 0
        self.initial_subscription_done = False
        self.is_paused = False  # Reset pause state when bot starts
        self.token_to_symbol = {self.index_token: self.index_symbol}
        self.uoa_watchlist = {}
        self.performance_stats = {"total_trades": 0, "winning_trades": 0, "losing_trades": 0}
        self.exit_cooldown_until: Optional[datetime] = None
        self.disconnected_since: Optional[datetime] = None
        self.next_partial_profit_level = 1
        self.trend_candle_count = 0
        self.live_capital_cache = None
        self.live_capital_last_fetched = None  # Cache for live capital
        # Freeze limit and lot size (will be fetched from API)
        self.freeze_limit = None  # Max quantity per order
        self.lot_size = None  # Lot size for the index
        
        # Duplicate trade prevention
        self.last_trade_time = None
        self.trade_cooldown_seconds = 60  # 1 minute cooldown
        self.recent_trades = {}  # Track recent trades by symbol
        self.strategy_lock = asyncio.Lock()  # Prevent concurrent strategy execution
        
        # Active symbol tracking for Liquidity Stock Options
        self.active_stock_symbol = None  # Currently traded stock symbol
        self.active_stock_token = None   # Stock token for price updates

    async def _fetch_live_capital_from_zerodha(self):
        """
        Fetch live available capital from Zerodha margins.
        Returns available cash for trading or None if fetch fails.
        Caches result for 30 seconds to avoid excessive API calls.
        """
        try:
            # Check cache (valid for 30 seconds)
            if self.live_capital_cache is not None and self.live_capital_last_fetched:
                if (datetime.now() - self.live_capital_last_fetched).total_seconds() < 30:
                    await self._log_debug("Capital", f"Using cached live capital: ₹{self.live_capital_cache:.2f}")
                    return self.live_capital_cache

            # Fetch fresh data from Zerodha
            await self._log_debug("Capital", "Fetching live capital from Zerodha...")

            def fetch_margins():
                margins = kite.margins()
                # Get available cash from equity segment
                equity_margin = margins.get('equity', {})
                available = equity_margin.get('available', {})
                live_balance = available.get('live_balance', 0)
                return live_balance

            live_capital = await asyncio.to_thread(fetch_margins)

            # Update cache
            self.live_capital_cache = float(live_capital)
            self.live_capital_last_fetched = datetime.now()

            await self._log_debug("Capital", f"✅ Live capital fetched from Zerodha: ₹{live_capital:.2f}")
            return live_capital

        except Exception as e:
            await self._log_debug("Capital", f"⚠️ Failed to fetch live capital from Zerodha: {e}. Using GUI threshold only.")
            return None

    async def _restore_daily_performance(self):
        await self._log_debug("Persistence", "Restoring daily performance from database...")

        def db_call():
            try:
                with today_engine.connect() as conn:
                    query = sql_text(
                        "SELECT SUM(pnl), SUM(charges), SUM(net_pnl), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) FROM trades"
                    )
                    return conn.execute(query).fetchone()
            except Exception as e:
                print(f"Error restoring performance: {e}")
                return None

        data = await asyncio.to_thread(db_call)
        if data and data[0] is not None:
            gross_pnl, charges, net_pnl, wins, losses = data
            self.daily_gross_pnl = gross_pnl or 0
            self.total_charges = charges or 0
            self.daily_net_pnl = net_pnl or 0
            self.performance_stats["winning_trades"] = wins or 0
            self.performance_stats["losing_trades"] = losses or 0
            if self.performance_stats["winning_trades"] > 0:
                self.daily_profit = self.daily_gross_pnl + abs(self.daily_loss) if self.daily_gross_pnl < 0 else self.daily_gross_pnl
            await self._log_debug("Persistence", f"Restored state: Net P&L: ₹{self.daily_net_pnl:.2f}, Trades: {(wins or 0)+(losses or 0)}")
            await self._update_ui()
        else:
            await self._log_debug("Persistence", "No prior trades found for today. Starting fresh.")
    
    async def _can_take_trade(self, side, symbol):
        """Prevent duplicate trades"""
        # Check if we already have a position
        if self.position:
            await self._log_debug("Duplicate", f"BLOCKED: Already in position")
            return False
        
        # Check cooldown period
        if self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
            if time_since_last < self.trade_cooldown_seconds:
                await self._log_debug("Duplicate", f"BLOCKED: Cooldown active ({time_since_last:.0f}s)")
                return False
        
        # Check recent trades for same symbol
        recent_key = f"{symbol}_{side}"
        if recent_key in self.recent_trades:
            time_since = (datetime.now() - self.recent_trades[recent_key]).total_seconds()
            if time_since < 300:  # 5 minute symbol cooldown
                await self._log_debug("Duplicate", f"BLOCKED: Recent trade on {symbol}")
                return False
        
        return True

    async def _record_trade_attempt(self, side, symbol):
        """Record trade attempt to prevent duplicates"""
        self.last_trade_time = datetime.now()
        self.recent_trades[f"{symbol}_{side}"] = datetime.now()
        
        # Clean old entries (older than 1 hour)
        cutoff_time = datetime.now() - timedelta(hours=1)
        self.recent_trades = {k: v for k, v in self.recent_trades.items() if v > cutoff_time}
    
    def _sanitize_params(self, params):
        """Sanitize and validate strategy parameters"""
        return params.copy() if params else {}

    @property
    def STRATEGY_PARAMS(self):
        """Get strategy parameters with defaults"""
        return {**MARKET_STANDARD_PARAMS, **self.params}

    async def _log_debug(self, category, message):
        """Centralized logging method"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] {category}: {message}"
        print(log_msg)
        if self.manager:
            await self.manager.broadcast({
                "type": "log", 
                "payload": {"category": category, "message": message}
            })

    def load_instruments(self):
        """Load option instruments from Kite"""
        try:
            return kite.instruments(self.exchange)
        except Exception as e:
            print(f"Failed to load instruments: {e}")
            return []

    def get_weekly_expiry(self):
        """Get current weekly expiry date"""
        # Basic implementation - should be enhanced based on your needs
        today = datetime.now().date()
        days_ahead = 3 - today.weekday()  # Thursday is 3
        if days_ahead <= 0:
            days_ahead += 7
        return today + timedelta(days_ahead)

    async def on_trend_update(self, trend_data):
        """Handle trend updates from data manager"""
        pass

    async def _update_ui(self):
        """Update UI with current state"""
        if self.manager:
            await self.manager.broadcast({
                "type": "strategy_update",
                "payload": {
                    "daily_pnl": self.daily_net_pnl,
                    "position": self.position,
                    "is_paused": self.is_paused
                }
            })

    def _is_bullish_engulfing(self, prev, last):
        if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']):
            return False
        prev_body = abs(prev['close'] - prev['open'])
        last_body = abs(last['close'] - last['open'])
        return (prev['close'] < prev['open'] and last['close'] > last['open'] and
                last['close'] > prev['open'] and last['open'] < prev['close'] and
                last_body > prev_body * 0.8)

    def _is_bearish_engulfing(self, prev, last):
        if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']):
            return False
        prev_body = abs(prev['close'] - prev['open'])
        last_body = abs(last['close'] - last['open'])
        return (prev['close'] > prev['open'] and last['close'] < last['open'] and
                last['open'] > prev['close'] and last['close'] < prev['open'] and
                last_body > prev_body * 0.8)

    async def reload_params(self):
        await self._log_debug("System", "Live reloading of strategy parameters requested...")
        new_params = self.STRATEGY_PARAMS
        self.data_manager.strategy_params = new_params
        await self._log_debug("System", "Strategy parameters have been reloaded successfully.")
        return new_params

    async def run(self):
        await self._log_debug("System", "Strategy instance created.")
        await self.data_manager.bootstrap_data()
        await self._restore_daily_performance()
        self.exit_cooldown_until = datetime.now() + timedelta(seconds=5)
        await self._log_debug("System", "Initial 5-second startup wait initiated. No trades will be taken.")
        if not self.ui_update_task or self.ui_update_task.done():
            self.ui_update_task = asyncio.create_task(self.periodic_ui_updater())

    async def periodic_ui_updater(self):
        while True:
            try:
                if self.position and (not self.ticker_manager or not self.ticker_manager.is_connected):
                    if self.disconnected_since is None:
                        self.disconnected_since = datetime.now()
                        await self._log_debug("CRITICAL", "Ticker disconnected in trade! Starting 15s failsafe timer.")
                    if datetime.now() - self.disconnected_since > timedelta(seconds=15):
                        await self._log_debug("CRITICAL", "Failsafe triggered! Exiting position due to prolonged disconnection.")
                        await self.exit_position("Failsafe: Ticker Disconnected")
                        continue
                elif self.ticker_manager and self.ticker_manager.is_connected:
                    if self.disconnected_since is not None:
                        await self._log_debug("INFO", "Ticker reconnected, failsafe timer cancelled.")
                        self.disconnected_since = None
                    if self.position and datetime.now().time() >= time(15, 15):
                        await self._log_debug("RISK", f"EOD square-off time reached. Exiting position.")
                        await self.exit_position("End of Day Auto-Square Off")
                        continue
                    await self._update_ui_status()
                    await self._update_ui_option_chain()
                    await self._update_ui_chart_data()
                    await self._update_ui_straddle_monitor()
                    await self._update_ui_selected_strike_data()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                await self._log_debug("UI Updater", "Task cancelled.")
                break
            except Exception as e:
                await self._log_debug("UI Updater Error", f"An error occurred: {e}")
                await asyncio.sleep(5)

    async def take_equity_trade(self, trigger, stock_data, side):
        """Execute equity trade on liquidity stocks with trend continuation"""
        async with self.position_lock:
            if self.position:
                return

        symbol = stock_data['stock']
        current_price = float(stock_data['futPrice'])
        
        # Calculate equity position size
        live_capital = None
        if self.params.get("trading_mode") == "Live Trading":
            live_capital = await self._fetch_live_capital_from_zerodha()

        capital_per_trade = (live_capital or float(self.params.get("start_capital", 50000))) * 0.02
        qty = int(capital_per_trade / current_price)
        
        if qty <= 0:
            await self._log_debug("Equity Trade", f"❌ Insufficient capital for {symbol}")
            return

        try:
            if self.params.get("trading_mode") == "Live Trading":
                order_result = await self.order_manager.execute_order(
                    transaction_type=kite.TRANSACTION_TYPE_BUY if side == 'BUY' else kite.TRANSACTION_TYPE_SELL,
                    tradingsymbol=symbol,
                    exchange='NSE',
                    quantity=qty,
                    order_type=kite.ORDER_TYPE_MARKET,
                    product=kite.PRODUCT_MIS
                )
                
                if order_result != "COMPLETE":
                    await self._log_debug("Equity Trade", f"❌ Order failed for {symbol}")
                    return
                    
                await self._log_debug("LIVE EQUITY", f"✅ {side} {symbol}: {qty} shares @ ₹{current_price:.2f}")
            else:
                await self._log_debug("PAPER EQUITY", f"Simulating {side} {symbol}: {qty} shares @ ₹{current_price:.2f}")

            # Store equity position
            self.position = {
                "symbol": symbol,
                "entry_price": current_price,
                "direction": side,
                "qty": qty,
                "trail_sl": current_price * 0.95 if side == 'BUY' else current_price * 1.05,
                "max_price": current_price,
                "trigger_reason": trigger,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "lot_size": 1,
                "instrument_type": "EQ"
            }
            
            # Update active stock for equity trading
            await self.active_stock_tracker.update_from_trade(trigger, symbol)

            await self._record_trade_attempt(side, symbol)
            await self._update_ui_trade_status()
            _play_sound(self.manager, "entry")

        except Exception as e:
            await self._log_debug("EQUITY-TRADE-FAIL", f"Failed to execute {side} on {symbol}: {e}")

    async def take_trade(self, trigger, opt, custom_entry_price=None):
        async with self.position_lock:
            if self.position or not opt:
                return

        # Add micro-momentum final check before executing - SKIP for liquidity trades
        symbol = opt["tradingsymbol"]
        if hasattr(self, 'v47_coordinator') and self.v47_coordinator:
            # Skip momentum check for liquidity trades
            if not trigger.startswith("Liquidity_"):
                if not self.v47_coordinator._is_price_actively_rising(symbol, ticks=3):
                    await self._log_debug("Final Check", f"❌ ABORTED {trigger}: Price for {symbol} is not actively rising at execution time.")
                    return

        instrument_token = opt.get("instrument_token")
        side, lot_size = opt["instrument_type"], opt.get("lot_size")

        # Use custom entry price if provided (for ₹0.10 premium logic), otherwise use current market price
        if custom_entry_price is not None:
            price = custom_entry_price
            await self._log_debug("Entry Price", f"Using custom entry price: ₹{price:.2f} (Previous Close + ₹0.10)")
        else:
            price = self.data_manager.prices.get(symbol)
            if price is None:
                await self._log_debug("Trade Rejected", f"No price data available for {symbol}")
                return
            await self._log_debug("Entry Price", f"Using market price: ₹{price:.2f}")

        # Hybrid Capital System: Fetch live capital from Zerodha ONLY for Live Trading
        live_capital = None
        if self.params.get("trading_mode") == "Live Trading":
            live_capital = await self._fetch_live_capital_from_zerodha()
            await self._log_debug("Capital Mode", "Using Live Trading mode - fetching real Zerodha capital")
        else:
            await self._log_debug("Capital Mode", "Using Paper Trading mode - using GUI threshold only")

        # Calculate position size with hybrid capital logic
        qty, initial_sl_price = self.risk_manager.calculate_trade_details(
            price, lot_size,
            available_cash=live_capital,  # Pass live Zerodha capital (None for paper trading)
            daily_pnl=self.daily_net_pnl
        )

        # Send any queued logs from risk manager
        for source, message in self.risk_manager.pending_logs:
            await self._log_debug(source, message)

        if qty is None or instrument_token is None or price is None:
            await self._log_debug("Trade Rejected", "Could not calculate quantity, find instrument token, or get price.")
            return

        # ✅ Use BASKET ORDER - it handles freeze limit slicing internally
        try:
            trading_mode = self.params.get("trading_mode", "Paper Trading")
            if trading_mode == "Live Trading":
                # Basket order handles all slicing and parallel execution automatically
                basket_result = await self.order_manager.execute_basket_order(
                    quantity=qty,  # Just pass total quantity
                    transaction_type=kite.TRANSACTION_TYPE_BUY,
                    tradingsymbol=symbol,
                    exchange=self.exchange,
                    freeze_limit=self.freeze_limit,  # Basket handles slicing if needed
                    price=price
                )

                # Check basket execution result
                if basket_result["status"] in ["COMPLETE", "PARTIAL"]:
                    total_filled_qty = basket_result["total_filled"]

                    if basket_result["status"] == "COMPLETE":
                        await self._log_debug("LIVE TRADE",
                                              f"✅ BUY {symbol}: {total_filled_qty} qty @ ₹{price:.2f}. Reason: {trigger}")
                    else:  # PARTIAL
                        await self._log_debug("LIVE TRADE",
                                              f"⚠️ PARTIAL BUY {symbol}: {total_filled_qty}/{qty} qty @ ₹{price:.2f}. Reason: {trigger}")
                else:  # FAILED
                    await self._log_debug("Trade Rejected", f"❌ Basket order FAILED for {symbol}.")
                    return
            else:
                # Paper trading - simulate execution
                total_filled_qty = qty
                if self.freeze_limit and qty > self.freeze_limit:
                    num_slices = math.ceil(qty / self.freeze_limit)
                    await self._log_debug("PAPER TRADE",
                                          f"Simulating BASKET order: {num_slices} slices, Total: {qty} qty @ ₹{price:.2f}")
                else:
                    await self._log_debug("PAPER TRADE",
                                          f"Simulating BUY {symbol}. Qty: {qty} @ ₹{price:.2f}")

            self.position = {
                "symbol": symbol,
                "entry_price": price,
                "direction": side,
                "qty": total_filled_qty,  # Total quantity filled
                "trail_sl": round(initial_sl_price, 2),
                "atr_trailing_sl": 0 if self.config.get("trading_mode") in ["stock_options", "high_volume_options"] else None,
                "max_price": price,
                "trigger_reason": trigger,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "lot_size": lot_size,
                "breakeven_triggered": False,  # Flag for breakeven strategy
                "segment": "OPTSTK" if self.config.get("trading_mode") == "stock_options" else "OPTIDX"
            }
            
            # Clear option chain subscription cache when new stock trade is taken
            if hasattr(self, '_option_chain_subscribed'):
                self._option_chain_subscribed = None
            
            # Update active stock for Liquidity Stock Options mode
            await self.active_stock_tracker.update_from_trade(trigger, symbol)

            if self.ticker_manager:
                await self._log_debug("WebSocket", f"Subscribing to active trade token: {instrument_token}")
                # Map token immediately so ticks are recognized
                self.token_to_symbol[instrument_token] = symbol
                trading_mode = self.config.get("trading_mode", "index_options")
                ws_mode = 'FULL' if trading_mode == "stock_options" else 'LTP'
                self.ticker_manager.subscribe([instrument_token], mode=ws_mode)

            self.trades_this_minute += 1
            self.performance_stats["total_trades"] += 1
            self.next_partial_profit_level = 1
            _play_sound(self.manager, "entry")
            await self._update_ui_trade_status()

        except Exception as e:
            await self._log_debug("CRITICAL-ENTRY-FAIL", f"Failed to execute entry for {symbol}: {e}")
            _play_sound(self.manager, "loss")

    async def exit_position(self, reason):
        if not self.position:
            return
        p = self.position
        exit_price = self.data_manager.prices.get(p["symbol"], p.get("max_price", p.get("entry_price")) or 0)
        
        # Store last traded option for chart persistence
        last_option_symbol = p.get("symbol")
        
        try:
            sell_log_message = f"Exiting {p['symbol']} ({p['qty']} qty). Reason: {reason}"

            if self.params.get("trading_mode") == "Live Trading":
                if p.get("instrument_type") == "EQ":
                    # Equity exit
                    exit_side = kite.TRANSACTION_TYPE_SELL if p["direction"] == "BUY" else kite.TRANSACTION_TYPE_BUY
                    order_result = await self.order_manager.execute_order(
                        transaction_type=exit_side,
                        tradingsymbol=p["symbol"],
                        exchange='NSE',
                        quantity=p["qty"],
                        order_type=kite.ORDER_TYPE_MARKET,
                        product=kite.PRODUCT_MIS
                    )
                    
                    if order_result != "COMPLETE":
                        await self._log_debug("CRITICAL-EXIT-FAIL", f"❌ FAILED TO EXIT {p['symbol']}!")
                        _play_sound(self.manager, "warning")
                        return
                        
                    await self._log_debug("LIVE EQUITY", f"✅ EXIT {p['symbol']}: {p['qty']} shares @ ₹{exit_price:.2f}")
                else:
                    # Original options exit logic
                    basket_result = await self.order_manager.execute_basket_order(
                        quantity=p["qty"],
                        transaction_type=kite.TRANSACTION_TYPE_SELL,
                        tradingsymbol=p["symbol"],
                        exchange=self.exchange,
                        freeze_limit=self.freeze_limit,
                        price=exit_price
                    )

                    if basket_result["status"] in ["COMPLETE", "PARTIAL"]:
                        p["qty"] = basket_result["total_filled"]
                        await self._log_debug("LIVE TRADE", f"✅ SELL {p['symbol']}: {p['qty']} qty @ ₹{exit_price:.2f}")
                    else:
                        await self._log_debug("CRITICAL-EXIT-FAIL", f"❌ FAILED TO EXIT {p['symbol']}!")
                        _play_sound(self.manager, "warning")
                        return
            else:
                await self._log_debug("PAPER TRADE", sell_log_message)

            gross_pnl = (exit_price - p["entry_price"]) * p["qty"]
            # Calculate charges based on instrument type
            if p.get("instrument_type") == "EQ":
                charges = await self._calculate_trade_charges(tradingsymbol=p["symbol"], exchange='NSE',
                                                             entry_price=p["entry_price"], exit_price=exit_price,
                                                             quantity=p["qty"])
            else:
                charges = await self._calculate_trade_charges(tradingsymbol=p["symbol"], exchange=self.exchange,
                                                             entry_price=p["entry_price"], exit_price=exit_price,
                                                             quantity=p["qty"])
            net_pnl = gross_pnl - charges
            self.daily_gross_pnl += gross_pnl
            self.total_charges += charges
            self.daily_net_pnl += net_pnl
            if gross_pnl > 0:
                self.performance_stats["winning_trades"] += 1
                self.daily_profit += gross_pnl
                _play_sound(self.manager, "profit")
            else:
                self.performance_stats["losing_trades"] += 1
                self.daily_loss += gross_pnl
                _play_sound(self.manager, "loss")
            final_pnl = round(gross_pnl, 2)
            final_charges = round(charges, 2)
            final_net_pnl = round(net_pnl, 2)
            if not all(isinstance(v, (int, float)) for v in [p["entry_price"], exit_price, final_pnl, final_charges, final_net_pnl]):
                await self._log_debug("CRITICAL-LOG-FAIL", f"Aborting trade log for {p['symbol']} due to invalid numeric data.")
                _play_sound(self.manager, "warning")
                self.position = None
                self.exit_cooldown_until = datetime.now() + timedelta(seconds=5)
                await self._update_ui_trade_status()
                await self._update_ui_performance()
                return
            log_info = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                "trigger_reason": p["trigger_reason"],
                "symbol": p["symbol"],
                "quantity": p["qty"],
                "pnl": final_pnl,
                "entry_price": p["entry_price"],
                "exit_price": exit_price,
                "exit_reason": reason,
                "trend_state": self.data_manager.trend_state,
                "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0,
                "charges": final_charges,
                "net_pnl": final_net_pnl,
                "entry_time": p.get("entry_time"),
                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": int((datetime.now() - datetime.strptime(p["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds()) if p.get("entry_time") else 0
            }
            await self.trade_logger.log_trade(log_info)
            await self._log_debug("Database", f"Trade for {p['symbol']} logged successfully.")
            await self.manager.broadcast({"type": "new_trade_log", "payload": log_info})
            
            # Store last option for chart persistence
            if self.config.get("trading_mode") in ["stock_options", "high_volume_options"] and last_option_symbol:
                self._last_option_symbol = last_option_symbol
            else:
                # Clear active stock when trade exits in other modes
                if hasattr(self, 'active_stock_tracker'):
                    self.active_stock_tracker.active_stock_symbol = None
                # Broadcast clear active stock to frontend
                await self.manager.broadcast({
                    "type": "active_stock_update",
                    "payload": None
                })
            
            self.position = None
            self.exit_cooldown_until = datetime.now() + timedelta(seconds=5)
            await self._log_debug("System", "Exit cooldown initiated for 5 seconds.")
            await self._update_ui_trade_status()
            await self._update_ui_performance()
        except Exception as e:
            await self._log_debug("CRITICAL-EXIT-FAIL", f"FAILED TO EXIT {p['symbol']}! MANUAL INTERVENTION REQUIRED! Error: {e}")
            _play_sound(self.manager, "warning")

    async def evaluate_exit_logic(self):
        async with self.position_lock:
            if not self.position:
                return
            p = self.position
            ltp = self.data_manager.prices.get(self.position["symbol"])
            if ltp is None:
                return

            # Update max price seen
            if ltp > p.get("max_price", 0):
                p["max_price"] = ltp

            # trailing SL params (safe parsing)
            try:
                sl_points = float(self.params.get("trailing_sl_points", 5.0))
            except (ValueError, TypeError):
                sl_points = 5.0
            try:
                sl_percent = float(self.params.get("trailing_sl_percent", 10.0))
            except (ValueError, TypeError):
                sl_percent = 10.0

            # Calculate standard trailing SL first (based on points or percent)
            new_trail_sl = round(max(p.get("trail_sl", 0),
                                     max(p.get("max_price", ltp) - sl_points,
                                         p.get("max_price", ltp) * (1 - sl_percent / 100))), 2)

            # --- NEW BREAKEVEN LOGIC ---
            # Check if breakeven hasn't been triggered yet
            try:
                breakeven_profit_pct = float(self.params.get("breakeven_profit_pct", 0)) if self.params.get("breakeven_profit_pct") else 0
            except (ValueError, TypeError):
                breakeven_profit_pct = 0

            if not p.get("breakeven_triggered", False) and breakeven_profit_pct > 0:
                current_profit_pct = (((ltp - p["entry_price"]) / p["entry_price"]) * 100) if p["entry_price"] > 0 else 0
                # If profit target is hit...
                if current_profit_pct >= breakeven_profit_pct:
                    # ...move SL to breakeven (entry price), but only if it's higher than the current new_trail_sl
                    if p["entry_price"] > new_trail_sl:
                        new_trail_sl = p["entry_price"]
                        await self._log_debug("Breakeven", f"Profit {current_profit_pct:.2f}% >= {breakeven_profit_pct}%. SL moved to Entry: ₹{p['entry_price']:.2f}")
                    p["breakeven_triggered"] = True  # Mark as triggered so this doesn't run again
            # --- END OF NEW LOGIC ---

            # --- DYNAMIC ATR TRAILING STOP (Period: 14, Multiplier: 1.2) ---
            current_atr = self.data_manager.get_latest_atr()
            if current_atr and current_atr > 0:
                atr_multiplier = 1.2  # 1.2x ATR distance
                
                if p.get('direction') == 'CE':
                    # For CE: SL is 1.2 ATR below highest price
                    atr_sl = p.get("max_price", ltp) - (current_atr * atr_multiplier)
                    # Only move SL up, never down
                    if atr_sl > new_trail_sl:
                        new_trail_sl = round(atr_sl, 2)
                        await self._log_debug("ATR-SL", 
                            f"Trailed SL to ₹{new_trail_sl:.2f} (ATR: {current_atr:.2f})")
                
                elif p.get('direction') == 'PE':
                    # For PE: SL is 1.2 ATR above current price (trails down as price drops)
                    atr_sl = ltp + (current_atr * atr_multiplier)
                    # Only move SL down (tighten), never up
                    if new_trail_sl == 0 or atr_sl < new_trail_sl:
                        new_trail_sl = round(atr_sl, 2)
                        await self._log_debug("ATR-SL", 
                            f"PE Trailed SL to ₹{new_trail_sl:.2f} (LTP: {ltp:.2f}, ATR: {current_atr:.2f})")
            # --- END ATR LOGIC ---
            
            # Assign the final, highest-priority SL
            p["trail_sl"] = new_trail_sl
            
            # For stock options, also update atr_trailing_sl field for UI display
            if p.get("segment") == "OPTSTK":
                p["atr_trailing_sl"] = new_trail_sl
            
            await self._update_ui_trade_status()

            # Exit logic based on instrument type
            if p.get("instrument_type") == "EQ":
                # Equity exit: SL or trend reversal
                if ((p["direction"] == "BUY" and ltp <= p["trail_sl"]) or 
                    (p["direction"] == "SELL" and ltp >= p["trail_sl"])):
                    await self.exit_position("Trailing SL")
                    return
                    
                # Exit on trend reversal
                if (p["direction"] == "BUY" and self.data_manager.trend_state == "BEARISH") or \
                   (p["direction"] == "SELL" and self.data_manager.trend_state == "BULLISH"):
                    await self.exit_position("Trend Reversal")
                    return
            else:
                # Check liquidity-specific exit conditions (ATR trailing stop) for both modes
                trading_mode = self.config.get("trading_mode", "")
                if trading_mode in ["stock_options", "high_volume_options"] and p.get('trigger_reason', '').startswith('Liquidity_'):
                    should_exit, exit_reason = await self.liquidity_engine.check_exit_conditions(p)
                    if should_exit:
                        print(f"\n{'='*60}")
                        print(f"🚨 EXIT TRIGGERED: {exit_reason}")
                        print(f"{'='*60}")
                        print(f"  Symbol: {p['symbol']}")
                        print(f"  Entry: ₹{p['entry_price']:.2f}")
                        print(f"  Current: ₹{self.data_manager.prices.get(p['symbol'], 0):.2f}")
                        print(f"  PnL: {((self.data_manager.prices.get(p['symbol'], 0) - p['entry_price']) / p['entry_price'] * 100):.2f}%")
                        print(f"{'='*60}\n")
                        await self.exit_position(exit_reason)
                    return
                
                # ATR Trailing Stop Exit - Handle CE and PE separately
                if p.get('direction') == 'CE':
                    # CE: Exit when price drops below trailing SL
                    if ltp <= p["trail_sl"]:
                        await self.exit_position("ATR Trailing SL")
                        return
                elif p.get('direction') == 'PE':
                    # PE: Exit when price rises above trailing SL
                    if ltp >= p["trail_sl"]:
                        await self.exit_position("ATR Trailing SL")
                        return
                else:
                    # Fallback for unknown direction
                    if ltp <= p["trail_sl"]:
                        await self.exit_position("Trailing SL")
                        return

                # CONFIGURABLE TREND INVALIDATION LOGIC
                enable_invalidation = self.STRATEGY_PARAMS.get('enable_trend_invalidation', True)
                required_candle_count = self.STRATEGY_PARAMS.get('invalidation_candle_count', 2)
                
                if enable_invalidation and 'open' in self.data_manager.current_candle and not self.data_manager.data_df.empty:
                    live_index_candle = self.data_manager.current_candle
                    prev_index_candle = self.data_manager.data_df.iloc[-1]
                    
                    # Initialize invalidation counter if not exists
                    if 'invalidation_count' not in p:
                        p['invalidation_count'] = 0
                    
                    # Check for opposing candle
                    is_opposing_candle = False
                    if p.get('direction') == 'CE' and live_index_candle['close'] < live_index_candle['open']:
                        is_opposing_candle = True
                    elif p.get('direction') == 'PE' and live_index_candle['close'] > live_index_candle['open']:
                        is_opposing_candle = True
                    
                    if is_opposing_candle:
                        p['invalidation_count'] += 1
                        await self._log_debug("Trend Watch", f"Opposing candle #{p['invalidation_count']} detected. Need {required_candle_count} for exit.")
                        
                        # Exit only after required number of consecutive opposing candles
                        if p['invalidation_count'] >= required_candle_count:
                            direction_text = "Red" if p.get('direction') == 'CE' else "Green"
                            await self.exit_position(f"Invalidation: {required_candle_count} Consecutive {direction_text} Candles")
                            return
                    else:
                        # Reset counter if candle is in favor
                        if p['invalidation_count'] > 0:
                            await self._log_debug("Trend Watch", "Favorable candle detected. Invalidation counter reset.")
                        p['invalidation_count'] = 0

                    # Keep engulfing pattern logic but make it less sensitive
                    if p.get('direction') == 'CE' and self._is_bearish_engulfing(prev_index_candle, live_index_candle):
                        # Only exit on strong engulfing (body > 1.5x previous)
                        prev_body = abs(prev_index_candle['close'] - prev_index_candle['open'])
                        curr_body = abs(live_index_candle['close'] - live_index_candle['open'])
                        if curr_body > prev_body * 1.5:
                            await self.exit_position("Invalidation: Strong Bearish Engulfing")
                            return
                    elif p.get('direction') == 'PE' and self._is_bullish_engulfing(prev_index_candle, live_index_candle):
                        # Only exit on strong engulfing (body > 1.5x previous)
                        prev_body = abs(prev_index_candle['close'] - prev_index_candle['open'])
                        curr_body = abs(live_index_candle['close'] - live_index_candle['open'])
                        if curr_body > prev_body * 1.5:
                            await self.exit_position("Invalidation: Strong Bullish Engulfing")
                            return
                elif not enable_invalidation:
                    # Trend invalidation is disabled - only use trailing SL
                    pass

    async def partial_exit_position(self):
        if not self.position:
            return
        p = self.position
        # Ensure partial_exit_pct is a number to prevent calculation errors
        try:
            partial_exit_pct = float(self.params.get("partial_exit_pct", 50)) if self.params.get("partial_exit_pct") else 50
        except (ValueError, TypeError):
            partial_exit_pct = 50
        lot_size = p.get("lot_size", 1)
        if lot_size <= 0:
            lot_size = 1
        qty_to_exit = int(min(math.ceil((p["qty"] / lot_size) * (partial_exit_pct / 100)) * lot_size, p["qty"]))
        if qty_to_exit <= 0:
            return
        if (p["qty"] - qty_to_exit) < lot_size:
            await self.exit_position(f"Final Partial Profit-Take")
            return
        exit_price = self.data_manager.prices.get(p["symbol"], p["entry_price"])
        try:
            if self.params.get("trading_mode") == "Live Trading":
                # ✅ Use basket order for partial exits (handles freeze limit automatically)
                basket_result = await self.order_manager.execute_basket_order(
                    quantity=qty_to_exit,
                    transaction_type=kite.TRANSACTION_TYPE_SELL,
                    tradingsymbol=p["symbol"],
                    exchange=self.exchange,
                    freeze_limit=self.freeze_limit,
                    price=exit_price
                )

                # Check if partial exit was successful
                if basket_result["status"] not in ["COMPLETE", "PARTIAL"]:
                    await self._log_debug("CRITICAL-PARTIAL-EXIT-FAIL",
                                          f"❌ Failed to partially exit {p['symbol']}: Basket order FAILED")
                    _play_sound(self.manager, "warning")
                    return

                # Use actual filled quantity
                qty_to_exit = basket_result["total_filled"]

                if basket_result["status"] == "PARTIAL":
                    await self._log_debug("Profit.Take",
                                          f"⚠️ Partial fill: {qty_to_exit} qty exited (some orders failed)")

            gross_pnl = (exit_price - p["entry_price"]) * qty_to_exit
            charges = await self._calculate_trade_charges(tradingsymbol=p["symbol"], exchange=self.exchange,
                                                         entry_price=p["entry_price"], exit_price=exit_price,
                                                         quantity=qty_to_exit)
            net_pnl = gross_pnl - charges
            self.daily_gross_pnl += gross_pnl
            self.total_charges += charges
            self.daily_net_pnl += net_pnl
            if gross_pnl > 0:
                self.daily_profit += gross_pnl
                _play_sound(self.manager, "profit")
            reason = f"Partial Profit-Take ({self.next_partial_profit_level})"
            log_info = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                "trigger_reason": p["trigger_reason"],
                "symbol": p["symbol"],
                "quantity": qty_to_exit,
                "pnl": round(gross_pnl, 2),
                "entry_price": p["entry_price"],
                "exit_price": exit_price,
                "exit_reason": reason,
                "trend_state": self.data_manager.trend_state,
                "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0,
                "charges": round(charges, 2),
                "net_pnl": round(net_pnl, 2),
                "entry_time": p.get("entry_time"),
                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": int((datetime.now() - datetime.strptime(p["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds()) if p.get("entry_time") else 0
            }
            await self.trade_logger.log_trade(log_info)
            await self.manager.broadcast({"type": "new_trade_log", "payload": log_info})
            p["qty"] -= qty_to_exit
            self.next_partial_profit_level += 1
            await self._log_debug("Profit.Take", f"Remaining quantity: {p['qty']}.")
            await self._update_ui_trade_status()
            await self._update_ui_performance()
        except Exception as e:
            await self._log_debug("CRITICAL-PARTIAL-EXIT-FAIL", f"Failed to partially exit {p['symbol']}: {e}")
            _play_sound(self.manager, "warning")

    async def check_partial_profit_take(self):
        if not self.position:
            return
        async with self.position_lock:
            if not self.position:
                return
            p, ltp = self.position, self.data_manager.prices.get(self.position["symbol"])
            if ltp is None:
                return
            # Ensure partial_profit_pct is a number to prevent comparison errors
            try:
                partial_profit_pct = float(self.params.get("partial_profit_pct", 0)) if self.params.get(
                    "partial_profit_pct") else 0
            except (ValueError, TypeError):
                partial_profit_pct = 0
            if partial_profit_pct <= 0:
                return
            profit_pct = (((ltp - p["entry_price"]) / p["entry_price"]) * 100 if p["entry_price"] > 0 else 0)
            target_pct = partial_profit_pct * self.next_partial_profit_level
            if profit_pct >= target_pct:
                await self.partial_exit_position()

    async def handle_ticks_async(self, ticks):
        try:
            if not self.initial_subscription_done and any(t.get("instrument_token") == self.index_token for t in ticks):
                index_price = next(t["last_price"] for t in ticks if t.get("instrument_token") == self.index_token)
                # Ensure index price is always a number
                try:
                    index_price = float(index_price)
                    self.data_manager.prices[self.index_symbol] = index_price
                except (ValueError, TypeError):
                    return  # Skip this batch if index price conversion fails

                await self._log_debug("WebSocket", "Index price received. Subscribing to full token list.")
                tokens = self.get_all_option_tokens()
                await self.map_option_tokens(tokens)
                if self.ticker_manager:
                    trading_mode = self.config.get("trading_mode", "index_options")
                    ws_mode = 'FULL' if trading_mode in ["stock_options", "high_volume_options"] else 'LTP'
                    mode_display = "Stock Options" if trading_mode in ["stock_options", "high_volume_options"] else trading_mode
                    await self._log_debug("WebSocket", f"Using {ws_mode} mode for {mode_display}")
                    self.ticker_manager.resubscribe(tokens, mode=ws_mode)
                    
                    # Log stock subscriptions for verification
                    if trading_mode in ["stock_options", "high_volume_options"]:
                        from .stock_token_cache import get_stock_tokens
                        stock_tokens = get_stock_tokens(kite)
                        subscribed_stocks = [sym for sym, tok in stock_tokens.items() if tok in tokens]
                        await self._log_debug("WebSocket", f"✅ Subscribed to {len(subscribed_stocks)} stock spot prices")
                self.initial_subscription_done = True
            for tick in ticks:
                token, ltp = tick.get("instrument_token"), tick.get("last_price")
                if token is not None and ltp is not None and (symbol := self.token_to_symbol.get(token)):
                    # Ensure ltp is always a number to prevent comparison errors
                    try:
                        ltp = float(ltp)
                    except (ValueError, TypeError):
                        continue  # Skip this tick if price conversion fails

                    self.data_manager.prices[symbol] = ltp
                    self.data_manager.update_price_history(symbol, ltp)
                    
                    # Store market depth for spread validation
                    if 'depth' in tick:
                        self.data_manager.market_depth[symbol] = tick['depth']
                    
                    # DEBUG: Log option price updates for stock options
                    if self.config.get("trading_mode") in ["stock_options", "high_volume_options"] and not symbol.startswith("NSE:") and symbol != self.index_symbol:
                        if not hasattr(self, '_option_price_log_count'):
                            self._option_price_log_count = {}
                        if symbol not in self._option_price_log_count or self._option_price_log_count[symbol] < 3:
                            self._option_price_log_count[symbol] = self._option_price_log_count.get(symbol, 0) + 1
                            await self._log_debug("Option Price", f"✅ {symbol} = ₹{ltp:.2f}")
                    
                    # Log stock spot prices for verification
                    if self.config.get("trading_mode") in ["stock_options", "high_volume_options"] and symbol.startswith("NSE:") and symbol != self.index_symbol:
                        if not hasattr(self, '_stock_price_logged') or symbol not in self._stock_price_logged:
                            if not hasattr(self, '_stock_price_logged'):
                                self._stock_price_logged = set()
                            self._stock_price_logged.add(symbol)
                            await self._log_debug("Stock Price", f"✅ {symbol} = ₹{ltp:.2f}")
                    
                    is_new_minute = self.data_manager.update_live_candle(ltp, symbol)
                    if symbol == self.index_symbol:
                        if is_new_minute:
                            self.trades_this_minute = 0
                            await self.data_manager.on_new_minute(ltp)
                            # V47.14 - Trigger new candle analysis
                            await self.v47_coordinator.on_new_candle()
                            # Check entries only after candle closes
                            await self.check_trade_entry()
                    if self.position and self.position["symbol"] == symbol:
                        await self._log_debug("Price Update", f"{symbol} = ₹{ltp:.2f}")
                        # Store candle data for ATR calculation on every tick (both modes)
                        trading_mode = self.config.get("trading_mode", "")
                        if trading_mode in ["stock_options", "high_volume_options"]:
                            self.liquidity_engine._store_option_candle(symbol)
                        await self.check_partial_profit_take()
                        await self.evaluate_exit_logic()
        except Exception as e:
            await self._log_debug("Tick Handler Error", f"Critical error: {e}")

    async def check_trade_entry(self):
        if not await self.can_trade():
            return

        # Check regular entry strategies first (including LIQUIDITY_TREND)
        for strategy in self.entry_strategies:
            try:
                side, reason, opt = await strategy.check()
                if side and reason and opt:
                    # Handle equity trades differently
                    if isinstance(opt, dict) and 'stock' in opt:
                        await self.take_equity_trade(reason, opt, side)
                    else:
                        await self.take_trade(reason, opt)
                    return  # Exit after first successful trade
            except Exception as e:
                await self._log_debug("Strategy Error", f"Error in {strategy.__class__.__name__}: {e}")
        
        # Then check V47 coordinator as fallback (only for index_options mode)
        trading_mode = self.config.get("trading_mode", "index_options")
        if trading_mode == "index_options":
            await self.v47_coordinator.continuous_monitoring()

    async def can_trade(self):
        """V47.14 - Unified trade validation"""
        if self.is_paused:
            return False  # Bot is paused, no new trades allowed
        if self.position is not None or self.daily_trade_limit_hit:
            return False
        if self.exit_cooldown_until and datetime.now() < self.exit_cooldown_until:
            return False
        if self.trades_this_minute >= 2:
            return False
        
        # No new trades after 3:20 PM
        if datetime.now().time() >= time(15, 20):
            if not hasattr(self, '_cutoff_logged'):
                await self._log_debug("RISK", "Trade entry disabled: the daily cutoff time of 3:20 PM has been reached.")
                self._cutoff_logged = True
            return False

        # Ensure daily_sl and daily_pt are always numbers to prevent string comparison errors
        try:
            daily_sl = float(self.params.get("daily_sl", 0)) if self.params.get("daily_sl") else 0
            daily_pt = float(self.params.get("daily_pt", 0)) if self.params.get("daily_pt") else 0
        except (ValueError, TypeError):
            daily_sl, daily_pt = 0, 0

        if (daily_sl < 0 and self.daily_net_pnl <= daily_sl) or (daily_pt > 0 and self.daily_net_pnl >= daily_pt):
            self.daily_trade_limit_hit = True
            await self._log_debug("RISK", "Daily Net SL/PT hit. Trading disabled.")
            return False

        return True

    async def on_ticker_connect(self):
        await self._log_debug("WebSocket", f"Connected. Subscribing to index: {self.index_symbol}")
        await self._update_ui_status()
        if self.ticker_manager:
            trading_mode = self.config.get("trading_mode", "index_options")
            ws_mode = 'FULL' if trading_mode == "stock_options" else 'LTP'
            self.ticker_manager.resubscribe([self.index_token], mode=ws_mode)

    async def on_ticker_disconnect(self):
        await self._update_ui_status()
        await self._log_debug("WebSocket", "Kite Ticker Disconnected.")

    @property
    def STRATEGY_PARAMS(self):
        try:
            with open("strategy_params.json", "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return MARKET_STANDARD_PARAMS.copy()

    async def _log_debug(self, source, message):
        await self.manager.broadcast({"type": "debug_log", "payload": {"time": datetime.now().strftime("%H:%M:%S"), "source": source, "message": message}})

    async def _update_ui_status(self):
        is_running = self.ticker_manager and self.ticker_manager.is_connected

        # Get capital information for UI display
        try:
            gui_threshold = float(self.params.get("start_capital", 50000))
        except (ValueError, TypeError):
            gui_threshold = 50000
        live_capital_display = self.live_capital_cache if self.live_capital_cache is not None else None

        # Calculate effective capital (after daily P&L adjustments)
        if live_capital_display is not None:
            base_capital = min(live_capital_display, gui_threshold)
        else:
            base_capital = gui_threshold
        current_capital = base_capital + self.daily_net_pnl
        effective_capital = min(base_capital, current_capital)

        # For stock_options mode, show active option symbol and price instead of index
        if self.config.get("trading_mode") == "stock_options" and self.position:
            display_symbol = self.position["symbol"]
            display_price = self.data_manager.prices.get(display_symbol, 0)
        else:
            # Show "NIFTY 50" instead of "STOCK_OPTIONS" when no trade is active
            display_symbol = "NIFTY 50" if self.index_name == "STOCK_OPTIONS" else self.index_name
            display_price = self.data_manager.prices.get(self.index_symbol, 0)

        payload = {
            "connection": "CONNECTED" if is_running else "DISCONNECTED",
            "mode": self.params.get("trading_mode", "Paper").upper(),
            "indexPrice": display_price,
            "is_running": is_running,
            "is_paused": self.is_paused,
            "trend": self.data_manager.trend_state or "---",
            "indexName": display_symbol,
            # Capital information
            "live_capital": live_capital_display,
            "gui_threshold": gui_threshold,
            "effective_capital": effective_capital
        }
        await self.manager.broadcast({"type": "status_update", "payload": payload})

    async def _update_ui_performance(self):
        payload = {"grossPnl": self.daily_gross_pnl, "totalCharges": self.total_charges, "netPnl": self.daily_net_pnl, "wins": self.performance_stats["winning_trades"], "losses": self.performance_stats["losing_trades"]}
        await self.manager.broadcast({"type": "daily_performance_update", "payload": payload})

    async def _update_ui_trade_status(self):
        payload = None
        if self.position:
            p, ltp = self.position, self.data_manager.prices.get(self.position["symbol"], self.position["entry_price"])
            pnl = (ltp - p["entry_price"]) * p["qty"]
            profit_pct = (((ltp - p["entry_price"]) / p["entry_price"]) * 100 if p["entry_price"] > 0 else 0)
            payload = {
                "symbol": p["symbol"], 
                "entry_price": p["entry_price"], 
                "ltp": ltp, 
                "pnl": pnl, 
                "profit_pct": profit_pct, 
                "trail_sl": p["trail_sl"], 
                "atr_trailing_sl": p.get("atr_trailing_sl"),
                "segment": p.get("segment", "OPTIDX"),
                "max_price": p["max_price"]
            }
        await self.manager.broadcast({"type": "trade_status_update", "payload": payload})

    async def _update_ui_uoa_list(self):
        await self.manager.broadcast({"type": "uoa_list_update", "payload": list(self.uoa_watchlist.values())})

    async def _update_ui_option_chain(self):
        data = []
        trading_mode = self.config.get("trading_mode", "index_options")
        
        # Show stock option chain if in stock options mode and active stock exists
        if trading_mode in ["stock_options", "high_volume_options"]:
            # Get stock symbol from position or active tracker
            stock_symbol = None
            stock_price = None
            
            if self.position:
                pos_symbol = self.position['symbol']
                # Extract stock symbol dynamically from option symbol
                import re
                match = re.match(r'^([A-Z]+)', pos_symbol)
                if match:
                    stock_symbol = match.group(1)
                    self._last_stock_symbol = stock_symbol
            elif hasattr(self.active_stock_tracker, 'active_stock_symbol') and self.active_stock_tracker.active_stock_symbol:
                stock_symbol = self.active_stock_tracker.active_stock_symbol
                self._last_stock_symbol = stock_symbol
            elif hasattr(self, '_last_stock_symbol') and self._last_stock_symbol:
                stock_symbol = self._last_stock_symbol
            
            # Broadcast active stock update to sync with option chain
            if stock_symbol:
                stock_price_temp = self.data_manager.prices.get(f"NSE:{stock_symbol}")
                if not stock_price_temp or stock_price_temp <= 0:
                    if self.liquidity_stocks:
                        stock_data = next((s for s in self.liquidity_stocks if s.get('stock') == stock_symbol), None)
                        if stock_data and stock_data.get('futPrice'):
                            try:
                                stock_price_temp = float(str(stock_data.get('futPrice')).replace(',', ''))
                            except (ValueError, TypeError):
                                pass
                
                if stock_price_temp and stock_price_temp > 0:
                    await self.manager.broadcast({
                        "type": "active_stock_update",
                        "payload": {
                            "symbol": stock_symbol,
                            "price": stock_price_temp,
                            "mode": "stock_options"
                        }
                    })
            
            if stock_symbol:
                # Try WebSocket price first
                stock_price = self.data_manager.prices.get(f"NSE:{stock_symbol}")
                
                # Fallback to dashboard futPrice
                if (not stock_price or stock_price <= 0) and self.liquidity_stocks:
                    stock_data = next((s for s in self.liquidity_stocks if s.get('stock') == stock_symbol), None)
                    if stock_data and stock_data.get('futPrice'):
                        try:
                            stock_price = float(str(stock_data.get('futPrice')).replace(',', ''))
                        except (ValueError, TypeError):
                            pass
                
                if stock_price and stock_price > 0:
                    strike_interval = self._get_stock_strike_interval(stock_price)
                    atm_strike = int(round(stock_price / strike_interval)) * int(strike_interval)
                    strikes = [atm_strike + (i - 3) * int(strike_interval) for i in range(7)]
                    
                    # Subscribe to all option tokens ONCE before fetching prices
                    if not hasattr(self, '_option_chain_subscribed') or self._option_chain_subscribed != stock_symbol:
                        tokens_to_subscribe = []
                        for strike in strikes:
                            for side in ['CE', 'PE']:
                                opt = self.get_stock_option(stock_symbol, side, strike)
                                if opt and opt.get('instrument_token'):
                                    tokens_to_subscribe.append(opt['instrument_token'])
                                    self.token_to_symbol[opt['instrument_token']] = opt['tradingsymbol']
                        
                        if tokens_to_subscribe and self.ticker_manager:
                            self.ticker_manager.subscribe(tokens_to_subscribe, mode='FULL')
                            await self._log_debug("OptionChain", f"✅ Subscribed to {len(tokens_to_subscribe)} option tokens for {stock_symbol}")
                            self._option_chain_subscribed = stock_symbol
                    
                    # Now fetch prices from data_manager
                    for strike in strikes:
                        ce_opt = self.get_stock_option(stock_symbol, 'CE', strike)
                        pe_opt = self.get_stock_option(stock_symbol, 'PE', strike)
                        
                        ce_ltp = self.data_manager.prices.get(ce_opt['tradingsymbol']) if ce_opt else None
                        pe_ltp = self.data_manager.prices.get(pe_opt['tradingsymbol']) if pe_opt else None
                        
                        ce_display = f"{ce_ltp:.2f}" if ce_ltp and ce_ltp > 0 else "--"
                        pe_display = f"{pe_ltp:.2f}" if pe_ltp and pe_ltp > 0 else "--"
                        
                        data.append({"strike": strike, "ce_ltp": ce_display, "pe_ltp": pe_display})
                else:
                    await self.manager.broadcast({"type": "option_chain_update", "payload": []})
                    return
        elif trading_mode == "index_options":
            # Default index option chain
            pairs = self.get_strike_pairs()
            if self.data_manager.prices.get(self.index_symbol) and pairs:
                for p in pairs:
                    ce_symbol = p["ce"]["tradingsymbol"] if p["ce"] else None
                    pe_symbol = p["pe"]["tradingsymbol"] if p["pe"] else None
                    data.append({"strike": p["strike"], "ce_ltp": self.data_manager.prices.get(ce_symbol, "--") if ce_symbol else "--", "pe_ltp": self.data_manager.prices.get(pe_symbol, "--") if pe_symbol else "--"})
        
        await self.manager.broadcast({"type": "option_chain_update", "payload": data})

    async def _update_ui_straddle_monitor(self):
        payload = {"current_straddle": 0, "open_straddle": 0, "change_pct": 0}
        spot = self.data_manager.prices.get(self.index_symbol)
        if not spot:
            await self.manager.broadcast({"type": "straddle_update", "payload": payload})
            return
        atm_strike = self.strike_step * round(spot / self.strike_step)
        ce_opt = self.get_entry_option('CE', atm_strike)
        pe_opt = self.get_entry_option('PE', atm_strike)
        if ce_opt and pe_opt:
            ce_sym, pe_sym = ce_opt['tradingsymbol'], pe_opt['tradingsymbol']
            ce_ltp = self.data_manager.prices.get(ce_sym)
            pe_ltp = self.data_manager.prices.get(pe_sym)
            ce_open = self.data_manager.option_open_prices.get(ce_sym)
            pe_open = self.data_manager.option_open_prices.get(pe_sym)
            if all([ce_ltp, pe_ltp, ce_open, pe_open]):
                current_straddle = ce_ltp + pe_ltp
                open_straddle = ce_open + pe_open
                change_pct = ((current_straddle / open_straddle) - 1) * 100 if open_straddle > 0 else 0
                payload = {"current_straddle": current_straddle, "open_straddle": open_straddle, "change_pct": change_pct}
        await self.manager.broadcast({"type": "straddle_update", "payload": payload})
    
    async def _update_ui_selected_strike_data(self):
        """Send selected strike option data for dynamic option chain"""
        if self.config.get("trading_mode") not in ["stock_options", "high_volume_options"]:
            return
            
        # Get active stock from tracker or current trade
        stock_symbol = None
        stock_price = None
        
        if self.position and self.position.get('symbol'):
            # Extract stock symbol from option symbol (e.g., "TCS24DEC2700CE" -> "TCS")
            option_symbol = self.position['symbol']
            for stock in ['TCS', 'INFY', 'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'ITC', 'LT', 'HINDUNILVR']:
                if option_symbol.startswith(stock):
                    stock_symbol = stock
                    break
        elif hasattr(self.active_stock_tracker, 'active_stock_symbol') and self.active_stock_tracker.active_stock_symbol:
            stock_symbol = self.active_stock_tracker.active_stock_symbol
            
        if not stock_symbol:
            return
            
        stock_price = self.data_manager.prices.get(f"NSE:{stock_symbol}")
        if not stock_price or stock_price <= 0:
            return
            
        # Calculate strike based on bot's selection
        strike_selection = self.params.get('strike_selection', 'ATM')
        strike_interval = self._get_stock_strike_interval(stock_price)
        atm_strike = round(stock_price / strike_interval) * strike_interval
        
        if strike_selection == 'ATM':
            selected_strike = atm_strike
        elif strike_selection == 'ITM':
            selected_strike = atm_strike - strike_interval
        elif strike_selection == 'OTM':
            selected_strike = atm_strike + strike_interval
        else:
            selected_strike = atm_strike
            
        # Get CE and PE options for selected strike
        ce_opt = self.get_stock_option(stock_symbol, 'CE', selected_strike)
        pe_opt = self.get_stock_option(stock_symbol, 'PE', selected_strike)
        
        ce_ltp = self.data_manager.prices.get(ce_opt['tradingsymbol']) if ce_opt else 0
        pe_ltp = self.data_manager.prices.get(pe_opt['tradingsymbol']) if pe_opt else 0
        
        # Subscribe to option tokens for real-time updates
        if ce_opt and self.ticker_manager and ce_opt.get('instrument_token'):
            self.ticker_manager.subscribe([ce_opt['instrument_token']])
        if pe_opt and self.ticker_manager and pe_opt.get('instrument_token'):
            self.ticker_manager.subscribe([pe_opt['instrument_token']])
        
        # Broadcast selected strike data
        await self.manager.broadcast({
            "type": "selected_strike_update",
            "payload": {
                "stock_symbol": stock_symbol,
                "spot_price": stock_price,
                "strike": selected_strike,
                "strike_selection": strike_selection,
                "ce_ltp": ce_ltp,
                "pe_ltp": pe_ltp,
                "ce_symbol": ce_opt['tradingsymbol'] if ce_opt else None,
                "pe_symbol": pe_opt['tradingsymbol'] if pe_opt else None
            }
        })

    async def _fetch_option_chart_data(self, option_symbol, instrument_token):
        """Fetch and cache option contract historical data"""
        if not hasattr(self, '_option_chart_cache'):
            self._option_chart_cache = {}
        
        cache_key = option_symbol
        now = datetime.now()
        
        # Use cache if less than 1 second old
        if cache_key in self._option_chart_cache:
            cached_data, cached_time = self._option_chart_cache[cache_key]
            if (now - cached_time).total_seconds() < 1:
                print(f"Chart: Using cached data for {option_symbol}")
                return cached_data
        
        try:
            print(f"Chart: Fetching historical data for {option_symbol} (token: {instrument_token})...")
            
            # Fetch historical data for option contract
            def get_data():
                return kite.historical_data(
                    instrument_token,
                    datetime.now() - timedelta(days=7),
                    datetime.now(),
                    "minute"
                )
            
            data = await asyncio.to_thread(get_data)
            
            if not data:
                print(f"Chart: ❌ No historical data returned for {option_symbol}")
                return None
                
            print(f"Chart: ✅ Received {len(data)} candles for {option_symbol}")
            
            df = pd.DataFrame(data).tail(700)
            df['date'] = pd.to_datetime(df['date'])
            # Remove timezone to avoid comparison issues
            if df['date'].dt.tz is not None:
                df['date'] = df['date'].dt.tz_localize(None)
            df.set_index('date', inplace=True)
            df = self.data_manager._calculate_indicators(df)
            
            print(f"Chart: ✅ Processed {len(df)} candles with indicators for {option_symbol}")
            
            # Cache the result
            self._option_chart_cache[cache_key] = (df, now)
            return df
        except Exception as e:
            print(f"Chart: ❌ Error fetching {option_symbol} data: {str(e)}")
            import traceback
            traceback.print_exc()
        
        return None

    async def _update_ui_chart_data(self):
        trading_mode = self.config.get("trading_mode", "index_options")
        
        # Check if we should use option chart for stock options mode
        use_option_chart = False
        option_symbol = None
        option_token = None
        
        # Use active position OR last traded option (persists after exit)
        if trading_mode in ["stock_options", "high_volume_options"]:
            if self.position:
                option_symbol = self.position.get('symbol')
            elif hasattr(self, '_last_option_symbol') and self._last_option_symbol:
                option_symbol = self._last_option_symbol
            
            if option_symbol:
                # Find token from option_instruments list
                matching_opt = next((opt for opt in self.option_instruments 
                                   if opt.get('tradingsymbol') == option_symbol), None)
                if matching_opt:
                    option_token = matching_opt.get('instrument_token')
                    use_option_chart = True
                    # Log only once when switching options
                    if not hasattr(self, '_current_chart_option') or self._current_chart_option != option_symbol:
                        print(f"Chart: 📊 Fetching {option_symbol} chart (token: {option_token})")
                        self._current_chart_option = option_symbol
                else:
                    print(f"Chart: ⚠️ Could not find token for {option_symbol}, using index chart")
        
        if use_option_chart and option_symbol and option_token:
            # Fetch option contract chart data
            option_df = await self._fetch_option_chart_data(option_symbol, option_token)
            
            if option_df is not None:
                temp_df = option_df.copy()
                # Add live candle from option_candles if available
                option_candle = self.data_manager.option_candles.get(option_symbol)
                if option_candle and option_candle.get('minute'):
                    current_minute = option_candle['minute']
                    # Ensure both are timezone-naive for comparison
                    if hasattr(current_minute, 'tzinfo') and current_minute.tzinfo is not None:
                        current_minute = current_minute.replace(tzinfo=None)
                    
                    if len(temp_df) > 0:
                        last_index = temp_df.index[-1]
                        # DataFrame index is already timezone-naive from _fetch_option_chart_data
                        if last_index < current_minute:
                            live_candle_df = pd.DataFrame([option_candle], index=[current_minute])
                            temp_df = pd.concat([temp_df, live_candle_df])
            else:
                print(f"Chart: ⚠️ Failed to fetch {option_symbol} data, using index chart")
                # Fallback to index chart if option data unavailable
                temp_df = self.data_manager.data_df.copy()
                if self.data_manager.current_candle.get("minute"):
                    live_candle_df = pd.DataFrame([self.data_manager.current_candle], index=[self.data_manager.current_candle["minute"]])
                    temp_df = pd.concat([temp_df, live_candle_df])
        else:
            # Default: Use index chart
            temp_df = self.data_manager.data_df.copy()
            if self.data_manager.current_candle.get("minute"):
                live_candle_df = pd.DataFrame([self.data_manager.current_candle], index=[self.data_manager.current_candle["minute"]])
                temp_df = pd.concat([temp_df, live_candle_df])
        
        if not temp_df.index.is_unique:
            temp_df = temp_df[~temp_df.index.duplicated(keep='last')]
        if not temp_df.index.is_monotonic_increasing:
            temp_df.sort_index(inplace=True)
        chart_data = {"candles": [], "wma": [], "sma": [], "rsi": [], "rsi_sma": [], "supertrend": []}
        if not temp_df.empty:
            for index, row in temp_df.iterrows():
                timestamp = int(index.timestamp())
                chart_data["candles"].append({"time": timestamp, "open": row.get("open", 0), "high": row.get("high", 0), "low": row.get("low", 0), "close": row.get("close", 0)})
                if pd.notna(row.get("wma")):
                    chart_data["wma"].append({"time": timestamp, "value": row["wma"]})
                if pd.notna(row.get("sma")):
                    chart_data["sma"].append({"time": timestamp, "value": row["sma"]})
                if pd.notna(row.get("rsi")):
                    chart_data["rsi"].append({"time": timestamp, "value": row["rsi"]})
                if pd.notna(row.get("rsi_sma")):
                    chart_data["rsi_sma"].append({"time": timestamp, "value": row["rsi_sma"]})
                if pd.notna(row.get("supertrend")):
                    chart_data["supertrend"].append({"time": timestamp, "value": row["supertrend"]})
        await self.manager.broadcast({"type": "chart_data_update", "payload": chart_data})

    def calculate_uoa_conviction_score(self, option_data, atm_strike):
        score, v_oi_ratio = 0, option_data.get('volume', 0) / (option_data.get('oi', 0) + 1)
        score += min(v_oi_ratio / 2.0, 5)
        score += min(option_data.get('change', 0) / 10.0, 5)
        strike_distance = abs(option_data['strike'] - atm_strike) / self.strike_step
        if strike_distance <= 2:
            score += 3
        elif strike_distance <= 4:
            score += 1
        return score

    async def add_to_watchlist(self, side, strike):
        opt = self.get_entry_option(side, strike=strike)
        if opt:
            token = opt.get('instrument_token', opt.get('tradingsymbol'))
            if token in self.uoa_watchlist:
                return False
            self.uoa_watchlist[token] = {'symbol': opt['tradingsymbol'], 'type': side, 'strike': strike}
            await self._log_debug("UOA", f"Added {opt['tradingsymbol']} to watchlist.")
            await self._update_ui_uoa_list()
            _play_sound(self.manager, "entry")
            if self.ticker_manager and not self.is_backtest:
                tokens = self.get_all_option_tokens()
                await self.map_option_tokens(tokens)
                self.ticker_manager.resubscribe(tokens)
            return True
        await self._log_debug("UOA", f"Could not find {side} option for strike {strike}")
        _play_sound(self.manager, "warning")
        return False

    async def reset_uoa_watchlist(self):
        await self._log_debug("UOA", "Watchlist reset requested by user.")
        self.uoa_watchlist.clear()
        await self._update_ui_uoa_list()
        _play_sound(self.manager, "warning")

    async def update_liquidity_data(self, liquidity_stocks):
        """Update liquidity stocks from dashboard feed and trigger liquidity engine"""
        if self.last_liquidity_update:
            elapsed = (datetime.now() - self.last_liquidity_update).total_seconds()
            if elapsed < 1.0:
                return
        
        self.last_liquidity_update = datetime.now()
        self.liquidity_stocks = liquidity_stocks
        
        if liquidity_stocks and not hasattr(self, '_liquidity_data_received'):
            await self._log_debug("Liquidity", f"✅ Receiving data: {len(liquidity_stocks)} stocks")
            self._liquidity_data_received = True
        
        # Trigger liquidity engine for both stock_options and high_volume_options modes
        trading_mode = self.config.get("trading_mode", "")
        if trading_mode in ["stock_options", "high_volume_options"] and liquidity_stocks:
            # Set active stock for option chain display (high volume mode)
            if trading_mode == "high_volume_options" and not self.position:
                top_stock = liquidity_stocks[0].get('stock') if liquidity_stocks else None
                if top_stock:
                    self.active_stock_tracker.active_stock_symbol = top_stock
                    # Subscribe to stock spot price for option chain
                    from .stock_token_cache import get_stock_tokens
                    stock_tokens = get_stock_tokens(kite)
                    if top_stock in stock_tokens and self.ticker_manager:
                        stock_token = stock_tokens[top_stock]
                        self.ticker_manager.subscribe([stock_token], mode='FULL')
                        self.token_to_symbol[stock_token] = f"NSE:{top_stock}"
            await self.liquidity_engine.update_liquidity_data(liquidity_stocks)

    async def scan_for_unusual_activity(self):
        if self.is_backtest:
            return
        try:
            await self._log_debug("Scanner", "Running intelligent UOA scan...")
            spot = self.data_manager.prices.get(self.index_symbol)
            if not spot:
                await self._log_debug("Scanner", "Aborting scan: Index price not available.")
                return
            atm_strike = self.strike_step * round(spot / self.strike_step)
            scan_range = 5 if self.index_name == "NIFTY" else 8
            target_strikes = [atm_strike + (i * self.strike_step) for i in range(-scan_range, scan_range + 1)]
            target_options = [i for i in self.option_instruments if i['expiry'] == self.last_used_expiry and i['strike'] in target_strikes]
            if not target_options:
                return
            quotes = await asyncio.to_thread(lambda: kite.quote([opt['instrument_token'] for opt in target_options]))
            found_count, CONVICTION_THRESHOLD = 0, 7.0
            for instrument, data in quotes.items():
                opt_details = next((opt for opt in target_options if opt['instrument_token'] == data['instrument_token']), None)
                if not opt_details:
                    continue
                quote_data = {"volume": data.get('volume', 0), "oi": data.get('oi', 0), "change": data.get('change', 0), "strike": opt_details['strike']}
                score = self.calculate_uoa_conviction_score(quote_data, atm_strike)
                if score >= CONVICTION_THRESHOLD:
                    if await self.add_to_watchlist(opt_details['instrument_type'], opt_details['strike']):
                        await self._log_debug("Scanner", f"High conviction: {opt_details['tradingsymbol']} (Score: {score:.1f}). Added.")
                        found_count += 1
            if found_count == 0:
                await self._log_debug("Scanner", "Scan complete. No new high-conviction opportunities found.")
        except Exception as e:
            await self._log_debug("Scanner ERROR", f"An error occurred during UOA scan: {e}")

    async def on_trend_update(self, new_trend):
        if self.data_manager.trend_state != new_trend:
            self.trend_candle_count = 1
        else:
            self.trend_candle_count += 1

    def load_instruments(self):
        import pickle
        from pathlib import Path
        
        try:
            trading_mode = self.config.get("trading_mode", "index_options")
            cache_file = Path(f"instruments_{trading_mode}_{self.exchange}.pkl")
            
            # Try cache (valid for 24 hours)
            if cache_file.exists():
                age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
                if age_hours < 24:
                    with open(cache_file, 'rb') as f:
                        instruments = pickle.load(f)
                    print(f"✅ Loaded {len(instruments)} instruments from cache ({age_hours:.1f}h old)")
                    
                    if instruments:
                        self.lot_size = instruments[0].get('lot_size', 1)
                        freeze_qty = instruments[0].get('freeze_quantity', None)
                        self.freeze_limit = freeze_qty if freeze_qty and freeze_qty > 0 else None
                    else:
                        self.lot_size = 1
                        self.freeze_limit = None
                    return instruments
            
            # Fetch from API
            instruments = []
            if trading_mode == "stock_options":
                nse_instruments = kite.instruments('NFO')
                # Load ALL stock options (no filtering) - dynamic detection
                instruments = [i for i in nse_instruments 
                             if i.get('instrument_type') in ['CE', 'PE'] and 
                             i.get('segment') == 'NFO-OPT']
                
                print(f"✅ Stock Options: Loaded {len(instruments)} contracts (ALL stocks)")
            elif trading_mode == "high_volume_options":
                # Load stock options for High Volume mode (same as stock_options)
                nse_instruments = kite.instruments('NFO')
                instruments = [i for i in nse_instruments 
                             if i.get('instrument_type') in ['CE', 'PE'] and 
                             i.get('segment') == 'NFO-OPT']
                print(f"✅ High Volume Stock Options Mode: Loaded {len(instruments)} stock option contracts")
            else:
                instruments = [i for i in kite.instruments(self.exchange) if i['name'] == self.index_name and i['instrument_type'] in ['CE', 'PE']]
                print(f"✅ Index Options: Loaded {len(instruments)} {self.index_name} contracts")
            
            # Cache it
            with open(cache_file, 'wb') as f:
                pickle.dump(instruments, f)
            
            if instruments:
                self.lot_size = instruments[0].get('lot_size', 1)
                freeze_qty = instruments[0].get('freeze_quantity', None)
                self.freeze_limit = freeze_qty if freeze_qty and freeze_qty > 0 else None
                print(f"✅ Lot: {self.lot_size}, Freeze: {self.freeze_limit}")
            else:
                self.lot_size = 1
                self.freeze_limit = None
            
            return instruments
        except Exception as e:
            print(f"FATAL: Could not load instruments: {e}")
            raise e

    def get_weekly_expiry(self):
        today = date.today()
        future_expiries = sorted([i['expiry'] for i in self.option_instruments if i.get('expiry') and i['expiry'] >= today])
        return future_expiries[0] if future_expiries else None

    def get_all_option_tokens(self):
        spot = self.data_manager.prices.get(self.index_symbol)
        tokens = {self.index_token}
        
        trading_mode = self.config.get("trading_mode", "index_options")
        
        if trading_mode in ["stock_options", "high_volume_options"]:
            # 1. Subscribe to stock spot prices (NSE equity tokens)
            from .stock_token_cache import get_stock_tokens
            stock_tokens = get_stock_tokens(kite)
            tokens.update(stock_tokens.values())
            
            # 2. Subscribe to option contracts for active stock
            if hasattr(self.active_stock_tracker, 'active_stock_symbol') and self.active_stock_tracker.active_stock_symbol:
                active_stock = self.active_stock_tracker.active_stock_symbol
                stock_price = self.data_manager.prices.get(f"NSE:{active_stock}")
                
                if stock_price and stock_price > 0:
                    strike_interval = self._get_stock_strike_interval(stock_price)
                    atm_strike = round(stock_price / strike_interval) * strike_interval
                    strikes = [atm_strike + (i - 3) * strike_interval for i in range(7)]
                    
                    for strike in strikes:
                        for side in ['CE', 'PE']:
                            opt = self.get_stock_option(active_stock, side, strike)
                            if opt and opt.get('instrument_token'):
                                tokens.add(opt['instrument_token'])
        else:
            # Index options mode
            if spot:
                atm_strike = self.strike_step * round(spot / self.strike_step)
                strikes = [atm_strike + (i - 3) * self.strike_step for i in range(7)]
                tokens.update([opt['instrument_token'] for strike in strikes for side in ['CE', 'PE'] if (opt := self.get_entry_option(side, strike))])
        
        tokens.update(self.uoa_watchlist.keys())
        return list(tokens)

    async def map_option_tokens(self, tokens):
        # Map option contracts
        self.token_to_symbol = {o['instrument_token']: o['tradingsymbol'] for o in self.option_instruments if o['instrument_token'] in tokens}
        self.token_to_symbol[self.index_token] = self.index_symbol
        
        # Map stock spot tokens (NSE equity)
        from .stock_token_cache import get_stock_tokens
        stock_tokens = get_stock_tokens(kite)
        for symbol, token in stock_tokens.items():
            if token in tokens:
                self.token_to_symbol[token] = f"NSE:{symbol}"

    def get_strike_pairs(self, count=7):
        spot = self.data_manager.prices.get(self.index_symbol)
        if not spot:
            return []
        atm_strike = self.strike_step * round(spot / self.strike_step)
        strikes = [atm_strike + (i - count // 2) * self.strike_step for i in range(count)]
        return [{"strike": strike, "ce": self.get_entry_option('CE', strike), "pe": self.get_entry_option('PE', strike)} for strike in strikes]

    def get_entry_option(self, side, strike=None):
        spot = self.data_manager.prices.get(self.index_symbol)
        if not spot:
            return None
        if strike is None:
            strike = self.strike_step * round(spot / self.strike_step)
        for o in self.option_instruments:
            if o['expiry'] == self.last_used_expiry and o['strike'] == strike and o['instrument_type'] == side:
                return o
        return None
    
    def get_stock_option(self, stock_symbol, side, strike=None):
        """Get stock option for liquidity trading"""
        if not stock_symbol:
            return None
            
        # Get stock price for strike calculation
        stock_price = self.data_manager.prices.get(f"NSE:{stock_symbol}")
        if not stock_price:
            return None
            
        if strike is None:
            # Calculate ATM strike for stock
            strike_interval = self._get_stock_strike_interval(stock_price)
            strike = round(stock_price / strike_interval) * strike_interval
        
        # Find matching stock option - get nearest expiry
        today = date.today()
        
        # Try tradingsymbol pattern match first (more reliable)
        pattern_match = [o for o in self.option_instruments 
                       if o.get('tradingsymbol', '').startswith(stock_symbol) 
                       and o.get('instrument_type') == side 
                       and o.get('strike') == strike
                       and o.get('expiry') and o['expiry'] >= today]
        
        if pattern_match:
            return min(pattern_match, key=lambda x: x['expiry'])
        
        # Fallback to name match
        matching = [o for o in self.option_instruments
                   if (o.get('name') == stock_symbol and 
                       o.get('instrument_type') == side and 
                       o.get('strike') == strike and
                       o.get('expiry') and o['expiry'] >= today)]
        
        return min(matching, key=lambda x: x['expiry']) if matching else None
    
    def _get_stock_strike_interval(self, price):
        """Get strike interval for stock options based on price"""
        if price < 150: return 2.5
        elif price < 500: return 5.0
        elif price < 1000: return 10.0
        elif price < 2500: return 20.0
        elif price < 5000: return 50.0
        else: return 100.0

    def _sanitize_params(self, params):
        p = params.copy()
        try:
            keys_to_convert = [
                "start_capital", "trailing_sl_points", "trailing_sl_percent",
                "daily_sl", "daily_pt", "partial_profit_pct", "partial_exit_pct",
                "risk_per_trade_percent", "recovery_threshold_pct", "max_lots_per_order"
            ]
            for key in keys_to_convert:
                if key in p:
                    try:
                        # Convert to float, handling empty strings and None values
                        if p[key] is None or p[key] == '':
                            p[key] = 0.0
                        else:
                            p[key] = float(p[key])
                    except (ValueError, TypeError):
                        # If conversion fails, set to 0.0 as default
                        p[key] = 0.0
                        print(f"Warning: Could not convert parameter '{key}' with value '{p[key]}' to float. Setting to 0.0")
        except Exception as e:
            print(f"Warning: Error in parameter sanitization: {e}")
        return p
    async def _update_ui_liquidity_option_chain(self):
        """Send targeted option chain for liquidity trades"""
        if (self.config.get("trading_mode") != "stock_options" or 
            not hasattr(self.active_stock_tracker, 'active_stock_symbol') or 
            not self.active_stock_tracker.active_stock_symbol):
            return
            
        stock_symbol = self.active_stock_tracker.active_stock_symbol
        stock_price = self.data_manager.prices.get(f"NSE:{stock_symbol}")
        
        if not stock_price or stock_price <= 0:
            return
            
        # Calculate ATM strike for the active stock
        strike_interval = self._get_stock_strike_interval(stock_price)
        atm_strike = round(stock_price / strike_interval) * strike_interval
        
        # Get CE and PE options for ATM strike
        ce_opt = self.get_stock_option(stock_symbol, 'CE', atm_strike)
        pe_opt = self.get_stock_option(stock_symbol, 'PE', atm_strike)
        
        ce_ltp = self.data_manager.prices.get(ce_opt['tradingsymbol']) if ce_opt else None
        pe_ltp = self.data_manager.prices.get(pe_opt['tradingsymbol']) if pe_opt else None
        
        # Subscribe to option tokens for real-time updates
        if ce_opt and self.ticker_manager and ce_opt.get('instrument_token'):
            self.ticker_manager.subscribe([ce_opt['instrument_token']])
        if pe_opt and self.ticker_manager and pe_opt.get('instrument_token'):
            self.ticker_manager.subscribe([pe_opt['instrument_token']])
        
        # Broadcast targeted option chain data
        await self.manager.broadcast({
            "type": "liquidity_option_chain_update",
            "payload": {
                "stock_symbol": stock_symbol,
                "stock_price": stock_price,
                "atm_strike": atm_strike,
                "ce_ltp": ce_ltp or 0,
                "pe_ltp": pe_ltp or 0,
                "ce_symbol": ce_opt['tradingsymbol'] if ce_opt else None,
                "pe_symbol": pe_opt['tradingsymbol'] if pe_opt else None
            }
        })



