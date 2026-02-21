import os
import requests
import time
import hmac
import hashlib
import json
import logging

# ตั้งค่า Log ให้ละเอียดเพื่อดูบน Railway
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

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
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        return res.json().get('result', {})
    except Exception as e:
        logging.error(f"Wallet Error: {e}")
        return {}

def place_order(side, amount, rate):
    url = f"{API_HOST}/api/market/place-{side}"
    # XRP: ราคา 4 ตำแหน่ง, จำนวน 8 ตำแหน่ง
    payload = {
        "sym": SYMBOL, 
        "amt": round(float(amount), 8), 
        "rat": round(float(rate), 4), 
        "typ": "limit", 
        "ts": int(time.time())
    }
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        return res.json()
    except Exception as e:
        logging.error(f"Order Error: {e}")
        return {"error": 1}

def get_market_data():
    now = int(time.time())
    url = f"https://api.bitkub.com/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            return max(data['h']), min(data['l']), data['c'][-1]
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
    return None, None, None

# --- จุดเริ่มต้นการทำงาน ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT STARTED (XRP) ---")

# ใช้ While True แบบรัดกุมเพื่อกัน Container หยุดทำงาน
while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"XRP: {current_price} | Mid: {mid_price:.4f} | Holding: {holding_token}")

            if not holding_token:
                if current_price <= mid_price:
                    wallet = get_wallet()
                    thb_balance = float(wallet.get('THB', 0))
                    if thb_balance >= 10:
                        logging.info(f">>> Buying XRP at {current_price}")
                        order = place_order("bid", thb_balance, current_price)
                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
            else:
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= sell_target:
                    wallet = get_wallet()
                    coin_balance = float(wallet.get('XRP', 0))
                    if coin_balance > 0:
                        logging.info(f">>> Selling XRP at {current_price} (Target: {sell_target:.4f})")
                        order = place_order("ask", coin_balance, current_price)
                        if order.get('error') == 0:
                            holding_token = False
        else:
            logging.warning("API Unreachable, waiting 30s...")

    except Exception as e:
        logging.error(f"Loop Crash avoided: {e}")
    
    # พัก 30 วินาที เพื่อรักษาเสถียรภาพและไม่ให้โดนแบน IP
    time.sleep(30)
