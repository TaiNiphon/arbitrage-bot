import os
import requests
import time
import hmac
import hashlib
import json

# --- ดึงค่าจากระบบ Railway Variables ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET", "").encode()
SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155 

API_HOST = "https://api.bitkub.com"

def generate_signature(payload):
    json_payload = json.dumps(payload, separators=(',', ':'))
    return hmac.new(API_SECRET, msg=json_payload.encode(), digestmod=hashlib.sha256).hexdigest()

def get_header():
    return {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-BTK-APIKEY': API_KEY}

def get_wallet():
    url = f"{API_HOST}/api/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload).json()
        return res.get('result', {})
    except: return {}

def place_order(side, amount, rate):
    url = f"{API_HOST}/api/market/place-{side}"
    payload = {"sym": SYMBOL, "amt": amount, "rat": rate, "typ": "limit", "ts": int(time.time())}
    payload["sig"] = generate_signature(payload)
    return requests.post(url, headers=get_header(), json=payload).json()

def get_market_data():
    url = f"https://api.bitkub.com/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={int(time.time())-86400}&to={int(time.time())}"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get('s') == 'ok':
            return max(res['h']), min(res['l']), res['c'][-1]
    except: pass
    return None, None, None

holding_token = False
last_buy_price = 0

print(f"--- BITKUB BOT STARTED (Pair: {SYMBOL}) ---")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        if high_24h is None:
            time.sleep(10)
            continue
        mid_price = (high_24h + low_24h) / 2
        
        if not holding_token:
            if current_price <= mid_price:
                wallet = get_wallet()
                thb_balance = wallet.get('THB', 0)
                if thb_balance >= 10:
                    print(f"Buying {SYMBOL} at {current_price} with {thb_balance} THB")
                    order = place_order("bid", thb_balance, current_price)
                    if order.get('error') == 0:
                        last_buy_price, holding_token = current_price, True
        else:
            sell_target = last_buy_price * (1 + PROFIT_TARGET)
            if current_price >= sell_target:
                wallet = get_wallet()
                coin_balance = wallet.get(SYMBOL.split('_')[1], 0)
                if coin_balance > 0:
                    print(f"Selling {SYMBOL} at {current_price} for profit")
                    order = place_order("ask", coin_balance, current_price)
                    if order.get('error') == 0: holding_token = False
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(10)
