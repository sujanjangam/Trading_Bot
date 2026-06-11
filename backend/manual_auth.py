"""Manual authentication helper for Kite Connect"""
import os
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

kite = KiteConnect(api_key=API_KEY)

print("\n=== Kite Connect Manual Authentication ===\n")
print(f"1. Open this URL in your browser:\n{kite.login_url()}\n")
print("2. Login with your Kite credentials")
print("3. After redirect, copy the 'request_token' from the URL")
print("4. Paste it below\n")

request_token = input("Enter request_token: ").strip()

try:
    session = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = session["access_token"]
    
    # Save token
    import json
    from datetime import datetime
    with open("access_token.json", "w") as f:
        json.dump({
            "access_token": access_token,
            "date": datetime.now().strftime("%Y-%m-%d")
        }, f)
    
    print(f"\n✅ Authentication successful!")
    print(f"User: {session.get('user_id')}")
    print(f"Token saved to access_token.json")
    
except Exception as e:
    print(f"\n❌ Authentication failed: {e}")
