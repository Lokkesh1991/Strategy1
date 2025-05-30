import os
import time
from datetime import datetime, timedelta, time as dtime
from kiteconnect import KiteConnect
from token_manager import get_access_token

# === CONFIG ===
LOTS_PER_ENTRY = 1
TOTAL_LOTS = 3
DRY_RUN = False
MONITOR_INTERVAL = 3600  # 1 hour
STAGGER_DELAY = 1800     # 30 minutes

# === SETUP ===
def get_kite_client():
    api_key = os.getenv("KITE_API_KEY")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(get_access_token())
    kite.profile()
    return kite

# === UTILITIES ===
def is_trading_day(date):
    mcx_holidays = ["2025-04-10", "2025-04-14", "2025-05-01"]
    return date.strftime('%Y-%m-%d') not in mcx_holidays and date.weekday() < 5

def get_last_thursday(year, month):
    day = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year + 1, 1, 1) - timedelta(days=1)
    while day.weekday() != 3:
        day -= timedelta(days=1)
    return day.date()

def get_rollover_date(expiry):
    count, day = 0, expiry
    while count < 6:
        day -= timedelta(days=1)
        if is_trading_day(day):
            count += 1
    return day

def get_contract_symbol(year, month):
    return f"NIFTY{str(year)[2:]}{datetime(year, month, 1).strftime('%b').upper()}FUT"

def get_nifty_lot_size(symbol):
    try:
        month_str = symbol[7:10]  # e.g., "JUN"
        year_str = symbol[5:7]    # e.g., "25"
        month = datetime.strptime(month_str, "%b").month
        year = int("20" + year_str)
        if year > 2025 or (year == 2025 and month >= 3):
            return 75
    except:
        pass
    return 50  # fallback

def place_kite_order(kite, symbol, quantity, txn_type, retries=3):
    lot_size = get_nifty_lot_size(symbol)
    full_qty = quantity * lot_size

    if DRY_RUN:
        print(f"[DRY RUN] {txn_type} {quantity} lot(s) of {symbol} (Qty={full_qty})")
        return True

    for attempt in range(1, retries + 1):
        try:
            kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=txn_type,
                quantity=full_qty,
                product=kite.PRODUCT_NRML,
                order_type=kite.ORDER_TYPE_MARKET
            )
            print(f"✅ {txn_type} {quantity} lot(s) of {symbol} (Qty={full_qty}) [Attempt {attempt}]")
            return True
        except Exception as e:
            print(f"❌ Attempt {attempt} failed: {e}")
            if attempt < retries:
                time.sleep(5)
    return False

def get_current_position(kite):
    try:
        positions = kite.positions()['net']
    except:
        return 0, ""
    lots, symbol = 0, ""
    for p in positions:
        if "NIFTY" in p['tradingsymbol'] and p['product'] == "NRML" and p['quantity'] > 0:
            lot_size = get_nifty_lot_size(p['tradingsymbol'])
            lots += p['quantity'] // lot_size
            symbol = p['tradingsymbol']
    return lots, symbol

# === BOT LOOP ===
def run_nifty_monitor():
    kite = get_kite_client()
    while True:
        now = datetime.now()
        today = now.date()
        now_time = now.time()

        if dtime(9, 0) <= now_time < dtime(9, 5):
            wait = (datetime.combine(today, dtime(9, 5)) - now).seconds
            print(f"⛔ Between 9:00–9:05 AM. Sleeping {wait} seconds...")
            time.sleep(wait)
            continue

        if not is_trading_day(today):
            print("🔕 Market holiday. Sleeping 1 hour...")
            time.sleep(MONITOR_INTERVAL)
            continue

        expiry = get_last_thursday(2025, 6)
        rollover = get_rollover_date(expiry)
        days_to_expiry = (expiry - today).days
        new_contract = get_contract_symbol(2025, 7 if today >= rollover or days_to_expiry <= 8 else 6)

        current_lots, current_contract = get_current_position(kite)
        print(f"⏰ {now.strftime('%H:%M:%S')} | Holding: {current_lots} lot(s) in {current_contract or 'None'}")

        if today >= rollover and current_contract != new_contract and current_lots > 0:
            print("🔁 Rollover triggered...")
            for _ in range(current_lots):
                place_kite_order(kite, current_contract, 1, KiteConnect.TRANSACTION_TYPE_SELL)
                place_kite_order(kite, new_contract, 1, KiteConnect.TRANSACTION_TYPE_BUY)
                time.sleep(STAGGER_DELAY)
            print("✅ Rollover complete. Sleeping...")
            time.sleep(MONITOR_INTERVAL)
            continue

        if current_lots >= TOTAL_LOTS:
            print("✅ All lots held. Sleeping...")
            time.sleep(MONITOR_INTERVAL)
            continue

        print("⚠️ Starting staggered entries...")
        while current_lots < TOTAL_LOTS:
            now = datetime.now()
            if dtime(9, 0) <= now.time() < dtime(9, 5):
                wait = (datetime.combine(today, dtime(9, 5)) - now).seconds
                print(f"⛔ Waiting 9:00–9:05 window. Sleeping {wait} seconds...")
                time.sleep(wait)
                continue

            current_lots, _ = get_current_position(kite)
            if current_lots >= TOTAL_LOTS:
                break

            place_kite_order(kite, new_contract, 1, KiteConnect.TRANSACTION_TYPE_BUY)
            time.sleep(STAGGER_DELAY)

        print("✅ Entry done. Sleeping 1 hour...")
        time.sleep(MONITOR_INTERVAL)

# === MAIN ===
if __name__ == "__main__":
    print("🚀 Starting Nifty Futures Bot...")
    run_nifty_monitor()
