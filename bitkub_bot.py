import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่าระบบบันทึก Log ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 2. ระบบ Health Check สำหรับ Railway (ป้องกัน App หลับ) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is Active and Running")
        def log_message(self, format, *args): return

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. ระบบแจ้งเตือน LINE ---
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "").strip()
LINE_USER_ID = os.getenv("LINE_USER_ID", "Ua88ba52b810900b7ba8df4c08b376496").strip()

def send_line_message(text):
    if not LINE_ACCESS_TOKEN: return
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

# --- 4. ดึงการตั้งค่าจาก Railway Variables ---
API_KEY = os.getenv("BITKUB_KEY", "").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").strip()
SYMBOL = os.getenv("SYMBOL", "THB_XRP").strip()
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB").strip()
PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.008))
API_HOST = "https://api.bitkub.com"

# --- 5. ฟังก์ชันหัวใจสำคัญ (แก้ไข Signature 404) ---
def generate_signature(payload):
    """สร้างลายเซ็นดิจิทัลตามมาตรฐาน Bitkub (Strict JSON)"""
    # จุดสำคัญ: ต้องไม่มีช่องว่างใน JSON เพื่อให้ Signature ตรงกับ Server
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
    """ดึงยอดเงินคงเหลือในกระเป๋า"""
    url = f"{API_HOST}/api/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        data = res.json()
        if data.get('error') == 0:
            return data.get('result', {})
        logging.error(f"Wallet API Error: {data}")
        return None
    except Exception as e:
        logging.error(f"Connection Error: {e}")
        return None

def place_order(side, amount, rate):
    """ส่งคำสั่งซื้อหรือขาย"""
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
        logging.error(f"Order Error: {e}")
        return {"error": 1}

def get_market_data():
    """ดึงราคาปัจจุบันและราคาสูงสุด-ต่ำสุดใน 24 ชม."""
    now = int(time.time())
    url = f"{API_HOST}/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            high_24h = float(max(data['h']))
            low_24h = float(min(data['l']))
            current_price = float(data['c'][-1])
            return high_24h, low_24h, current_price
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
    return None, None, None

# --- 6. ลูปการทำงานของบอท ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BOT STARTED | Pair: {SYMBOL} | Target: {PROFIT_TARGET*100}% ---")
send_line_message(f"🚀 บอท Bitkub เริ่มทำงานแล้ว!\nคู่เทรด: {SYMBOL}\nเป้ากำไร: {PROFIT_TARGET*100}%")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            logging.info(f"Price: {current_price} | Mid: {mid_price:.4f} | Holding: {holding_token}")

            if not holding_token:
                # กลยุทธ์: ซื้อเมื่อราคาต่ำกว่าราคากลาง 24 ชม.
                if current_price <= mid_price:
                    wallet = get_wallet()
                    if wallet:
                        thb_balance = float(wallet.get('THB', 0))
                        logging.info(f"Wallet Balance: {thb_balance} THB")
                        
                        if thb_balance >= 10:  # ขั้นต่ำ Bitkub คือ 10 บาท
                            order = place_order("bid", thb_balance, current_price)
                            if order.get('error') == 0:
                                last_buy_price = current_price
                                holding_token = True
                                msg = f"✅ ซื้อสำเร็จ!\nคู่: {SYMBOL}\nราคา: {current_price}\nจำนวน: {thb_balance} THB"
                                send_line_message(msg)
                                logging.info(msg)
            else:
                # กลยุทธ์: ขายเมื่อได้กำไรตามเป้า
                target_price = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= target_price:
                    wallet = get_wallet()
                    if wallet:
                        coin_name = SYMBOL.split('_')[1] # เช่น XRP
                        coin_balance = float(wallet.get(coin_name, 0))
                        
                        if coin_balance > 0:
                            order = place_order("ask", coin_balance, current_price)
                            if order.
