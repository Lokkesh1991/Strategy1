import os
import json
import datetime
from kiteconnect import KiteConnect
from dotenv import load_dotenv

# === Load environment variables ===
load_dotenv()
api_key = os.getenv("KITE_API_KEY")
api_secret = os.getenv("KITE_API_SECRET")

# === Debug logging ===
print(f"🔍 Loaded KITE_API_KEY: {api_key if api_key else '❌ NOT SET'}")
print(f"🔍 Loaded KITE_API_SECRET: {'✅ YES' if api_secret else '❌ NOT SET'}")

# === Token storage path ===
TOKEN_FILE = "token.json"

def get_stored_token():
    """
    Return stored token if it exists and is not expired.
    """
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
                else:
                    print("⚠️ Stored token is expired.")
    else:
        print("⚠️ Token file does not exist.")
    return None

def store_token(token, expiry):
    """
    Save token and expiry to file.
    """
    data = {
        "access_token": token,
        "expiry": expiry.isoformat()
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)
    print("✅ New token saved to file.")

def generate_new_token():
    """
    Generate token manually using request token.
    """
    if not api_key or not api_secret:
        raise Exception("❌ API key or secret is missing. Check your .env or environment settings.")

    kite = KiteConnect(api_key=api_key)
    print("🔗 Login URL:", kite.login_url())
    request_token = input("📥 Enter the request token from the redirected URL: ").strip()
    
    try:
        session_data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session_data["access_token"]

        # Expiry set to today 11:59 PM
        today = datetime.date.today()
        expiry = datetime.datetime.combine(today, datetime.time(23, 59))
        store_token(access_token, expiry)
        print("✅ New access token generated and stored.")
        return access_token
    except Exception as e:
        print(f"❌ Failed to generate token: {e}")
        raise

def get_access_token():
    """
    Return a valid access token.
    """
    token = get_stored_token()
    if token:
        return token
    else:
        print("🔄 No valid token found, generating new one...")
        return generate_new_token()
