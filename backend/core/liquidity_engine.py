# backend/core/liquidity_engine.py
import asyncio
from datetime import datetime, date, time
from typing import Optional, Dict, List, Tuple

class LiquiditySignal:
    def __init__(self, action, symbol=None, strike=None, option_type=None, change_pct=None, 
                 contract=None, atm_strike=None, strike_interval=None, entry_price=None, 
                 exit_price=None, timestamp=None, reason=None, direction=None):
        self.action = action
        self.symbol = symbol
        self.strike = strike
        self.option_type = option_type
        self.change_pct = change_pct
        self.contract = contract
        self.atm_strike = atm_strike
        self.strike_interval = strike_interval
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.reason = reason
        self.direction = direction

class LiquidityEngine:
    # Stock to Exchange mapping (NFO vs BFO)
    STOCK_EXCHANGE_MAP = {
        # NFO stocks (Nifty 50)
        'RELIANCE': 'NFO', 'TCS': 'NFO', 'HDFCBANK': 'NFO', 'BHARTIARTL': 'NFO', 'ICICIBANK': 'NFO',
        'INFY': 'NFO', 'SBIN': 'NFO', 'ITC': 'NFO', 'HINDUNILVR': 'NFO', 'LT': 'NFO',
        'HCLTECH': 'NFO', 'MARUTI': 'NFO', 'SUNPHARMA': 'NFO', 'ONGC': 'NFO', 'TATAMOTORS': 'NFO',
        'NTPC': 'NFO', 'AXISBANK': 'NFO', 'KOTAKBANK': 'NFO', 'ULTRACEMCO': 'NFO', 'ASIANPAINT': 'NFO',
        'BAJFINANCE': 'NFO', 'M&M': 'NFO', 'POWERGRID': 'NFO', 'TECHM': 'NFO', 'HINDALCO': 'NFO',
        'COALINDIA': 'NFO', 'INDUSINDBK': 'NFO', 'TATASTEEL': 'NFO', 'CIPLA': 'NFO', 'BAJAJFINSV': 'NFO',
        'GRASIM': 'NFO', 'HDFCLIFE': 'NFO', 'SBILIFE': 'NFO', 'BPCL': 'NFO', 'EICHERMOT': 'NFO',
        'HEROMOTOCO': 'NFO', 'APOLLOHOSP': 'NFO', 'ADANIENT': 'NFO', 'BRITANNIA': 'NFO',
        'DIVISLAB': 'NFO', 'DRREDDY': 'NFO', 'TRENT': 'NFO', 'ADANIPORTS': 'NFO', 'BAJAJ-AUTO': 'NFO',
        'SHRIRAMFIN': 'NFO', 'LICI': 'NFO', 'LTIM': 'NFO', 'TATACONSUM': 'NFO', 'JSWSTEEL': 'NFO',
        # BFO stocks (Sensex exclusive)
        'NESTLEIND': 'BFO', 'TITAN': 'BFO', 'WIPRO': 'BFO'
    }
    
    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        self.params = strategy_instance.params
        self.data_manager = strategy_instance.data_manager
        self._instruments_cache = None
        self._cache_time = None
        self._stock_momentum_tracker = {}
        self._last_signals = {}
        self._last_log_time = {}
        self._atm_price_history = {}  # Track 1-min price history for ATM performance validation
        self._option_candle_history = {}  # Track option candles for ATR calculation
        self._last_trade_time = {}  # Track last trade time per stock for cooldown

    def _is_enabled(self) -> bool:
        """Enable for both stock_options mode and high_volume_options mode"""
        trading_mode = self.strategy.config.get("trading_mode", "")
        return trading_mode in ["stock_options", "high_volume_options"]
    
    def get_executable_price(self, symbol: str, side: str = "BUY") -> Optional[float]:
        """Get executable price from LTP with buffer for instant fill"""
        ltp = self.data_manager.prices.get(symbol)
        if not ltp or ltp <= 0:
            return None
        
        buffer = 0.10  # 10 paisa buffer for instant fill
        
        if side == "BUY":
            return ltp + buffer
        else:
            return ltp - buffer
    
    def _validate_5paisa_spread(self, option_symbol: str) -> Tuple[bool, str]:
        """Validate 5 paisa spread check for option liquidity"""
        try:
            depth = self.data_manager.market_depth.get(option_symbol, {})
            if not depth or 'buy' not in depth or 'sell' not in depth:
                return False, "No market depth available"
            
            best_bid = depth['buy'][0]['price'] if depth['buy'] else 0
            best_ask = depth['sell'][0]['price'] if depth['sell'] else 0
            
            if best_bid <= 0 or best_ask <= 0:
                return False, "Invalid bid/ask prices"
            
            spread = best_ask - best_bid
            if spread > 0.05:  # 5 paisa = ₹0.05
                return False, f"Spread too wide: ₹{spread:.2f} > ₹0.05"
            
            return True, f"Spread valid: ₹{spread:.2f} (bid={best_bid:.2f}, ask={best_ask:.2f})"
        except Exception as e:
            return False, f"Spread check error: {e}"
    
    def _validate_trend_continuation(self, signal: LiquiditySignal, option_symbol: str) -> Tuple[bool, str]:
        """Validate option premium and trend continuation"""
        try:
            ltp = self.data_manager.prices.get(option_symbol)
            if not ltp or ltp <= 0:
                current_candle = self.data_manager.option_candles.get(option_symbol, {})
                if not current_candle or current_candle.get('close', 0) <= 0:
                    return False, "No price data available"
                ltp = current_candle.get('close', 0)
            
            if ltp < 5:
                return False, f"Premium too low: ₹{ltp:.2f} < ₹5"
            
            return True, f"Valid premium: ₹{ltp:.2f}"
        except Exception as e:
            return False, f"Validation error: {e}"
    


    def get_liquidity_signal(self, stock_data: Dict, log_enabled: bool = False) -> LiquiditySignal:
        """Generate liquidity signal for top movers from dashboard"""
        try:
            if not self._is_enabled():
                print(f"DEBUG: Liquidity engine DISABLED - trading_mode={self.strategy.config.get('trading_mode', 'N/A')}")
                return LiquiditySignal("NO_TRADE")
            
            symbol = stock_data.get('stock', '')
            change_pct = float(str(stock_data.get('priceChange', '0')).replace('%', '').replace('+', ''))
            
            # DEBUG: Log all stocks being scanned
            if not hasattr(self, '_scan_log_count'):
                self._scan_log_count = 0
            if self._scan_log_count < 5:
                print(f"Liquidity Scan: {symbol} @ {change_pct:+.2f}%")
                self._scan_log_count += 1
            
            # Get spot price from futPrice (Nifty50/Sensex dashboard)
            spot_price = 0
            fut_price = stock_data.get('futPrice', '')
            if fut_price:
                try:
                    spot_price = float(str(fut_price).replace(',', ''))
                except (ValueError, TypeError):
                    pass
            
            # Fallback to WebSocket live price
            if spot_price <= 0 and symbol:
                spot_price = self.data_manager.prices.get(f"NSE:{symbol}", 0)
            
            if not symbol or spot_price <= 0:
                print(f"⚠️ {symbol}: Missing price data (futPrice={fut_price}, change={change_pct:+.2f}%)")
                return LiquiditySignal("NO_TRADE")
            
            threshold = 1.0
            is_rising = change_pct >= threshold
            is_falling = change_pct <= -threshold
            
            # DEBUG: Log threshold checks
            if abs(change_pct) >= 0.8:  # Close to threshold
                print(f"Liquidity: {symbol} @ {change_pct:+.2f}% (threshold=±{threshold}%) - {'✅ PASS' if (is_rising or is_falling) else '❌ SKIP'}")
            
            if not is_rising and not is_falling:
                return LiquiditySignal("NO_TRADE")
            
            # STOCK OPTIONS: Trade based on threshold only
            action = option_type = direction = reason = None
            
            # CE Entry: Stock rising ≥ +1.0%
            if is_rising:
                action, option_type = "BUY_CE", "CE"
                direction = "RISING"
                reason = f"Top mover: {symbol} up {change_pct:+.2f}%"
                print(f"Liquidity: ✅ {symbol} SIGNAL GENERATED - CE @ {change_pct:+.2f}%")
                self._last_signals[symbol] = 'signal_generated'
            # PE Entry: Stock falling ≤ -1.0%
            elif is_falling:
                action, option_type = "BUY_PE", "PE"
                direction = "FALLING"
                reason = f"Top mover: {symbol} down {change_pct:+.2f}%"
                print(f"Liquidity: ✅ {symbol} SIGNAL GENERATED - PE @ {change_pct:+.2f}%")
                self._last_signals[symbol] = 'signal_generated'
            
            if not action:
                return LiquiditySignal("NO_TRADE")
            
            strike_interval = self._get_strike_interval(spot_price)
            atm_strike = self._get_optimal_strike(spot_price, option_type, change_pct)
            
            return LiquiditySignal(
                action=action,
                symbol=symbol,
                strike=atm_strike,
                option_type=option_type,
                change_pct=change_pct,
                atm_strike=atm_strike,
                strike_interval=strike_interval,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                reason=reason,
                direction=direction
            )
            
        except Exception as e:
            print(f"Error in get_liquidity_signal: {e}")
            return LiquiditySignal("NO_TRADE")

    async def get_option_contract_with_premium_filter(self, symbol: str, strike: float, option_type: str) -> Optional[Dict]:
        """Fetch option contract with ₹5 minimum premium filter - ATM ONLY"""
        try:
            print(f"\n🔍 STRIKE SELECTION: {symbol} {option_type} @ ATM {strike}")
            
            # Only check ATM strike
            contract = await self._get_contract_with_ltp(symbol, strike, option_type)
            if contract and contract.get('ltp', 0) >= 5:
                print(f"✅ ATM Strike Valid: {strike} @ ₹{contract['ltp']:.2f}")
                return contract
            
            if contract:
                print(f"❌ ATM Strike Invalid: {strike} @ ₹{contract['ltp']:.2f} (< ₹5) - Skip to next stock")
            else:
                print(f"❌ No ATM contract found for {symbol} - Skip to next stock")
            
            return None
            
        except Exception as e:
            print(f"❌ Strike selection error: {e}")
            return None
    
    async def _get_contract_with_ltp(self, symbol: str, strike: float, option_type: str) -> Optional[Dict]:
        """Get contract and fetch its LTP"""
        try:
            contract = await self.get_option_contract(symbol, strike, option_type)
            if not contract:
                return None
            
            option_symbol = contract.get('tradingsymbol')
            option_token = contract.get('instrument_token')
            
            # Subscribe and wait for LTP
            if option_token and self.strategy.ticker_manager:
                self.strategy.ticker_manager.subscribe([option_token], mode='FULL')
                await self.strategy.map_option_tokens([option_token])
                await asyncio.sleep(0.5)  # Wait for tick
            
            ltp = self.data_manager.prices.get(option_symbol, 0)
            contract['ltp'] = ltp
            return contract
            
        except Exception:
            return None

    async def get_option_contract(self, symbol: str, strike: float, option_type: str) -> Optional[Dict]:
        """Fetch option contract from correct exchange (NFO or BFO) - HYBRID MODE"""
        try:
            # Determine exchange: Check mapping first, fallback to NFO for High Volume stocks
            exchange = self.STOCK_EXCHANGE_MAP.get(symbol, 'NFO')
            
            now = datetime.now()
            cache_key = f"{exchange}_{symbol}"
            
            if not self._instruments_cache or cache_key not in self._instruments_cache or \
               not self._cache_time or (now - self._cache_time).total_seconds() > 300:
                from .kite import kite
                instruments = await asyncio.to_thread(kite.instruments, exchange)
                if not self._instruments_cache:
                    self._instruments_cache = {}
                self._instruments_cache[cache_key] = instruments
                self._cache_time = now
                print(f"✅ Loaded {len(instruments)} instruments from {exchange} for {symbol}")
            
            options = [i for i in self._instruments_cache[cache_key] 
                      if i.get('name') == symbol and i.get('instrument_type') in ['CE', 'PE']]
            
            if not options:
                print(f"❌ No options found for {symbol} on {exchange}")
                return None
            
            today = date.today()
            matching = [opt for opt in options 
                       if opt.get('expiry') and opt['expiry'] >= today 
                       and opt.get('strike') == strike 
                       and opt.get('instrument_type') == option_type]
            
            if not matching:
                return None
            
            return min(matching, key=lambda x: x['expiry'])
        except Exception as e:
            print(f"Liquidity: {symbol} - Contract lookup error: {e}")
            return None
    
    async def update_liquidity_data(self, stocks_data: List[Dict]):
        """Update liquidity engine and execute trades directly - HYBRID MODE"""
        try:
            print(f"\n{'='*60}")
            print(f"🔍 LIQUIDITY ENGINE - HYBRID MODE (High Volume + Nifty50/Sensex)")
            print(f"{'='*60}")
            
            trading_mode = self.strategy.config.get("trading_mode", "")
            
            # HYBRID: Accept both High Volume stocks AND stocks with exchange mapping
            if trading_mode == "high_volume_options":
                # High Volume mode: Accept ALL stocks from dashboard (no exchange filter)
                valid_stocks = stocks_data
                print(f"\n📥 HIGH VOLUME MODE: {len(valid_stocks)} stocks loaded (no exchange filter)")
            else:
                # Stock Options mode: Filter to Nifty50/Sensex only
                valid_stocks = [s for s in stocks_data if s.get('stock') in self.STOCK_EXCHANGE_MAP]
                print(f"\n📥 STOCK OPTIONS MODE: {len(valid_stocks)}/{len(stocks_data)} stocks with exchange mapping")
            
            for stock in valid_stocks[:10]:  # Show first 10
                exchange = self.STOCK_EXCHANGE_MAP.get(stock.get('stock'), 'NFO (High Volume)')
                print(f"  {stock.get('stock')} ({exchange}): {stock.get('priceChange')}")
            
            # Use valid stocks
            self.strategy.liquidity_stocks = valid_stocks
            print(f"\n🔍 Liquidity: {len(valid_stocks)} stocks available")
            
            # CHECK 1: Position exists?
            if self.strategy.position:
                print(f"\n❌ TRADE BLOCKED: Position exists - {self.strategy.position.get('symbol', 'Unknown')}")
                return
            print(f"✅ CHECK 1 PASSED: No existing position")
            
            # CHECK 1.2: Cooldown check
            now = datetime.now()
            for stock in valid_stocks:
                symbol = stock.get('stock', '')
                if symbol in self._last_trade_time:
                    time_since_last = (now - self._last_trade_time[symbol]).total_seconds()
                    if time_since_last < 60:
                        print(f"\n❌ TRADE BLOCKED: {symbol} in cooldown ({time_since_last:.0f}s / 60s)")
                        return
            print(f"✅ CHECK 1.2 PASSED: No stocks in cooldown")
            
            # CHECK 1.5: Cutoff time check
            if datetime.now().time() >= time(15, 20):
                print(f"\n❌ TRADE BLOCKED: Cutoff time reached (3:20 PM)")
                return
            print(f"✅ CHECK 1.5 PASSED: Before cutoff time")
            
            # Get top 3 gainers and losers with fallback
            print(f"\n🔍 CHECK 2: Finding top gainers/losers...")
            from config.trading_config import MIN_LIQUIDITY_CHANGE_PCT
            
            # Debug: Show all stock changes
            print(f"\n📊 Stock Changes (threshold: ±{MIN_LIQUIDITY_CHANGE_PCT}%):")
            for s in valid_stocks[:10]:
                change = float(str(s.get('priceChange', '0')).replace('%', '').replace('+', ''))
                print(f"  {s.get('stock')}: {change:+.2f}%")
            
            gainers = sorted([s for s in valid_stocks if float(str(s.get('priceChange', '0')).replace('%', '').replace('+', '')) >= MIN_LIQUIDITY_CHANGE_PCT],
                           key=lambda x: float(str(x.get('priceChange', '0')).replace('%', '').replace('+', '')), reverse=True)[:3]
            losers = sorted([s for s in valid_stocks if float(str(s.get('priceChange', '0')).replace('%', '').replace('+', '')) <= -MIN_LIQUIDITY_CHANGE_PCT],
                          key=lambda x: float(str(x.get('priceChange', '0')).replace('%', '').replace('+', '')))[:3]
            
            if not gainers and not losers:
                print(f"❌ TRADE BLOCKED: No gainers or losers found")
                return
            print(f"✅ CHECK 2 PASSED: {len(gainers)} gainers, {len(losers)} losers")
            
            # Try CE with fallback to next gainer
            print(f"\n🔍 CHECK 3: Generating signals with premium filter...")
            ce_signal = pe_signal = None
            
            for gainer in gainers:
                # Update active stock for option chain display
                stock_symbol = gainer.get('stock')
                if stock_symbol and hasattr(self.strategy, 'active_stock_tracker'):
                    self.strategy.active_stock_tracker.active_stock_symbol = stock_symbol
                    # Subscribe to stock spot price immediately
                    from .stock_token_cache import get_stock_tokens
                    from .kite import kite
                    stock_tokens = get_stock_tokens(kite)
                    if stock_symbol in stock_tokens and self.strategy.ticker_manager:
                        stock_token = stock_tokens[stock_symbol]
                        self.strategy.ticker_manager.subscribe([stock_token], mode='FULL')
                        self.strategy.token_to_symbol[stock_token] = f"NSE:{stock_symbol}"
                    # Set futPrice as fallback for immediate option chain display
                    fut_price = gainer.get('futPrice', '')
                    if fut_price:
                        try:
                            price = float(str(fut_price).replace(',', ''))
                            if price > 0:
                                self.data_manager.prices[f"NSE:{stock_symbol}"] = price
                        except (ValueError, TypeError):
                            pass
                    await self.strategy.manager.broadcast({
                        "type": "active_stock_update",
                        "payload": {"symbol": stock_symbol, "mode": trading_mode}
                    })
                
                print(f"  Checking CE for {gainer.get('stock')} @ {gainer.get('priceChange')}...")
                signal = self.get_liquidity_signal(gainer, log_enabled=False)
                if signal.action == "BUY_CE":
                    # Early supertrend check
                    if not await self._check_stock_supertrend_early(signal.symbol, signal.option_type):
                        print(f"  ❌ CE supertrend invalid for {signal.symbol}, trying next...")
                        continue
                    
                    contract = await self.get_option_contract_with_premium_filter(signal.symbol, signal.strike, signal.option_type)
                    if contract:
                        option_symbol = contract.get('tradingsymbol')
                        
                        # Validate 5 paisa spread (optional - skip if no depth data)
                        spread_valid, spread_msg = self._validate_5paisa_spread(option_symbol)
                        if spread_valid:
                            print(f"  ✅ CE spread check passed: {spread_msg}")
                        else:
                            print(f"  ⚠️ CE spread check skipped: {spread_msg} (proceeding anyway)")
                        
                        is_valid, validation_msg = self._validate_trend_continuation(signal, option_symbol)
                        if is_valid:
                            ce_signal = signal
                            print(f"  ✅ CE signal valid: {signal.symbol} @ {signal.change_pct:+.2f}%")
                            break
                        else:
                            print(f"  ❌ CE validation failed for {signal.symbol}: {validation_msg}")
                    else:
                        print(f"  ❌ CE abandoned: No valid strikes for {signal.symbol}, trying next...")
            
            # Try PE with fallback to next loser
            for loser in losers:
                # Update active stock for option chain display
                stock_symbol = loser.get('stock')
                if stock_symbol and hasattr(self.strategy, 'active_stock_tracker'):
                    self.strategy.active_stock_tracker.active_stock_symbol = stock_symbol
                    # Subscribe to stock spot price immediately
                    from .stock_token_cache import get_stock_tokens
                    from .kite import kite
                    stock_tokens = get_stock_tokens(kite)
                    if stock_symbol in stock_tokens and self.strategy.ticker_manager:
                        stock_token = stock_tokens[stock_symbol]
                        self.strategy.ticker_manager.subscribe([stock_token], mode='FULL')
                        self.strategy.token_to_symbol[stock_token] = f"NSE:{stock_symbol}"
                    # Set futPrice as fallback for immediate option chain display
                    fut_price = loser.get('futPrice', '')
                    if fut_price:
                        try:
                            price = float(str(fut_price).replace(',', ''))
                            if price > 0:
                                self.data_manager.prices[f"NSE:{stock_symbol}"] = price
                        except (ValueError, TypeError):
                            pass
                    await self.strategy.manager.broadcast({
                        "type": "active_stock_update",
                        "payload": {"symbol": stock_symbol, "mode": trading_mode}
                    })
                
                print(f"  Checking PE for {loser.get('stock')} @ {loser.get('priceChange')}...")
                signal = self.get_liquidity_signal(loser, log_enabled=False)
                if signal.action == "BUY_PE":
                    # Early supertrend check
                    if not await self._check_stock_supertrend_early(signal.symbol, signal.option_type):
                        print(f"  ❌ PE supertrend invalid for {signal.symbol}, trying next...")
                        continue
                    
                    contract = await self.get_option_contract_with_premium_filter(signal.symbol, signal.strike, signal.option_type)
                    if contract:
                        option_symbol = contract.get('tradingsymbol')
                        
                        # Validate 5 paisa spread (optional - skip if no depth data)
                        spread_valid, spread_msg = self._validate_5paisa_spread(option_symbol)
                        if spread_valid:
                            print(f"  ✅ PE spread check passed: {spread_msg}")
                        else:
                            print(f"  ⚠️ PE spread check skipped: {spread_msg} (proceeding anyway)")
                        
                        is_valid, validation_msg = self._validate_trend_continuation(signal, option_symbol)
                        if is_valid:
                            pe_signal = signal
                            print(f"  ✅ PE signal valid: {signal.symbol} @ {signal.change_pct:+.2f}%")
                            break
                        else:
                            print(f"  ❌ PE validation failed for {signal.symbol}: {validation_msg}")
                    else:
                        print(f"  ❌ PE abandoned: No valid strikes for {signal.symbol}, trying next...")
            
            if not ce_signal and not pe_signal:
                print(f"❌ TRADE BLOCKED: No valid signals after premium filter")
                return
            print(f"✅ CHECK 3 PASSED: Valid signal(s) generated")
            
            # Execute signal with higher absolute percentage change
            print(f"\n🔍 CHECK 4: Executing trade...")
            if ce_signal and pe_signal:
                ce_abs = abs(ce_signal.change_pct)
                pe_abs = abs(pe_signal.change_pct)
                print(f"  📊 COMPARISON: CE={ce_signal.symbol} ({ce_signal.change_pct:+.2f}%, abs={ce_abs:.2f}) vs PE={pe_signal.symbol} ({pe_signal.change_pct:+.2f}%, abs={pe_abs:.2f})")
                selected = ce_signal if ce_abs >= pe_abs else pe_signal
                print(f"  ✅ SELECTED: {selected.symbol} {selected.option_type} (abs={abs(selected.change_pct):.2f}%)")
                await self._execute_liquidity_trade(selected)
            elif ce_signal:
                print(f"  Executing CE signal: {ce_signal.symbol}")
                await self._execute_liquidity_trade(ce_signal)
            elif pe_signal:
                print(f"  Executing PE signal: {pe_signal.symbol}")
                await self._execute_liquidity_trade(pe_signal)
                    
        except Exception as e:
            print(f"Error updating liquidity data: {e}")

    async def _backfill_candle_history(self, instrument_token: int, symbol: str):
        """Fetch last 20 mins of historical data so ATR works immediately"""
        try:
            print(f"⏳ Backfilling candle history for {symbol}...")
            from .kite import kite
            from datetime import timedelta
            
            now = datetime.now()
            from_time = now - timedelta(minutes=25)
            
            history = await asyncio.to_thread(
                kite.historical_data, 
                instrument_token, 
                from_time, 
                now, 
                "minute"
            )
            
            if not history:
                print(f"⚠️ No history found for {symbol}")
                return

            clean_history = []
            for candle in history:
                clean_history.append({
                    'open': candle['open'],
                    'high': candle['high'],
                    'low': candle['low'],
                    'close': candle['close'],
                    'timestamp': candle['date'].replace(tzinfo=None)
                })
            
            self._option_candle_history[symbol] = clean_history[-20:]
            
            count = len(self._option_candle_history[symbol])
            print(f"✅ History Backfilled: Loaded {count} candles for {symbol}. ATR is ready.")
            
            # Calculate supertrend after backfill
            supertrend_value = self._calculate_supertrend(symbol)
            if supertrend_value:
                print(f"✅ Supertrend calculated: {supertrend_value}")
            
        except Exception as e:
            print(f"❌ Error backfilling history for {symbol}: {e}")

    async def _execute_liquidity_trade(self, signal: LiquiditySignal):
        """Execute option trade IMMEDIATELY with executable price + validations"""
        try:
            if datetime.now().time() >= time(15, 20):
                print(f"\n❌ TRADE BLOCKED: After 3:20 PM cutoff")
                return
            
            print(f"\n{'='*60}")
            print(f"🚀 EXECUTING TRADE: {signal.symbol} {signal.option_type}")
            print(f"{'='*60}")
            
            # Get contract
            contract = await self.get_option_contract_with_premium_filter(signal.symbol, signal.strike, signal.option_type)
            if not contract:
                print(f"❌ No valid contract found")
                return
            
            option_symbol = contract.get('tradingsymbol')
            option_token = contract.get('instrument_token')
            
            # 🔥 VALIDATION 1: Expiry Check
            expiry_valid, expiry_msg = self._validate_expiry(contract)
            if not expiry_valid:
                print(f"❌ TRADE BLOCKED: {expiry_msg}")
                return
            print(f"✅ Expiry: {expiry_msg}")
            
            # 🔥 VALIDATION 2: Option Liquidity Check
            liquidity_valid, liquidity_msg = self._validate_option_liquidity(option_symbol)
            if not liquidity_valid:
                print(f"❌ TRADE BLOCKED: {liquidity_msg}")
                return
            print(f"✅ Liquidity: {liquidity_msg}")
            
            # Subscribe for live depth and backfill history
            if option_token and self.strategy.ticker_manager:
                self.strategy.ticker_manager.subscribe([option_token], mode='FULL')
                await self.strategy.map_option_tokens([option_token])
                await asyncio.sleep(0.3)
                
                # 🔥 BACKFILL HISTORY BEFORE TRADE (for ATR)
                await self._backfill_candle_history(option_token, option_symbol)
                
                # 🔥 VALIDATION 3: ATR Ready Check (after backfill)
                atr_valid, atr_msg = self._validate_atr_ready(option_symbol)
                if not atr_valid:
                    print(f"⚠️ WARNING: {atr_msg} - Trade will proceed but ATR stop unavailable initially")
                else:
                    print(f"✅ ATR: {atr_msg}")
            
            # 🔥 GET EXECUTABLE PRICE IMMEDIATELY
            exec_price = self.get_executable_price(option_symbol, "BUY")
            if not exec_price:
                print(f"❌ No executable price for {option_symbol}")
                return
            
            # Timing verification log
            ltp = self.data_manager.prices.get(option_symbol, 0)
            print(f"⏱️ ENTRY TIMING | {option_symbol} | LTP={ltp:.2f} | Exec={exec_price:.2f} | Time={datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            print(f"🚀 IMMEDIATE ENTRY: {option_symbol} @ ₹{exec_price:.2f} (LTP + ₹0.10 buffer)")
            
            # 🔥 PLACE ORDER NOW with custom entry price
            reason = f"Liquidity_{signal.option_type}_{signal.symbol}"
            await self.strategy.take_trade(reason, contract, custom_entry_price=exec_price)
            
            # 🔥 SET INITIAL ATR TRAILING STOP IMMEDIATELY
            if self.strategy.position and len(self._option_candle_history.get(option_symbol, [])) >= 10:
                atr_value = self._get_atr_value(option_symbol, period=10)
                if atr_value and atr_value > 0:
                    atr_multiplier = 1.2
                    if signal.option_type == 'CE':
                        initial_stop = exec_price - (atr_value * atr_multiplier)
                    else:  # PE
                        initial_stop = exec_price + (atr_value * atr_multiplier)
                    
                    self.strategy.position['atr_trailing_sl'] = initial_stop
                    self.strategy.position['trailing_stop'] = initial_stop
                    print(f"✅ Initial ATR Stop Set: ₹{initial_stop:.2f} (Entry=₹{exec_price:.2f}, ATR={atr_value:.2f})")
            
            self._last_trade_time[signal.symbol] = datetime.now()
            
            # Background tasks (non-blocking)
            asyncio.create_task(self.strategy.manager.broadcast({
                "type": "active_stock_update",
                "payload": {"symbol": signal.symbol, "mode": "stock_options"}
            }))
            
            print(f"✅ Trade executed - {signal.symbol} cooldown started")
            print(f"{'='*60}\n")
        except Exception as e:
            print(f"\n❌ TRADE EXECUTION ERROR: {e}")
            import traceback
            traceback.print_exc()
            print(f"{'='*60}\n")
    
    async def _check_stock_supertrend_early(self, symbol: str, option_type: str) -> bool:
        """Validate 3-candle trend continuation on stock"""
        try:
            stock_token = await self._get_stock_token(symbol)
            if not stock_token:
                print(f"❌ {symbol}: Could not get stock token")
                return False
            
            from .kite import kite
            from datetime import timedelta
            now = datetime.now()
            from_time = now - timedelta(minutes=15)
            
            stock_history = await asyncio.to_thread(
                kite.historical_data,
                stock_token,
                from_time,
                now,
                "minute"
            )
            
            if not stock_history or len(stock_history) < 3:
                print(f"⚠️ {symbol}: Insufficient data, allowing trade")
                return True
            
            last_3 = stock_history[-3:]
            
            if option_type == "CE":
                green_count = sum(1 for c in last_3 if c['close'] > c['open'])
                if green_count >= 2:
                    print(f"✅ {symbol}: CE trend strong ({green_count}/3 green candles)")
                    return True
                print(f"❌ {symbol}: CE trend weak ({green_count}/3 green candles)")
                return False
            else:
                red_count = sum(1 for c in last_3 if c['close'] < c['open'])
                if red_count >= 2:
                    print(f"✅ {symbol}: PE trend strong ({red_count}/3 red candles)")
                    return True
                print(f"❌ {symbol}: PE trend weak ({red_count}/3 red candles)")
                return False
            
        except Exception as e:
            print(f"⚠️ Trend check error for {symbol}: {e}, allowing trade")
            return True
    
    async def _get_stock_token(self, symbol: str) -> Optional[int]:
        """Get stock token for WebSocket subscription"""
        try:
            from .kite import kite
            instruments = await asyncio.to_thread(kite.instruments, 'NSE')
            stock = next((i for i in instruments if i.get('tradingsymbol') == symbol), None)
            return stock.get('instrument_token') if stock else None
        except Exception as e:
            print(f"❌ Error fetching stock token for {symbol}: {e}")
            return None
    

    


    def _validate_option_momentum(self, option_symbol: str) -> bool:
        """Validate option momentum: current price > previous close (green candle)"""
        try:
            current_price = self.data_manager.prices.get(option_symbol)
            if not current_price or current_price <= 0:
                print(f"❌ {option_symbol}: No current price")
                return False
            
            # Get option candle history
            candles = self._option_candle_history.get(option_symbol, [])
            if not candles:
                print(f"❌ {option_symbol}: No candle history")
                return False
            
            prev_close = candles[-1].get('close', 0)
            if prev_close <= 0:
                print(f"⚠️ {option_symbol}: Invalid prev close, allowing entry")
                return True
            
            if current_price < prev_close:
                print(f"❌ {option_symbol}: Not green - Current ₹{current_price:.2f} < Prev Close ₹{prev_close:.2f}")
                return False
            
            print(f"✅ {option_symbol}: Green candle - Current ₹{current_price:.2f} >= Prev Close ₹{prev_close:.2f}")
            return True
            
        except Exception as e:
            print(f"❌ Option momentum validation error: {e}")
            return False
    

    def _validate_candle_structure(self, option_symbol: str) -> bool:
        """Validate candle structure: strong green candle with 0.3% body + breakout above prev close"""
        try:
            candles = self._option_candle_history.get(option_symbol, [])
            current_price = self.data_manager.prices.get(option_symbol, 0)
            
            if len(candles) < 1 or current_price <= 0:
                print(f"❌ {option_symbol}: Insufficient data")
                return False
            
            current = candles[-1]
            close = current.get('close', 0)
            open_price = current.get('open', 0)
            
            if close <= 0 or open_price <= 0:
                print(f"❌ {option_symbol}: Invalid candle data")
                return False
            
            # Check strong green candle (close > open) - LAST COMPLETED CANDLE
            if close <= open_price:
                print(f"❌ {option_symbol}: Last candle not green (close ₹{close:.2f} <= open ₹{open_price:.2f})")
                return False
            
            # CRITICAL: Check if CURRENT FORMING CANDLE is also green
            # Current candle's open = last candle's close
            if current_price < close:
                print(f"❌ {option_symbol}: Current forming candle not green (current ₹{current_price:.2f} < last close ₹{close:.2f})")
                return False
            print(f"✅ {option_symbol}: Current candle is green (current ₹{current_price:.2f} >= last close ₹{close:.2f})")
            
            # Check 0.3% minimum body
            body_pct = ((close - open_price) / open_price) * 100
            if body_pct < 0.3:
                print(f"❌ {option_symbol}: Body {body_pct:.2f}% < 0.3% requirement")
                return False
            
            # Breakout logic: current price must break above previous close (regardless of prev candle color)
            if len(candles) >= 2:
                prev_candle = candles[-2]
                prev_close = prev_candle.get('close', 0)
                prev_open = prev_candle.get('open', 0)
                
                if prev_close > 0 and prev_open > 0:
                    # Determine previous candle color
                    prev_is_green = prev_close > prev_open
                    candle_color = "green" if prev_is_green else "red"
                    
                    # Both red and green: break above previous close
                    if current_price <= prev_close:
                        print(f"❌ {option_symbol}: No breakout above prev {candle_color} close (current ₹{current_price:.2f} <= prev close ₹{prev_close:.2f})")
                        return False
                    print(f"✅ {option_symbol}: Breakout above prev {candle_color} close (current ₹{current_price:.2f} > prev close ₹{prev_close:.2f})")
            
            print(f"✅ {option_symbol}: Strong green candle, body {body_pct:.2f}%")
            return True
            
        except Exception as e:
            print(f"❌ Candle structure validation error: {e}")
            return False
    
    def _validate_momentum_checks(self, option_symbol: str) -> bool:
        """Validate momentum: 2/3 checks must pass (rising, accelerating, synchronized)"""
        try:
            candles = self._option_candle_history.get(option_symbol, [])
            current_price = self.data_manager.prices.get(option_symbol, 0)
            
            if len(candles) < 2 or current_price <= 0:
                print(f"❌ {option_symbol}: Insufficient data for momentum checks")
                return False
            
            passed = 0
            
            # Check 1: Price actively rising (current > last close)
            last_close = candles[-1].get('close', 0)
            if last_close > 0 and current_price > last_close:
                print(f"  ✅ Rising: ₹{current_price:.2f} > ₹{last_close:.2f}")
                passed += 1
            else:
                print(f"  ❌ Rising: ₹{current_price:.2f} <= ₹{last_close:.2f}")
            
            # Check 2: Price accelerating (current momentum > previous momentum)
            if len(candles) >= 2:
                prev_candle = candles[-2]
                curr_candle = candles[-1]
                
                prev_momentum = ((curr_candle.get('close', 0) - prev_candle.get('close', 0)) / prev_candle.get('close', 1)) * 100 if prev_candle.get('close', 0) > 0 else 0
                curr_momentum = ((current_price - curr_candle.get('close', 0)) / curr_candle.get('close', 1)) * 100 if curr_candle.get('close', 0) > 0 else 0
                
                if curr_momentum > prev_momentum:
                    print(f"  ✅ Accelerating: {curr_momentum:.2f}% > {prev_momentum:.2f}%")
                    passed += 1
                else:
                    print(f"  ❌ Accelerating: {curr_momentum:.2f}% <= {prev_momentum:.2f}%")
            
            # Check 3: Option momentum synchronized (current > open of last candle)
            last_open = candles[-1].get('open', 0)
            if last_open > 0 and current_price > last_open:
                print(f"  ✅ Synchronized: ₹{current_price:.2f} > open ₹{last_open:.2f}")
                passed += 1
            else:
                print(f"  ❌ Synchronized: ₹{current_price:.2f} <= open ₹{last_open:.2f}")
            
            if passed >= 2:
                print(f"✅ {option_symbol}: Momentum {passed}/3 checks passed")
                return True
            else:
                print(f"❌ {option_symbol}: Momentum {passed}/3 checks passed (need 2/3)")
                return False
            
        except Exception as e:
            print(f"❌ Momentum validation error: {e}")
            return False

    async def check_exit_conditions(self, position: Dict) -> Tuple[bool, Optional[str]]:
        """Check if liquidity position should be exited - ATR first, then profit targets"""
        if not position:
            return False, None
            
        symbol = position.get('symbol')
        entry_price = position.get('entry_price')
        current_price = self.data_manager.prices.get(symbol)
        direction = position.get('direction')
        
        if not current_price or not entry_price:
            return False, None
        
        if 'entry_time' not in position:
            position['entry_time'] = datetime.now()
        
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        
        # 0. Minimum Hold Time - FIRST (prevent premature exits)
        entry_time = position.get('entry_time')
        if entry_time:
            if isinstance(entry_time, str):
                from dateutil import parser
                entry_time = parser.parse(entry_time)
            hold_duration = (datetime.now() - entry_time).total_seconds()
            if hold_duration < 180:  # 3 minutes minimum
                return False, None
        
        # 1. EOD Square-Off Check
        from config.trading_config import CUTOFF_TIME_STR
        cutoff_hour, cutoff_minute = map(int, CUTOFF_TIME_STR.split(':'))
        current_time = datetime.now().time()
        cutoff_time = time(cutoff_hour, cutoff_minute)
        
        if current_time >= cutoff_time:
            print(f"\n🚨 EOD SQUARE-OFF TRIGGERED")
            print(f"  Current Time: {current_time.strftime('%H:%M:%S')}")
            print(f"  Cutoff Time: {cutoff_time.strftime('%H:%M:%S')}")
            print(f"  Position: {symbol} @ ₹{current_price:.2f} (PnL: {pnl_pct:+.2f}%)")
            return True, "EOD_SquareOff"
        
        # 2. ATR Trailing Stop - PRIMARY (period 10, multiplier 1.2)
        candles = self._option_candle_history.get(symbol, [])
        candle_count = len(candles)
        print(f"\n🔍 ATR: PnL={pnl_pct:.2f}%, Candles={candle_count}, Current=₹{current_price:.2f}, Direction={direction}")
        
        # CRITICAL FIX: Fetch historical data if insufficient candles
        if candle_count < 10:
            # Find instrument token for this option
            option_token = None
            for opt in self.strategy.option_instruments:
                if opt.get('tradingsymbol') == symbol:
                    option_token = opt.get('instrument_token')
                    break
            
            if option_token:
                print(f"  📥 Fetching historical data for {symbol} (token: {option_token})...")
                await self._backfill_candle_history(option_token, symbol)
                candle_count = len(self._option_candle_history.get(symbol, []))
                print(f"  ✅ After fetch: {candle_count}/10 candles")
            else:
                print(f"  ⚠️ Could not find token for {symbol}")
        
        if candle_count >= 10:
            atr_value = self._get_atr_value(symbol, period=10)
            
            if atr_value and atr_value > 0:
                atr_multiplier = 1.2
                
                if direction == 'CE':
                    # CE: Trail below max price
                    max_price = position.get('max_price', entry_price)
                    if current_price > max_price:
                        position['max_price'] = current_price
                        max_price = current_price
                    
                    new_stop = max_price - (atr_value * atr_multiplier)
                    old_stop = position.get('trailing_stop', 0)
                    
                    # Only trail up, never down
                    if new_stop > old_stop:
                        position['trailing_stop'] = new_stop
                        position['atr_trailing_sl'] = new_stop
                        print(f"  📈 CE TRAIL UP: ₹{old_stop:.2f} → ₹{new_stop:.2f} (Max=₹{max_price:.2f}, ATR={atr_value:.2f})")
                    
                    # Exit if price drops below stop
                    if current_price <= position.get('trailing_stop', 0):
                        print(f"  🚨 CE EXIT: ₹{current_price:.2f} <= Stop ₹{position['trailing_stop']:.2f}")
                        return True, f"ATR_TrailingStop_{pnl_pct:.1f}%"
                
                elif direction == 'PE':
                    # PE: Trail above min price
                    min_price = position.get('min_price', entry_price)
                    if current_price < min_price:
                        position['min_price'] = current_price
                        min_price = current_price
                    
                    new_stop = min_price + (atr_value * atr_multiplier)
                    old_stop = position.get('trailing_stop', float('inf'))
                    
                    # Only trail down, never up
                    if new_stop < old_stop:
                        position['trailing_stop'] = new_stop
                        position['atr_trailing_sl'] = new_stop
                        print(f"  📉 PE TRAIL DOWN: ₹{old_stop:.2f} → ₹{new_stop:.2f} (Min=₹{min_price:.2f}, ATR={atr_value:.2f})")
                    
                    # Exit if price rises above stop
                    if current_price >= position.get('trailing_stop', float('inf')):
                        print(f"  🚨 PE EXIT: ₹{current_price:.2f} >= Stop ₹{position['trailing_stop']:.2f}")
                        return True, f"ATR_TrailingStop_{pnl_pct:.1f}%"
            else:
                print(f"  ⚠️ ATR not ready (need {10-candle_count+1} more candles)")
        else:
            print(f"  ⏳ Building ATR: {candle_count}/10 candles")
        
        # 3. Profit Target Exit - SECONDARY
        profit_target_pct = float(self.params.get('profit_target_pct', 0)) if self.params.get('profit_target_pct') else 0
        if profit_target_pct > 0 and pnl_pct >= profit_target_pct:
            return True, f"ProfitTarget_{profit_target_pct}%"
        
        # 4. Partial Profit Exit - SECONDARY
        partial_profit_pct = float(self.params.get('partial_profit_pct', 0)) if self.params.get('partial_profit_pct') else 0
        if partial_profit_pct > 0 and pnl_pct >= partial_profit_pct and not position.get('partial_exit_done', False):
            position['partial_exit_done'] = True
            return True, f"PartialProfit_{partial_profit_pct}%"
        
        return False, None
    
    def _store_option_candle(self, symbol: str):
        """Store option candle data for ATR calculation - builds from live ticks"""
        try:
            current_price = self.data_manager.prices.get(symbol)
            if not current_price or current_price <= 0:
                return
            
            if symbol not in self._option_candle_history:
                self._option_candle_history[symbol] = []
            
            now = datetime.now()
            current_minute = now.replace(second=0, microsecond=0)
            
            # Update liquidity_engine history (for ATR)
            if not self._option_candle_history[symbol]:
                self._option_candle_history[symbol].append({
                    'open': current_price,
                    'high': current_price,
                    'low': current_price,
                    'close': current_price,
                    'timestamp': current_minute
                })
            else:
                last_candle = self._option_candle_history[symbol][-1]
                
                if last_candle['timestamp'] < current_minute:
                    self._option_candle_history[symbol].append({
                        'open': current_price,
                        'high': current_price,
                        'low': current_price,
                        'close': current_price,
                        'timestamp': current_minute
                    })
                else:
                    last_candle['high'] = max(last_candle['high'], current_price)
                    last_candle['low'] = min(last_candle['low'], current_price)
                    last_candle['close'] = current_price
            
            if len(self._option_candle_history[symbol]) > 20:
                self._option_candle_history[symbol] = self._option_candle_history[symbol][-20:]
            
            # CRITICAL: Update data_manager.option_candles for chart display (no supertrend in chart data)
            self.data_manager.option_candles[symbol] = {
                'minute': current_minute,
                'open': self._option_candle_history[symbol][-1]['open'],
                'high': self._option_candle_history[symbol][-1]['high'],
                'low': self._option_candle_history[symbol][-1]['low'],
                'close': current_price
            }
        except Exception as e:
            print(f"Error storing candle: {e}")
    
    def _get_atr_value(self, symbol: str, period: int = 10) -> Optional[float]:
        """Calculate ATR from option candle history"""
        try:
            if symbol not in self._option_candle_history:
                return None
            
            candles = self._option_candle_history[symbol]
            if len(candles) < period + 1:  # Need period+1 candles (for prev_close)
                return None
            
            # Calculate True Range for each candle
            true_ranges = []
            for i in range(1, len(candles)):
                high = candles[i]['high']
                low = candles[i]['low']
                prev_close = candles[i-1]['close']
                
                if high <= 0 or low <= 0 or prev_close <= 0:
                    continue
                
                # True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            if len(true_ranges) < period:
                return None
            
            # ATR = Average of last 'period' true ranges
            atr = sum(true_ranges[-period:]) / period
            return float(atr) if atr > 0 else None
            
        except Exception as e:
            print(f"Error calculating ATR: {e}")
            return None
    
    def _calculate_supertrend(self, symbol: str, length: int = 5, multiplier: float = 0.7) -> Optional[str]:
        """Calculate supertrend for stock options (returns 'bullish' or 'bearish')"""
        try:
            if symbol not in self._option_candle_history:
                return None
            
            candles = self._option_candle_history[symbol]
            if len(candles) < 15:  # Need minimum 15 candles for supertrend
                return None
            
            import pandas as pd
            import pandas_ta as ta
            
            df = pd.DataFrame(candles)
            result = ta.supertrend(df['high'], df['low'], df['close'], length=length, multiplier=multiplier)
            
            if result is None or result.empty:
                return None
            
            last_close = df['close'].iloc[-1]
            last_supertrend = result.iloc[-1, 0]
            
            return 'bullish' if last_close > last_supertrend else 'bearish'
        except Exception:
            return None

    
    def _get_optimal_strike(self, spot_price: float, option_type: str, momentum: float) -> float:
        """Select optimal strike - always ATM"""
        interval = self._get_strike_interval(spot_price)
        atm = round(spot_price / interval) * interval
        return atm
    
    def _get_top_gainer(self, stocks_data: List[Dict]) -> Optional[Dict]:
        """Get TOP 1 gainer from dashboard top 10 only"""
        try:
            from config.trading_config import MIN_LIQUIDITY_CHANGE_PCT
            gainers = [stock for stock in stocks_data 
                      if float(str(stock.get('priceChange', '0')).replace('%', '').replace('+', '')) >= MIN_LIQUIDITY_CHANGE_PCT]
            if not gainers:
                print(f"Liquidity: 📊 No gainers ≥ +{MIN_LIQUIDITY_CHANGE_PCT}% found in {len(stocks_data)} stocks")
                return None
            top = max(gainers, key=lambda x: float(str(x.get('priceChange', '0')).replace('%', '').replace('+', '')))
            print(f"Liquidity: 📈 Top gainer: {top.get('stock')} at {top.get('priceChange')}")
            return top
        except Exception as e:
            print(f"Liquidity: Error in _get_top_gainer: {e}")
            return None
    
    def _get_top_loser(self, stocks_data: List[Dict]) -> Optional[Dict]:
        """Get TOP 1 loser from dashboard top 10 only"""
        try:
            from config.trading_config import MIN_LIQUIDITY_CHANGE_PCT
            losers = [stock for stock in stocks_data 
                     if float(str(stock.get('priceChange', '0')).replace('%', '').replace('+', '')) <= -MIN_LIQUIDITY_CHANGE_PCT]
            if not losers:
                print(f"Liquidity: 📊 No losers ≤ -{MIN_LIQUIDITY_CHANGE_PCT}% found in {len(stocks_data)} stocks")
                return None
            top = min(losers, key=lambda x: float(str(x.get('priceChange', '0')).replace('%', '').replace('+', '')))
            print(f"Liquidity: 📉 Top loser: {top.get('stock')} at {top.get('priceChange')}")
            return top
        except Exception as e:
            print(f"Liquidity: Error in _get_top_loser: {e}")
            return None
    
    def _get_strike_interval(self, price: float) -> float:
        """NSE standard strike intervals"""
        if price < 150: return 2.5
        elif price < 500: return 5.0
        elif price < 1000: return 10.0
        elif price < 2500: return 20.0
        elif price < 5000: return 50.0
        else: return 100.0
    
    def _validate_option_liquidity(self, option_symbol: str) -> tuple[bool, str]:
        """Validate option has sufficient liquidity before entry"""
        try:
            from core.kite import kite
            quote = kite.quote([f"NFO:{option_symbol}"])
            if not quote or f"NFO:{option_symbol}" not in quote:
                return False, "Quote unavailable"
            
            data = quote[f"NFO:{option_symbol}"]
            
            # Check volume
            volume = data.get('volume', 0)
            min_vol = self.params.get('min_option_volume', 50)
            if volume < min_vol:
                return False, f"Low volume ({volume} < {min_vol})"
            
            # Check OI
            oi = data.get('oi', 0)
            min_oi = self.params.get('min_option_oi', 200)
            if oi < min_oi:
                return False, f"Low OI ({oi} < {min_oi})"
            
            # Check bid-ask spread
            depth = data.get('depth', {})
            buy_depth = depth.get('buy', [])
            sell_depth = depth.get('sell', [])
            
            if buy_depth and sell_depth:
                bid = buy_depth[0].get('price', 0)
                ask = sell_depth[0].get('price', 0)
                if bid > 0:
                    spread_pct = ((ask - bid) / bid) * 100
                    max_spread = self.params.get('max_bid_ask_spread_pct', 12)
                    if spread_pct > max_spread:
                        return False, f"Wide spread ({spread_pct:.1f}% > {max_spread}%)"
            
            print(f"✅ Liquidity OK: {option_symbol} (Vol: {volume}, OI: {oi})")
            return True, "OK"
            
        except Exception as e:
            return False, f"Validation error: {e}"
    
    def _validate_expiry(self, option_data: dict) -> tuple[bool, str]:
        """Ensure option has sufficient time to expiry"""
        try:
            from datetime import date
            expiry = option_data.get('expiry')
            if not expiry:
                return False, "No expiry date"
            
            days_to_expiry = (expiry - date.today()).days
            if days_to_expiry < 2:
                return False, f"Expiry too soon ({days_to_expiry} days)"
            
            return True, f"{days_to_expiry} days to expiry"
        except Exception as e:
            return False, f"Expiry check error: {e}"
    
    def _validate_atr_ready(self, symbol: str) -> tuple[bool, str]:
        """Check if ATR is ready before taking trade"""
        if symbol not in self._option_candle_history:
            return False, "No candle history"
        
        candle_count = len(self._option_candle_history[symbol])
        if candle_count < 11:  # Need 11 candles for 10-period ATR
            return False, f"ATR not ready ({candle_count}/11 candles)"
        
        atr = self._get_atr_value(symbol, period=10)
        if not atr or atr <= 0:
            return False, "ATR calculation failed"
        
        return True, f"ATR ready: {atr:.2f}"
    
    def _log_once(self, symbol: str, key: str, message: str, cooldown: int = 60):
        """Log message only once per cooldown period to avoid spam"""
        now = datetime.now().timestamp()
        log_key = f"{symbol}_{key}"
        
        if log_key not in self._last_log_time or (now - self._last_log_time[log_key]) >= cooldown:
            print(message)
            self._last_log_time[log_key] = now
