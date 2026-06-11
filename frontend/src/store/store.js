import { create } from 'zustand';

const spectatorFlag = !!import.meta.env.VITE_MASTER_BACKEND_URL;

const initialRealtimeState = {
    chartData: null,
    botStatus: { connection: 'DISCONNECTED', mode: 'NOT STARTED', indexPrice: 0, trend: '---', indexName: 'INDEX', is_running: false, is_paused: false },
    dailyPerformance: { grossPnl: 0, totalCharges: 0, netPnl: 0, wins: 0, losses: 0 },
    currentTrade: null,
    debugLogs: [],
    tradeHistory: [],
    allTimeTradeHistory: [], 
    optionChain: [],
    uoaList: [],
    straddleData: null,
    socketStatus: 'DISCONNECTED',
    activeStock: null,
    lastTradedSymbol: null,
    highVolumeStocks: [],
    selectedStrikeData: null,
    config: { cutoff_time: '15:15', liquidity_top_n: 10, min_change_pct: 0.8 },
};

// ===== Parameters Slice =====
const createParametersSlice = (set) => ({
    params: {},
    loadParams: () => {
        const savedParams = localStorage.getItem('tradingParams');
        const defaultParams = {
            selectedIndex: 'SENSEX', trading_mode: 'Paper Trading',
            start_capital: 50000, risk_per_trade_percent: 2.0, trailing_sl_points: 5, 
            trailing_sl_percent: 2.5, daily_sl: -20000, daily_pt: 40000, 
            trade_profit_target: 1000, break_even_percent: 5, partial_profit_pct: 3, partial_exit_pct: 30, auto_scan_uoa: false,
            supertrend_period: 5, supertrend_multiplier: 0.7,
            stock_momentum_pct: 1.0, liquidity_rank: '3', strike_selection: 'ATM', max_positions: 3, enable_liquidity_logic: false
        };
        set({ params: savedParams ? JSON.parse(savedParams) : defaultParams });
    },
    setParams: (newParams) => {
        localStorage.setItem('tradingParams', JSON.stringify(newParams));
        set({ params: newParams });
    },
    updateParam: (name, value) => set((state) => {
        const updatedParams = { ...state.params, [name]: value };
        localStorage.setItem('tradingParams', JSON.stringify(updatedParams));
        return { params: updatedParams };
    }),
});

// ===== Real-time Data Slice =====
const createRealtimeDataSlice = (set) => ({
    ...initialRealtimeState,
    resetRealtimeData: () => set(initialRealtimeState),
    isSpectatorMode: spectatorFlag,
    setSocketStatus: (status) => set({ socketStatus: status }),
    setTradeHistory: (history) => set({ tradeHistory: history }),
    setAllTimeTradeHistory: (history) => set({ allTimeTradeHistory: history }),
    updateBotStatus: (payload) => set({ botStatus: payload }),
    updateDailyPerformance: (payload) => set({ dailyPerformance: payload }),
    updateCurrentTrade: (payload) => set((state) => {
        const updates = { currentTrade: payload };
        // Persist option symbol when trade is active
        if (payload?.symbol && payload.symbol.match(/\d{2}[A-Z]{3}\d+[CP]E$/)) {
            updates.lastTradedSymbol = payload.symbol;
        }
        return updates;
    }),
    addDebugLog: (payload) => set(state => ({ debugLogs: [payload, ...state.debugLogs].slice(0, 500) })),
    updateOptionChain: (payload) => set({ optionChain: payload }),
    updateUoaList: (payload) => set({ uoaList: payload }),
    updateChartData: (payload) => set({ chartData: payload }),
    updateStraddleData: (payload) => set({ straddleData: payload }),
    updateActiveStock: (payload) => set((state) => {
        const updates = { activeStock: payload };
        if (payload?.mode === 'stock_options' && payload?.optionSymbol) {
            updates.lastTradedSymbol = payload.optionSymbol;
        }
        return updates;
    }),
    updateHighVolumeStocks: (payload) => set({ highVolumeStocks: payload }),
    updateSelectedStrikeData: (payload) => set({ selectedStrikeData: payload }),
    updateConfig: (payload) => set({ config: payload }),
    addTradeToHistory: (trade) => set(state => ({ 
        tradeHistory: [trade, ...state.tradeHistory],
        allTimeTradeHistory: [trade, ...state.allTimeTradeHistory]
    })),
});

export const useStore = create((...a) => ({
    ...createParametersSlice(...a),
    ...createRealtimeDataSlice(...a),
}));

// Load params after store creation
setTimeout(() => {
    useStore.getState().loadParams();
}, 0);

