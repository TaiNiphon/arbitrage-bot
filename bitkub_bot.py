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
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": str(text)}]
    }
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"LINE Error: {e}")

# --- 4. CONFIGURATION ---
API_KEY = os.getenv("BITKUB_KEY", "ใส่_KEY_ที่นี่").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "ใส่_SECRET_ที่นี่").strip().encode()
SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155  # กำไร 1.55%
API_HOST = "https://api.bitkub.com"

# --- 5. Functions จัดการ API Bitkub (ปรับเป็น V3) ---
def get_signature(payload):
    json_payload = json.dumps(payload, separators=(',', ':'))
    return hmac.new(API_SECRET, msg=json_payload.encode(), digestmod=hashlib.sha256).hexdigest()

def get_header():
    return {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-BTK-APIKEY': API_KEY
    }

def get_wallet():
    # แก้ไขเป็น API V3 เพื่อแก้ Error 404
    url = f"{API_HOST}/api/v3/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = get_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        data = res.json()
        if data.get('error') == 0:
            return data.get('result', {})
        else:
            logging.error(f"Bitkub Wallet Error: {data}")
            return {}
    except Exception as e:
        logging.error(f"Wallet Request Failed: {e}")
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
    url = f"{API_HOST}/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            # คำนวณ Mid Price จาก High/Low 24 ชม.
            return max(data['h']), min(data['l']), data['c'][-1]
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
    return None, None, None

# --- 6. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT STARTED ({SYMBOL}) ---")
send_line_message(f"🚀 บอทเริ่มทำงาน\nคู่เทรด: {SYMBOL}\nเป้ากำไร: {PROFIT_TARGET*100}%")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()

        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.4f} | Holding: {holding_token}")

            if not holding_token:
                # ซื้อเมื่อราคาปัจจุบันต่ำกว่าหรือเท่ากับราคากลาง
                if current_price <= mid_price:
                    wallet = get_wallet()
                    thb_balance = float(wallet.get('THB', 0))
                    
                    logging.info(f"Check Balance: {thb_balance} THB")

                    if thb_balance >= 10:
                        logging.info(f">>> Sending BUY order at {current_price}")
                        order = place_order("bid", thb_balance, current_price)

                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
                            send_line_message(f"✅ ซื้อสำเร็จ!\nราคา: {current_price} THB\nใช้เงิน: {thb_balance} THB")
            else:
                # ขายเมื่อได้กำไรตามเป้า
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= sell_target:
                    wallet = get_wallet()
                    coin_ticker = SYMBOL.split('_')[1]
                    coin_balance = float(wallet.get(coin_ticker, 0))

                    if coin_balance > 0:
                        logging.info(f">>> Sending SELL order at {current_price}")
                        order = place_order("ask", coin_balance, current_price)

                        if order.get('error') == 0:
                            holding_token = False
                            profit_pct = ((current_price - last_buy_price) / last_buy_price) * 100
                            send_line_message(f"💰 ขายสำเร็จ!\nราคาขาย: {current_price} THB\nกำไร: {profit_pct:.2f}%")

    except Exception as e:
        logging.error(f"Main Loop Error: {e}")

    time.sleep(30)
