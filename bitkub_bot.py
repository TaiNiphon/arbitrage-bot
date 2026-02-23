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
SYMBOL = os.getenv("SYMBOL", "xrp_thb").lower() 

PROFIT_TARGET = 0.015  # กำไร 1.5%
STOP_LOSS = 0.020      # ตัดขาดทุน 2.0%
EMA_PERIOD = 50        # ใช้ 50 แท่งย้อนหลัง
TIMEFRAME = "15"       # ใช้ Timeframe 15 นาที ตามกราฟล่าสุด

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
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text
        json_body = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}
        res = requests.post(f"{API_HOST}{path}", headers=headers, data=json_body, timeout=15)
        return res.json()
    except Exception as e:
        logging.error(f"API Connection Error: {e}")
        return {"error": 1}

def get_market_data():
    try:
        # ดึงราคาปัจจุบัน
        ticker = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL}").json()
        current_price = float(ticker[0]['last'])

        # ดึงข้อมูลแท่งเทียน
        candles = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYMBOL}&p={TIMEFRAME}&l=100").json()
        closes = [float(c['c']) for c in candles['result']]

        # --- คำนวณ EMA ของจริง (Exponential) ---
        ema = closes[0]
        multiplier = 2 / (EMA_PERIOD + 1)
        for price in closes:
            ema = (price - ema) * multiplier + ema
            
        return current_price, ema
    except Exception as e:
        logging.error(f"Data Error: {e}")
        return None, None

# --- 5. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- COMPLETE BOT STARTED: {SYMBOL} ---")
msg = f"🚀 บอทเริ่มทำงานบน Railway (EMA สูตรใหม่)\nเหรียญ: {SYMBOL}\nTimeframe: {TIMEFRAME}m\nเป้ากำไร: {PROFIT_TARGET*100}%"
send_line_message(msg)

while True:
    try:
        current_price, ema_val = get_market_data()

        if current_price and ema_val:
            trend = "UP" if current_price > ema_val else "DOWN"
            logging.info(f"Price: {current_price} | EMA{EMA_PERIOD}: {ema_val:.2f} | Trend: {trend}")

            if not holding_token:
                if trend == "UP":
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    thb_balance = float(wallet.get('result', {}).get('THB', 0))

                    if thb_balance >= 10:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {
                            "sym": SYMBOL, "amt": int(thb_balance), "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
                            send_line_message(f"🚀 ซื้อสำเร็จ!\nราคา: {current_price} THB\nEMA: {ema_val:.2f}")

            else:
                profit_pct = (current_price - last_buy_price) / last_buy_price
                sell_trigger = profit_pct >= PROFIT_TARGET or profit_pct <= -STOP_LOSS

                if sell_trigger:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin_name = SYMBOL.split('_')[0].upper()
                    coin_balance = float(wallet.get('result', {}).get(coin_name, 0))

                    if coin_balance > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL, "amt": coin_balance, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding_token = False
                            status = "✅ กำไร" if profit_pct > 0 else "❌ ตัดขาดทุน"
                            send_line_message(f"💰 ขายแล้ว ({status})\nผลลัพธ์: {profit_pct*100:.2f}%\nราคาขาย: {current_price} THB")

    except Exception as e:
        logging.error(f"Loop Error: {e}")

    time.sleep(30)
