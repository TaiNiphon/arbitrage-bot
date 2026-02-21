import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่า Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 2. ระบบ Dummy Server สำหรับ Railway Health Check ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args): return

    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Starting Health Check Server on port {port}")
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# เริ่ม Server ใน Background
threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. ดึงค่า Configuration จาก Railway Variables ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET", "").encode()
SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155 
API_HOST = "https://api.bitkub.com"

# --- 4. ฟังก์ชันจัดการ API ---
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

# --- 5. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT STARTED (Pair: {SYMBOL}) ---")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.4f} | Holding: {holding_token}")

            if not holding_token:
                if current_price <= mid_price:
                    wallet = get_wallet()
                    thb_balance = float(wallet.get('THB', 0))
                    if thb_balance >= 10:
                        logging.info(f">>> Buying {SYMBOL} at {current_price}")
                        order = place_order("bid", thb_balance, current_price)
                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
            else:
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= sell_target:
                    wallet = get_wallet()
                    coin_ticker = SYMBOL.split('_')[1] # XRP
                    coin_balance = float(wallet.get(coin_ticker, 0))
                    if coin_balance > 0:
                        logging.info(f">>> Selling {SYMBOL} at {current_price} (Target: {sell_target:.4f})")
                        order = place_order("ask", coin_balance, current_price)
                        if order.get('error') == 0:
                            holding_token = False
        else:
            logging.warning("Waiting for market data...")

    except Exception as e:
        logging.error(f"Loop error: {e}")
    
    time.sleep(30)
