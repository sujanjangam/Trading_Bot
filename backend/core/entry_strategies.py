# backend/core/entry_strategies.py
import math
import asyncio
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Tuple, Optional
from typing import Dict, Optional, Tuple

# Candlestick patterns omitted for brevity - keeping only what's needed

def is_bullish_engulfing(prev, last):
    if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']): return False
    prev_body = abs(prev['close'] - prev['open'])
    last_body = abs(last['close'] - last['open'])
    return (prev['close'] < prev['open'] and last['close'] > last['open'] and
            last['close'] > prev['open'] and last['open'] < prev['close'] and
            last_body > prev_body * 0.8)

def is_bearish_engulfing(prev, last):
    if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']): return False
    prev_body = abs(prev['close'] - prev['open'])
    last_body = abs(last['close'] - last['open'])
    return (prev['close'] > prev['open'] and last['close'] < last['open'] and
            last['open'] > prev['close'] and last['close'] < prev['open'] and
            last_body > prev_body * 0.8)

def is_morning_star(c1, c2, c3):
    if c1 is None or c2 is None or c3 is None or any(pd.isna(c['open']) for c in [c1, c2, c3]): return False
    b1 = abs(c1['close'] - c1['open']); b2 = abs(c2['close'] - c2['open']); b3 = abs(c3['close'] - c3['open'])
    return (c1['close'] < c1['open'] and b2 < b1 * 0.3 and c2['high'] < c1['close'] and 
            c3['open'] > c2['high'] and c3['close'] > c3['open'] and b3 > b1 * 0.6)

def is_evening_star(c1, c2, c3):
    if c1 is None or c2 is None or c3 is None or any(pd.isna(c['open']) for c in [c1, c2, c3]): return False
    b1 = abs(c1['close'] - c1['open']); b2 = abs(c2['close'] - c2['open']); b3 = abs(c3['close'] - c3['open'])
    return (c1['close'] > c1['open'] and b2 < b1 * 0.3 and c2['low'] > c1['close'] and 
            c3['open'] < c2['low'] and c3['close'] < c3['open'] and b3 > b1 * 0.6)

def is_hammer(c):
    if c is None or pd.isna(c['open']): return False
    body = abs(c['close'] - c['open'])
    if body == 0: return False
    lower_wick = min(c['open'], c['close']) - c['low']
    upper_wick = c['high'] - max(c['open'], c['close'])
    price_range = c['high'] - c['low']
    return (lower_wick > body * 2.5 and upper_wick < body * 0.5 and (min(c['open'], c['close']) - c['low']) > price_range * 0.6)

def is_hanging_man(c):
    return is_hammer(c)

def is_doji(c, tol=0.05):
    if c is None or pd.isna(c['open']): return False
    body = abs(c['close'] - c['open']); rng = c['high'] - c['low']
    if rng == 0: return False
    return (body / rng) < tol

class BaseEntryStrategy(ABC):
    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        self.params = strategy_instance.params
        self.data_manager = strategy_instance.data_manager

    @abstractmethod
    async def check(self):
        pass

    async def _validate_entry_conditions(self, side, opt):
        if not opt: return False
        symbol = opt['tradingsymbol']
        strike = opt['strike']
        
        option_candle = self.data_manager.option_candles.get(symbol)
        current_price = self.data_manager.prices.get(symbol)
        
        if option_candle and 'open' in option_candle and current_price and current_price <= option_candle['open']:
             await self.strategy._log_debug("Validation", f"REJECTED {symbol}: Option price {current_price} is not above its 1-min open {option_candle['open']}.")
             return False

        if not self.data_manager.is_average_price_trending(symbol, 'up'):
            return False

        if not await self._is_opposite_falling(side, strike):
            return False
            
        if not self._momentum_ok(side, symbol): return False
        if not self._is_accelerating(symbol): return False
        await self.strategy._log_debug("Validation", f"PASS: All entry conditions met for {symbol}.")
        return True

    async def _is_opposite_falling(self, side, strike):
        opposite_side = 'PE' if side == 'CE' else 'CE'
        opposite_opt = self.strategy.get_entry_option(opposite_side, strike)
        if not opposite_opt: return True
        
        opposite_symbol = opposite_opt['tradingsymbol']
        return self.data_manager.is_average_price_trending(opposite_symbol, 'down')

    def _momentum_ok(self, side, opt_sym, look=20):
        idx_prices = self.data_manager.price_history.get(self.strategy.index_symbol, [])
        opt_prices = self.data_manager.price_history.get(opt_sym, [])
        if len(idx_prices) < look or len(opt_prices) < look: return False
        
        idx_price_values = [p for ts, p in idx_prices]
        opt_price_values = [p for ts, p in opt_prices]

        idx_up = sum(1 for i in range(1, look) if idx_price_values[-i] > idx_price_values[-i - 1])
        opt_up = sum(1 for i in range(1, look) if opt_price_values[-i] > opt_price_values[-i - 1])
        
        if side == 'CE':
            return idx_up >= 1 and opt_up >= 1
        else:
            idx_dn = (look - 1) - idx_up
            return idx_dn >= 1 and opt_up >= 1

    def _is_accelerating(self, symbol, lookback_ticks=20, acceleration_factor=2.0):
        history = self.data_manager.price_history.get(symbol, [])
        if len(history) < lookback_ticks: return False
        
        prices = [p for ts, p in history]
        recent_prices = prices[-lookback_ticks:]
        diffs = np.diff(recent_prices)
        if len(diffs) < 2: return False

        current_velocity = diffs[-1]
        avg_velocity = np.mean(diffs[:-1])

        if current_velocity <= 0: return False
        if avg_velocity > 0 and current_velocity > avg_velocity * acceleration_factor:
            return True
            
        return False

class UoaEntryStrategy(BaseEntryStrategy):
    async def check(self):
        if self.strategy.position or not self.strategy.uoa_watchlist: return None, None, None
        
        for token, data in list(self.strategy.uoa_watchlist.items()):
            symbol, side, strike = data['symbol'], data['type'], data['strike']
            option_candle = self.data_manager.option_candles.get(symbol)
            current_price = self.data_manager.prices.get(symbol)
            if not option_candle or 'open' not in option_candle or not current_price: continue
            if current_price <= option_candle['open']:
                await self.strategy._log_debug("UOA Trigger", f"REJECTED: {symbol} price {current_price} is not above its 1-min open {option_candle['open']}.")
                continue
            
            opt = self.strategy.get_entry_option(side, strike)
            
            if await self._validate_entry_conditions1(side, opt):
                del self.strategy.uoa_watchlist[token]
                await self.strategy._update_ui_uoa_list()
                return side, "UOA_Entry", opt
        return None, None, None

    async def _validate_entry_conditions1(self, side, opt):
        if not opt: return False
        symbol = opt['tradingsymbol']
        log_report = []

        is_trending = self.data_manager.is_average_price_trending(symbol, 'up')
        log_report.append(f"Avg Price Trending: {is_trending}")
        if not is_trending:
            await self.strategy._log_debug("UOA Validation", f"REJECTED {symbol} | Report: [{', '.join(log_report)}]")
            return False

        momentum_is_ok = self._momentum_ok(side, symbol)
        log_report.append(f"Momentum OK: {momentum_is_ok}")
        if not momentum_is_ok:
            await self.strategy._log_debug("UOA Validation", f"REJECTED {symbol} | Report: [{', '.join(log_report)}]")
            return False
        
        await self.strategy._log_debug("UOA Validation", f"PASS {symbol} | Report: [{', '.join(log_report)}]")
        return True

class TrendContinuationStrategy(BaseEntryStrategy):
    async def check(self):
        if self.strategy.position: return None, None, None
        trend = self.data_manager.trend_state
        if not trend or len(self.data_manager.data_df) < 2: return None, None, None
        prev_candle = self.data_manager.data_df.iloc[-1]
        current_price = self.data_manager.prices.get(self.strategy.index_symbol)
        if not current_price: return None, None, None
        side, reason = None, None
        
        if trend == 'BULLISH' and current_price > prev_candle['high']: 
            side, reason = 'CE', 'Trend_Continuation_CE_Breakout'
        elif trend == 'BEARISH' and current_price < prev_candle['low']: 
            side, reason = 'PE', 'Trend_Continuation_PE_Breakout'

        if side:
            opt = self.strategy.get_entry_option(side)
            if await self._validate_entry_conditions(side, opt):
                return side, reason, opt
        return None, None, None

class MaCrossoverStrategy(BaseEntryStrategy):
    async def check(self):
        if self.strategy.position: return None, None, None
        df = self.data_manager.data_df
        if len(df) < 2: return None, None, None
        last, prev = df.iloc[-1], df.iloc[-2]
        if any(pd.isna(v) for v in [last['wma'], last['sma'], prev['wma'], prev['sma']]): return None, None, None
        side, reason = None, None
        if prev['wma'] <= prev['sma'] and last['wma'] > last['sma'] and last['close'] > last['open']: side, reason = 'CE', "MA_Crossover_CE"
        elif prev['wma'] >= prev['sma'] and last['wma'] < last['sma'] and last['close'] < last['open']: side, reason = 'PE', "MA_Crossover_PE"
        if side:
            opt = self.strategy.get_entry_option(side)
            if await self._validate_entry_conditions(side, opt):
                return side, reason, opt
        return None, None, None

class CandlePatternEntryStrategy(BaseEntryStrategy):
    async def check(self):
        if self.strategy.position: return None, None, None
        df = self.data_manager.data_df
        if len(df) < 3 or not self.data_manager.trend_state: return None, None, None
        last = df.iloc[-1]
        pattern, side = None, None
        if is_doji(last) and self.strategy.trend_candle_count >= 5:
            if self.data_manager.trend_state == 'BULLISH': pattern, side = 'Doji_Reversal_Elite', 'PE'
            elif self.data_manager.trend_state == 'BEARISH': pattern, side = 'Doji_Reversal_Elite', 'CE'
        if pattern:
            opt = self.strategy.get_entry_option(side)
            if await self._validate_entry_conditions(side, opt):
                return side, pattern, opt
        return None, None, None

class IntraCandlePatternStrategy(BaseEntryStrategy):
    async def check(self):
        if self.strategy.position: return None, None, None
        df = self.data_manager.data_df
        if len(df) < 3 or 'open' not in self.data_manager.current_candle: return None, None, None
        live_candle = self.data_manager.current_candle
        prev_candle = df.iloc[-1]
        prev_candle_2 = df.iloc[-2]
        pattern, side = None, None
        if is_bullish_engulfing(prev_candle, live_candle): pattern, side = 'Live_BullishEngulf', 'CE'
        elif is_bearish_engulfing(prev_candle, live_candle): pattern, side = 'Live_BearishEngulf', 'PE'
        elif is_morning_star(prev_candle_2, prev_candle, live_candle): pattern, side = 'Live_MorningStar', 'CE'
        elif is_evening_star(prev_candle_2, prev_candle, live_candle): pattern, side = 'Live_EveningStar', 'PE'
        elif is_hammer(live_candle) and self.data_manager.trend_state == 'BEARISH': pattern, side = 'Live_Hammer', 'CE'
        elif is_hanging_man(live_candle) and self.data_manager.trend_state == 'BULLISH': pattern, side = 'Live_HangingMan', 'PE'
        if pattern:
            opt = self.strategy.get_entry_option(side)
            if await self._validate_entry_conditions(side, opt):
                return side, pattern, opt
        return None, None, None



class EquityTrendStrategy(BaseEntryStrategy):
    async def check(self):
        if self.strategy.position: return None, None, None
        if not hasattr(self.strategy, 'liquidity_stocks') or not self.strategy.liquidity_stocks:
            return None, None, None
            
        trend = self.data_manager.trend_state
        if not trend or len(self.data_manager.data_df) < 2: 
            return None, None, None
        
        prev_candle = self.data_manager.data_df.iloc[-1]
        current_price = self.data_manager.prices.get(self.strategy.index_symbol)
        if not current_price:
            return None, None, None
            
        index_breakout = False
        if trend == 'BULLISH' and current_price > prev_candle['high']:
            index_breakout = True
            trade_side = 'BUY'
        elif trend == 'BEARISH' and current_price < prev_candle['low']:
            index_breakout = True
            trade_side = 'SELL'
        
        if not index_breakout:
            return None, None, None
        
        for stock in self.strategy.liquidity_stocks[:3]:
            try:
                change_pct = float(stock.get('priceChange', '0').replace('%', '').replace('+', ''))
            except (ValueError, TypeError, AttributeError):
                continue
            
            if ((trade_side == 'BUY' and change_pct > 1.5) or 
                (trade_side == 'SELL' and change_pct < -1.5)):
                
                if await self._validate_equity_conditions(trade_side, stock):
                    reason = f'TrendCont_{trade_side}_{stock.get("stock", "Unknown")}'
                    await self.strategy._log_debug("Equity Trend", 
                        f"Index breakout + {stock.get('stock', 'Unknown')} momentum ({change_pct:+.1f}%)")
                    return trade_side, reason, stock
                
        return None, None, None
    
    async def _validate_equity_conditions(self, side, stock_data):
        symbol = stock_data['stock']
        
        nifty_trend = self.data_manager.get_index_trend()
        
        if side == "BUY" and nifty_trend == "BEARISH":
            await self.strategy._log_debug("Filter", 
                f"⚠️ Skipped {symbol} BUY: Nifty is BEARISH (don't swim upstream)")
            return False
        
        if side == "SELL" and nifty_trend == "BULLISH":
            await self.strategy._log_debug("Filter", 
                f"⚠️ Skipped {symbol} SELL: Nifty is BULLISH (don't swim upstream)")
            return False
        
        return True
