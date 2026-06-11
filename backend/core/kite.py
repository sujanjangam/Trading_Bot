import os
import json
from datetime import datetime
from dotenv import load_dotenv
from kiteconnect import KiteConnect
import random

load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
PAPER_TRADING = os.getenv("PAPER_TRADING", "false").lower() == "true"

kite = KiteConnect(api_key=API_KEY)
access_token = None

# Paper trading order counter
_paper_order_id = 1000000 

def save_access_token(token):
    with open("access_token.json", "w") as f:
        json.dump({"access_token": token, "date": datetime.now().strftime("%Y-%m-%d")}, f)

def load_access_token():
    try:
        with open("access_token.json", "r") as f:
            data = json.load(f)
            if data["date"] == datetime.now().strftime("%Y-%m-%d"): return data["access_token"]
    except Exception: return None
    return None

def set_access_token(token):
    global access_token
    if not token: 
        access_token = None
        return False, "Token is null or empty."
    try:
        kite.set_access_token(token)
        profile = kite.profile()
        access_token = token
        print(f"Kite connection verified for user: {profile['user_id']}")
        return True, profile
    except Exception as e:
        error_message = f"Error setting access token: {e}"
        print(error_message)
        access_token = None
        return False, str(e)

def generate_session_and_set_token(request_token):
    try:
        session = kite.generate_session(request_token, api_secret=API_SECRET)
        token = session["access_token"]
        save_access_token(token)
        return set_access_token(token)
    except Exception as e:
        error_message = f"Authentication failed: {e}"
        print(error_message)
        return False, str(e)

# --- NEW REUSABLE FUNCTION ---
def re_initialize_session_from_file():
    """Loads the access token from the JSON file and sets the session."""
    print("--- Attempting to initialize session from file... ---")
    saved_token = load_access_token()
    if saved_token:
        print(f"Found saved token: {saved_token[:10]}...")
        success, result = set_access_token(saved_token)
        if not success:
            print(f"Token validation failed: {result}")
        return success
    else:
        print("--- No valid access token file found. ---")
        return False

class PaperTradingWrapper:
    """Wrapper for paper trading mode"""
    
    def __init__(self, kite_instance):
        self._kite = kite_instance
    
    def place_order(self, **kwargs):
        """Simulate order placement in paper trading mode"""
        global _paper_order_id
        if PAPER_TRADING:
            _paper_order_id += 1
            print(f"📝 PAPER TRADE: {kwargs.get('transaction_type')} {kwargs.get('quantity')} {kwargs.get('tradingsymbol')} @ {kwargs.get('order_type')}")
            return str(_paper_order_id)
        return self._kite.place_order(**kwargs)
    
    def order_history(self, order_id):
        """Simulate order history in paper trading mode"""
        if PAPER_TRADING:
            return [{
                "order_id": order_id,
                "status": "COMPLETE",
                "status_message": "Paper trade - simulated fill",
                "filled_quantity": 0,
                "pending_quantity": 0
            }]
        return self._kite.order_history(order_id=order_id)
    
    def cancel_order(self, variety, order_id):
        """Simulate order cancellation in paper trading mode"""
        if PAPER_TRADING:
            print(f"📝 PAPER TRADE: Cancelled order {order_id}")
            return order_id
        return self._kite.cancel_order(variety, order_id)
    
    def __getattr__(self, name):
        """Delegate all other methods to real kite instance"""
        return getattr(self._kite, name)

# Wrap kite instance if paper trading is enabled
if PAPER_TRADING:
    print("⚠️ PAPER TRADING MODE ENABLED - No real orders will be placed")
    kite = PaperTradingWrapper(kite)

# --- INITIAL STARTUP CALL ---
re_initialize_session_from_file()