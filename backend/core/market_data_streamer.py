import asyncio
import json
from typing import Dict, List
from datetime import datetime
from kiteconnect import KiteTicker
from core.kite import kite, API_KEY, access_token
from core.websocket_manager import manager

class MarketDataStreamer:
    def __init__(self):
        self.kws = None
        self.is_running = False
        self.subscribed_tokens = {}
        self.latest_quotes = {}
        self.nifty50_symbols = [
            "RELIANCE", "TCS", "HDFCBANK", "BHARTIARTL", "ICICIBANK", "INFOSYS", "SBIN", "LICI", "ITC", "HINDUNILVR",
            "LT", "HCLTECH", "MARUTI", "SUNPHARMA", "TITAN", "ONGC", "TATAMOTORS", "NTPC", "AXISBANK", "NESTLEIND",
            "KOTAKBANK", "ULTRACEMCO", "ASIANPAINT", "BAJFINANCE", "WIPRO", "M&M", "TATACONSUM", "JSWSTEEL", "POWERGRID",
            "LTIM", "TECHM", "HINDALCO", "COALINDIA", "INDUSINDBK", "TATASTEEL", "CIPLA", "BAJAJFINSV", "GRASIM",
            "HDFCLIFE", "SBILIFE", "BPCL", "EICHERMOT", "HEROMOTOCO", "APOLLOHOSP", "ADANIENT", "BRITANNIA", "DIVISLAB",
            "DRREDDY", "TRENT", "ADANIPORTS"
        ]
        self.sensex_symbols = [
            "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "BHARTIARTL", "INFOSYS", "SBIN", "LICI", "ITC", "HINDUNILVR",
            "LT", "MARUTI", "SUNPHARMA", "ONGC", "NTPC", "AXISBANK", "KOTAKBANK", "ULTRACEMCO", "ASIANPAINT", "NESTLEIND",
            "BAJFINANCE", "M&M", "POWERGRID", "TECHM", "TATAMOTORS", "TITAN", "INDUSINDBK", "HCLTECH", "WIPRO", "JSWSTEEL"
        ]
        self.free_float_shares = {}
        self.prev_close_prices = {}
        
    async def initialize(self):
        """Load config and get instrument tokens"""
        try:
            with open("config/free_float_shares.json", "r") as f:
                ff_config = json.load(f)
                self.free_float_shares = {
                    **ff_config["nifty50"],
                    **ff_config["sensex"]
                }
            
            # Get instrument tokens
            instruments = await asyncio.to_thread(kite.instruments, "NSE")
            for symbol in set(self.nifty50_symbols + self.sensex_symbols):
                for inst in instruments:
                    if inst['tradingsymbol'] == symbol and inst['exchange'] == 'NSE':
                        self.subscribed_tokens[inst['instrument_token']] = symbol
                        break
            
            # Get previous close prices
            all_symbols = list(set(self.nifty50_symbols + self.sensex_symbols))
            quotes = await asyncio.to_thread(kite.quote, [f"NSE:{s}" for s in all_symbols])
            for symbol in all_symbols:
                key = f"NSE:{symbol}"
                if key in quotes:
                    self.prev_close_prices[symbol] = quotes[key]['ohlc']['close']
            
            print(f"✅ Market streamer initialized with {len(self.subscribed_tokens)} tokens")
        except Exception as e:
            print(f"❌ Failed to initialize market streamer: {e}")
            raise

    def start(self):
        """Start WebSocket streaming"""
        if not access_token or self.is_running:
            return
        
        self.kws = KiteTicker(API_KEY, access_token)
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        
        self.kws.connect(threaded=True)
        self.is_running = True
        print("🚀 Market data streamer started")

    def on_connect(self, ws, response):
        """Subscribe to tokens on connection"""
        tokens = list(self.subscribed_tokens.keys())
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_QUOTE, tokens)
            print(f"📡 Subscribed to {len(tokens)} instruments")

    def on_ticks(self, ws, ticks):
        """Process incoming ticks and broadcast to frontend"""
        try:
            for tick in ticks:
                token = tick['instrument_token']
                if token in self.subscribed_tokens:
                    symbol = self.subscribed_tokens[token]
                    self.latest_quotes[symbol] = {
                        'last_price': tick.get('last_price', 0),
                        'change': tick.get('change', 0),
                        'ohlc': tick.get('ohlc', {})
                    }
            
            # Broadcast updated data
            asyncio.create_task(self._broadcast_market_data())
        except Exception as e:
            print(f"Error processing ticks: {e}")

    async def _broadcast_market_data(self):
        """Format and broadcast market data to frontend"""
        try:
            nifty_data = self._format_market_data(self.nifty50_symbols, self.free_float_shares)
            sensex_data = self._format_market_data(self.sensex_symbols, self.free_float_shares)
            
            await manager.broadcast({
                "type": "market_data_update",
                "nifty": nifty_data,
                "sensex": sensex_data,
                "timestamp": datetime.now().isoformat()
            })
        except Exception as e:
            print(f"Error broadcasting market data: {e}")

    def _format_market_data(self, symbols: List[str], free_float: Dict) -> List[Dict]:
        """Format market data for frontend"""
        result = []
        market_caps = []
        
        for symbol in symbols:
            if symbol not in self.latest_quotes:
                continue
            
            quote = self.latest_quotes[symbol]
            price = quote['last_price']
            shares = free_float.get(symbol, 1000000)
            market_caps.append((symbol, price * shares))
        
        total_cap = sum(cap for _, cap in market_caps)
        weightages = {s: round((cap / total_cap) * 100, 1) if total_cap > 0 else 0.1 
                     for s, cap in market_caps}
        
        for symbol in symbols:
            if symbol not in self.latest_quotes:
                continue
            
            quote = self.latest_quotes[symbol]
            price = quote['last_price']
            prev_close = self.prev_close_prices.get(symbol, price)
            change_pct = ((price - prev_close) / prev_close) * 100 if prev_close else 0
            
            result.append({
                "stock": symbol,
                "futPrice": f"{price:.2f}",
                "priceChange": f"{change_pct:+.2f}%",
                "weightage": weightages.get(symbol, 0.1),
                "atmIV": "--",
                "ivChg": "--",
                "open": f"{quote['ohlc'].get('open', 0):.2f}",
                "high": f"{quote['ohlc'].get('high', 0):.2f}",
                "low": f"{quote['ohlc'].get('low', 0):.2f}",
                "close": f"{prev_close:.2f}",
                "oiChg": "--",
                "pcr": "--",
                "maxPain": "--",
                "lotSize": "--"
            })
        
        return result

    def on_close(self, ws, code, reason):
        """Handle WebSocket close"""
        self.is_running = False
        print(f"📴 Market streamer disconnected: {code} - {reason}")

    def on_error(self, ws, code, reason):
        """Handle WebSocket error"""
        print(f"❌ Market streamer error: {code} - {reason}")

    def stop(self):
        """Stop WebSocket streaming"""
        if self.kws and self.is_running:
            self.kws.close()
            self.is_running = False
            print("🛑 Market data streamer stopped")

# Global instance
market_streamer = MarketDataStreamer()
