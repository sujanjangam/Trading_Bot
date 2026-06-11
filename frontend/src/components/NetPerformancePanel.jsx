import React from 'react';
import { Paper, Typography, Box, Grid } from '@mui/material';

// A small, reusable component for consistent text styling within the panel
const PnlText = ({ title, value, color = 'text.primary', isBold = false }) => (
    <Typography variant="body1" sx={{ color, fontWeight: isBold ? 'bold' : 'normal' }}>
        {title}: <Typography component="span" sx={{ fontWeight: 'bold' }}>₹ {value.toFixed(2)}</Typography>
    </Typography>
);

export default function NetPerformancePanel({ data }) {
    // Determine the color for P&L values based on whether they are positive or negative
    const netPnlColor = data.netPnl > 0 ? 'success.main' : data.netPnl < 0 ? 'error.main' : 'text.primary';
    const grossPnlColor = data.grossPnl > 0 ? 'success.main' : data.grossPnl < 0 ? 'error.main' : 'text.primary';
    
    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>Daily Performance</Typography>
            <Grid container spacing={0.5} sx={{ pl: 1 }}>
                <Grid item xs={12}>
                    <PnlText title="Gross P&L" value={data.grossPnl} color={grossPnlColor} />
                </Grid>
                <Grid item xs={12}>
                    <PnlText title="(-) Total Charges" value={data.totalCharges} color="text.secondary" />
                </Grid>
                <Grid item xs={12} sx={{ borderTop: '1px solid #e0e0e0', pt: 0.5, mt: 0.5 }}>
                    <PnlText title="Net P&L" value={data.netPnl} color={netPnlColor} isBold={true} />
                </Grid>
                <Grid item xs={12} sx={{ mt: 1 }}>
                     <Typography variant="body2">Wins: {data.wins} | Losses: {data.losses}</Typography>
                </Grid>
            </Grid>
        </Paper>
    );
}

