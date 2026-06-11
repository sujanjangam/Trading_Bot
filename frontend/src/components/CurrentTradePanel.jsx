import React, { useState } from 'react';
import { Paper, Typography, Box, Grid, Button, CircularProgress } from '@mui/material';
import { useStore } from '../store/store'; // Add this import

export default function CurrentTradePanel({ trade, onManualExit }) {
    const [loading, setLoading] = useState(false);
    const isSpectator = useStore(state => state.isSpectatorMode); // Get the flag

    const handleExitClick = async () => {
        setLoading(true);
        await onManualExit();
        setLoading(false);
    };

    if (!trade) {
        return (
            <Paper elevation={3} sx={{ p: 2 }}>
                <Typography variant="body2" sx={{ mb: 1 }}>Current Trade</Typography>
                <Typography sx={{ fontWeight: 'bold' }}>STATUS: No Active Trade</Typography>
            </Paper>
        );
    }

    const pnlColor = trade.pnl >= 0 ? 'success.main' : 'error.main';
    const ltp = trade.ltp || trade.entry_price; // Use LTP, fallback to entry price if needed

    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>Current Trade</Typography>
            
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 'bold' }}>{trade.symbol}</Typography>
                <Typography variant="h6" sx={{ fontWeight: 'bold' }}>Entry @ {trade.entry_price.toFixed(2)}</Typography>
            </Box>

            <Grid container spacing={1.5} sx={{ textAlign: 'left' }}>
                <Grid item xs={6}>
                    <Typography variant="body2">LTP</Typography>
                    <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                        {ltp.toFixed(2)}
                    </Typography>
                </Grid>
                <Grid item xs={6}>
                    <Typography variant="body2">P&L</Typography>
                    <Typography variant="h6" sx={{ color: pnlColor, fontWeight: 'bold' }}>
                        ₹ {trade.pnl.toFixed(2)}
                    </Typography>
                </Grid>
                <Grid item xs={6}>
                    <Typography variant="body2">
                        {trade.atr_trailing_sl !== undefined ? 'ATR TrailingStop' : (trade.segment === 'OPTSTK' ? 'ATR Trailing SL' : 'Trail SL')}
                    </Typography>
                    <Typography 
                        variant="h6" 
                        sx={{ 
                            fontWeight: 'bold',
                            color: (trade.atr_trailing_sl !== undefined || trade.segment === 'OPTSTK') ? 'warning.main' : 'text.primary'
                        }}
                    >
                        {trade.atr_trailing_sl !== undefined
                            ? (trade.atr_trailing_sl > 0 ? trade.atr_trailing_sl.toFixed(2) : 'Building...')
                            : (trade.segment === 'OPTSTK'
                                ? (trade.atr_trailing_sl?.toFixed(2) ?? '--')
                                : trade.trail_sl.toFixed(2)
                            )
                        }
                    </Typography>
                </Grid>
                <Grid item xs={6}>
                    <Typography variant="body2">Profit %</Typography>
                    <Typography variant="h6" sx={{ color: pnlColor, fontWeight: 'bold' }}>
                        {trade.profit_pct.toFixed(2)} %
                    </Typography>
                </Grid>
            </Grid>
            
            <Button 
                fullWidth 
                variant="contained" 
                color="error" 
                sx={{ mt: 2 }} 
                onClick={handleExitClick}
                disabled={loading || isSpectator}
            >
                {loading ? <CircularProgress size={24} color="inherit" /> : 'Manual Exit Trade'}
            </Button>
        </Paper>
    );
}