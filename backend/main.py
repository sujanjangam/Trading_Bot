import asyncio
import json
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from datetime import datetime, timedelta
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from core.kite import kite, generate_session_and_set_token, access_token
from core.websocket_manager import manager
from core.strategy import MARKET_STANDARD_PARAMS
from core.optimiser import OptimizerBot
from core.trade_logger import TradeLogger
from core.bot_service import TradingBotService, get_bot_service
from core.database import today_engine, all_engine
from core.option_chain_api import OptionChainAPI
from core.selected_strike_api import SelectedStrikeAPI
from config.cors_config import CORS_ORIGINS
from config.trading_config import NIFTY50_SYMBOLS, SENSEX_SYMBOLS, LIQUIDITY_TOP_N
from core.iv_calculator import IVCalculator


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Application startup...")
    TradeLogger.setup_databases()

    # --- ADDED: Open Position Reconciliation Logic ---
    # Small delay to ensure WebSocket manager is ready for potential connections
    await asyncio.sleep(2)
    if access_token:
        try:
            print("Reconciling open positions...")
            positions = await asyncio.to_thread(kite.positions)
            net_positions = positions.get('net', [])
            open_mis_positions = [
                p['tradingsymbol'] for p in net_positions 
                if p.get('product') == 'MIS' and p.get('quantity') != 0
            ]
            if open_mis_positions:
                warning_message = f"Found open MIS positions at broker: {', '.join(open_mis_positions)}. Manual action may be required."
                print(f"WARNING: {warning_message}")
                # Broadcast a warning to any connected frontend
                await manager.broadcast({
                    "type": "system_warning", 
                    "payload": {
                        "title": "Open Positions Detected on Startup",
                        "message": warning_message
                    }
                })
        except Exception as e:
            print(f"Could not reconcile open positions: {e}")
    # --- END OF ADDED LOGIC ---

    yield
    print("Application shutdown...")
    service = await get_bot_service()
    if service.ticker_manager_instance:
        await service.stop_bot()
    print("Shutdown tasks complete.")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

class TokenRequest(BaseModel): request_token: str
class StartRequest(BaseModel): params: dict; selectedIndex: str
class WatchlistRequest(BaseModel): side: str; strike: int
class LiquidityDataRequest(BaseModel): stocks: list
class OptionChainRequest(BaseModel): symbol: str; spot_price: float; strikes_count: int = 10
class ChartDataRequest(BaseModel): symbol: str; interval: str = "5minute"; days: int = 5
class SelectedStrikeRequest(BaseModel): symbol: str; spot_price: float; strike_selection: str = 'ATM'

@app.get("/api/status")
async def get_status():
    # Check if the global access_token variable exists first
    if access_token:
        try:
            # Actively VERIFY the token by making a network API call.
            profile = await asyncio.to_thread(kite.profile)
            # If the call succeeds, we are truly authenticated.
            return {"status": "authenticated", "user": profile.get('user_id')}
        except Exception as e:
            # If kite.profile() fails, it means the token is invalid.
            print(f"Token validation failed in status check: {e}")
            # Try to re-initialize from file
            from core.kite import re_initialize_session_from_file
            if re_initialize_session_from_file():
                try:
                    profile = await asyncio.to_thread(kite.profile)
                    return {"status": "authenticated", "user": profile.get('user_id')}
                except Exception as e2:
                    print(f"Re-initialization also failed: {e2}")
    
    # This is the fallback for BOTH "no token" and "invalid token" cases.
    return {"status": "unauthenticated", "login_url": kite.login_url()}

@app.post("/api/authenticate")
async def authenticate(token_request: TokenRequest):
    success, data = generate_session_and_set_token(token_request.request_token)
    if success:
        return {"status": "success", "message": "Authentication successful.", "user": data.get('user_id')}
    raise HTTPException(status_code=400, detail=data)

@app.get("/api/trade_history")
async def get_trade_history():
    def db_call():
        with today_engine.connect() as conn:
            df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
            # Fix NaN values before JSON serialization
            df = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
            return df.to_dict('records')
    try:
        return await asyncio.to_thread(db_call)
    except Exception as e:
        print(f"Error fetching today's trade history: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch trade history: {str(e)}")

@app.get("/api/trade_history_all")
async def get_all_trade_history():
    def db_call():
        with all_engine.connect() as conn:
            df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
            # Fix NaN values before JSON serialization
            df = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
            return df.to_dict('records')
    try:
        return await asyncio.to_thread(db_call)
    except Exception as e:
        print(f"Error fetching all trade history: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch all trade history: {str(e)}")

@app.post("/api/optimize")
async def run_optimizer(service: TradingBotService = Depends(get_bot_service)):
    optimizer = OptimizerBot()
    new_params, justifications = await optimizer.find_optimal_parameters()
    if new_params:
        optimizer.update_strategy_file(new_params)
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("Optimizer", "Live parameter reload successful.")
        return {"status": "success", "report": justifications}
    return {"status": "error", "report": justifications or ["Optimization failed."]}

@app.post("/api/reset_uoa_watchlist")
async def reset_uoa(service: TradingBotService = Depends(get_bot_service)):
    if not service.strategy_instance:
        raise HTTPException(status_code=400, detail="Bot is not running.")
    
    await service.strategy_instance.reset_uoa_watchlist()
    return {"status": "success", "message": "UOA Watchlist has been cleared."}

def calculate_dynamic_weightage(quotes, symbols, free_float_shares):
    """Calculate dynamic weightage based on live market cap"""
    market_caps = []
    
    for symbol in symbols:
        key = f"NSE:{symbol}"
        if key in quotes:
            price = quotes[key].get('last_price', 0)
            shares = free_float_shares.get(symbol, 1000000)  # Default fallback
            market_cap = price * shares
            market_caps.append((symbol, market_cap))
    
    total_market_cap = sum(cap for _, cap in market_caps)
    
    weightages = {}
    for symbol, market_cap in market_caps:
        weightage = (market_cap / total_market_cap) * 100 if total_market_cap > 0 else 0.1
        weightages[symbol] = round(weightage, 1)
    
    return weightages

iv_calculator = IVCalculator(kite)

async def get_atm_iv_for_stock(symbol, spot_price):
    """Get real ATM IV from option chain"""
    try:
        iv = await iv_calculator.get_atm_iv(symbol, spot_price)
        return iv
    except Exception as e:
        logging.error(f"Failed to get IV for {symbol}: {e}")
        return "--"

@app.get("/api/nifty50_data")
async def get_nifty50_data():
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        from core.kite import re_initialize_session_from_file
        if re_initialize_session_from_file():
            try:
                await asyncio.to_thread(kite.profile)
            except Exception:
                raise HTTPException(status_code=401, detail="Please authenticate first")
        else:
            raise HTTPException(status_code=401, detail="Please authenticate first")
    
    nifty50_symbols = NIFTY50_SYMBOLS
    
    with open("config/free_float_shares.json", "r") as f:
        ff_config = json.load(f)
        free_float_shares = ff_config["nifty50"]
    
    try:
        quotes = await asyncio.to_thread(kite.quote, [f"NSE:{symbol}" for symbol in nifty50_symbols])
        dynamic_weights = calculate_dynamic_weightage(quotes, nifty50_symbols, free_float_shares)
        
        result = []
        iv_map = {}
        for symbol in nifty50_symbols:
            key = f"NSE:{symbol}"
            if key in quotes:
                spot_price = quotes[key].get('last_price', 0)
                try:
                    iv = await get_atm_iv_for_stock(symbol, spot_price)
                    iv_map[symbol] = iv
                except:
                    iv_map[symbol] = "--"
                await asyncio.sleep(0.05)
        
        for symbol in nifty50_symbols:
            key = f"NSE:{symbol}"
            if key in quotes:
                quote = quotes[key]
                ohlc = quote.get('ohlc', {})
                change_pct = ((quote.get('last_price', 0) - ohlc.get('close', 1)) / ohlc.get('close', 1)) * 100
                
                result.append({
                    "stock": symbol,
                    "futPrice": f"{quote.get('last_price', 0):.2f}",
                    "priceChange": f"{change_pct:+.2f}%",
                    "weightage": dynamic_weights.get(symbol, 0.1),
                    "atmIV": iv_map.get(symbol, "--"),
                    "ivChg": "--",
                    "open": f"{ohlc.get('open', 0):.2f}",
                    "high": f"{ohlc.get('high', 0):.2f}",
                    "low": f"{ohlc.get('low', 0):.2f}",
                    "close": f"{ohlc.get('close', 0):.2f}",
                    "oiChg": "--",
                    "pcr": "--",
                    "maxPain": "--",
                    "lotSize": "--"
                })
            else:
                logging.warning(f"No quote data for Nifty50 stock: {symbol}")
                result.append({
                    "stock": symbol,
                    "futPrice": "--",
                    "priceChange": "0.00%",
                    "weightage": dynamic_weights.get(symbol, 0.1),
                    "atmIV": "--",
                    "ivChg": "--",
                    "open": "--",
                    "high": "--",
                    "low": "--",
                    "close": "--",
                    "oiChg": "--",
                    "pcr": "--",
                    "maxPain": "--",
                    "lotSize": "--"
                })
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Nifty 50 data: {str(e)}")

@app.get("/api/sensex_data")
async def get_sensex_data():
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        from core.kite import re_initialize_session_from_file
        if re_initialize_session_from_file():
            try:
                await asyncio.to_thread(kite.profile)
            except Exception:
                raise HTTPException(status_code=401, detail="Please authenticate first")
        else:
            raise HTTPException(status_code=401, detail="Please authenticate first")
    
    sensex_symbols = SENSEX_SYMBOLS
    
    with open("config/free_float_shares.json", "r") as f:
        ff_config = json.load(f)
        sensex_free_float = ff_config["sensex"]
    
    try:
        quotes = await asyncio.to_thread(kite.quote, [f"NSE:{symbol}" for symbol in sensex_symbols])
        dynamic_weights = calculate_dynamic_weightage(quotes, sensex_symbols, sensex_free_float)
        
        result = []
        iv_map = {}
        for symbol in sensex_symbols:
            key = f"NSE:{symbol}"
            if key in quotes:
                spot_price = quotes[key].get('last_price', 0)
                try:
                    iv = await get_atm_iv_for_stock(symbol, spot_price)
                    iv_map[symbol] = iv
                except:
                    iv_map[symbol] = "--"
                await asyncio.sleep(0.05)
        
        for symbol in sensex_symbols:
            key = f"NSE:{symbol}"
            if key in quotes:
                quote = quotes[key]
                ohlc = quote.get('ohlc', {})
                change_pct = ((quote.get('last_price', 0) - ohlc.get('close', 1)) / ohlc.get('close', 1)) * 100
                
                result.append({
                    "stock": symbol,
                    "futPrice": f"{quote.get('last_price', 0):.2f}",
                    "priceChange": f"{change_pct:+.2f}%",
                    "weightage": dynamic_weights.get(symbol, 0.1),
                    "atmIV": iv_map.get(symbol, "--"),
                    "ivChg": "--",
                    "open": f"{ohlc.get('open', 0):.2f}",
                    "high": f"{ohlc.get('high', 0):.2f}",
                    "low": f"{ohlc.get('low', 0):.2f}",
                    "close": f"{ohlc.get('close', 0):.2f}",
                    "oiChg": "--",
                    "pcr": "--",
                    "maxPain": "--",
                    "lotSize": "--"
                })
            else:
                logging.warning(f"No quote data for Sensex stock: {symbol}")
                result.append({
                    "stock": symbol,
                    "futPrice": "--",
                    "priceChange": "0.00%",
                    "weightage": dynamic_weights.get(symbol, 0.1),
                    "atmIV": "--",
                    "ivChg": "--",
                    "open": "--",
                    "high": "--",
                    "low": "--",
                    "close": "--",
                    "oiChg": "--",
                    "pcr": "--",
                    "maxPain": "--",
                    "lotSize": "--"
                })
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Sensex data: {str(e)}")

# --- THIS IS THE CORRECTED FUNCTION ---
@app.post("/api/update_strategy_params")
async def update_strategy_parameters(params: dict, service: TradingBotService = Depends(get_bot_service)):
    try:
        # Step 1: Update the JSON file with new parameters
        with open("strategy_params.json", "w") as f:
            json.dump(params, f, indent=4)
        
        # Step 2: If the bot is running, tell it to reload its parameters from the file
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("System", "Parameters have been updated from UI.")
            
        return {"status": "success", "message": "Parameters updated successfully.", "params": params}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update parameters: {str(e)}")

@app.post("/api/reset_params")
async def reset_parameters(service: TradingBotService = Depends(get_bot_service)):
    try:
        # Step 1: Overwrite the JSON file with the market standard defaults.
        with open("strategy_params.json", "w") as f:
            json.dump(MARKET_STANDARD_PARAMS, f, indent=4)
        
        # Step 2: If the bot is running, tell it to reload its parameters from the file.
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("System", "Parameters have been reset to market defaults.")
            
        return {"status": "success", "message": "Parameters reset.", "params": MARKET_STANDARD_PARAMS}
    except Exception as e:
        # The str(e) is included for better debugging if something else goes wrong.
        raise HTTPException(status_code=500, detail=f"Failed to reset parameters: {str(e)}")

# Rate limiting for bot start
_last_start_attempt = None
_start_cooldown_seconds = 5

@app.post("/api/start")
async def start_bot(req: StartRequest, service: TradingBotService = Depends(get_bot_service)):
    global _last_start_attempt
    
    print(f"🔍 BACKEND RECEIVED: selectedIndex = '{req.selectedIndex}'")
    print(f"📋 Full params: {req.params}")
    
    if _last_start_attempt:
        elapsed = (datetime.now() - _last_start_attempt).total_seconds()
        if elapsed < _start_cooldown_seconds:
            raise HTTPException(status_code=429, detail=f"Please wait {_start_cooldown_seconds - int(elapsed)} seconds before starting again")
    
    _last_start_attempt = datetime.now()
    return await service.start_bot(req.params, req.selectedIndex)

@app.post("/api/stop")
async def stop_bot(service: TradingBotService = Depends(get_bot_service)):
    return await service.stop_bot()

@app.post("/api/pause")
async def pause_bot(service: TradingBotService = Depends(get_bot_service)):
    return await service.pause_bot()

@app.post("/api/unpause")
async def unpause_bot(service: TradingBotService = Depends(get_bot_service)):
    return await service.unpause_bot()

@app.post("/api/manual_exit")
async def manual_exit_trade(service: TradingBotService = Depends(get_bot_service)):
    return await service.manual_exit_trade()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, service: TradingBotService = Depends(get_bot_service)):
    await manager.connect(websocket)
    print("Client connected. Synchronizing state...")
    try:
        if service.strategy_instance:
            await service.strategy_instance._update_ui_status()
            await service.strategy_instance._update_ui_performance()
            await service.strategy_instance._update_ui_trade_status()
            print("State synchronization complete.")
        else:
             await manager.broadcast({"type": "status_update", "payload": {
                "connection": "DISCONNECTED", "mode": "NOT STARTED", "is_running": False,
                "indexPrice": 0, "trend": "---", "indexName": "INDEX"
            }})

        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "ping":
                await websocket.send_text('{"type": "pong"}')
                continue
            
            if message.get("type") == "add_to_watchlist":
                payload = message.get("payload", {})
                if service.strategy_instance:
                    await service.strategy_instance.add_to_watchlist(payload.get("side"), payload.get("strike"))
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"Error in websocket endpoint: {e}")
        manager.disconnect(websocket)

@app.get("/api/debug/option_chain/{symbol}")
async def debug_option_chain(symbol: str, service: TradingBotService = Depends(get_bot_service)):
    """Debug endpoint to check option chain data flow"""
    if not service.strategy_instance:
        raise HTTPException(status_code=400, detail="Bot is not running")
    
    strategy = service.strategy_instance
    stock_price = strategy.data_manager.prices.get(f"NSE:{symbol}")
    
    debug_info = {
        "symbol": symbol,
        "stock_price": stock_price,
        "active_stock": strategy.active_stock_tracker.active_stock_symbol if hasattr(strategy.active_stock_tracker, 'active_stock_symbol') else None,
        "trading_mode": strategy.config.get("trading_mode"),
        "option_chain_data": []
    }
    
    if stock_price and stock_price > 0:
        strike_interval = strategy._get_stock_strike_interval(stock_price)
        atm_strike = round(stock_price / strike_interval) * strike_interval
        strikes = [atm_strike + (i - 3) * strike_interval for i in range(7)]
        
        for strike in strikes:
            ce_opt = strategy.get_stock_option(symbol, 'CE', strike)
            pe_opt = strategy.get_stock_option(symbol, 'PE', strike)
            
            strike_info = {
                "strike": strike,
                "ce": {
                    "found": ce_opt is not None,
                    "symbol": ce_opt['tradingsymbol'] if ce_opt else None,
                    "token": ce_opt.get('instrument_token') if ce_opt else None,
                    "token_mapped": ce_opt.get('instrument_token') in strategy.token_to_symbol if ce_opt else False,
                    "price": strategy.data_manager.prices.get(ce_opt['tradingsymbol']) if ce_opt else None
                },
                "pe": {
                    "found": pe_opt is not None,
                    "symbol": pe_opt['tradingsymbol'] if pe_opt else None,
                    "token": pe_opt.get('instrument_token') if pe_opt else None,
                    "token_mapped": pe_opt.get('instrument_token') in strategy.token_to_symbol if pe_opt else False,
                    "price": strategy.data_manager.prices.get(pe_opt['tradingsymbol']) if pe_opt else None
                }
            }
            debug_info["option_chain_data"].append(strike_info)
    
    return debug_info

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
# Global storage for liquidity data
_liquidity_data_cache = []
option_chain_api = OptionChainAPI()
selected_strike_api = SelectedStrikeAPI()

@app.post("/api/update_liquidity_data")
async def update_liquidity_data(data: dict, service: TradingBotService = Depends(get_bot_service)):
    """Update liquidity data from dashboard feed"""
    global _liquidity_data_cache
    try:
        liquidity_stocks = data.get('stocks', [])
        _liquidity_data_cache = liquidity_stocks
        
        if service.strategy_instance:
            await service.strategy_instance.update_liquidity_data(liquidity_stocks)
            return {"status": "success", "message": f"Updated {len(liquidity_stocks)} stocks (bot running)"}
        else:
            return {"status": "success", "message": f"Cached {len(liquidity_stocks)} stocks (bot not running)"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update liquidity data: {str(e)}")

@app.post("/api/option_chain")
async def get_option_chain(request: OptionChainRequest):
    """Get option chain data for a stock"""
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        raise HTTPException(status_code=401, detail="Please authenticate first")
    
    try:
        option_chain = await option_chain_api.get_option_chain(
            request.symbol, 
            request.spot_price, 
            request.strikes_count
        )
        return {"status": "success", "data": option_chain}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch option chain: {str(e)}")

@app.post("/api/chart_data")
async def get_chart_data(request: ChartDataRequest):
    """Get historical chart data for a stock"""
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        raise HTTPException(status_code=401, detail="Please authenticate first")
    
    try:
        chart_data = await option_chain_api.get_stock_chart_data(
            request.symbol,
            request.interval,
            request.days
        )
        return {"status": "success", "data": chart_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chart data: {str(e)}")
@app.get("/api/liquidity_stocks")
async def get_liquidity_stocks():
    """Get current liquidity stocks data"""
    global _liquidity_data_cache
    return {"status": "success", "data": _liquidity_data_cache}

@app.get("/api/liquidity_option_chain/{symbol}")
async def get_liquidity_option_chain(symbol: str, spot_price: float = None, strikes_count: int = 10):
    """Get option chain for a liquidity stock"""
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        raise HTTPException(status_code=401, detail="Please authenticate first")
    
    if spot_price is None:
        try:
            quote = await asyncio.to_thread(kite.quote, [f"NSE:{symbol}"])
            spot_price = quote[f"NSE:{symbol}"].get('last_price', 0)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Could not fetch spot price for {symbol}")
    
    try:
        option_chain = await option_chain_api.get_option_chain(symbol, spot_price, strikes_count)
        return {"status": "success", "symbol": symbol, "spot_price": spot_price, "data": option_chain}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch option chain for {symbol}: {str(e)}")

@app.get("/api/liquidity_chart/{symbol}")
async def get_liquidity_chart(symbol: str, interval: str = "5minute", days: int = 5):
    """Get chart data for a liquidity stock"""
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        raise HTTPException(status_code=401, detail="Please authenticate first")
    
    try:
        chart_data = await option_chain_api.get_stock_chart_data(symbol, interval, days)
        return {"status": "success", "symbol": symbol, "data": chart_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chart data for {symbol}: {str(e)}")

@app.get("/api/high_volume_stocks")
async def get_high_volume_stocks():
    """Get high volume liquid stocks excluding NIFTY50 and SENSEX constituents"""
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        raise HTTPException(status_code=401, detail="Please authenticate first")
    
    try:
        from core.high_volume_scanner import HighVolumeScanner
        scanner = HighVolumeScanner(kite)
        
        # Scan with default criteria
        stocks = await scanner.scan_high_volume_stocks(
            min_notional=100000,  # ₹1,00,000
            max_spread=1.0,       # ₹1.00
            min_volume=100000,    # 1,00,000 shares
            min_delivery_pct=30.0 # 30%
        )
        
        return {"status": "success", "data": stocks, "count": len(stocks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch high volume stocks: {str(e)}")

@app.get("/api/config")
async def get_config():
    """Get trading configuration for UI"""
    from config.trading_config import CUTOFF_TIME_STR, LIQUIDITY_TOP_N, MIN_LIQUIDITY_CHANGE_PCT
    return {
        "cutoff_time": CUTOFF_TIME_STR,
        "liquidity_top_n": LIQUIDITY_TOP_N,
        "min_change_pct": MIN_LIQUIDITY_CHANGE_PCT
    }

@app.post("/api/selected_strike_options")
async def get_selected_strike_options(request: SelectedStrikeRequest):
    """Get CE and PE LTP for bot's selected strike"""
    try:
        await asyncio.to_thread(kite.profile)
    except Exception:
        raise HTTPException(status_code=401, detail="Please authenticate first")
    
    try:
        option_data = await selected_strike_api.get_selected_strike_options(
            request.symbol,
            request.spot_price,
            request.strike_selection
        )
        return {"status": "success", "data": option_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch selected strike options: {str(e)}")