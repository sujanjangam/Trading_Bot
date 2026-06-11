import asyncio
from datetime import datetime, date
import logging
import math

logger = logging.getLogger(__name__)

class IVCalculator:
    def __init__(self, kite):
        self.kite = kite
        self._cache = {}
        self._cache_timeout = 60
        self._instruments_cache = None
    
    def _calculate_days_to_expiry(self, expiry_date):
        """Calculate days to expiry"""
        today = date.today()
        delta = expiry_date - today
        return max(1, delta.days)
    
    def _estimate_iv_from_option_price(self, option_price, spot_price, strike, days_to_expiry, option_type='CE'):
        """Estimate IV using simplified approximation"""
        try:
            T = days_to_expiry / 365.0
            
            if option_type == 'CE':
                intrinsic = max(0, spot_price - strike)
            else:
                intrinsic = max(0, strike - spot_price)
            
            time_value = option_price - intrinsic
            
            if time_value <= 0 or T <= 0:
                return None
            
            # Brenner-Subrahmanyam approximation
            iv = (time_value / spot_price) * math.sqrt(2 * math.pi / T) * 100
            
            return iv if 5 <= iv <= 100 else None
        except:
            return None
    
    async def get_atm_iv(self, symbol, spot_price):
        """Get real ATM IV from option chain"""
        cache_key = f"{symbol}_{int(spot_price)}"
        now = datetime.now().timestamp()
        
        if cache_key in self._cache:
            cached_iv, cached_time = self._cache[cache_key]
            if now - cached_time < self._cache_timeout:
                return cached_iv
        
        try:
            if not self._instruments_cache:
                self._instruments_cache = await asyncio.to_thread(self.kite.instruments, 'NFO')
            
            if spot_price < 150:
                strike_interval = 2.5
            elif spot_price < 500:
                strike_interval = 5
            elif spot_price < 1000:
                strike_interval = 10
            elif spot_price < 2500:
                strike_interval = 20
            elif spot_price < 5000:
                strike_interval = 50
            else:
                strike_interval = 100
            
            atm_strike = round(spot_price / strike_interval) * strike_interval
            
            today = date.today()
            ce_opts = [i for i in self._instruments_cache 
                      if i.get('name') == symbol and 
                      i.get('instrument_type') == 'CE' and 
                      i.get('strike') == atm_strike and
                      i.get('expiry') and i['expiry'] >= today]
            
            pe_opts = [i for i in self._instruments_cache 
                      if i.get('name') == symbol and 
                      i.get('instrument_type') == 'PE' and 
                      i.get('strike') == atm_strike and
                      i.get('expiry') and i['expiry'] >= today]
            
            if not ce_opts or not pe_opts:
                self._cache[cache_key] = ("--", now)
                return "--"
            
            ce_opt = min(ce_opts, key=lambda x: x['expiry'])
            pe_opt = min(pe_opts, key=lambda x: x['expiry'])
            
            days_to_expiry = self._calculate_days_to_expiry(ce_opt['expiry'])
            
            quotes = await asyncio.to_thread(
                self.kite.quote, 
                [f"NFO:{ce_opt['tradingsymbol']}", f"NFO:{pe_opt['tradingsymbol']}"]
            )
            
            ce_key = f"NFO:{ce_opt['tradingsymbol']}"
            pe_key = f"NFO:{pe_opt['tradingsymbol']}"
            
            ce_data = quotes.get(ce_key, {})
            pe_data = quotes.get(pe_key, {})
            
            ce_price = ce_data.get('last_price', 0)
            pe_price = pe_data.get('last_price', 0)
            
            if ce_price > 0 and pe_price > 0:
                ce_iv = self._estimate_iv_from_option_price(ce_price, spot_price, atm_strike, days_to_expiry, 'CE')
                pe_iv = self._estimate_iv_from_option_price(pe_price, spot_price, atm_strike, days_to_expiry, 'PE')
                
                valid_ivs = [iv for iv in [ce_iv, pe_iv] if iv is not None]
                
                if valid_ivs:
                    avg_iv = sum(valid_ivs) / len(valid_ivs)
                    result = f"{avg_iv:.1f}%"
                    self._cache[cache_key] = (result, now)
                    return result
        except Exception as e:
            logger.debug(f"{symbol}: IV calc error - {str(e)}")
        
        self._cache[cache_key] = ("--", now)
        return "--"
