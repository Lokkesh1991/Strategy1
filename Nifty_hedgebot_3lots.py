import os
import time
import re
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
from token_manager import get_access_token

LOTS_TO_SELL = 3
BATCH_SIZE = 1
REBALANCE_INTERVAL = 2 * 60 * 60
PRICE_STEP = 1.0
MAX_ATTEMPTS = 5
CHECK_INTERVAL = 5
MAX_WAIT_CYCLES = 6

def get_kite_client():
    print("⚡ Connecting to Kite...")
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise Exception("API Key not set in environment variables.")
    kite = KiteConnect(api_key=api_key)
    access_token = get_access_token()
    if not access_token:
        raise Exception("Access token not retrieved.")
    kite.set_access_token(access_token)
    kite.profile()
    print("✅ Connected to Kite.")
    return kite

def get_last_thursday(year, month):
    last_day = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year + 1, 1, 1) - timedelta(days=1)
    while last_day.weekday() != 3:
        last_day -= timedelta(days=1)
    return last_day.date()

def get_next_month_expiry(current_expiry):
    return get_last_thursday(current_expiry.year + (1 if current_expiry.month == 12 else 0),
                              1 if current_expiry.month == 12 else current_expiry.month + 1)

def format_expiry_for_symbol(expiry_date):
    return expiry_date.strftime("%y%b").upper()

def get_existing_ce_positions(kite):
    positions = kite.positions()
    ce_pos = {}
    for pos in positions['net']:
        if "NIFTY" in pos['tradingsymbol'] and "CE" in pos['tradingsymbol'] and pos['product'] == "NRML" and pos['quantity'] < 0:
            match = re.search(r'NIFTY\d{2}[A-Z]{3}(\d+)CE', pos['tradingsymbol'])
            if match:
                strike = int(match.group(1))
                ce_pos[strike] = ce_pos.get(strike, 0) + abs(pos['quantity'] // 75)
    return ce_pos

def get_total_ce_lots(kite):
    return sum(get_existing_ce_positions(kite).values())

def get_nifty_futures_ltp(kite):
    today = datetime.today()
    expiry = get_last_thursday(today.year, today.month)
    if (expiry - today.date()).days <= 4:
        expiry = get_next_month_expiry(expiry)
    expiry_code = expiry.strftime("%y%b").upper()
    symbol = f"NIFTY{expiry_code}FUT"
    print(f"🔎 Looking for LTP of: NFO:{symbol}")
    ltp_data = kite.ltp(f"NFO:{symbol}")
    if not ltp_data or not list(ltp_data.values()):
        raise Exception(f"LTP data not available for {symbol}. Check if contract exists.")
    return list(ltp_data.values())[0]['last_price']

def get_ce_strike_distribution(fut_price):
    atm = int((fut_price + 99) / 100) * 100  # round up
    return {atm + 300: 1, atm + 400: 1, atm + 500: 1}

def place_ce_sell_order(kite, strike, expiry_date, lots):
    expiry_code = expiry_date.strftime("%y%b").upper()
    symbol = f"NIFTY{expiry_code}{strike}CE"
    for attempt in range(MAX_ATTEMPTS):
        try:
            if get_total_ce_lots(kite) >= LOTS_TO_SELL:
                print("🛑 Aborting, target CE lots already held.")
                return
            ltp_data = kite.ltp(f"NFO:{symbol}")
            ltp = list(ltp_data.values())[0]['last_price']
            price = round(ltp - PRICE_STEP, 1)
            print(f"{datetime.now()} - SELL {lots} lot(s) of {symbol} @ {price} (Attempt {attempt + 1})")
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=lots * 75,
                price=price,
                product=kite.PRODUCT_NRML,
                order_type=kite.ORDER_TYPE_LIMIT
            )
            for _ in range(MAX_WAIT_CYCLES):
                order = kite.order_history(order_id)[-1]
                if order["status"] == "COMPLETE":
                    print(f"✅ Order filled: {symbol}")
                    return
                elif order["status"] in ["REJECTED", "CANCELLED"]:
                    raise Exception(f"Order failed: {order['status']}")
                time.sleep(CHECK_INTERVAL)
            kite.cancel_order(kite.VARIETY_REGULAR, order_id)
        except Exception as e:
            print(f"❌ Attempt {attempt + 1} failed for {symbol}: {e}")
            time.sleep(5)
    print(f"❌ All attempts failed for {symbol}. Skipping.")

def run_nifty_ce_hedge_bot():
    print("🚀 Starting NIFTY CE Hedge Bot...")
    kite = get_kite_client()
    today = datetime.today().date()
    expiry = get_last_thursday(today.year, today.month)
    if (expiry - today).days <= 4:
        expiry = get_next_month_expiry(expiry)
    print(f"📅 Using expiry: {expiry}")

    while True:
        current_lots = get_total_ce_lots(kite)
        if current_lots < LOTS_TO_SELL:
            print(f"📉 Holding {current_lots} CE lots. Placing hedge orders...")
            fut_price = get_nifty_futures_ltp(kite)
            dist = get_ce_strike_distribution(fut_price)
            for strike, qty in dist.items():
                for _ in range(qty):
                    place_ce_sell_order(kite, strike, expiry, 1)
                    time.sleep(3)
        else:
            print(f"✅ Holding full CE lots ({current_lots}). Next rebalance after {REBALANCE_INTERVAL//60} mins.")
        next_time = datetime.now() + timedelta(seconds=REBALANCE_INTERVAL)
        print(f"⏳ Next check at {next_time.strftime('%H:%M:%S')}")
        time.sleep(REBALANCE_INTERVAL)

if __name__ == "__main__":
    run_nifty_ce_hedge_bot()
