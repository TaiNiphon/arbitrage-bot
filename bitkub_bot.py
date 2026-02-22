import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่า Log ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 2. ระบบ Health Check (สำหรับ Railway) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bitkub Bot is Active")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. ดึงค่าจาก Variables พร้อมลบ "ช่องว่าง" และ "บรรทัดใหม่" อัตโนมัติ ---
# แก้ปัญหาจากรูป 1000054013.jpg ที่รหัสถูกตัดเป็นสองบรรทัด
API_KEY = os.getenv("BITKUB_KEY", "").replace("\n", "").replace(" ", "").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").replace("\n", "").replace(" ", "").strip()
SYMBOL = "THB_XRP"
SYMBOL_STR = "XRP_THB"
API_HOST = "https://api.bitkub.com"

# --- 4. ฟังก์ชันสร้าง Signature (Strict JSON Format) ---
def generate_signature(payload):
    # Bitkub กำหนดว่า JSON ต้องไม่มีช่องว่างระหว่างตัวคั่น (separators)
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

# --- 5. ฟังก์ชันหลักในการดึง Wallet ---
def get_wallet():
    url = f"{API_HOST}/api/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        data = res.json()
        if data.get('error') == 0:
            return data.get('result', {})
        # ถ้ายัง Error 404 บรรทัดนี้จะแจ้งรายละเอียดที่ชัดเจนขึ้น
        logging.error(f"Wallet API Error: {data}")
        return None
    except Exception as e:
        logging.error(f"Connection Error: {e}")
        return None

def get_market_price():
    url = f"{API_HOST}/api/market/ticker?sym={SYMBOL}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        return float(data[SYMBOL]['last'])
    except:
        return None

# --- 6. ลูปการทำงาน ---
logging.info(f"--- บอทเริ่มทำงาน (Key: {API_KEY[:5]}...{API_KEY[-5:]}) ---")

while True:
    try:
        # เช็คราคาตลาด
        price = get_market_price()
        
        # เช็คยอดเงินในกระเป๋า (เพื่อทดสอบ API Key)
        wallet = get_wallet()
        
        if wallet:
            thb_balance = wallet.get('THB', 0)
            logging.info(f"Price: {price} | Wallet THB: {thb_balance}")
        else:
            logging.info(f"Price: {price} | Waiting for Wallet connection...")

    except Exception as e:
        logging.error(f"Main Loop Error: {e}")
    
    time.sleep(30) # เช็คทุก 30 วินาที
