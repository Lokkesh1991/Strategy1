import os
import time
from datetime import datetime, timedelta, time as dtime
from kiteconnect import KiteConnect

# Use Railway_token_manager on cloud, default one locally
if os.getenv("RAILWAY_ENVIRONMENT"):
    from Railway_token_manager import get_access_token
else:
    from token_manager import get_access_token

# === CONFIG ===
LOTS_PER_ENTRY = 5
TOTAL_LOTS = 15
DRY_RUN = False
MONITOR_INTERVAL = 3600
STAGGER_DELAY = 1800

# === KITE SETUP ===
def get_kite_client():
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise Exception("API Key not set in environment variables.")

    kite = KiteConnect(api_key=api_key)
    access_token = get_access_token()
    kite.set_access_token(access_token)
    kite.profile()
    print("✅ Connected to Kite.")
    return kite

# === UTILITIES ===
def get_contract_symbol(year, month):
    return f"GOLDM{str(year)[2:]}{datetime(year, month, 1).strftime('%b').upper()}FUT"

def is_trading_day(date):
    mcx_holidays = ["2025-04-10", "2025-04-14", "2025-05-01"]
    return date.strftime('%Y-%m-%d') not in mcx_holidays and date.weekday() < 5

def get_expiry_date(year, month):
    expiry = datetime(year, month, 5).date()
    while not is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry

def get_rollover_date(expiry):
    count = 0
    day = expiry
    while count < 6:
        day -= timedelta(days=1)
        if is_trading_day(day):
            count += 1
    return day

def place_kite_order(kite, symbol, quantity, transaction_type, retries=3):
    if DRY_RUN:
        print(f"[DRY RUN] {datetime.now()} - {transaction_type} {quantity} of {symbol}")
        return True
    for attempt in range(1, retries + 1):
        try:
            kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange="MCX",
                tradingsymbol=symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=kite.PRODUCT_NRML,
                order_type=kite.ORDER_TYPE_MARKET
            )
            print(f"{datetime.now()} - {transaction_type} {quantity} of {symbol} (Attempt {attempt})")
            return True
        except Exception as e:
            print(f"{datetime.now()} - Attempt {attempt} failed: {e}")
            if attempt < retries:
                print("\U0001F501 Retrying in 5 seconds...")
                time.sleep(5)
    print("\u274C All retries failed.")
    return False

def get_current_position(kite, retries=3):
    for attempt in range(1, retries + 1):
        try:
            positions = kite.positions()['net']
            break
        except Exception as e:
            print(f"\u26A0\uFE0F Attempt {attempt} to fetch positions failed: {e}")
            if attempt < retries:
                time.sleep(5)
            else:
                print("\u274C Could not fetch positions after retries.")
                return 0, ""
    total_lots = 0
    latest_symbol = ""
    for pos in positions:
        if "GOLDM" in pos['tradingsymbol'] and pos['product'] == "NRML" and pos['quantity'] > 0:
            total_lots += pos['quantity']
            latest_symbol = pos['tradingsymbol']
    return total_lots, latest_symbol

# === MONITORING STRATEGY ===
def run_goldm_monitor():
    kite = get_kite_client()

    for batch in range(3):
        now = datetime.now()
        today = now.date()
        now_time = now.time()

        if dtime(9, 0) <= now_time < dtime(9, 5):
            wait_sec = (datetime.combine(today, dtime(9, 5)) - now).seconds
            print(f"\u26D4 Between 9:00–9:05 AM. Sleeping {wait_sec} seconds...")
            time.sleep(wait_sec)
            continue

        market_open_today = is_trading_day(today)
        expiry = get_expiry_date(2025, 6)
        rollover = get_rollover_date(expiry)
        days_to_expiry = (expiry - today).days

        new_contract = get_contract_symbol(2025, 7 if today >= rollover or days_to_expiry <= 8 else 6)
        current_lots, current_contract = get_current_position(kite)
        print(f"\U0001F551 [{now.strftime('%H:%M:%S')}] Held: {current_lots} in {current_contract}")

        if today >= rollover and current_contract != new_contract and current_lots > 0:
            print(f"\U0001F504 Rollover triggered. Switching from {current_contract} to {new_contract}...")
            for i in range(current_lots):
                print(f"\u274C Selling 1 lot of {current_contract} and Buying 1 lot of {new_contract}...")
                place_kite_order(kite, current_contract, 1, KiteConnect.TRANSACTION_TYPE_SELL)
                place_kite_order(kite, new_contract, 1, KiteConnect.TRANSACTION_TYPE_BUY)
                time.sleep(STAGGER_DELAY)

            print("\u2705 Rollover complete. Sleeping 1 hour before next cycle...")
            time.sleep(MONITOR_INTERVAL)
            continue

        if current_lots >= TOTAL_LOTS:
            print(f"\u2705 Target ({TOTAL_LOTS}) lots already held. Sleeping for 1 hour...")
            time.sleep(MONITOR_INTERVAL)
            continue

        if not market_open_today:
            print("\U0001F514 Today is not a trading day. No orders will be placed, sleeping for 1 hour...")
            time.sleep(MONITOR_INTERVAL)
            continue

        print(f"\u26A0\uFE0F Less than {TOTAL_LOTS} lots held. Starting staggered entry...")

        while True:
            now = datetime.now()
            now_time = now.time()

            if dtime(9, 0) <= now_time < dtime(9, 5):
                wait_sec = (datetime.combine(today, dtime(9, 5)) - now).seconds
                print(f"\u26D4 Between 9:00–9:05 AM. Sleeping {wait_sec} seconds...")
                time.sleep(wait_sec)
                continue

            expiry = get_expiry_date(2025, 6)
            rollover = get_rollover_date(expiry)
            days_to_expiry = (expiry - today).days
            new_contract = get_contract_symbol(2025, 7 if today >= rollover or days_to_expiry <= 8 else 6)

            current_lots, _ = get_current_position(kite)
            if current_lots >= TOTAL_LOTS:
                print("\u2705 Reached required lots during stagger. Stopping entry loop.")
                break

            lots_to_place = min(LOTS_PER_ENTRY, TOTAL_LOTS - current_lots)
            print(f"\U0001F4C8 Placing {lots_to_place} lot(s) of {new_contract} at {now.strftime('%H:%M:%S')}.")

            success = place_kite_order(kite, new_contract, lots_to_place, KiteConnect.TRANSACTION_TYPE_BUY)
            if success:
                print(f"\u2705 Placed {lots_to_place} lot(s).")
            else:
                print(f"\u274C Order failed. Will retry after delay.")

            time.sleep(STAGGER_DELAY)

        print(f"\u2705 All {TOTAL_LOTS} lots placed. Sleeping 1 hour before next cycle...")
        time.sleep(MONITOR_INTERVAL)

# === MAIN ===
if __name__ == "__main__":
    run_goldm_monitor()
