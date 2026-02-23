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

# --- 2. Dummy Server (คงเดิม) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

run_dummy_server()

# --- 3. CONFIGURATION ---
API_KEY = os.getenv("BITKUB_KEY", "").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").strip() # เก็บเป็น String ก่อน
SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155
API_HOST = "https://api.bitkub.com"

# --- 4. แก้ไขการสร้าง Signature สำหรับ API V3 ---
def generate_signature(api_secret, timestamp, method, path, query='', body=''):
    """สร้าง Signature ตามมาตรฐาน Bitkub API V3"""
    message = f"{timestamp}{method}{path}{query}{body}"
    return hmac.new(api_secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

def get_header(timestamp, sig):
    return {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-BTK-APIKEY': API_KEY,
        'X-BTK-TIMESTAMP': str(timestamp),
        'X-BTK-SIGN': sig
    }

def get_wallet():
    """ดึงยอดเงินจาก API V3 (แก้ไขใหม่)"""
    path = "/api/v3/market/wallet"
    timestamp = int(time.time() * 1000) # V3 มักใช้ millisecond
    
    # สำหรับ GET/POST ที่ไม่มี body ใน v3 wallet
    sig = generate_signature(API_SECRET, timestamp, "POST", path)
    
    try:
        res = requests.post(f"{API_HOST}{path}", 
                             headers=get_header(timestamp, sig), 
                             json={}, # ส่ง body เปล่า
                             timeout=15)
        data = res.json()
        if data.get('error') == 0:
            result = data.get('result', {})
            # API V3 Wallet คืนค่ามาเป็น Dict { "THB": 100, "BTC": 0.5 } อยู่แล้ว
            return result
        else:
            logging.error(f"Bitkub Wallet API Error: {data}")
            return {}
    except Exception as e:
        logging.error(f"Wallet Connection Failed: {e}")
        return {}

# --- 5. แก้ไขการวาง Order (API V3) ---
def place_order_v3(side, amount, rate):
    """ส่งคำสั่งซื้อขายแบบ V3"""
    path = f"/api/v3/market/place-{side}"
    timestamp = int(time.time() * 1000)
    
    body_dict = {
        "symbol": SYMBOL,
        "amount": round(float(amount), 8),
        "rate": round(float(rate), 4),
        "type": "limit"
    }
    body_json = json.dumps(body_dict, separators=(',', ':'))
    sig = generate_signature(API_SECRET, timestamp, "POST", path, body=body_json)
    
    try:
        res = requests.post(f"{API_HOST}{path}", 
                             headers=get_header(timestamp, sig), 
                             json=body_dict, 
                             timeout=15)
        return res.json()
    except Exception as e:
        return {"error": 1, "message": str(e)}

# --- ส่วนดึงข้อมูลตลาด (ใช้ TradingView API เหมือนเดิมได้เพราะไม่ต้องใช้ Key) ---
def get_market_data():
    now = int(time.time())
    url = f"{API_HOST}/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            return max(data['h']), min(data['l']), data['c'][-1]
    except: pass
    return None, None, None

# --- 6. Main Loop (ปรับปรุง Logic เล็กน้อย) ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT V3 STARTED ({SYMBOL}) ---")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()

        if current_price:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.2f} | Holding: {holding_token}")

            wallet = get_wallet()
            
            if not holding_token:
                thb_balance = float(wallet.get('THB', 0))
                logging.info(f"💰 Balance: {thb_balance} THB")

                if current_price <= mid_price and thb_balance >= 10:
                    logging.info(">>> BUYING...")
                    order = place_order_v3("bid", thb_balance / current_price, current_price)
                    if order.get('error') == 0:
                        last_buy_price = current_price
                        holding_token = True
            else:
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                coin_ticker = SYMBOL.split('_')[1]
                coin_balance = float(wallet.get(coin_ticker, 0))
                
                if current_price >= sell_target and coin_balance > 0:
                    logging.info(">>> SELLING...")
                    order = place_order_v3("ask", coin_balance, current_price)
                    if order.get('error') == 0:
                        holding_token = False

    except Exception as e:
        logging.error(f"Loop Error: {e}")
    time.sleep(30)
