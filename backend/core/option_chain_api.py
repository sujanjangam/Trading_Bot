# backend/core/option_chain_api.py
import asyncio
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
import math
from .kite import kite

class OptionChainAPI:
    def __init__(self):
        self._instruments_cache = None
        self._cache_time = None
    
    def _calculate_iv(self, option_price: float, spot_price: float, strike: float, days_to_expiry: int, option_type: str) -> Optional[float]:
        """Calculate IV using simplified approximation"""
        try:
            T = days_to_expiry / 365.0
            if T <= 0:
                return None
            
            intrinsic = max(0, spot_price - strike) if option_type == 'CE' else max(0, strike - spot_price)
            time_value = option_price - intrinsic
            
            if time_value <= 0:
                return None
            
            iv = (time_value / spot_price) * math.sqrt(2 * math.pi / T) * 100
            return iv if 5 <= iv <= 100 else None
        except:
            return None
        
    async def get_option_chain(self, symbol: str, spot_price: float, strikes_count: int = 10) -> List[Dict]:
        """Fetch option chain data for a given stock symbol"""
        try:
            options = await self._get_option_instruments(symbol)
            if not options:
                return []
            
            strike_interval = self._get_strike_interval(spot_price)
            atm_strike = round(spot_price / strike_interval) * strike_interval
            strikes = [atm_strike + (i - strikes_count//2) * strike_interval for i in range(strikes_count)]
            
            # Build list of all quote keys for bulk fetch
            quote_keys = []
            strike_map = {}  # Map strike -> {ce_symbol, pe_symbol}
            
            for strike in strikes:
                ce_opt = next((opt for opt in options if opt.get('strike') == strike and opt.get('instrument_type') == 'CE'), None)
                pe_opt = next((opt for opt in options if opt.get('strike') == strike and opt.get('instrument_type') == 'PE'), None)
                
                strike_map[strike] = {'ce': ce_opt, 'pe': pe_opt}
                
                if ce_opt:
                    quote_keys.append(f"NFO:{ce_opt['tradingsymbol']}")
                if pe_opt:
                    quote_keys.append(f"NFO:{pe_opt['tradingsymbol']}")
            
            # Single bulk API call
            quotes = await asyncio.to_thread(kite.quote, quote_keys) if quote_keys else {}
            
            # Build option chain from bulk data
            option_chain = []
            for strike in strikes:
                ce_opt = strike_map[strike]['ce']
                pe_opt = strike_map[strike]['pe']
                
                ce_data = self._extract_quote_data(quotes, ce_opt)
                pe_data = self._extract_quote_data(quotes, pe_opt)
                
                # Calculate IV
                ce_iv = None
                pe_iv = None
                if ce_opt and ce_data.get('ltp', 0) > 0:
                    days_to_expiry = (ce_opt['expiry'] - date.today()).days
                    ce_iv = self._calculate_iv(ce_data['ltp'], spot_price, strike, days_to_expiry, 'CE')
                if pe_opt and pe_data.get('ltp', 0) > 0:
                    days_to_expiry = (pe_opt['expiry'] - date.today()).days
                    pe_iv = self._calculate_iv(pe_data['ltp'], spot_price, strike, days_to_expiry, 'PE')
                
                option_chain.append({
                    'strike': strike,
                    'is_atm': strike == atm_strike,
                    'ce_ltp': ce_data.get('ltp', 0),
                    'ce_volume': ce_data.get('volume', 0),
                    'ce_oi': ce_data.get('oi', 0),
                    'ce_spread': ce_data.get('spread_pct', 0),
                    'ce_liquidity': self._calc_liquidity_score(ce_data),
                    'ce_iv': round(ce_iv, 1) if ce_iv else None,
                    'pe_ltp': pe_data.get('ltp', 0),
                    'pe_volume': pe_data.get('volume', 0),
                    'pe_oi': pe_data.get('oi', 0),
                    'pe_spread': pe_data.get('spread_pct', 0),
                    'pe_liquidity': self._calc_liquidity_score(pe_data),
                    'pe_iv': round(pe_iv, 1) if pe_iv else None
                })
            
            return option_chain
            
        except Exception as e:
            print(f"Error fetching option chain for {symbol}: {e}")
            return []
    
    async def get_stock_chart_data(self, symbol: str, interval: str = "5minute", days: int = 5) -> List[Dict]:
        """Fetch historical chart data for a stock"""
        try:
            # Get instrument token for the stock
            instruments = await self._get_instruments('NSE')
            stock_token = None
            
            for inst in instruments:
                if inst.get('tradingsymbol') == symbol and inst.get('segment') == 'NSE':
                    stock_token = inst.get('instrument_token')
                    break
            
            if not stock_token:
                return []
            
            # Fetch historical data
            from_date = datetime.now() - timedelta(days=days)
            to_date = datetime.now()
            
            data = await asyncio.to_thread(
                kite.historical_data,
                stock_token,
                from_date,
                to_date,
                interval
            )
            
            # Format data for chart
            chart_data = []
            for candle in data:
                chart_data.append({
                    'timestamp': candle['date'].isoformat(),
                    'open': candle['open'],
                    'high': candle['high'],
                    'low': candle['low'],
                    'close': candle['close'],
                    'volume': candle['volume']
                })
            
            return chart_data
            
        except Exception as e:
            print(f"Error fetching chart data for {symbol}: {e}")
            return []
    
    async def _get_option_instruments(self, symbol: str) -> List[Dict]:
        """Get option instruments for a symbol with caching"""
        try:
            now = datetime.now()
            if not self._instruments_cache or not self._cache_time or (now - self._cache_time).total_seconds() > 300:
                self._instruments_cache = await asyncio.to_thread(kite.instruments, 'NFO')
                self._cache_time = now
            
            # Filter options - exact name match only
            options = [i for i in self._instruments_cache 
                      if i.get('name') == symbol and i.get('instrument_type') in ['CE', 'PE']]
            
            if not options:
                return []
            
            # Get nearest expiry only
            today = date.today()
            future_options = [opt for opt in options if opt.get('expiry') and opt['expiry'] >= today]
            
            if not future_options:
                return []
            
            nearest_expiry = min(opt['expiry'] for opt in future_options)
            return [opt for opt in future_options if opt['expiry'] == nearest_expiry]
            
        except Exception as e:
            print(f"Error getting option instruments: {e}")
            return []
    
    async def _get_instruments(self, exchange: str) -> List[Dict]:
        """Get instruments for an exchange"""
        try:
            return await asyncio.to_thread(kite.instruments, exchange)
        except Exception as e:
            print(f"Error getting instruments for {exchange}: {e}")
            return []
    
    def _extract_quote_data(self, quotes: Dict, option: Optional[Dict]) -> Dict:
        """Extract quote data from bulk response"""
        if not option:
            return {'ltp': 0, 'volume': 0, 'oi': 0, 'spread_pct': 0}
        
        quote_key = f"NFO:{option['tradingsymbol']}"
        if quote_key not in quotes:
            return {'ltp': 0, 'volume': 0, 'oi': 0, 'spread_pct': 0}
        
        quote = quotes[quote_key]
        depth = quote.get('depth', {})
        buy = depth.get('buy', [{}])[0] if depth.get('buy') else {}
        sell = depth.get('sell', [{}])[0] if depth.get('sell') else {}
        
        bid = buy.get('price', 0)
        ask = sell.get('price', 0)
        ltp = quote.get('last_price', 0)
        
        spread_pct = 0
        if bid > 0 and ask > 0 and ltp > 0:
            spread_pct = ((ask - bid) / ltp) * 100
        
        return {
            'ltp': ltp,
            'volume': quote.get('volume', 0),
            'oi': quote.get('oi', 0),
            'spread_pct': spread_pct
        }
    
    def _get_strike_interval(self, price: float) -> float:
        """Get NSE standard strike intervals"""
        if price < 150: return 2.5
        elif price < 500: return 5.0
        elif price < 1000: return 10.0
        elif price < 2500: return 20.0
        elif price < 5000: return 50.0
        else: return 100.0
    
    def _calc_liquidity_score(self, data: Dict) -> int:
        """Calculate liquidity score 0-100"""
        score = 0
        vol = data.get('volume', 0)
        oi = data.get('oi', 0)
        spread = data.get('spread_pct', 100)
        
        if vol > 10000: score += 40
        elif vol > 5000: score += 30
        elif vol > 1000: score += 20
        elif vol > 100: score += 10
        
        if oi > 50000: score += 40
        elif oi > 20000: score += 30
        elif oi > 5000: score += 20
        elif oi > 500: score += 10
        
        if spread < 2: score += 20
        elif spread < 5: score += 15
        elif spread < 10: score += 10
        elif spread < 20: score += 5
        
        return min(score, 100)