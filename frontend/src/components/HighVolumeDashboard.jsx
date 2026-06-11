import { useState, useEffect, useRef } from 'react'
import { getHighVolumeStocks, getStatus, updateLiquidityData } from '../services/api'
import { useStore } from '../store/store'
import './DashboardStyles.css'

function HighVolumeDashboard() {
  const selectedMode = useStore(state => state.params.selectedIndex)
  const [viewMode, setViewMode] = useState('heatmap')
  const [stocksData, setStocksData] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [authStatus, setAuthStatus] = useState(null)
  const [isMarketClosed, setIsMarketClosed] = useState(false)
  const lastDataRef = useRef(null)

  const isMarketOpen = () => {
    const now = new Date()
    const day = now.getDay()
    if (day === 0 || day === 6) return false
    const time = now.getHours() * 60 + now.getMinutes()
    return time >= 555 && time < 930 // 9:15 AM to 3:30 PM
  }

  const fetchData = async () => {
    const marketOpen = isMarketOpen()
    setIsMarketClosed(!marketOpen)

    if (!marketOpen && lastDataRef.current) {
      setStocksData(lastDataRef.current)
      return
    }

    try {
      setError(null)
      setLoading(true)
      
      const [status, response] = await Promise.all([
        getStatus(),
        getHighVolumeStocks()
      ])
      
      setAuthStatus(status)
      const stocks = response.data || []
      setStocksData(stocks)
      lastDataRef.current = stocks
      
      if (stocks.length > 0) {
        await updateLiquidityData(stocks)
      }
    } catch (err) {
      console.error('Error fetching high volume stocks:', err)
      setError(err.message)
      
      try {
        const status = await getStatus()
        setAuthStatus(status)
      } catch (statusErr) {
        console.error('Failed to get auth status:', statusErr)
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (selectedMode !== 'High Volume Liquidity Stock (ON SPREAD VOLUME)') {
      setStocksData([])
      setLoading(false)
      return
    }

    fetchData()
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [selectedMode])

  const getCardClass = (changeVal) => {
    if (changeVal > 2.0) return 'card-bg-high-pos'
    if (changeVal > 1.0) return 'card-bg-med-pos'
    if (changeVal > 0) return 'card-bg-low-pos'
    return 'card-bg-neg'
  }

  const getPriceChangeClass = (changeVal) => {
    return changeVal >= 0 ? 'positive' : 'negative'
  }

  const formatNumber = (num) => {
    if (num >= 10000000) return `${(num / 10000000).toFixed(2)}Cr`
    if (num >= 100000) return `${(num / 100000).toFixed(2)}L`
    return num.toLocaleString('en-IN')
  }

  if (selectedMode !== 'High Volume Liquidity Stock (ON SPREAD VOLUME)') return null

  if (loading && stocksData.length === 0) return <div className="loading-state">Scanning High Volume Stocks...</div>
  
  if (error && stocksData.length === 0) {
    return (
      <div className="error-state">
        <h3>⚠️ Error</h3>
        <p>{error}</p>
        {authStatus?.login_url && (
          <p>
            <a href={authStatus.login_url} target="_blank" rel="noopener noreferrer" 
               style={{color: '#2980b9', textDecoration: 'underline'}}>
              Click here to authenticate with Zerodha
            </a>
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="analyze-dashboard">
      <div className="dashboard-controls">
        <h2>🔥 High Volume Liquid Stocks (Excluding NIFTY50 & SENSEX)</h2>
        <p style={{fontSize: '0.9em', color: '#666'}}>
          Criteria: Notional ≥ ₹1L | Spread ≤ ₹1 | Volume &gt; 1L | Top 50 by Volume
        </p>
        {isMarketClosed && stocksData.length > 0 && (
          <p style={{fontSize: '0.85em', color: '#e67e22', fontWeight: 'bold'}}>
            📊 Market Closed - Showing last closing data
          </p>
        )}
        {!isMarketClosed && stocksData.length > 0 && (
          <p style={{fontSize: '0.85em', color: '#27ae60', fontWeight: 'bold'}}>
            ✅ Live data sent to Liquidity Engine - Trading top gainers/losers
          </p>
        )}
        <div className="view-controls">
          <button 
            className="view-btn"
            onClick={fetchData}
            disabled={loading}
            style={{marginRight: '10px'}}
          >
            {loading ? '🔄 Refreshing...' : '🔄 Refresh Now'}
          </button>
          <button 
            className={`view-btn ${viewMode === 'table' ? 'active' : ''}`}
            onClick={() => setViewMode('table')}
          >
            Table View
          </button>
          <button 
            className={`view-btn ${viewMode === 'heatmap' ? 'active' : ''}`}
            onClick={() => setViewMode('heatmap')}
          >
            Heat Map
          </button>
        </div>
      </div>

      <div className="market-section">
        <h3 className="section-title" style={{ borderLeft: '4px solid #27ae60' }}>
          High Volume Stocks <span className="count-badge">{stocksData.length} Stocks</span>
        </h3>
        
        {stocksData.length > 0 ? (
          viewMode === 'heatmap' ? (
            <div className="heatmap-grid">
              {stocksData.map((stock, idx) => (
                <div 
                  key={`${stock.stock}-${idx}`} 
                  className={`stock-card ${getCardClass(stock.priceChange)}`}
                >
                  <div className="card-header">
                    <span>Vol: {formatNumber(stock.volume)}</span>
                    <span className="stock-price-highlight">₹{stock.lastPrice}</span>
                  </div>
                  <div className="card-body">
                    <span className="stock-symbol">{stock.stock}</span>
                    <div className="card-percent">{stock.priceChange > 0 ? '+' : ''}{stock.priceChange}%</div>
                  </div>
                  <div className="card-footer">
                    <span>Spread: ₹{stock.spread}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="analyze-table-wrapper">
              <table className="analyze-table">
                <thead>
                  <tr>
                    <th>Stock</th>
                    <th>Last Price</th>
                    <th>Bid</th>
                    <th>Ask</th>
                    <th>Spread</th>
                    <th>Bid Notional</th>
                    <th>Ask Notional</th>
                    <th>Volume</th>
                    <th>Change %</th>
                  </tr>
                </thead>
                <tbody>
                  {stocksData.map((stock, i) => (
                    <tr key={i} className="clickable-row">
                      <td><strong>{stock.stock}</strong></td>
                      <td>₹{stock.lastPrice}</td>
                      <td>
                        ₹{stock.bidPrice}
                        <br />
                        <small style={{color: '#666'}}>Qty: {stock.bidQty}</small>
                      </td>
                      <td>
                        ₹{stock.askPrice}
                        <br />
                        <small style={{color: '#666'}}>Qty: {stock.askQty}</small>
                      </td>
                      <td>
                        ₹{stock.spread}
                        <br />
                        <small style={{color: '#666'}}>({stock.spreadPct}%)</small>
                      </td>
                      <td>₹{formatNumber(stock.bidNotional)}</td>
                      <td>₹{formatNumber(stock.askNotional)}</td>
                      <td>{formatNumber(stock.volume)}</td>
                      <td>
                        <span className={`trend-value ${getPriceChangeClass(stock.priceChange)}`}>
                          {stock.priceChange > 0 ? '+' : ''}{stock.priceChange}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          <p>No high volume stocks found matching criteria</p>
        )}
      </div>
    </div>
  )
}

export default HighVolumeDashboard
