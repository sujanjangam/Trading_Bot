# backend/core/stock_token_cache.py
from datetime import datetime
import pickle
from pathlib import Path

class StockTokenCache:
    def __init__(self):
        self.cache_file = Path("nse_stock_tokens.pkl")
        self.cache = None
        self.cache_time = None
    
    def get_stock_tokens(self, kite):
        """Get NSE stock tokens with 24h cache"""
        now = datetime.now()
        
        # Check cache
        if self.cache_file.exists():
            age_hours = (now.timestamp() - self.cache_file.stat().st_mtime) / 3600
            if age_hours < 24:
                with open(self.cache_file, 'rb') as f:
                    self.cache = pickle.load(f)
                return self.cache
        
        # Fetch from API
        liquid_stocks = ['TCS', 'INFY', 'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'ITC', 'LT', 'HINDUNILVR',
                        'BHARTIARTL', 'TITAN', 'SUNPHARMA', 'AXISBANK', 'EICHERMOT', 'HINDALCO', 'MARUTI',
                        'KOTAKBANK', 'ULTRACEMCO', 'ASIANPAINT', 'NESTLEIND', 'BAJFINANCE', 'WIPRO', 'M&M',
                        'TATACONSUM', 'JSWSTEEL', 'POWERGRID', 'LTIM', 'TECHM', 'COALINDIA', 'INDUSINDBK',
                        'TATASTEEL', 'CIPLA', 'BAJAJFINSV', 'GRASIM', 'HDFCLIFE', 'SBILIFE', 'BPCL',
                        'HEROMOTOCO', 'APOLLOHOSP', 'ADANIENT', 'BRITANNIA', 'DIVISLAB', 'DRREDDY', 'TRENT', 'ADANIPORTS']
        nse_instruments = kite.instruments('NSE')
        
        stock_tokens = {}
        for stock in liquid_stocks:
            inst = next((i for i in nse_instruments if i.get('tradingsymbol') == stock and i.get('instrument_type') == 'EQ'), None)
            if inst:
                stock_tokens[stock] = inst['instrument_token']
        
        # Cache it
        with open(self.cache_file, 'wb') as f:
            pickle.dump(stock_tokens, f)
        
        self.cache = stock_tokens
        return stock_tokens

_cache = StockTokenCache()

def get_stock_tokens(kite):
    return _cache.get_stock_tokens(kite)
