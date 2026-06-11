import { useState, useEffect, useMemo } from 'react'
import { getNifty50Data, getSensexData } from '../services/api'
import { useStore } from '../store/store'
import './DashboardStyles.css'

function AnalyzeDashboard() {
  const [viewMode, setViewMode] = useState('heatmap')
  const [niftyData, setNiftyData] = useState([])
  const [sensexData, setSensexData] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const socket = useStore(state => state.socket)
  const selectedMode = useStore(state => state.params.selectedIndex)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const requestToken = params.get('request_token')
    
    if (requestToken) {
      import('../services/api').then(({ authenticate }) => {
        authenticate(requestToken)
          .then(() => {
            window.location.href = window.location.pathname
          })
          .catch((err) => console.error('Auth failed:', err))
      })
      return
    }

    if (!selectedMode?.includes('Liquidity Stock Options')) {
      setLoading(false)
      return
    }

    const fetchData = async () => {
      try {
        setError(null)
        const [niftyRes, sensexRes] = await Promise.all([
          getNifty50Data(),
          getSensexData()
        ])
        setNiftyData(niftyRes || [])
        setSensexData(sensexRes || [])
      } catch (err) {
        console.error('Error fetching data:', err)
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [selectedMode])

  const handleManualRefresh = async () => {
    setRefreshing(true)
    try {
      const [niftyRes, sensexRes] = await Promise.all([
        getNifty50Data(),
        getSensexData()
      ])
      setNiftyData(niftyRes || [])
      setSensexData(sensexRes || [])
      setError(null)
    } catch (err) {
      console.error('Error refreshing data:', err)
      setError(err.message)
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    if (!socket) return

    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'market_data_update') {
          if (data.nifty) setNiftyData(data.nifty)
          if (data.sensex) setSensexData(data.sensex)
        }
      } catch (err) {
        console.error('WebSocket message error:', err)
      }
    }

    socket.addEventListener('message', handleMessage)
    return () => socket.removeEventListener('message', handleMessage)
  }, [socket])

  const processLiquidityStocks = (data) => {
    if (!data || data.length === 0) return []
    const sorted = [...data].sort((a, b) => {
      const aChange = parseFloat(a.priceChange.replace(/[%+]/g, ''))
      const bChange = parseFloat(b.priceChange.replace(/[%+]/g, ''))
      return bChange - aChange
    })
    return [...sorted.slice(0, 5), ...sorted.slice(-5).reverse()]
  }

  // Top 5 gainers + bottom 5 losers for BOTH display AND backend
  const niftyLiquidity = useMemo(() => processLiquidityStocks(niftyData), [niftyData])
  const sensexLiquidity = useMemo(() => processLiquidityStocks(sensexData), [sensexData])
  
  const niftyDisplay = niftyLiquidity
  const sensexDisplay = sensexLiquidity

  useEffect(() => {
    const combined = [...niftyLiquidity, ...sensexLiquidity]
    if (combined.length === 0) return
    
    // ONLY send data when in "Liquidity Stock Options" mode
    if (!selectedMode?.includes('Liquidity Stock Options')) return
    
    // Send to backend
    fetch(`${import.meta.env.VITE_API_HTTP_URL}/api/update_liquidity_data`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stocks: combined })
    }).catch((err) => console.error('Failed to update liquidity data:', err))
    
    // Broadcast to WebSocket for option chain
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'liquidity_stocks_update', payload: combined }))
    }
  }, [niftyLiquidity, sensexLiquidity, socket, selectedMode])

  const getCardClass = (changeStr) => {
    const val = parseFloat(changeStr.replace('%', ''))
    if (val > 2.0) return 'card-bg-high-pos'
    if (val > 1.5) return 'card-bg-med-pos'
    if (val > 0) return 'card-bg-low-pos'
    return 'card-bg-neg'
  }

  const getPriceChangeClass = (changeStr) => {
    const val = parseFloat(changeStr.replace(/[%+]/g, ''))
    return val >= 0 ? 'positive' : 'negative'
  }

  if (loading) return <div className="loading-state">Loading Market Data...</div>
  
  if (error && (!niftyData.length && !sensexData.length)) {
    return (
      <div className="error-state">
        <h3>⚠️ Error</h3>
        <p>{error}</p>
      </div>
    )
  }

  if (!selectedMode?.includes('Liquidity Stock Options')) return null

  return (
    <div className="analyze-dashboard">
      <div className="dashboard-controls">
        <h2>Liquidity Dashboard (Top 5 Gainers + Bottom 5 Losers)</h2>
        <p>Debug: Nifty {niftyData.length} items, Sensex {sensexData.length} items | Loading: {loading ? 'Yes' : 'No'} | Error: {error || 'None'}</p>
        <div className="view-controls">
          <button 
            className="view-btn refresh-btn"
            onClick={handleManualRefresh}
            disabled={refreshing}
          >
            {refreshing ? '🔄 Refreshing...' : '🔄 Refresh'}
          </button>
          <button 
            className={`view-btn ${viewMode === 'table' ? 'active' : ''}`}
            onClick={() => setViewMode('table')}
          >
            Table
          </button>
          <button 
            className={`view-btn ${viewMode === 'heatmap' ? 'active' : ''}`}
            onClick={() => setViewMode('heatmap')}
          >
            Heat Map
          </button>
        </div>
      </div>

      {/* Test data display */}
      {niftyData.length === 0 && sensexData.length === 0 && (
        <div style={{padding: '20px', background: '#f0f0f0', margin: '10px 0'}}>
          <h4>No data received from API</h4>
          <p>Check browser Network tab for API errors</p>
        </div>
      )}

      {/* Nifty Section */}
      <div className="market-section">
        <h3 className="section-title" style={{ borderLeft: '4px solid #2980b9' }}>
          Nifty 50 Liquidity <span className="count-badge">{niftyDisplay.length} Stocks</span>
        </h3>
        
        {niftyDisplay.length > 0 ? (
          viewMode === 'heatmap' ? (
            <div className="heatmap-grid">
              {niftyDisplay.map((row, idx) => (
                <div 
                  key={`${row.stock}-${idx}`} 
                  className={`stock-card ${getCardClass(row.priceChange)}`}
                >
                  <div className="card-header">
                    <span>{row.weightage}% Wgt</span>
                    <span className="stock-price-highlight">{row.futPrice}</span>
                  </div>
                  <div className="card-body">
                    <span className="stock-symbol">{row.stock}</span>
                    <div className="card-percent">{row.priceChange}</div>
                  </div>
                  <div className="card-footer">
                    <span>IV: {row.atmIV}</span>
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
                     <th>Fut Price</th>
                     <th>ATM IV</th>
                     <th>Weight</th>
                   </tr>
                 </thead>
                 <tbody>
                   {niftyDisplay.map((r, i) => (
                     <tr key={i} className="clickable-row">
                       <td>{r.stock}</td>
                       <td>
                         ₹{r.futPrice} <span className={`trend-value ${getPriceChangeClass(r.priceChange)}`}>{r.priceChange}</span>
                       </td>
                       <td>{r.atmIV}</td>
                       <td>{r.weightage}%</td>
                     </tr>
                   ))}
                 </tbody>
              </table>
            </div>
          )
        ) : (
          <p>No Nifty data available</p>
        )}
      </div>

      <hr className="section-divider" />

      {/* Sensex Section */}
      <div className="market-section">
        <h3 className="section-title" style={{ borderLeft: '4px solid #e67e22' }}>
          Sensex Liquidity <span className="count-badge">{sensexDisplay.length} Stocks</span>
        </h3>
        
        {sensexDisplay.length > 0 ? (
          viewMode === 'heatmap' ? (
            <div className="heatmap-grid">
              {sensexDisplay.map((row, idx) => (
                <div 
                  key={`${row.stock}-${idx}`} 
                  className={`stock-card ${getCardClass(row.priceChange)}`}
                >
                  <div className="card-header">
                    <span>{row.weightage}% Wgt</span>
                    <span className="stock-price-highlight">{row.futPrice}</span>
                  </div>
                  <div className="card-body">
                    <span className="stock-symbol">{row.stock}</span>
                    <div className="card-percent">{row.priceChange}</div>
                  </div>
                  <div className="card-footer">
                    <span>IV: {row.atmIV}</span>
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
                     <th>Fut Price</th>
                     <th>ATM IV</th>
                     <th>Weight</th>
                   </tr>
                 </thead>
                 <tbody>
                   {sensexDisplay.map((r, i) => (
                     <tr key={i} className="clickable-row">
                       <td>{r.stock}</td>
                       <td>
                         ₹{r.futPrice} <span className={`trend-value ${getPriceChangeClass(r.priceChange)}`}>{r.priceChange}</span>
                       </td>
                       <td>{r.atmIV}</td>
                       <td>{r.weightage}%</td>
                     </tr>
                   ))}
                 </tbody>
              </table>
            </div>
          )
        ) : (
          <p>No Sensex data available</p>
        )}
      </div>
    </div>
  )
}

export default AnalyzeDashboard