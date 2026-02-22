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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 2. ระบบ Dummy Server สำหรับ Railway ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args): return

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logging.info(f"Dummy server started on port {port}")
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. ระบบแจ้งเตือน LINE ---
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "").strip()
LINE_USER_ID = os.getenv("LINE_USER_ID", "").strip()

def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except: pass

# --- 4. CONFIGURATION ---
API_KEY = os.getenv("BITKUB_KEY", "").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").strip().encode()
SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155 
API_HOST = "https://api.bitkub.com"

# --- 5. Bitkub API Functions ---
def get_signature(payload):
    json_payload = json.dumps(payload, separators=(',', ':'))
    return hmac.new(API_SECRET, msg=json_payload.encode(), digestmod=hashlib.sha256).hexdigest()

def get_header():
    return {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-BTK-APIKEY': API_KEY}

def get_wallet():
    # แก้ไขจุดที่พิมพ์ผิด def def และใช้ v3 ตามที่แนะนำ
    url = f"{API_HOST}/api/v3/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = get_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        data = res.json()
        return data.get('result', {}) if data.get('error') == 0 else {}
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
    payload["sig"] = get_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        return res.json()
    except Exception as e:
        return {"error": 1, "message": str(e)}

def get_market_data():
    now = int(time.time())
    url = f"https://api.bitkub.com/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            return max(data['h']), min(data['l']), data['c'][-1]
    except: pass
    return None, None, None

# --- 6. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT STARTED ({SYMBOL}) ---")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()

        if current_price:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.2f} | Holding: {holding_token}")

            if not holding_token:
                if current_price <= mid_price:
                    wallet = get_wallet()
                    thb_balance = float(wallet.get('THB', 0))
                    if thb_balance >= 10:
                        order = place_order("bid", thb_balance, current_price)
                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
                            send_line_message(f"✅ BUY {SYMBOL} @ {current_price}")
            else:
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= sell_target:
                    wallet = get_wallet()
                    coin_ticker = SYMBOL.split('_')[1]
                    coin_balance = float(wallet.get(coin_ticker, 0))
                    if coin_balance > 0:
                        order = place_order("ask", coin_balance, current_price)
                        if order.get('error') == 0:
                            holding_token = False
                            send_line_message(f"💰 SELL {SYMBOL} @ {current_price}")

    except Exception as e:
        logging.error(f"Loop Error: {e}")

    time.sleep(30)
