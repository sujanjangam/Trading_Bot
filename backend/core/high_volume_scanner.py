"""High Volume Liquid Stocks Scanner with Market Depth Analysis"""
import asyncio
import json
from typing import List, Dict
from datetime import datetime, timedelta

class HighVolumeScanner:
    def __init__(self, kite_instance):
        self.kite = kite_instance
        
        # Load exclusion lists from config
        with open("config/exclusion_lists.json", "r") as f:
            config = json.load(f)
            self.nifty50 = set(config["nifty50"])
            self.sensex = set(config["sensex"])
        
        self.excluded_stocks = self.nifty50.union(self.sensex)
        
        # Curated list of liquid stocks (NIFTY Next 50 + other liquid stocks)
        self.liquid_universe = [
            "ADANIGREEN", "ADANIPOWER", "ATGL", "AMBUJACEM", "BAJAJ-AUTO", "BANKBARODA", "BEL", 
            "BERGEPAINT", "BOSCHLTD", "CANBK", "CHOLAFIN", "COLPAL", "DABUR", "DLF", "DMART", 
            "GAIL", "GODREJCP", "HAVELLS", "ICICIPRULI", "IDEA", "INDIGO", "IOC", "IRCTC", 
            "JINDALSTEL", "LICHSGFIN", "LUPIN", "MCDOWELL-N", "MOTHERSON", "MPHASIS", "NMDC", 
            "NYKAA", "OFSS", "OIL", "PAGEIND", "PERSISTENT", "PETRONET", "PFC", "PIDILITIND", 
            "PNB", "RECLTD", "SAIL", "SHREECEM", "SIEMENS", "TATACOMM", "TATAPOWER", "TORNTPHARM", 
            "TVSMOTOR", "UBL", "VEDL", "VOLTAS", "ZOMATO", "ZYDUSLIFE", "ABCAPITAL", "ABFRL", 
            "ACC", "ALKEM", "APOLLOTYRE", "ASHOKLEY", "AUROPHARMA", "BALKRISIND", "BANDHANBNK", 
            "BATAINDIA", "BIOCON", "BOSCHLTD", "CHAMBLFERT", "COFORGE", "CONCOR", "COROMANDEL", 
            "CROMPTON", "CUB", "CUMMINSIND", "DEEPAKNTR", "DIXON", "ESCORTS", "EXIDEIND", 
            "FEDERALBNK", "GLENMARK", "GMRINFRA", "GNFC", "GODREJPROP", "GRANULES", "GUJGASLTD", 
            "HAL", "HINDCOPPER", "HINDPETRO", "HONAUT", "IDFCFIRSTB", "IEX", "IGL", "INDHOTEL", 
            "INDUSTOWER", "INTELLECT", "IPCALAB", "IRFC", "JUBLFOOD", "KPITTECH", "L&TFH", 
            "LALPATHLAB", "LAURUSLABS", "LTTS", "MANAPPURAM", "MARICO", "MAXHEALTH", "MFSL", 
            "MGL", "MUTHOOTFIN", "NAM-INDIA", "NATIONALUM", "NAUKRI", "NAVINFLUOR", "OBEROIRLTY", 
            "PAYTM", "PIIND", "PVR", "RAIN", "RAJESHEXPO", "RBLBANK", "SBICARD", "SBILIFE", 
            "SHRIRAMFIN", "SRF", "SRTRANSFIN", "SUNPHARMA", "SUNTV", "SUPREMEIND", "TATAELXSI", 
            "TATAINVEST", "TIINDIA", "TORNTPOWER", "TRENT", "UNIONBANK", "UPL", "WHIRLPOOL"
        ]
    
    async def scan_high_volume_stocks(self, 
                                     min_notional: float = 100000,
                                     max_spread: float = 1.0,
                                     min_volume: int = 100000,
                                     min_delivery_pct: float = 30.0) -> List[Dict]:
        """
        Scan for high volume liquid stocks excluding NIFTY50 and SENSEX constituents
        
        Args:
            min_notional: Minimum notional value (₹1,00,000)
            max_spread: Maximum bid-ask spread (₹1.00)
            min_volume: Minimum daily volume (1,00,000)
            min_delivery_pct: Minimum delivery percentage (30%)
        """
        try:
            # Use curated liquid universe instead of all NSE stocks
            candidate_stocks = [s for s in self.liquid_universe if s not in self.excluded_stocks]
            
            # Get quotes in batches
            batch_size = 200
            all_quotes = {}
            
            for i in range(0, len(candidate_stocks), batch_size):
                batch = candidate_stocks[i:i + batch_size]
                symbols = [f"NSE:{stock}" for stock in batch]
                
                try:
                    quotes = await asyncio.to_thread(self.kite.quote, symbols)
                    all_quotes.update(quotes)
                    await asyncio.sleep(0.3)  # Rate limiting
                except Exception as e:
                    print(f"Error fetching batch {i}: {e}")
                    continue
            
            # Filter stocks based on liquidity criteria
            liquid_stocks = []
            
            for symbol, quote in all_quotes.items():
                try:
                    stock_symbol = symbol.replace('NSE:', '')
                    
                    # Extract market depth data
                    depth = quote.get('depth', {})
                    buy_depth = depth.get('buy', [])
                    sell_depth = depth.get('sell', [])
                    
                    if not buy_depth or not sell_depth:
                        continue
                    
                    # Best bid and ask
                    best_bid = buy_depth[0]
                    best_ask = sell_depth[0]
                    
                    bid_price = best_bid.get('price', 0)
                    bid_qty = best_bid.get('quantity', 0)
                    ask_price = best_ask.get('price', 0)
                    ask_qty = best_ask.get('quantity', 0)
                    
                    if bid_price <= 0 or ask_price <= 0:
                        continue
                    
                    # Calculate notional values
                    bid_notional = bid_price * bid_qty
                    ask_notional = ask_price * ask_qty
                    
                    # Calculate spread
                    spread = ask_price - bid_price
                    spread_pct = (spread / bid_price) * 100 if bid_price > 0 else 999
                    
                    # Get volume data
                    volume = quote.get('volume', 0)
                    last_price = quote.get('last_price', 0)
                    ohlc = quote.get('ohlc', {})
                    
                    # Apply filters
                    if (bid_notional >= min_notional or ask_notional >= min_notional) and \
                       spread <= max_spread and \
                       volume >= min_volume:
                        
                        # Calculate price change
                        prev_close = ohlc.get('close', last_price)
                        price_change = ((last_price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                        
                        liquid_stocks.append({
                            'stock': stock_symbol,
                            'lastPrice': round(last_price, 2),
                            'futPrice': round(last_price, 2),  # Add futPrice for liquidity engine
                            'bidPrice': round(bid_price, 2),
                            'bidQty': bid_qty,
                            'askPrice': round(ask_price, 2),
                            'askQty': ask_qty,
                            'spread': round(spread, 2),
                            'spreadPct': round(spread_pct, 2),
                            'bidNotional': round(bid_notional, 2),
                            'askNotional': round(ask_notional, 2),
                            'volume': volume,
                            'priceChange': round(price_change, 2),
                            'open': round(ohlc.get('open', 0), 2),
                            'high': round(ohlc.get('high', 0), 2),
                            'low': round(ohlc.get('low', 0), 2),
                            'close': round(ohlc.get('close', 0), 2),
                        })
                
                except Exception as e:
                    continue
            
            # Sort by volume (highest first)
            liquid_stocks.sort(key=lambda x: x['volume'], reverse=True)
            
            # Return top 50 most liquid stocks
            return liquid_stocks[:50]
            
        except Exception as e:
            print(f"Error in scan_high_volume_stocks: {e}")
            return []
