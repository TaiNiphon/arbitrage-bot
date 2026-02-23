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
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. ดึงค่าจาก Railway Variables ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
# ดึงค่า SYMBOL และแปลงเป็นตัวเล็กสำหรับ V3
SYMBOL = os.getenv("SYMBOL", "xrp_thb").lower() 
PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.0155))
API_HOST = "https://api.bitkub.com"

# --- 4. ระบบแจ้งเตือน LINE ---
def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code != 200: logging.error(f"LINE Error: {res.text}")
    except Exception as e: logging.error(f"LINE Connection Error: {e}")

# --- 5. Functions จัดการ Bitkub V3 (ชุดที่รันผ่านชัวร์) ---
def bitkub_v3_auth(method, path, body={}):
    try:
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text
        json_body = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY,
            'X-BTK-TIMESTAMP': ts,
            'X-BTK-SIGN': sig
        }
        res = requests.post(f"{API_HOST}{path}", headers=headers, data=json_body, timeout=15)
        return res.json()
    except Exception as e:
        logging.error(f"API Error: {e}")
        return {"error": 1}

def get_wallet():
    res = bitkub_v3_auth("POST", "/api/v3/market/wallet")
    return res.get('result', {})

def get_market_data():
    try:
        ticker = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL}").json()
        return float(ticker[0]['high_24_hr']), float(ticker[0]['low_24_hr']), float(ticker[0]['last'])
    except: return None, None, None

# --- 6. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BOT STARTED (Pair: {SYMBOL}) ---")
send_line_message(f"🚀 บอทเริ่มทำงานบน Railway\nเหรียญ: {SYMBOL}\nเป้ากำไร: {PROFIT_TARGET*100}%")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()

        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.2f} | Holding: {holding_token}")

            # สเต็ป 1: เงื่อนไขการซื้อ (ถ้าไม่มีเหรียญและราคาต่ำกว่าค่าเฉลี่ย)
            if not holding_token:
                if current_price <= mid_price:
                    wallet = get_wallet()
                    thb_balance = float(wallet.get('THB', 0))
                    
                    if thb_balance >= 10:
                        logging.info(">>> Executing Market Buy...")
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {
                            "sym": SYMBOL, "amt": int(thb_balance), "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
                            send_line_message(f"✅ ซื้อสำเร็จ! (BUY)\nราคา: {current_price} THB\nใช้เงิน: {thb_balance:.2f} THB")

            # สเต็ป 2: เงื่อนไขการขาย (ถ้ามีเหรียญและถึงเป้ากำไร)
            else:
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= sell_target:
                    wallet = get_wallet()
                    coin_name = SYMBOL.split('_')[0].upper()
                    coin_balance = float(wallet.get(coin_name, 0))
                    
                    if coin_balance > 0:
                        logging.info(">>> Executing Market Sell...")
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL, "amt": coin_balance, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding_token = False
                            profit_pct = ((current_price - last_buy_price) / last_buy_price) * 100
                            send_line_message(f"💰 ขายสำเร็จ! (SELL)\nกำไร: {profit_pct:.2f}%\nราคาขาย: {current_price} THB")

    except Exception as e:
        logging.error(f"Loop error: {e}")
    
    time.sleep(30)
