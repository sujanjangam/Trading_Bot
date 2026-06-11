import asyncio
from typing import Dict, Optional
from .kite import kite
from .option_chain_api import OptionChainAPI

class SelectedStrikeAPI:
    def __init__(self):
        self.option_chain_api = OptionChainAPI()
    
    async def get_selected_strike_options(self, symbol: str, spot_price: float, strike_selection: str = 'ATM') -> Dict:
        """Get CE and PE LTP for the bot's selected strike"""
        try:
            # Calculate the strike based on selection
            strike = self._calculate_strike(spot_price, strike_selection)
            
            # Get option instruments
            options = await self.option_chain_api._get_option_instruments(symbol)
            if not options:
                return {'strike': strike, 'ce_ltp': 0, 'pe_ltp': 0, 'error': 'No options found'}
            
            # Get CE and PE data for the calculated strike
            ce_data = await self.option_chain_api._get_option_data(options, strike, 'CE')
            pe_data = await self.option_chain_api._get_option_data(options, strike, 'PE')
            
            return {
                'strike': strike,
                'ce_ltp': ce_data.get('ltp', 0),
                'pe_ltp': pe_data.get('ltp', 0),
                'ce_volume': ce_data.get('volume', 0),
                'pe_volume': pe_data.get('volume', 0),
                'spot_price': spot_price,
                'strike_selection': strike_selection
            }
            
        except Exception as e:
            print(f"Error fetching selected strike options for {symbol}: {e}")
            return {'strike': 0, 'ce_ltp': 0, 'pe_ltp': 0, 'error': str(e)}
    
    def _calculate_strike(self, spot_price: float, strike_selection: str) -> float:
        """Calculate strike based on selection (ATM/ITM/OTM)"""
        strike_interval = self.option_chain_api._get_strike_interval(spot_price)
        atm_strike = round(spot_price / strike_interval) * strike_interval
        
        if strike_selection == 'ATM':
            return atm_strike
        elif strike_selection == 'ITM':
            # For ITM, go one strike in the money
            return atm_strike - strike_interval
        elif strike_selection == 'OTM':
            # For OTM, go one strike out of the money
            return atm_strike + strike_interval
        else:
            return atm_strike  # Default to ATM