import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่า Logging ให้ละเอียดขึ้น ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 2. ระบบ Dummy Server สำหรับ Railway (Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bitkub Trading Bot is Online")
        def log_message(self, format, *args): return

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. ระบบแจ้งเตือน LINE Messaging API ---
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "").strip()
LINE_USER_ID = os.getenv("LINE_USER_ID", "").strip()

def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code != 200:
            logging.error(f"LINE API Error: {res.text}")
    except Exception as e:
        logging.error(f"LINE Connection Error: {e}")

# --- 4. CONFIGURATION (Bitkub & Strategy) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET", "").encode()
SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
# ดึงกำไรจาก Variables ได้ เช่น ตั้งไว้ 0.008 (0.8%)
PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.0155)) 
API_HOST = "https://api.bitkub.com"

# --- 5. Functions จัดการ API Bitkub ---
def generate_signature(payload):
    json_payload = json.dumps(payload, separators=(',', ':'))
    return hmac.new(API_SECRET, msg=json_payload.encode(), digestmod=hashlib.sha256).hexdigest()

def get_header():
    return {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-BTK-APIKEY': API_KEY}

def get_wallet():
    url = f"{API_HOST}/api/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        data = res.json()
        if data.get('error') == 0:
            return data.get('result', {})
        else:
            logging.error(f"API Wallet Error: {data}")
            return None
    except Exception as e:
        logging.error(f"Wallet Connection Error: {e}")
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
    except Exception as e:
        logging.error(f"Place Order Connection Error: {e}")
        return {"error": 1}

def get_market_data():
    now = int(time.time())
    url = f"https://api.bitkub.com/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            return float(max(data['h'])), float(min(data['l'])), float(data['c'][-1])
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
    return None, None, None

# --- 6. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BOT STARTED | {SYMBOL} | Target: {PROFIT_TARGET*100}% ---")
send_line_message(f"🚀 บอทเริ่มทำงานแล้ว\nเหรียญ: {SYMBOL}\nเป้ากำไร: {PROFIT_TARGET*100}%")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()

        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.4f} | Holding: {holding_token}")

            if not holding_token:
                # เงื่อนไขการซื้อ
                if current_price <= mid_price:
                    wallet = get_wallet()
                    if wallet is not None:
                        thb_balance = float(wallet.get('THB', 0))
                        logging.info(f"Available Balance: {thb_balance} THB")
                        
                        if thb_balance >= 10:
                            logging.info(f">>> Sending BUY order at {current_price}")
                            order = place_order("bid", thb_balance, current_price)
                            if order.get('error') == 0:
                                last_buy_price = current_price
                                holding_token = True
                                send_line_message(f"✅ ซื้อสำเร็จ! (BUY)\nเหรียญ: {SYMBOL}\nราคา: {current_price} THB\nใช้เงิน: {thb_balance} THB")
                            else:
                                logging.error(f"Order Rejected by Bitkub: {order}")
                        else:
                            logging.warning(f"Balance too low ({thb_balance} THB). Need at least 10 THB.")
                    else:
                        logging.error("Failed to fetch wallet. Check API Key/Secret.")
            else:
                # เงื่อนไขการขาย
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= sell_target:
                    wallet = get_wallet()
                    if wallet is not None:
                        coin_ticker = SYMBOL.split('_')[1]
                        coin_balance = float(wallet.get(coin_ticker, 0))
                        logging.info(f"Holding {coin_ticker}: {coin_balance}")

                        if coin_balance > 0:
                            logging.info(f">>> Sending SELL order at {current_price}")
                            order = place_order("ask", coin_balance, current_price)
                            if order.get('error') == 0:
                                holding_token = False
                                profit_pct = ((current_price - last_buy_price) / last_buy_price) * 100
                                send_line_message(f"💰 ขายสำเร็จ! (SELL)\nเหรียญ: {SYMBOL}\nราคาขาย: {current_price} THB\nกำไร: {profit_pct:.2f}%")
                            else:
                                logging.error(f"Sell Order Rejected: {order}")
                    else:
                        logging.error("Failed to fetch wallet for selling.")
        else:
            logging.warning("Market data unavailable, retrying in 30s...")

    except Exception as e:
        logging.error(f"Critical Loop Error: {e}")
    
    time.sleep(30)
