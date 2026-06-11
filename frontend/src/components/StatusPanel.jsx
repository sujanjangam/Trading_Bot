import React from 'react';
import { Paper, Typography, Box } from '@mui/material';

export default function StatusPanel({ status, socketStatus, currentTrade }) {
    const isConnected = status.connection === 'CONNECTED';
    const modeColor = status.mode.includes("PAPER") ? 'success.main' : 'error.main';

    const displaySymbol = currentTrade?.symbol || status.indexName || 'INDEX';
    const displayPrice = currentTrade?.ltp ?? status.indexPrice ?? 0;

    return (
        <Paper elevation={3} sx={{ p: 1.5 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>Live Status</Typography>
            <Box sx={{ pl: 1 }}>

                <Typography variant="body1" sx={{ color: socketStatus === 'CONNECTED' ? 'success.main' : 'error.main', fontWeight: 'bold' }}>
                    Status: {status.connection}
                </Typography>

                <Typography variant="h6" sx={{ my: 0.5, fontWeight: 'bold', color: modeColor }}>
                    MODE: {status.mode}
                </Typography>

                <Typography variant="h5" color="primary" sx={{ fontWeight: 'bold' }}>
                    {displaySymbol}: {displayPrice.toFixed(2)}
                </Typography>

                <Typography variant="body1" sx={{ fontWeight: 'bold', mt: 0.5 }}>
                    Trend: <Typography component="span" sx={{ color: status.trend === 'BULLISH' ? 'success.main' : status.trend === 'BEARISH' ? 'error.main' : 'text.primary', fontWeight: 'bold' }}>{status.trend}</Typography>
                </Typography>
            </Box>
        </Paper>
    );
}