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

# --- 3. Configuration (ปรับใหม่ให้ดึงค่าจาก Railway) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
SYMBOL = os.getenv("SYMBOL", "xrp_thb").lower() 

# ดึงค่ากำไรจาก Railway (ถ้าไม่ตั้งจะใช้ 0.015 เป็นค่าเริ่มต้น)
PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.015))  
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
        ticker_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL.upper()}").json()
        if isinstance(ticker_res, list) and len(ticker_res) > 0:
            current_price = float(ticker_res[0]['last'])
        else: return None, None

        candle_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYMBOL.upper()}&p={TIMEFRAME}&l=100").json()
        if 'result' in candle_res and len(candle_res['result']) > 0:
            closes = [float(c['c']) for c in candle_res['result']]
        else: return None, None

        ema = closes[0]
        multiplier = 2 / (EMA_PERIOD + 1)
        for price in closes:
            ema = (price - ema) * multiplier + ema

        return current_price, ema
    except Exception as e:
        logging.error(f"Get Data Error: {e}")
        return None, None

# --- 5. Main Loop ---
holding_token = False
last_buy_price = 0
last_report_time = 0 

logging.info(f"--- COMPLETE BOT STARTED: {SYMBOL} ---")
msg = (f"🤖 บอทเริ่มทำงาน (โหมดละเอียด)\n"
       f"📌 เหรียญ: {SYMBOL.upper()}\n"
       f"📈 กลยุทธ์: EMA {EMA_PERIOD} (Trend Follow)\n"
       f"⏱ Timeframe: {TIMEFRAME} นาที\n"
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
            if current_ts - last_report_time >= 3600: 
                diff = current_price - ema_val
                status_msg = (f"📊 รายงานสถานะรายชั่วโมง\n"
                             f"💵 ราคาตอนนี้: {current_price} THB\n"
                             f"📉 เส้น EMA50: {ema_val:.2f} THB\n"
                             f"🔄 เทรนด์: {'ขาขึ้น 🟢' if trend == 'UP' else 'ขาลง 🔴'}\n"
                             f"ℹ️ {'ราคาอยู่เหนือ EMA พร้อมซื้อ' if trend == 'UP' else f'ต้องขึ้นอีก {abs(diff):.2f} ถึงจะซื้อ'}\n"
                             f"📦 ถือเหรียญอยู่: {'ใช่' if holding_token else 'ไม่ใช่'}")
                send_line_message(status_msg)
                last_report_time = current_ts

            if not holding_token:
                if trend == "UP":
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    thb_balance = float(wallet.get('result', {}).get('THB', 0))

                    if thb_balance >= 10:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {
                            "sym": SYMBOL.upper(), "amt": int(thb_balance), "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
                            buy_msg = (f"🚀 บอทสั่งซื้อสำเร็จ! (Market Order)\n"
                                      f"💹 ราคาซื้อ: {current_price} THB\n"
                                      f"📊 ตัดเส้น EMA ที่: {ema_val:.2f}\n"
                                      f"⏱ Timeframe: {TIMEFRAME}m\n"
                                      f"🎯 เป้าขาย: {current_price * (1+PROFIT_TARGET):.2f} THB")
                            send_line_message(buy_msg)

            else:
                profit_pct = (current_price - last_buy_price) / last_buy_price
                sell_trigger = profit_pct >= PROFIT_TARGET or profit_pct <= -STOP_LOSS

                if sell_trigger:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin_name = SYMBOL.split('_')[0].upper()
                    coin_balance = float(wallet.get('result', {}).get(coin_name, 0))

                    if coin_balance > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL.upper(), "amt": coin_balance, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding_token = False
                            status = "✅ Take Profit" if profit_pct > 0 else "❌ Stop Loss"
                            sell_msg = (f"💰 บอทสั่งขายแล้ว! ({status})\n"
                                       f"📉 ราคาขาย: {current_price} THB\n"
                                       f"📈 ต้นทุน: {last_buy_price} THB\n"
                                       f"📊 ผลกำไร: {profit_pct*100:.2f}%")
                            send_line_message(sell_msg)

    except Exception as e:
        logging.error(f"Loop Error: {e}")

    time.sleep(30)
