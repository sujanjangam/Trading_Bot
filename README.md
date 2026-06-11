# Trading Bot - Advanced Algorithmic Trading System

[![CI](https://github.com/sujanjangam/Trading_Bot/actions/workflows/ci.yml/badge.svg)](https://github.com/sujanjangam/Trading_Bot/actions/workflows/ci.yml)

A production-ready, full-stack algorithmic trading bot built with Python (FastAPI) and React. This system integrates with Zerodha Kite broker API to execute sophisticated multi-strategy trading with real-time market data processing, intelligent risk management, and comprehensive performance analytics.

## 🎯 Overview

This is an enterprise-grade trading bot that combines cutting-edge trading strategies with robust risk management and real-time monitoring capabilities. The system supports multiple trading modes including index options, stock options, and high-volume liquidity trading.

### Key Highlights

- **Multi-Mode Trading**: Index Options (NIFTY, SENSEX, BANKNIFTY), Stock Options (NIFTY50/SENSEX constituents), and High Volume Liquidity Trading
- **Advanced Trading Strategies**: Liquidity Trend Following, RSI Crossover, MA Crossover, Candlestick Patterns, Trend Continuation, and UOA Scanner
- **Real-time Market Data**: WebSocket-based live ticker from Zerodha Kite with intelligent subscription management
- **Intelligent Risk Management**: Hybrid capital system, dynamic position sizing, ATR-based trailing stops, and circuit breakers
- **Full-stack Dashboard**: React-based UI with real-time charts, analytics, and comprehensive bot control
- **Persistent Logging**: SQLite database for complete trade history and performance analysis
- **Parameter Optimization**: Built-in tools to analyze historical performance and suggest improvements

## 📁 Project Structure

```
Trading_Bot/
├── backend/                          # FastAPI backend service
│   ├── core/                        # Core trading engine modules
│   │   ├── strategy.py              # Main strategy orchestrator
│   │   ├── liquidity_engine.py      # Liquidity-based trading engine
│   │   ├── entry_strategies.py      # Multiple entry strategy implementations
│   │   ├── order_manager.py         # Order execution with freeze limit handling
│   │   ├── risk_manager.py          # Position sizing and risk calculations
│   │   ├── data_manager.py          # Market data processing and indicators
│   │   ├── bot_service.py           # Bot lifecycle management
│   │   ├── kite.py                  # Zerodha Kite integration
│   │   ├── kite_ticker_manager.py   # WebSocket ticker management
│   │   ├── websocket_manager.py     # Frontend WebSocket communication
│   │   ├── database.py              # SQLite database management
│   │   ├── trade_logger.py          # Trade logging and persistence
│   │   ├── circuit_breaker.py       # Circuit breaker implementation
│   │   ├── active_stock_tracker.py  # Active stock tracking for options
│   │   ├── high_volume_scanner.py   # High volume stock scanner
│   │   ├── iv_calculator.py         # Implied volatility calculator
│   │   ├── option_chain_api.py      # Option chain data provider
│   │   ├── selected_strike_api.py   # Strike selection API
│   │   ├── stock_token_cache.py     # Stock token caching
│   │   ├── order_monitor.py         # Order status monitoring
│   │   ├── optimiser.py             # Parameter optimization
│   │   ├── v47_coordinator.py       # V47 strategy coordinator
│   │   └── market_data_streamer.py  # Market data streaming
│   ├── config/                      # Configuration files
│   │   ├── trading_config.py        # Trading parameters and settings
│   │   ├── cors_config.py           # CORS configuration
│   │   ├── exclusion_lists.json     # Stocks to exclude from trading
│   │   ├── free_float_shares.json   # Free float data for weightage calculation
│   │   └── README.md                # Configuration documentation
│   ├── main.py                      # FastAPI application entry point
│   ├── manual_auth.py               # Manual authentication utility
│   ├── requirements.txt             # Python dependencies
│   ├── strategy_params.json         # Strategy-specific parameters
│   ├── .env.example                 # Environment variables template
│   ├── README.md                    # Backend documentation
│   ├── SECURITY.md                  # Security best practices
│   ├── PHASE2_IMPROVEMENTS.md       # Future enhancements
│   └── WEBSOCKET_SUBSCRIPTION_STRATEGY.md  # WebSocket strategy docs
│
├── frontend/                        # React frontend dashboard
│   ├── public/
│   │   └── sound/                   # Audio alert files
│   │       ├── entry.mp3
│   │       ├── profit.mp3
│   │       ├── loss.mp3
│   │       └── warning.mp3
│   ├── src/
│   │   ├── components/              # React components
│   │   │   ├── StatusPanel.jsx      # Bot status display
│   │   │   ├── CurrentTradePanel.jsx # Active trade monitoring
│   │   │   ├── ParametersPanel.jsx  # Strategy parameter configuration
│   │   │   ├── IntelligencePanel.jsx # Market intelligence
│   │   │   ├── NetPerformancePanel.jsx # Performance metrics
│   │   │   ├── IndexChart.jsx       # TradingView charts
│   │   │   ├── OptionChain.jsx      # Live option chain
│   │   │   ├── LogTabs.jsx          # Trade and debug logs
│   │   │   ├── StraddleMonitor.jsx  # Straddle monitoring
│   │   │   ├── AnalyzeDashboard.jsx # NIFTY50/SENSEX analysis
│   │   │   └── HighVolumeDashboard.jsx # High volume stocks
│   │   ├── services/                # API and WebSocket services
│   │   │   ├── api.js               # REST API client
│   │   │   └── socket.js            # WebSocket client
│   │   ├── store/                   # State management
│   │   │   └── store.js             # Zustand store
│   │   ├── App.jsx                  # Main application component
│   │   ├── ErrorBoundary.jsx        # Error handling
│   │   └── main.jsx                 # Application entry point
│   ├── package.json                 # NPM dependencies
│   ├── vite.config.js               # Vite configuration
│   └── README.md                    # Frontend documentation
│
└── README.md                        # This file
```

## ✨ Key Features

### Backend Features

#### Trading Modes
- **Index Options Trading**: Trade NIFTY, SENSEX, and BANKNIFTY options with weekly expiry
- **Stock Options Trading**: Trade options on NIFTY50 and SENSEX constituent stocks
- **High Volume Liquidity Trading**: Identify and trade high-volume liquid stocks with tight spreads

#### Broker Integration
- Secure authentication with Zerodha Kite API
- Automatic session management and token persistence
- Support for both NFO (National Futures & Options) and BFO (BSE Futures & Options) exchanges
- Intelligent instrument caching with 24-hour validity

#### Real-time Market Data
- WebSocket-based live ticker for instant market updates
- Intelligent token subscription management
- Support for both LTP (Last Traded Price) and FULL mode (with market depth)
- Automatic reconnection with exponential backoff
- Failsafe mechanism for disconnections during active trades

#### Advanced Trading Strategies
1. **Liquidity Trend Following** (Primary for Stock Options)
   - Identifies top gainers/losers from NIFTY50/SENSEX constituents
   - ATM strike selection with ₹5 minimum premium filter
   - 3-candle trend continuation validation
   - Dynamic strike interval calculation based on stock price

2. **RSI Crossover with Angle Confirmation**
   - Mean reversion based on RSI levels
   - RSI angle validation for momentum confirmation
   - Configurable RSI periods and thresholds

3. **MA Crossover Anticipation**
   - Trend-following using WMA and SMA
   - Gap threshold validation
   - Early entry on crossover anticipation

4. **Candlestick Pattern Recognition**
   - Bullish/Bearish Engulfing patterns
   - Hammer and Doji detection
   - Pattern strength validation

5. **Trend Continuation**
   - Supertrend-based trend identification
   - Multi-candle trend validation
   - Momentum confirmation

6. **UOA (Unusual Options Activity) Scanner**
   - Volume/OI ratio analysis
   - Conviction score calculation
   - Strike distance weighting

#### Intelligent Risk Management
- **Hybrid Capital System**: Combines live Zerodha capital with GUI threshold
- **Smart Capital Adjustment**: Reduces position size on losses, maintains on profits
- **Dynamic Position Sizing**: Based on capital, risk percentage, and stop-loss
- **ATR-Based Trailing Stops**: 10-period ATR with 1.2x multiplier
- **Breakeven Strategy**: Moves stop-loss to entry price after profit target
- **Circuit Breakers**: Daily stop-loss and profit targets
- **Freeze Limit Handling**: Automatic order slicing for large quantities
- **Basket Order Execution**: Parallel execution of multiple order slices

#### Position Management
- Entry with custom pricing (LTP + buffer for instant fills)
- Partial profit-taking at configurable levels
- Trailing stop-loss (both fixed and ATR-based)
- Trend invalidation exits (consecutive opposing candles)
- EOD auto-square-off at 3:20 PM
- Manual exit capability from UI

#### Data Management & Analytics
- **SQLite Database**: Separate databases for today's trades and all-time history
- **Trade Logging**: Complete audit trail with entry/exit prices, P&L, charges, duration
- **Performance Metrics**: Win rate, profit factor, max drawdown, Sharpe ratio
- **Historical Data**: Fetches and caches historical candles for indicators
- **Option Chain Data**: Real-time option chain with strike-wise CE/PE prices
- **Straddle Monitoring**: Tracks ATM straddle premium changes

#### WebSocket Communication
- Real-time status updates to frontend
- Live trade updates with P&L
- Chart data streaming (candlesticks, indicators)
- Option chain updates
- Debug logging
- Audio alert triggers

### Frontend Features

#### Dashboard Components
- **Status Panel**: Connection status, trading mode, index price, trend, capital info
- **Current Trade Panel**: Active position details, P&L, trailing stop, manual exit
- **Parameters Panel**: Dynamic strategy parameter configuration with validation
- **Intelligence Panel**: Market intelligence and strategy insights
- **Net Performance Panel**: Daily P&L, win rate, total trades, charges
- **Index Chart**: TradingView Lightweight Charts with candlesticks, WMA, SMA, RSI
- **Option Chain**: Real-time color-coded option chain with strike-wise prices
- **Straddle Monitor**: ATM straddle premium tracking with percentage change
- **Analyze Dashboard**: NIFTY50/SENSEX constituent analysis with IV, weightage
- **High Volume Dashboard**: High-volume liquid stocks scanner
- **Log Tabs**: Separate tabs for trade history and debug logs

#### User Experience
- **Real-time Updates**: WebSocket-based live data streaming
- **Audio Alerts**: Entry, profit, loss, and warning sounds
- **Visual Notifications**: Snackbar notifications for important events
- **Responsive Design**: Material-UI components with mobile support
- **Error Boundaries**: Graceful error handling for each component
- **Persistent State**: Zustand store for state management
- **Auto-reconnection**: Automatic WebSocket reconnection with exponential backoff

## 🛠️ Tech Stack

### Backend
- **Framework**: FastAPI (async web framework)
- **Server**: Uvicorn (ASGI server) / Gunicorn (production)
- **Broker API**: KiteConnect (Zerodha)
- **Data Processing**: Pandas, NumPy, pandas_ta
- **Database**: SQLAlchemy with SQLite
- **Async**: asyncio, aiohttp
- **WebSocket**: websockets library
- **Technical Indicators**: pandas_ta, scipy
- **Python**: 3.9+

### Frontend
- **Framework**: React 18
- **Build Tool**: Vite
- **UI Components**: Material-UI (MUI) v5
- **Charting**: TradingView Lightweight Charts v4
- **Real-time**: WebSocket API
- **State Management**: Zustand v5
- **Notifications**: Notistack v3
- **Audio**: Howler.js v2
- **Data Visualization**: Recharts v3
- **Node.js**: v18+

## 🚀 Quick Start

### Prerequisites
- **Python 3.9+** with pip
- **Node.js v18+** with npm
- **Zerodha Kite Account** with API credentials

### Backend Setup

1. **Navigate to backend directory:**
   ```bash
   cd backend
   ```

2. **Create virtual environment:**
   ```bash
   # Windows
   python -m venv venv
   venv\Scripts\activate
   
   # Linux/macOS
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   - Copy `.env.example` to `.env`
   - Add your Zerodha Kite API credentials:
     ```env
     API_KEY="your_kite_api_key"
     API_SECRET="your_kite_api_secret"
     ```

5. **Configure trading parameters:**
   - Edit `config/trading_config.py` for market timings, stock lists, and thresholds
   - Edit `strategy_params.json` for strategy-specific parameters
   - Edit `config/exclusion_lists.json` to exclude specific stocks

6. **Run the backend:**
   ```bash
   python main.py
   ```
   The API will be available at `http://localhost:8000`

### Frontend Setup

1. **Navigate to frontend directory:**
   ```bash
   cd frontend
   ```

2. **Install dependencies:**
   ```bash
   npm install
   ```

3. **Configure environment (optional):**
   - Create `.env` file if you need to customize the backend URL
   - Default backend URL is `http://localhost:8000`

4. **Run development server:**
   ```bash
   npm run dev
   ```
   The dashboard will be available at `http://localhost:5173`

### First-Time Authentication

1. Open the frontend dashboard in your browser
2. Click on the login URL provided in the Status Panel
3. Authorize the application on Zerodha Kite
4. Copy the request token from the redirect URL
5. Paste it in the authentication dialog
6. The bot will save the access token for future sessions

## 📚 Documentation

- **[Backend Documentation](backend/README.md)** - Detailed backend setup, API endpoints, and architecture
- **[Frontend Documentation](frontend/README.md)** - UI components, state management, and usage guide
- **[Security Guide](backend/SECURITY.md)** - Security best practices and credential management
- **[Trading Config](backend/config/README.md)** - Configuration parameters explanation
- **[WebSocket Strategy](backend/WEBSOCKET_SUBSCRIPTION_STRATEGY.md)** - WebSocket subscription optimization
- **[Phase 2 Improvements](backend/PHASE2_IMPROVEMENTS.md)** - Planned enhancements and roadmap

## 🔧 Configuration

### Key Configuration Files

#### `backend/config/trading_config.py`
- Market timings (open, close, cutoff)
- NIFTY50 and SENSEX constituent lists
- Liquidity parameters (top N, minimum change percentage)

#### `backend/strategy_params.json`
- Strategy priority order
- Technical indicator periods (WMA, SMA, RSI, ATR)
- Entry/exit thresholds
- Volume and spread filters

#### `backend/config/exclusion_lists.json`
- Stocks to exclude from trading
- Useful for avoiding illiquid or problematic stocks

#### `backend/config/free_float_shares.json`
- Free float share data for NIFTY50 and SENSEX
- Used for dynamic weightage calculation

### Trading Parameters (Configurable from UI)

- **Capital Management**
  - Start Capital (GUI threshold)
  - Risk per Trade (%)
  - Trading Mode (Paper/Live)

- **Position Sizing**
  - Max Lots per Order
  - Freeze Limit Handling

- **Stop Loss & Profit Targets**
  - Trailing SL Points
  - Trailing SL Percentage
  - Breakeven Profit %
  - Daily Stop Loss
  - Daily Profit Target

- **Partial Exits**
  - Partial Profit %
  - Partial Exit %

- **Strategy Selection**
  - Strike Selection (ATM/ITM/OTM)
  - Enable/Disable specific strategies
  - Auto UOA Scanning

## 📊 Performance Monitoring

### Real-time Metrics
- Current position P&L (absolute and percentage)
- Daily gross P&L
- Total charges (brokerage, STT, GST, etc.)
- Daily net P&L
- Win rate (winning trades / total trades)
- Active trailing stop-loss level

### Historical Analytics
- Total trades executed
- Winning vs losing trades
- Profit factor (gross profit / gross loss)
- Maximum drawdown
- Average trade duration
- Risk-adjusted returns (Sharpe Ratio)
- Equity curve visualization

### Database Access
- Today's trades: `trading_data_today.db`
- All-time history: `trading_data_all.db`
- Query using SQLite tools or Python scripts

## 🔐 Security

### Authentication & Authorization
- Secure token-based authentication with Zerodha
- Access token persistence in `access_token.json`
- Automatic token refresh on expiry
- Session validation on startup

### Data Protection
- Credentials stored in `.env` file (never in version control)
- `.gitignore` configured to exclude sensitive files
- CORS properly configured for frontend communication
- WebSocket authentication

### Best Practices
- Use environment variables for all secrets
- Regularly rotate API keys
- Monitor access logs
- Use HTTPS/WSS in production
- Implement rate limiting for API endpoints

See [Security Guide](backend/SECURITY.md) for detailed security recommendations.

## 🐛 Troubleshooting

### Backend Issues

**Problem**: Bot fails to start
- **Solution**: Check if Zerodha credentials are correct in `.env`
- **Solution**: Verify market is open (9:15 AM - 3:30 PM IST)
- **Solution**: Check if access token is valid (re-authenticate if needed)

**Problem**: WebSocket disconnects frequently
- **Solution**: Check internet connection stability
- **Solution**: Verify Zerodha API status
- **Solution**: Check logs for specific error messages

**Problem**: Orders not executing
- **Solution**: Verify trading mode is set to "Live Trading"
- **Solution**: Check if sufficient capital is available
- **Solution**: Verify freeze limit is not exceeded
- **Solution**: Check if daily stop-loss/profit target is hit

**Problem**: No trades being taken
- **Solution**: Verify strategy parameters are not too restrictive
- **Solution**: Check if cutoff time (3:20 PM) has passed
- **Solution**: Verify liquidity data is being received (for stock options mode)
- **Solution**: Check if bot is paused

### Frontend Issues

**Problem**: Dashboard not loading
- **Solution**: Verify backend is running on `http://localhost:8000`
- **Solution**: Check browser console for errors (F12)
- **Solution**: Clear browser cache and refresh (Ctrl+Shift+R)

**Problem**: WebSocket not connecting
- **Solution**: Verify backend WebSocket endpoint is accessible
- **Solution**: Check if firewall is blocking WebSocket connections
- **Solution**: Verify CORS configuration in backend

**Problem**: Charts not displaying
- **Solution**: Verify chart data is being received via WebSocket
- **Solution**: Check if TradingView Lightweight Charts library loaded correctly
- **Solution**: Clear browser cache and reload

**Problem**: Option chain not updating
- **Solution**: Verify active stock is selected (for stock options mode)
- **Solution**: Check if WebSocket is connected
- **Solution**: Verify option instruments are loaded in backend

## 📈 Trading Strategies Explained

### 1. Liquidity Trend Following (Stock Options)

**Entry Conditions:**
- Stock must be in NIFTY50 or SENSEX constituents
- Price change ≥ ±1.0% from previous close
- ATM option premium ≥ ₹5
- 3-candle trend continuation on stock chart
- Option spread ≤ 5 paisa (optional validation)

**Exit Conditions:**
- ATR-based trailing stop (10-period, 1.2x multiplier)
- Profit target (configurable)
- EOD square-off at 3:20 PM
- Manual exit from UI

**Strike Selection:**
- Always ATM (At-The-Money)
- Dynamic strike interval based on stock price

### 2. RSI Crossover (Index Options)

**Entry Conditions:**
- RSI crosses above/below signal line
- RSI angle exceeds threshold (momentum confirmation)
- Trend alignment with RSI direction
- Minimum ATR value met

**Exit Conditions:**
- Trailing stop-loss
- Trend invalidation (consecutive opposing candles)
- Partial profit-taking
- Daily stop-loss/profit target

### 3. MA Crossover Anticipation (Index Options)

**Entry Conditions:**
- WMA approaching SMA (gap < threshold)
- Trend direction confirmed
- Volume above average
- ATR above minimum

**Exit Conditions:**
- MA crossover reversal
- Trailing stop-loss
- Trend invalidation

### 4. Candlestick Patterns (Index Options)

**Supported Patterns:**
- Bullish/Bearish Engulfing
- Hammer
- Doji
- Shooting Star

**Entry Conditions:**
- Pattern detected on index chart
- Pattern strength validation
- Trend alignment
- Volume confirmation

**Exit Conditions:**
- Opposite pattern detected
- Trailing stop-loss
- Profit target

### 5. UOA Scanner (Index Options)

**Entry Conditions:**
- High volume/OI ratio
- Significant premium change
- Strike distance from ATM
- Conviction score above threshold

**Exit Conditions:**
- Trailing stop-loss
- Profit target
- Time-based exit

## ⚠️ Disclaimer

This trading bot is provided as-is for educational and research purposes. Trading involves significant risk of loss and is not suitable for all investors. 

**Important Considerations:**
- Always start with paper trading to test strategies
- Use appropriate position sizing (never risk more than 1-2% per trade)
- Never risk more than you can afford to lose
- Conduct your own due diligence before using in production
- Monitor the bot during trading hours
- Understand all strategies before deploying
- Be aware of market risks, slippage, and execution delays
- Past performance does not guarantee future results

**Liability:**
The creators and contributors of this project are not liable for any financial losses incurred through the use of this software. Use at your own risk.

## 📝 License

This project is provided as-is. Check LICENSE file for details.

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

### Development Guidelines
- Follow PEP 8 style guide for Python code
- Use ESLint configuration for JavaScript/React code
- Write descriptive commit messages
- Add comments for complex logic
- Update documentation for new features
- Test thoroughly before submitting PR

## 📧 Support

For issues, questions, or suggestions:
- Open a GitHub issue with detailed description
- Check existing documentation first
- Review backend and frontend README files
- Include logs and error messages in bug reports

## 🚀 Future Enhancements

### Planned Features
- **Machine Learning Integration**: ML-based strategy optimization and prediction
- **Advanced Backtesting Engine**: Historical strategy testing with realistic slippage
- **Multi-Symbol Concurrent Trading**: Trade multiple symbols simultaneously
- **Mobile App**: iOS and Android apps for remote monitoring
- **Additional Brokers**: Integration with Upstox, Angel One, Fyers
- **Advanced Reporting**: Detailed performance reports and tax reporting
- **Strategy Builder**: Visual strategy builder for non-programmers
- **Cloud Deployment**: Docker containers and Kubernetes support
- **Telegram Bot**: Trade notifications and control via Telegram
- **Portfolio Management**: Multi-account and portfolio-level risk management

### Performance Improvements
- Redis caching for faster data access
- PostgreSQL for better database performance
- Microservices architecture for scalability
- Load balancing for high-frequency trading
- GPU acceleration for ML models

See [PHASE2_IMPROVEMENTS.md](backend/PHASE2_IMPROVEMENTS.md) for detailed roadmap.

## 📊 System Requirements

### Minimum Requirements
- **CPU**: Dual-core processor (2.0 GHz+)
- **RAM**: 4 GB
- **Storage**: 1 GB free space
- **Internet**: Stable broadband connection (1 Mbps+)
- **OS**: Windows 10/11, Ubuntu 20.04+, macOS 10.15+

### Recommended Requirements
- **CPU**: Quad-core processor (3.0 GHz+)
- **RAM**: 8 GB
- **Storage**: 5 GB free space (for historical data)
- **Internet**: High-speed broadband (10 Mbps+)
- **OS**: Windows 11, Ubuntu 22.04+, macOS 12+

## 🔄 Version History

### Version 1.0 (January 2026)
- Initial production release
- Multi-mode trading support (Index, Stock, High Volume)
- Liquidity trend following strategy
- ATR-based trailing stops
- Hybrid capital system
- Real-time dashboard with TradingView charts
- Complete trade logging and analytics

---

**Last Updated**: January 2026  
**Version**: 1.0  
**Status**: Production-Ready  
**Maintainer**: Trading Bot Development Team

---

## 🙏 Acknowledgments

- **Zerodha Kite**: For providing robust API and documentation
- **TradingView**: For the excellent Lightweight Charts library
- **FastAPI**: For the modern, fast web framework
- **React**: For the powerful UI library
- **Material-UI**: For beautiful, accessible components
- **Open Source Community**: For all the amazing libraries and tools

---

**Happy Trading! 📈🚀**
