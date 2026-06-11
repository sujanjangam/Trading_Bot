# Configuration Files

## free_float_shares.json

Contains free float share data (in crores) for Nifty 50 and Sensex stocks.

### Update Schedule
- **Frequency**: Quarterly (or after corporate actions)
- **Next Review**: Check `metadata.next_review_date` in the file
- **Last Updated**: Check `metadata.last_updated` in the file

### When to Update
Update this file immediately after:
- Stock splits
- Bonus issues
- Buyback programs
- Rights issues
- Any corporate action affecting share count

### How to Update
1. Open `free_float_shares.json`
2. Update the relevant stock's share count
3. Update `metadata.last_updated` to current date
4. Update `metadata.next_review_date` to next quarter end
5. Add notes in `metadata.notes` if needed

### Data Sources
- NSE official website: https://www.nseindia.com/market-data/live-equity-market
- Company annual reports
- BSE corporate announcements

### Automated Updates (Future Enhancement)
Consider implementing automated fetching of:
- Index constituents from NSE API
- Free float shares from company filings
- Corporate action notifications

## cors_config.py

CORS (Cross-Origin Resource Sharing) configuration for API security.

### Development
Uses localhost origins by default:
- http://localhost:3000
- http://localhost:3001

### Production
Set the `ALLOWED_ORIGINS` environment variable:

```bash
# Windows
set ALLOWED_ORIGINS=https://myapp.com,https://www.myapp.com

# Linux/Mac
export ALLOWED_ORIGINS=https://myapp.com,https://www.myapp.com
```

### Security Note
Never use `allow_origins=["*"]` in production as it allows any website to access your API.
