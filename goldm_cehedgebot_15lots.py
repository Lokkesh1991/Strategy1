import os
import time
import re
from datetime import datetime, timedelta
from kiteconnect import KiteConnect

# === ENV-SMART TOKEN MANAGER ===
if os.getenv("RAILWAY_ENVIRONMENT"):
    from Railway_token_manager import get_access_token
else:
    from token_manager import get_access_token

# === CONFIG ===
LOTS_TO_SELL = 15
BATCH_SIZE = 5
REBALANCE_INTERVAL = 4 * 60 * 60
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

def format_expiry_for_symbol(expiry_date):
    return expiry_date.strftime("%y%b").upper()

def get_existing_ce_positions(kite):
    positions = kite.positions()
    ce_pos = {}
    for pos in positions['net']:
        if (
            "GOLDM" in pos['tradingsymbol'] and
            "CE" in pos['tradingsymbol'] and
            pos['product'] == "NRML" and
            pos['quantity'] < 0
        ):
            match = re.search(r'(\d{5})CE$', pos['tradingsymbol'])
            if match:
                strike = int(match.group(1))
                ce_pos[strike] = ce_pos.get(strike, 0) + abs(pos['quantity'])
    return ce_pos

def get_total_ce_lots(kite):
    return sum(get_existing_ce_positions(kite).values())

def get_goldm_futures_ltp(kite):
    instruments = kite.instruments("MCX")
    symbol = next((i['tradingsymbol'] for i in instruments if "GOLDM" in i['tradingsymbol'] and i['segment'] == "MCX-FUT"), None)
    if not symbol:
        raise Exception("GoldM Futures contract not found.")
    ltp = kite.ltp(f"MCX:{symbol}")
    return list(ltp.values())[0]['last_price']

def get_ce_strike_distribution(fut_price):
    rounded_price = int((fut_price + 999) / 1000) * 1000
    strike1 = rounded_price + 1000
    strike2 = rounded_price + 2000
    strike3 = rounded_price + 3000
    return {
        strike1: 5,
        strike2: 5,
        strike3: 5
    }

def place_ce_sell_order(kite, strike, expiry_date, lots):
    expiry_code = format_expiry_for_symbol(expiry_date)
    symbol = f"GOLDM{expiry_code}{strike}CE"

    for attempt in range(MAX_ATTEMPTS):
        try:
            current_total_lots = get_total_ce_lots(kite)
            if current_total_lots >= LOTS_TO_SELL:
                print(f"🛑 Aborting order: already holding {current_total_lots} CE lots (target: {LOTS_TO_SELL})")
                return

            ltp_data = kite.ltp(f"MCX:{symbol}")
            if not ltp_data or not list(ltp_data.values()):
                raise Exception(f"LTP data not available for {symbol}")
            ltp = list(ltp_data.values())[0]['last_price']
            price = round(ltp - PRICE_STEP, 1)
            print(f"{datetime.now()} - SELL {lots} lot(s) of {symbol} @ {price} (Attempt {attempt + 1})")

            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange="MCX",
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=lots,
                price=price,
                product=kite.PRODUCT_NRML,
                order_type=kite.ORDER_TYPE_LIMIT
            )

            filled = False
            for _ in range(MAX_WAIT_CYCLES):
                order = kite.order_history(order_id)[-1]
                status = order["status"]
                if status == "COMPLETE":
                    print(f"✅ Order filled: {symbol} (Order ID: {order_id})")
                    filled = True
                    break
                elif status in ["REJECTED", "CANCELLED"]:
                    raise Exception(f"Order {order_id} for {symbol} failed: {status}")
                else:
                    print(f"⌛ Waiting for order to fill... Status: {status}")
                    time.sleep(CHECK_INTERVAL)

            if filled:
                return

            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
            print(f"⚠️ Cancelled stale order: {order_id}")

        except Exception as e:
            print(f"❌ Attempt {attempt + 1} failed for {symbol}: {e}")
            time.sleep(5)

    print("❌ All attempts failed for", symbol)
    print("⏸️ Skipping this strike and moving to next after maintaining only current lots")

def run_ce_hedge_bot():
    print("🚀 Starting CE Hedge Bot...")
    kite = get_kite_client()
    today = datetime.today().date()
    expiry = get_last_thursday(today.year, today.month)
    if (expiry - today).days <= 4:
        expiry = get_next_month_expiry(expiry)
    print(f"📅 Using expiry: {expiry}")

    while True:
        current_lots = get_total_ce_lots(kite)
        if current_lots < LOTS_TO_SELL:
            print(f"📉 Holding {current_lots} CE lots. Starting new round of CE hedging...")
            fut_price = get_goldm_futures_ltp(kite)
            dist = get_ce_strike_distribution(fut_price)
            print(f"🎯 Target strike distribution: {dist}")
            for strike, qty in dist.items():
                for _ in range(qty):
                    place_ce_sell_order(kite, strike, expiry, 1)
                    time.sleep(3)
        else:
            print(f"✅ Already holding {current_lots} CE lots. Performing rebalance...")
            fut_price = get_goldm_futures_ltp(kite)
            dist = get_ce_strike_distribution(fut_price)
            print(f"🔁 Rebalancing target: {dist}")
            from copy import deepcopy
            current_distribution = deepcopy(get_existing_ce_positions(kite))
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

            ei, oi = 0, 0
            exit_strikes = list(to_exit.items())
            enter_strikes = list(to_enter.items())

            while oi < len(exit_strikes) and ei < len(enter_strikes):
                exit_strike, exit_qty = exit_strikes[oi]
                enter_strike, enter_qty = enter_strikes[ei]
                try:
                    exit_symbol = f"GOLDM{format_expiry_for_symbol(expiry)}{exit_strike}CE"
                    ltp = kite.ltp(f"MCX:{exit_symbol}")
                    buy_price = round(list(ltp.values())[0]['last_price'] + 0.5, 1)
                    print(f"🔽 Exiting 1 lot from {exit_symbol} @ {buy_price}")
                    order_id = kite.place_order(
                        variety=kite.VARIETY_REGULAR,
                        exchange="MCX",
                        tradingsymbol=exit_symbol,
                        transaction_type=kite.TRANSACTION_TYPE_BUY,
                        quantity=1,
                        price=buy_price,
                        product=kite.PRODUCT_NRML,
                        order_type=kite.ORDER_TYPE_LIMIT
                    )
                    for _ in range(MAX_WAIT_CYCLES):
                        order = kite.order_history(order_id)[-1]
                        status = order["status"]
                        if status == "COMPLETE":
                            print(f"✅ Buyback filled for {exit_symbol}")
                            break
                        elif status in ["REJECTED", "CANCELLED"]:
                            raise Exception(f"Buyback {order_id} failed: {status}")
                        else:
                            print(f"⌛ Waiting for buyback fill... Status: {status}")
                            time.sleep(CHECK_INTERVAL)
                    else:
                        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
                        print(f"⚠️ Cancelled stale buy order: {order_id}")
                        continue

                    time.sleep(5)
                    print(f"🔼 Selling 1 lot to {enter_strike}")
                    place_ce_sell_order(kite, enter_strike, expiry, 1)

                    exit_strikes[oi] = (exit_strike, exit_qty - 1)
                    enter_strikes[ei] = (enter_strike, enter_qty - 1)

                    if exit_strikes[oi][1] == 0:
                        oi += 1
                    if enter_strikes[ei][1] == 0:
                        ei += 1
                except Exception as e:
                    print(f"❌ Error during rebalance swap: {e}")

        next_time = datetime.now() + timedelta(seconds=REBALANCE_INTERVAL)
        print(f"⏳ Next check at {next_time.strftime('%H:%M:%S')}\n")
        time.sleep(REBALANCE_INTERVAL)

if __name__ == "__main__":
    run_ce_hedge_bot() 