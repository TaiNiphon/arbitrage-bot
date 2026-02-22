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

# --- 2. Health Check Server for Railway ---
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

# --- 3. Configuration from Railway Variables ---
API_KEY = os.getenv("BITKUB_KEY", "").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").strip()
SYMBOL = os.getenv("SYMBOL", "THB_XRP").strip()
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB").strip()
PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.008))
API_HOST = "https://api.bitkub.com"

# --- 4. Fixed Signature Function (แก้ Error 404) ---
def generate_signature(payload):
    # สำคัญ: ต้องใช้ separators=(',', ':') เพื่อให้ JSON ไม่มีช่องว่าง
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

# --- 5. Main Loop ---
logging.info(f"--- BOT STARTED | Pair: {SYMBOL} ---")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.4f}")
            
            # ตรวจสอบ Wallet เพื่อยืนยันว่าเชื่อมต่อสำเร็จ
            wallet = get_wallet()
            if wallet:
                logging.info(f"Wallet Connected! Current THB: {wallet.get('THB', 0)}")
    except Exception as e:
        logging.error(f"Loop Error: {e}")
    time.sleep(30)
