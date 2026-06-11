import React from 'react';
import { Paper, Typography, TableContainer, Table, TableHead, TableBody, TableRow, TableCell } from '@mui/material';
import { useStore } from '../store/store';

export default function OptionChain({ activeStock }) {
    const indexPrice = useStore(state => state.botStatus.indexPrice);
    const data = useStore(state => state.optionChain);
    const currentTrade = useStore(state => state.currentTrade);

    // Hide option chain when no active trade
    if (!currentTrade) {
        return null;
    }

    // Use stock price if available, otherwise use index price
    const referencePrice = activeStock?.price || indexPrice;

    const getRowStyle = (strike) => {
        const diff = Math.abs(strike - referencePrice);
        let threshold;
        if (activeStock) {
            const price = activeStock.price || referencePrice;
            if (price < 150) threshold = 5;
            else if (price < 500) threshold = 10;
            else if (price < 1000) threshold = 20;
            else if (price < 2500) threshold = 40;
            else if (price < 5000) threshold = 100;
            else threshold = 200;
        } else {
            threshold = 100;
        }
        if (diff <= threshold) return { backgroundColor: 'rgba(255, 255, 0, 0.1)' };
        return {};
    };
    
    // Get option chain title
    const getChainTitle = () => {
        if (activeStock?.symbol) {
            return `${activeStock.symbol} Option Chain`;
        }
        return 'Option Chain';
    };
    
    return (
        <Paper elevation={3} sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 1, fontWeight: 'bold' }}>
                {getChainTitle()}
            </Typography>
            <TableContainer sx={{ maxHeight: 250 }}>
                <Table stickyHeader size="small">
                    <TableHead>
                        <TableRow>
                            <TableCell align="center" sx={{color: 'success.main'}}>LTP (CE)</TableCell>
                            <TableCell align="center">Strike</TableCell>
                            <TableCell align="center" sx={{color: 'error.main'}}>LTP (PE)</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {data.map((row) => (
                            <TableRow key={row.strike} sx={getRowStyle(row.strike)}>
                                <TableCell align="center" sx={{backgroundColor: row.strike < referencePrice ? 'rgba(0, 255, 0, 0.05)' : 'rgba(255, 0, 0, 0.05)'}}>
                                    {row.ce_ltp}
                                </TableCell>
                                <TableCell align="center" sx={{ fontWeight: 'bold' }}>{row.strike}</TableCell>
                                <TableCell align="center" sx={{backgroundColor: row.strike > referencePrice ? 'rgba(0, 255, 0, 0.05)' : 'rgba(255, 0, 0, 0.05)'}}>
                                    {row.pe_ltp}
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </TableContainer>
        </Paper>
    );
}
