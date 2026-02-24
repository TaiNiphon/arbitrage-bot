import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

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
SYMBOL = os.getenv("SYMBOL", "XRP_THB").upper()

PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.0255))  
STOP_LOSS = float(os.getenv("STOP_LOSS", 0.020))      
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
        # ดึง Server Time และตัดช่องว่างออก
        ts_res = requests.get(f"{API_HOST}/api/v3/servertime")
        ts = ts_res.text.strip()
        
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
        logging.error(f"API V3 Auth Error: {e}")
        return {"error": 1}

def get_market_data():
    try:
        # 1. ดึงราคาปัจจุบัน (Ticker V3)
        res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL}").json()
        
        current_price = None
        # แก้ไขการดึงราคาให้ตรงกับ Dict Key ของ V3
        if isinstance(res, dict) and SYMBOL in res:
            current_price = float(res[SYMBOL].get('last', 0))
        
        if not current_price:
            logging.warning(f"Waiting for price data... (Symbol: {SYMBOL})")
            return None, None

        # 2. ดึงข้อมูลแท่งเทียน (V3)
        candle_url = f"{API_HOST}/api/v3/market/candles?sym={SYMBOL}&p={TIMEFRAME}&l=100"
        candle_res = requests.get(candle_url).json()

        if 'result' in candle_res and len(candle_res['result']) > 0:
            closes = [float(c['c']) for c in candle_res['result']]
        else: 
            return None, None

        # 3. คำนวณ EMA50
        ema = closes[0]
        multiplier = 2 / (EMA_PERIOD + 1)
        for price in closes:
            ema = (price - ema) * multiplier + ema

        return current_price, ema
    except Exception as e:
        logging.error(f"Get Market Data Error: {e}")
        return None, None

# --- 5. Main Loop ---
holding_token = False
last_buy_price = 0
last_report_time = 0 # ตั้งเป็น 0 เพื่อให้รายงานเด้งทันทีที่เริ่ม

logging.info(f"--- COMPLETE BOT STARTED: {SYMBOL} ---")
msg = (f"🤖 บอทเริ่มทำงาน (V3 Updated)\n"
       f"📌 เหรียญ: {SYMBOL}\n"
       f"📈 กลยุทธ์: EMA {EMA_PERIOD}\n"
       f"💰 เป้ากำไร: {round(PROFIT_TARGET*100, 2)}%\n"
       f"🚫 Stop Loss: {round(STOP_LOSS*100, 2)}%")
send_line_message(msg)

while True:
    try:
        current_price, ema_val = get_market_data()

        if current_price and ema_val:
            trend = "UP" if current_price > ema_val else "DOWN"
            logging.info(f"Price: {current_price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            current_ts = time.time()
            # รายงานรายชั่วโมง
            if current_ts - last_report_time >= 3600: 
                diff = current_price - ema_val
                status_msg = (f"📊 รายงานสถานะรายชั่วโมง\n"
                             f"💵 ราคาตอนนี้: {current_price} THB\n"
                             f"📉 เส้น EMA50: {ema_val:.2f} THB\n"
                             f"🔄 เทรนด์: {'ขาขึ้น 🟢' if trend == 'UP' else 'ขาลง 🔴'}\n"
                             f"📦 ถือเหรียญอยู่: {'ใช่' if holding_token else 'ไม่ใช่'}")
                send_line_message(status_msg)
                last_report_time = current_ts

            # ตรรกะการซื้อ
            if not holding_token and trend == "UP":
                wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                thb_balance = float(wallet.get('result', {}).get('THB', 0))

                if thb_balance >= 10:
                    # ปรับ amt ให้เป็นเลข 2 ตำแหน่งตามกฎ V3
                    order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {
                        "sym": SYMBOL, "amt": round(thb_balance, 2), "rat": 0, "typ": "market"
                    })
                    if order.get('error') == 0:
                        last_buy_price = current_price
                        holding_token = True
                        send_line_message(f"🚀 ซื้อสำเร็จที่ราคา {current_price} THB")

            # ตรรกะการขาย
            elif holding_token:
                profit_pct = (current_price - last_buy_price) / last_buy_price
                if profit_pct >= PROFIT_TARGET or profit_pct <= -STOP_LOSS:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin_name = SYMBOL.split('_')[0].upper()
                    coin_balance = float(wallet.get('result', {}).get(coin_name, 0))

                    if coin_balance > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL, "amt": coin_balance, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding_token = False
                            send_line_message(f"💰 ขายสำเร็จ! กำไร: {profit_pct*100:.2f}%")

    except Exception as e:
        logging.error(f"Loop Error: {e}")

    time.sleep(30)
