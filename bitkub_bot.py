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

# --- 2. Dummy Server for Railway ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Trading Bot is Active")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. Configuration ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# ส่วนนี้ใช้สำหรับ Trading V3
SYMBOL = "XRP_THB" 
# ส่วนนี้ใช้สำหรับ Public API Ticker (ต้องเอา THB ขึ้นก่อน)
TICKER_SYMBOL = "THB_XRP" 

PROFIT_TARGET = 0.0255
STOP_LOSS = 0.020
EMA_PERIOD = 50        
TIMEFRAME = "15"       
API_HOST = "https://api.bitkub.com"

# --- 4. Helper Functions ---
def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try: requests.post(url, headers=headers, json=payload, timeout=10)
    except: pass

def bitkub_v3_auth(method, path, body={}):
    try:
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text.strip()
        json_body = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig
        }
        res = requests.post(f"{API_HOST}{path}", headers=headers, data=json_body, timeout=15)
        return res.json()
    except Exception as e:
        logging.error(f"Auth Error: {e}")
        return {"error": 1}

def get_market_data():
    try:
        # 1. ดึงราคาปัจจุบันจาก Ticker (ใช้ THB_XRP เท่านั้น)
        ticker_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={TICKER_SYMBOL}").json()
        current_price = float(ticker_res.get(TICKER_SYMBOL, {}).get('last', 0))

        if current_price <= 0:
            # ลองดึงแบบไม่ระบุ sym เพื่อความชัวร์
            all_ticker = requests.get(f"{API_HOST}/api/v3/market/ticker").json()
            current_price = float(all_ticker.get(TICKER_SYMBOL, {}).get('last', 0))

        # 2. ดึงข้อมูลแท่งเทียน (ใช้ THB_XRP)
        candle_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={TICKER_SYMBOL}&p={TIMEFRAME}&l=100").json()
        
        if 'result' in candle_res and len(candle_res['result']) > 0:
            closes = [float(c['c']) for c in candle_res['result']]
            # คำนวณ EMA50
            ema = closes[0]
            multiplier = 2 / (EMA_PERIOD + 1)
            for price in closes:
                ema = (price - ema) * multiplier + ema
            return current_price, ema
        
        return None, None
    except Exception as e:
        logging.error(f"Data Error: {e}")
        return None, None

# --- 5. Main Loop ---
holding_token = False
last_buy_price = 0
last_report_time = 0 

logging.info(f"--- BOT RUNNING: {SYMBOL} ---")
send_line_message(f"🚀 บอทเริ่มระบบสมบูรณ์\nเฝ้าเหรียญ: {SYMBOL}\nใช้ราคาจาก: {TICKER_SYMBOL}")

while True:
    try:
        current_price, ema_val = get_market_data()

        if current_price and ema_val:
            trend = "UP" if current_price > ema_val else "DOWN"
            logging.info(f"Price: {current_price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            # รายงานรายชั่วโมง
            if time.time() - last_report_time >= 3600:
                send_line_message(f"📊 สถานะตอนนี้: {current_price} THB\nEMA50: {ema_val:.2f}\nเทรนด์: {trend}")
                last_report_time = time.time()

            # ซื้อเมื่อราคาตัดขึ้น
            if not holding_token and trend == "UP":
                wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                balance = float(wallet.get('result', {}).get('THB', 0))
                if balance >= 10:
                    order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {
                        "sym": SYMBOL, "amt": round(balance, 2), "rat": 0, "typ": "market"
                    })
                    if order.get('error') == 0:
                        holding_token = True
                        last_buy_price = current_price
                        send_line_message(f"✅ ซื้อสำเร็จที่ {current_price}")

            # ขายเมื่อถึงเป้าหรือ Stop Loss
            elif holding_token:
                profit = (current_price - last_buy_price) / last_buy_price
                if profit >= PROFIT_TARGET or profit <= -STOP_LOSS:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin_amt = float(wallet.get('result', {}).get('XRP', 0))
                    if coin_amt > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL, "amt": coin_amt, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding_token = False
                            send_line_message(f"💰 ขายสำเร็จ! กำไร: {profit*100:.2f}%")
    except: pass
    time.sleep(30)
