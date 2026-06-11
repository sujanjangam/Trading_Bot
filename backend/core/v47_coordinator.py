# backend/core/v47_coordinator.py
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Tuple

class V47StrategyCoordinator:
    """V47.14 Strategy Coordination System"""
    
    def __init__(self, strategy):
        self.strategy = strategy
        
        self.atr_squeeze_detected = False
        self.squeeze_range = {'high': 0, 'low': 0}
        self.pending_steep_signal = None
        
        self.option_data_dfs = {}
        self.option_supertrend_state = {}
        self.previous_option_candles = {}
        
        self.volatility_engine = V47VolatilityBreakoutEngine(strategy)
        self.supertrend_engine = V47SupertrendFlipEngine(strategy)
        self.red_green_engine = V47RedGreenContinuationEngine(strategy, self)
        self.trend_engine = V47TrendContinuationEngine(strategy)
        self.counter_engine = V47CounterTrendEngine(strategy)
        
        self.engines = [
            self.volatility_engine,
            self.supertrend_engine,
            self.red_green_engine,
            self.trend_engine,
            self.counter_engine
        ]
    
    async def on_new_candle(self):
        """Called when a new minute candle is formed"""
        if self.strategy.config.get("trading_mode") in ["stock_options", "equity"]:
            return
        
        self._check_atr_squeeze()
        await self.scan_for_signals()
    
    async def continuous_monitoring(self):
        """Called every few seconds for intra-candle analysis"""
        if not await self.strategy.can_trade():
            return
        
        if self.strategy.config.get("trading_mode") in ["stock_options", "equity"]:
            return
            
        await self.scan_for_signals()
    
    async def scan_for_signals(self):
        """Scan all engines in priority order"""
        for engine in self.engines:
            try:
                result = await engine.check_entry()
                
                if len(result) == 3:
                    side, trigger, option = result
                    validation_data = None
                elif len(result) == 4:
                    side, trigger, option, validation_data = result
                else:
                    continue
                
                if side and trigger and option:
                    if validation_data and 'prev_close' in validation_data:
                        custom_entry_price = validation_data['prev_close'] + 0.10
                        await self.strategy._log_debug("V47.14", f"🎯 Red-Green signal validated: {trigger}")
                        await self.strategy.take_trade(trigger, option, custom_entry_price=custom_entry_price)
                    else:
                        is_valid = await self.universal_validation_gauntlet(option, side, trigger)
                        
                        if is_valid:
                            await self.strategy._log_debug("V47.14", f"🎯 Signal validated: {trigger}")
                            await self.strategy.take_trade(trigger, option)
                        return
                        
            except Exception as e:
                await self.strategy._log_debug("V47.14", f"Engine error: {e}")
    
    async def universal_validation_gauntlet(self, option, side, trigger):
        """V47.14 Universal Validation Gauntlet"""
        # Skip for stock options modes
        if self.strategy.config.get("trading_mode") in ["stock_options", "high_volume_options"]:
            return False
        
        is_reversal = 'Reversal' in trigger or 'Flip' in trigger or 'Counter' in trigger
        
        try:
            results = await asyncio.gather(
                self._is_atm_confirming(side, is_reversal=is_reversal),
                self._validate_candle_conditions(option, side, is_reversal),
                self._validate_momentum_conditions(option, side),
                return_exceptions=True
            )
            
            atm_valid, candle_valid, momentum_valid = results
            
            if not atm_valid:
                await self.strategy._log_debug("Gauntlet", f"❌ ATM confirmation failed for {trigger}")
                return False
            
            if not candle_valid:
                await self.strategy._log_debug("Gauntlet", f"❌ Candle validation failed for {trigger}")
                return False
            
            if not momentum_valid:
                await self.strategy._log_debug("Gauntlet", f"❌ Momentum validation failed for {trigger}")
                return False
            
            await self.strategy._log_debug("Gauntlet", f"✅ All validations passed for {trigger}")
            return True
            
        except Exception as e:
            await self.strategy._log_debug("Gauntlet", f"❌ Validation error: {e}")
            return False
    
    def _check_atr_squeeze(self, lookback_period=30, squeeze_range_candles=5):
        """V47.14 ATR Squeeze Detection"""
        # Skip for stock options modes
        if self.strategy.config.get("trading_mode") in ["stock_options", "high_volume_options"]:
            return {'in_squeeze': False}
        
        if len(self.strategy.data_manager.data_df) < lookback_period or 'atr' not in self.strategy.data_manager.data_df.columns:
            return {'in_squeeze': False}
        
        recent_atr = self.strategy.data_manager.data_df['atr'].tail(lookback_period)
        
        if recent_atr.iloc[-1] <= recent_atr.min():
            if not self.atr_squeeze_detected:
                squeeze_candles = self.strategy.data_manager.data_df.tail(squeeze_range_candles)
                self.squeeze_range['high'] = float(squeeze_candles['high'].max())
                self.squeeze_range['low'] = float(squeeze_candles['low'].min())
                self.atr_squeeze_detected = True
                asyncio.create_task(self.strategy._log_debug("V47.14", f"🔥 ATR Squeeze Detected. High={self.squeeze_range['high']:.2f}, Low={self.squeeze_range['low']:.2f}"))
            return {'in_squeeze': True, 'range_high': self.squeeze_range['high'], 'range_low': self.squeeze_range['low']}
        else:
            if self.atr_squeeze_detected:
                self.atr_squeeze_detected = False
                asyncio.create_task(self.strategy._log_debug("V47.14", "ATR Squeeze ended."))
            return {'in_squeeze': False}
    
    async def _is_atm_confirming(self, side, is_reversal=False):
        lookback_minutes = 1 if is_reversal else 3
        performance_spread = 1.0 if is_reversal else 2.0
        
        spot = self.strategy.data_manager.prices.get(self.strategy.index_symbol)
        if not spot:
            await self.strategy._log_debug("ATM Check", f"❌ No index price available")
            return False
        
        atm_cache = self.strategy.data_manager.atm_cache
        import time
        cache_is_valid = (atm_cache.get("last_update", 0) > time.time() - 3 and 
                         atm_cache.get("ce_symbol") and atm_cache.get("pe_symbol"))
        
        if cache_is_valid:
            ce_symbol = atm_cache["ce_symbol"]
            pe_symbol = atm_cache["pe_symbol"]
        else:
            atm_strike = self.strategy.config.get('strike_step', 50) * round(spot / self.strategy.config.get('strike_step', 50))
            ce_opt = self.strategy.get_entry_option('CE', atm_strike)
            pe_opt = self.strategy.get_entry_option('PE', atm_strike)
            
            if not (ce_opt and pe_opt):
                await self.strategy._log_debug("ATM Check", f"❌ ATM Options not found: CE={bool(ce_opt)}, PE={bool(pe_opt)}")
                return False
            
            ce_symbol = ce_opt['tradingsymbol']
            pe_symbol = pe_opt['tradingsymbol']
        
        ce_current_price = self.strategy.data_manager.prices.get(ce_symbol)
        pe_current_price = self.strategy.data_manager.prices.get(pe_symbol)
        ce_past_price = self._get_price_from_history(ce_symbol, lookback_minutes)
        pe_past_price = self._get_price_from_history(pe_symbol, lookback_minutes)
        
        if not all([ce_current_price, pe_current_price, ce_past_price, pe_past_price]):
            await self.strategy._log_debug("ATM Check", f"❌ Insufficient price data for ATM confirmation")
            return False
        
        ce_pct_change = ((ce_current_price - ce_past_price) / ce_past_price) * 100 if ce_past_price > 0 else 0
        pe_pct_change = ((pe_current_price - pe_past_price) / pe_past_price) * 100 if pe_past_price > 0 else 0
        
        spread = ce_pct_change - pe_pct_change
        
        if side == 'CE':
            is_confirming = spread >= performance_spread
        elif side == 'PE':
            is_confirming = spread <= -performance_spread
        else:
            is_confirming = False
        
        await self.strategy._log_debug("ATM Check", 
            f"{'✅' if is_confirming else '❌'} {side} ATM Confirmation: "
            f"CE%={ce_pct_change:.2f}, PE%={pe_pct_change:.2f}, "
            f"Spread={spread:.2f}, Required={'≥' if side == 'CE' else '≤'}{performance_spread if side == 'CE' else -performance_spread}")
        
        return is_confirming
    
    async def _validate_candle_conditions(self, option, side, is_reversal):
        symbol = option['tradingsymbol']
        current_price = self.strategy.data_manager.prices.get(symbol)
        
        if not current_price:
            return False
        
        option_candle = getattr(self.strategy.data_manager, 'option_candles', {}).get(symbol)
        previous_candle = getattr(self.strategy.data_manager, 'previous_option_candles', {}).get(symbol)
        
        if is_reversal:
            return True
        
        if option_candle:
            open_price = option_candle.get('open', current_price)
            high_price = option_candle.get('high', current_price)
            low_price = option_candle.get('low', current_price)
            
            if current_price <= open_price:
                await self.strategy._log_debug("Candle Check", f"❌ {symbol}: Not a green candle (Price: {current_price}, Open: {open_price})")
                return False
            
            minimum_body_price = open_price * 1.005
            if current_price < minimum_body_price:
                await self.strategy._log_debug("Candle Check", f"❌ {symbol}: Body too small (Current: {current_price}, Min Required: {minimum_body_price:.2f})")
                return False
            
            candle_range = high_price - low_price
            if candle_range > 0:
                top_70_percent_threshold = low_price + (candle_range * 0.30)
                if current_price < top_70_percent_threshold:
                    await self.strategy._log_debug("Candle Check", f"❌ {symbol}: Price not in top 70% of range (Price: {current_price}, Threshold: {top_70_percent_threshold:.2f})")
                    return False
        
        if previous_candle:
            prev_high = previous_candle.get('high', 0)
            prev_close = previous_candle.get('close', 0)
            prev_low = previous_candle.get('low', 0)
            
            breakout_condition_1 = current_price > prev_high
            
            if option_candle:
                current_low = option_candle.get('low', current_price)
                higher_low_condition = current_low > prev_low
                
                current_range = high_price - current_low if option_candle else 0
                upper_half_threshold = current_low + (current_range * 0.5) if current_range > 0 else current_price
                price_in_upper_half = current_price >= upper_half_threshold
                
                breakout_condition_2 = higher_low_condition and price_in_upper_half
            else:
                breakout_condition_2 = False
            
            if not (breakout_condition_1 or breakout_condition_2):
                await self.strategy._log_debug("Candle Check", f"❌ {symbol}: No valid breakout structure (Price: {current_price}, Prev High: {prev_high})")
                return False
        
        await self.strategy._log_debug("Candle Check", f"✅ {symbol}: All candle validations passed")
        return True
    
    async def _validate_momentum_conditions(self, option, side):
        symbol = option['tradingsymbol']
        
        momentum_checks = []
        
        is_rising = self._is_price_actively_rising(symbol, ticks=3)
        momentum_checks.append(("Price Rising (3 ticks)", is_rising))
        
        is_accelerating = self._is_accelerating(symbol)
        momentum_checks.append(("Price Accelerating", is_accelerating))
        
        index_price_trend = self._check_index_momentum_sync(symbol)
        momentum_checks.append(("Index/Option Sync", index_price_trend))
        
        passed_checks = sum(1 for _, passed in momentum_checks if passed)
        
        await self.strategy._log_debug("Momentum Check", 
            f"{'✅' if passed_checks >= 2 else '❌'} {symbol}: "
            f"{passed_checks}/3 momentum checks passed: "
            f"{', '.join([f'{name}={result}' for name, result in momentum_checks])}")
        
        return passed_checks >= 2
    
    def _check_index_momentum_sync(self, option_symbol):
        index_history = self.strategy.data_manager.price_history.get(self.strategy.index_symbol, [])
        option_history = self.strategy.data_manager.price_history.get(option_symbol, [])
        
        if len(index_history) < 3 or len(option_history) < 3:
            return True
        
        index_prices = [p[1] for p in index_history[-3:]]
        option_prices = [p[1] for p in option_history[-3:]]
        
        index_rising = index_prices[-1] > index_prices[0]
        option_rising = option_prices[-1] > option_prices[0]
        
        return index_rising and option_rising
    
    def _is_accelerating(self, symbol, lookback_ticks=20, acceleration_factor=1.5):
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) < lookback_ticks: 
            return False
        
        prices = [p for ts, p in history[-lookback_ticks:]]
        diffs = np.diff(prices)
        
        if len(diffs) < 2: 
            return False

        current_velocity = diffs[-1]
        avg_velocity = np.mean(diffs[:-1])

        if current_velocity <= 0: 
            return False
            
        if avg_velocity > 0 and current_velocity > avg_velocity * acceleration_factor:
            return True
            
        return False
    
    def _is_price_actively_rising(self, symbol, ticks=3):
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) < ticks:
            return False
        
        recent_prices = [p[1] for p in history[-ticks:]]
        for i in range(1, len(recent_prices)):
            if recent_prices[i] <= recent_prices[i-1]:
                return False
        return True
    
    def _get_price_from_history(self, symbol, lookback_minutes):
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if not history:
            return None
        
        lookback_time = datetime.now().timestamp() - (lookback_minutes * 60)
        
        for tick_time, price in reversed(history):
            if tick_time <= lookback_time:
                return price
        
        return history[0][1] if history else None
    
    async def _enhanced_validate_entry_conditions_with_candle_color(self, option, side, log=False):
        symbol = option['tradingsymbol']
        current_price = self.strategy.data_manager.prices.get(symbol)
        
        if not current_price:
            return False, {}
        
        prev_candle = self.strategy.data_manager.previous_option_candles.get(symbol)
        prev_close = prev_candle.get('close', current_price) if prev_candle else current_price
        
        validation_result = (
            await self._is_atm_confirming(side, is_reversal=False) and
            await self._validate_candle_conditions(option, side, is_reversal=False) and 
            await self._validate_momentum_conditions(option, side)
        )
        
        validation_data = {
            'prev_close': prev_close,
            'current_price': current_price,
            'symbol': symbol
        }
        
        if log:
            asyncio.create_task(self.strategy._log_debug("Enhanced Validation", 
                f"{'✅' if validation_result else '❌'} {side} validation for {symbol}: "
                f"Current={current_price:.2f}, PrevClose={prev_close:.2f}"))
        
        return validation_result, validation_data


class V47VolatilityBreakoutEngine:
    def __init__(self, strategy):
        self.strategy = strategy
    
    async def check_entry(self):
        coordinator = self.strategy.v47_coordinator
        
        if not coordinator.atr_squeeze_detected:
            return None, None, None
        
        current_price = self.strategy.data_manager.prices.get(self.strategy.index_symbol)
        if not current_price:
            return None, None, None
        
        breakout_side = None
        if current_price > coordinator.squeeze_range['high']:
            breakout_side = 'CE'
        elif current_price < coordinator.squeeze_range['low']:
            breakout_side = 'PE'
        
        if breakout_side:
            if len(self.strategy.data_manager.data_df) > 0 and 'supertrend_uptrend' in self.strategy.data_manager.data_df.columns:
                last_candle = self.strategy.data_manager.data_df.iloc[-1]
                supertrend_bullish = last_candle.get('supertrend_uptrend', False)
                
                if (breakout_side == 'CE' and supertrend_bullish) or (breakout_side == 'PE' and not supertrend_bullish):
                    option = self.strategy.get_entry_option(breakout_side)
                    if option:
                        return breakout_side, f'V47_Volatility_Breakout_{breakout_side}', option
        
        return None, None, None


class V47SupertrendFlipEngine:
    def __init__(self, strategy):
        self.strategy = strategy
    
    async def check_entry(self):
        if len(self.strategy.data_manager.data_df) < 2 or 'supertrend_uptrend' not in self.strategy.data_manager.data_df.columns:
            return None, None, None
        
        last = self.strategy.data_manager.data_df.iloc[-1]
        prev = self.strategy.data_manager.data_df.iloc[-2]
        
        curr_uptrend = last['supertrend_uptrend']
        prev_uptrend = prev['supertrend_uptrend']
        
        if pd.isna(curr_uptrend) or pd.isna(prev_uptrend):
            return None, None, None
        
        if prev_uptrend is False and curr_uptrend is True and last['close'] > last['open']:
            option = self.strategy.get_entry_option('CE')
            if option:
                return 'CE', 'V47_Enhanced_Supertrend_Flip_CE', option
        
        elif prev_uptrend is True and curr_uptrend is False and last['close'] < last['open']:
            option = self.strategy.get_entry_option('PE')
            if option:
                return 'PE', 'V47_Enhanced_Supertrend_Flip_PE', option
        
        return None, None, None


class V47TrendContinuationEngine:
    def __init__(self, strategy):
        self.strategy = strategy
    
    async def check_entry(self):
        if not self.strategy.data_manager.trend_state or len(self.strategy.data_manager.data_df) < 2:
            return None, None, None
        
        current_price = self.strategy.data_manager.prices.get(self.strategy.index_symbol)
        if not current_price:
            return None, None, None
        
        for candle in self.strategy.data_manager.data_df.tail(5).itertuples():
            if (self.strategy.data_manager.trend_state == 'BULLISH' and current_price > candle.high):
                option = self.strategy.get_entry_option('CE')
                if option:
                    return 'CE', 'V47_Trend_Continuation_CE', option
            elif (self.strategy.data_manager.trend_state == 'BEARISH' and current_price < candle.low):
                option = self.strategy.get_entry_option('PE')
                if option:
                    return 'PE', 'V47_Trend_Continuation_PE', option
        
        return None, None, None


class V47CounterTrendEngine:
    def __init__(self, strategy):
        self.strategy = strategy
    
    async def check_entry(self):
        if len(self.strategy.data_manager.data_df) < 1 or not self.strategy.data_manager.trend_state:
            return None, None, None
        
        last = self.strategy.data_manager.data_df.iloc[-1]
        is_bullish_candle = last['close'] > last['open']
        
        if self.strategy.data_manager.trend_state == 'BULLISH' and not is_bullish_candle:
            option = self.strategy.get_entry_option('PE')
            if option:
                return 'PE', 'V47_Counter_Trend_PE', option
        elif self.strategy.data_manager.trend_state == 'BEARISH' and is_bullish_candle:
            option = self.strategy.get_entry_option('CE')
            if option:
                return 'CE', 'V47_Counter_Trend_CE', option
        
        return None, None, None


class V47RedGreenContinuationEngine:
    def __init__(self, strategy, coordinator=None):
        self.strategy = strategy
        self.coordinator = coordinator
    
    async def check_entry(self):
        if not self.strategy.data_manager.trend_state or len(self.strategy.data_manager.data_df) < 2:
            return None, None, None
        
        last_candle = self.strategy.data_manager.data_df.iloc[-1]
        is_green_index = last_candle['close'] > last_candle['open']
        is_red_index = last_candle['close'] < last_candle['open']
        
        if (self.strategy.data_manager.trend_state == 'BULLISH' and is_green_index):
            opt = self.strategy.get_entry_option('CE')
            if not opt:
                return None, None, None
            
            symbol = opt['tradingsymbol']
            
            if await self._validate_red_green_conditions(symbol, 'CE'):
                if self.coordinator:
                    is_valid, validation_data = await self.coordinator._enhanced_validate_entry_conditions_with_candle_color(opt, 'CE', log=True)
                    if is_valid:
                        return 'CE', 'RedGreen_Cont_CE', opt, validation_data
                else:
                    return 'CE', 'RedGreen_Cont_CE', opt
        
        elif (self.strategy.data_manager.trend_state == 'BEARISH' and is_red_index):
            opt = self.strategy.get_entry_option('PE') 
            if not opt:
                return None, None, None
            
            symbol = opt['tradingsymbol']
            
            if await self._validate_red_green_conditions(symbol, 'PE'):
                if self.coordinator:
                    is_valid, validation_data = await self.coordinator._enhanced_validate_entry_conditions_with_candle_color(opt, 'PE', log=True)
                    if is_valid:
                        return 'PE', 'RedGreen_Cont_PE', opt, validation_data
                else:
                    return 'PE', 'RedGreen_Cont_PE', opt
        
        return None, None, None
    
    async def _validate_red_green_conditions(self, symbol, side):
        current_price = self.strategy.data_manager.prices.get(symbol)
        if not current_price:
            return False
        
        option_candle = getattr(self.strategy.data_manager, 'option_candles', {}).get(symbol)
        if option_candle:
            is_option_green = current_price > option_candle.get('open', current_price)
            if not is_option_green:
                return False
        
        prev_candle = getattr(self.strategy.data_manager, 'previous_option_candles', {}).get(symbol)
        if prev_candle:
            if prev_candle['close'] < prev_candle['open']:
                trigger_price = prev_candle['high']
            else:
                trigger_price = prev_candle['close']
            
            return current_price > trigger_price
        
        return True
