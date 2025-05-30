import os
import json
import datetime
from kiteconnect import KiteConnect

TOKEN_FILE = "token.json"

def get_stored_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
            token = data.get("access_token")
            expiry_str = data.get("expiry")
            if token and expiry_str:
                expiry = datetime.datetime.fromisoformat(expiry_str)
                if expiry > datetime.datetime.now():
                    print("✅ Using stored access token.")
                    return token
    return None

def get_access_token():
    """
    Return the stored token if valid. Railway can't use interactive login.
    """
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")

    print(f"🔍 KITE_API_KEY: {'✅ SET' if api_key else '❌ NOT SET'}")
    print(f"🔍 KITE_API_SECRET: {'✅ SET' if api_secret else '❌ NOT SET'}")

    if not api_key or not api_secret:
        raise Exception("❌ API key or secret missing from environment variables.")

    token = get_stored_token()
    if token:
        return token
    else:
        raise Exception("❌ No valid token found and Railway can't run interactive login. Upload a fresh token.json.")
