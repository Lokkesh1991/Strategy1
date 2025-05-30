import os
import time
import re
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
from token_manager import get_access_token

LOTS_TO_SELL = 3
BATCH_SIZE = 1
REBALANCE_INTERVAL = 2 * 60 * 60
ENTRY_DELAY = 1800
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

def get_existing_ce_positions(kite):
    positions = kite.positions()
    ce_pos = {}
    for pos in positions['net']:
        if (
            "NIFTY" in pos['tradingsymbol'] and
            "CE" in pos['tradingsymbol'] and
            pos['product'] == "NRML" and
            pos['quantity'] < 0
        ):
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
    base = int((fut_price + 99) // 100) * 100  # round up to next 100
    return {base + 300: 1, base + 400: 1, base + 500: 1}

def place_ce_sell_order(kite, strike, expiry_date, lots):
    expiry_code = expiry_date.strftime("%y%b").upper()
    symbol = f"NIFTY{expiry_code}{strike}CE"

    for attempt in range(MAX_ATTEMPTS):
        try:
            current_total_lots = get_total_ce_lots(kite)
            if current_total_lots >= LOTS_TO_SELL:
                print(f"🛑 Aborting order: already holding {current_total_lots} CE lots (target: {LOTS_TO_SELL})")
                return

            ltp_data = kite.ltp(f"NFO:{symbol}")
            if not ltp_data or not list(ltp_data.values()):
                raise Exception(f"LTP data not available for {symbol}")
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
                status = order["status"]
                if status == "COMPLETE":
                    print(f"✅ Order filled: {symbol} (Order ID: {order_id})")
                    return
                elif status in ["REJECTED", "CANCELLED"]:
                    raise Exception(f"Order {order_id} for {symbol} failed: {status}")
                print(f"⌛ Waiting for order to fill... Status: {status}")
                time.sleep(CHECK_INTERVAL)

            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
            print(f"⚠️ Cancelled stale order: {order_id}")

        except Exception as e:
            print(f"❌ Attempt {attempt + 1} failed for {symbol}: {e}")
            time.sleep(5)

    print("❌ All attempts failed for", symbol)
    print("⏸️ Skipping this strike and moving to next after maintaining only current lots")

def run_nifty_ce_hedge_bot():
    print("🚀 Starting NIFTY CE Hedge Bot...")
    kite = get_kite_client()
    today = datetime.today().date()
    expiry = get_last_thursday(today.year, today.month)
    if (expiry - today).days <= 4:
        expiry = get_next_month_expiry(expiry)
    print(f"📅 Using expiry: {expiry}")

    # Wait until 9:20 AM IST before placing first order
    now = datetime.now()
    first_trade_time = now.replace(hour=9, minute=20, second=0, microsecond=0)
    if now < first_trade_time:
        wait_seconds = (first_trade_time - now).total_seconds()
        print(f"⏳ Waiting until 9:20 AM to start CE hedge entry... ({int(wait_seconds)} seconds)")
        time.sleep(wait_seconds)

    while True:
        current_lots = get_total_ce_lots(kite)
        if current_lots < LOTS_TO_SELL:
            print(f"📉 Holding {current_lots} CE lots. Starting new round of CE hedging...")
            fut_price = get_nifty_futures_ltp(kite)
            dist = get_ce_strike_distribution(fut_price)
            print(f"🎯 Target strike distribution: {dist}")
            for strike, qty in dist.items():
                for _ in range(qty):
                    place_ce_sell_order(kite, strike, expiry, 1)
                    time.sleep(3)
        else:
            print(f"✅ Already holding {current_lots} CE lots. Performing rebalance...")
            fut_price = get_nifty_futures_ltp(kite)
            dist = get_ce_strike_distribution(fut_price)
            current_distribution = get_existing_ce_positions(kite)
            to_exit = {}
            to_enter = {}
            for strike in set(current_distribution.keys()).union(dist.keys()):
                cur = current_distribution.get(strike, 0)
                tgt = dist.get(strike, 0)
                if cur > tgt:
                    to_exit[strike] = cur - tgt
                elif cur < tgt:
                    to_enter[strike] = tgt - cur
            print(f"📤 To exit: {to_exit}")
            print(f"📥 To enter: {to_enter}")

        next_time = datetime.now() + timedelta(seconds=REBALANCE_INTERVAL)
        print(f"⏳ Next check at {next_time.strftime('%H:%M:%S')}\n")
        time.sleep(REBALANCE_INTERVAL)

if __name__ == "__main__":
    run_nifty_ce_hedge_bot()
