import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 2. Dummy Server for Railway Health Check ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is Active")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. LINE Notification Setup ---
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "").strip()
LINE_USER_ID = os.getenv("LINE_USER_ID", "Ua88ba52b810900b7ba8df4c08b376496").strip()

def send_line_message(text):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try: requests.post(url, headers=headers, json=payload, timeout=10)
    except: pass

# --- 4. CONFIGURATION (ดึงจาก Railway Variables) ---
API_KEY = os.getenv("BITKUB_KEY", "").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").strip()
SYMBOL = os.getenv("SYMBOL", "THB_XRP").strip()
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB").strip()
PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.008))
API_HOST = "https://api.bitkub.com"

# --- 5. FIXED BITKUB API FUNCTIONS (จุดที่แก้ไข Error 404) ---
def generate_signature(payload):
    # แก้ไขจุดนี้: ใช้ separators เพื่อลบช่องว่างใน JSON และ encode เป็น UTF-8
    json_payload = json.dumps(payload, separators=(',', ':'))
    return hmac.new(
        API_SECRET.encode('utf-8'), 
        msg=json_payload.encode('utf-8'), 
        digestmod=hashlib.sha256
    ).hexdigest()

def get_header():
    return {
        'Accept': 'application/json', 
        'Content-Type': 'application/json', 
        'X-BTK-APIKEY': API_KEY
    }

def get_wallet():
    url = f"{API_HOST}/api/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        data = res.json()
        if data.get('error') == 0: return data.get('result', {})
        logging.error(f"Wallet API Error: {data}")
        return None
    except Exception as e:
        logging.error(f"Connection Error: {e}")
        return None

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
    except: return {"error": 1}

def get_market_data():
    now = int(time.time())
    url = f"{API_HOST}/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            return float(max(data['h'])), float(min(data['l'])), float(data['c'][-1])
    except: pass
    return None, None, None

# --- 6. Main Trading Loop ---
holding_token = False
last_buy_price = 0
logging.info(f"--- BOT STARTED | Pair: {SYMBOL} | Target: {PROFIT_TARGET*100}% ---")
send_line_message(f"🚀 บอทเริ่มทำงานแล้ว (Pair: {SYMBOL})")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.4f} | Holding: {holding_token}")

            if not holding_token:
                if current_price <= mid_price:
                    wallet = get_wallet()
                    if wallet:
                        thb_balance = float(wallet.get('THB', 0))
                        logging.info(f"Wallet: {thb_balance} THB")
                        if thb_balance >= 10:
                            order = place_order("bid", thb_balance, current_price)
                            if order.get('error') == 0:
                                last_buy_price, holding_token = current_price, True
                                send_line_message(f"✅ ซื้อสำเร็จ! {SYMBOL} @ {current_price}")
            else:
                target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= target:
                    wallet = get_wallet()
                    if wallet:
                        coin = SYMBOL.split('_')[1]
                        balance = float(wallet.get(coin, 0))
                        if balance > 0:
                            order = place_order("ask", balance, current_price)
                            if order.get('error') == 0:
                                holding_token = False
                                send_line_message(f"💰 ขายสำเร็จ! {SYMBOL} @ {current_price}")
    except Exception as e:
        logging.error(f"Loop Error: {e}")
    time.sleep(30)
