# WebSocket Subscription Strategy for Stock Options Trading

## Overview
This document explains the correct WebSocket subscription strategy for trading stock options with Zerodha Kite Connect.

## ❌ What NOT to Do
**DO NOT** subscribe only to the index (NSE:NIFTY) when trading stock options.
- Index feed only gives you index LTP
- No option chain data
- No CE/PE prices for stocks
- Cannot trade stock options with index data alone

## ✅ Correct Subscription Strategy

### 1️⃣ Index Subscription (For ATM Calculation Only)
```python
# Subscribe to index to calculate ATM strike
subscribe(["NSE:NIFTY"])  # Token: 256265
```
**Purpose:** Get index LTP to calculate nearest ATM strike

### 2️⃣ Stock Spot Subscription (Mandatory for Stock Options)
```python
# Subscribe to stock spot prices
subscribe([
    "NSE:TCS",
    "NSE:INFY", 
    "NSE:RELIANCE",
    "NSE:HDFCBANK",
    # ... other liquid stocks
])
```
**Purpose:** 
- Get real-time stock price
- Calculate ATM strike for that stock
- Track stock movement

### 3️⃣ Option Contract Subscription (Mandatory for Trading)
```python
# Subscribe to option contracts for the active stock
subscribe([
    "NFO:TCS25JAN3500CE",
    "NFO:TCS25JAN3500PE",
    "NFO:TCS25JAN3550CE",
    "NFO:TCS25JAN3550PE",
    # ... ATM ± 3 strikes
])
```
**Purpose:**
- Get option LTP
- Get Bid/Ask prices
- Get Volume
- Get OI (Open Interest) in FULL mode

## 🔁 Dynamic Subscription Flow

### Step 1: Initial Connection
```python
# On WebSocket connect
subscribe([index_token])  # NSE:NIFTY
```

### Step 2: Calculate ATM
```python
# When index price received
atm_strike = round(nifty_ltp / 50) * 50
```

### Step 3: Subscribe to Stock Universe
```python
# Subscribe to all liquid stocks
liquid_stocks = ['TCS', 'INFY', 'RELIANCE', ...]
stock_tokens = get_stock_tokens(liquid_stocks)
subscribe(stock_tokens)
```

### Step 4: Dynamic Stock Selection
```python
# When a stock is selected for trading
active_stock = "TCS"
stock_price = get_price(f"NSE:{active_stock}")

# Calculate ATM for this stock
strike_interval = get_strike_interval(stock_price)
atm_strike = round(stock_price / strike_interval) * strike_interval

# Subscribe to option contracts
option_tokens = get_option_tokens(active_stock, atm_strike, range=3)
subscribe(option_tokens, mode='FULL')
```

## 📊 WebSocket Mode Recommendation

| Mode | Data Available | Use Case |
|------|---------------|----------|
| LTP | Last Price only | ❌ Not enough for options |
| QUOTE | LTP + OHLC + Volume | ⚠️ Limited |
| FULL | LTP + OI + Depth + Volume | ✅ Best for options |

**Recommendation:** Use `MODE_FULL` for stock options trading

## 🔥 Implementation in Code

### File: `strategy.py`

#### Method: `get_all_option_tokens()`
```python
def get_all_option_tokens(self):
    tokens = {self.index_token}  # Always include index
    
    if trading_mode == "stock_options":
        # 1. Subscribe to stock spots
        for stock in liquid_stocks:
            stock_token = get_stock_token(stock)
            tokens.add(stock_token)
        
        # 2. Subscribe to active stock options
        if self.active_stock_symbol:
            stock_price = self.prices[f"NSE:{self.active_stock_symbol}"]
            atm_strike = calculate_atm(stock_price)
            
            for strike in [atm_strike ± 3 strikes]:
                ce_token = get_option_token(stock, 'CE', strike)
                pe_token = get_option_token(stock, 'PE', strike)
                tokens.add(ce_token)
                tokens.add(pe_token)
    
    return list(tokens)
```

#### Method: `on_ticker_connect()`
```python
async def on_ticker_connect(self):
    # Use FULL mode for stock options
    ws_mode = 'FULL' if trading_mode == "stock_options" else 'LTP'
    self.ticker_manager.resubscribe([self.index_token], mode=ws_mode)
```

### File: `active_stock_tracker.py`

#### Method: `_subscribe_to_stock_options()`
```python
async def _subscribe_to_stock_options(self, stock_symbol):
    tokens = []
    
    # 1. Stock spot token
    stock_token = get_stock_token(stock_symbol)
    tokens.append(stock_token)
    
    # 2. Option contracts (ATM ± 3)
    stock_price = self.prices[f"NSE:{stock_symbol}"]
    atm_strike = calculate_atm(stock_price)
    
    for strike in range(atm_strike - 3*interval, atm_strike + 4*interval, interval):
        ce_opt = get_option(stock_symbol, 'CE', strike)
        pe_opt = get_option(stock_symbol, 'PE', strike)
        tokens.extend([ce_opt['token'], pe_opt['token']])
    
    # Subscribe with FULL mode
    self.ticker_manager.subscribe(tokens, mode='FULL')
```

## 📌 Key Points

1. **Index Feed** → Only for ATM calculation
2. **Stock Spot Feed** → Mandatory for stock price tracking
3. **Option Contract Feed** → Mandatory for trading
4. **FULL Mode** → Required for OI and depth data
5. **Dynamic Subscription** → Subscribe to active stock options on-demand

## 🎯 Final Answer

✅ **Subscribe to:**
- Index (NSE:NIFTY) → Find ATM
- Stock Spot (NSE:TCS) → Track stock price
- Option Contracts (NFO:TCS25JAN3500CE/PE) → Trade options

❌ **Don't rely on:**
- Index feed alone
- LTP mode for options trading

✔️ **Best Practice:**
- Use FULL mode
- Subscribe dynamically when stock is selected
- Unsubscribe from inactive stocks to save bandwidth
