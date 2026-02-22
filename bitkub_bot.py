import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่า Log และระบบพื้นฐาน ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is Active")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ดึงค่าและล้างขยะออกจากรหัส (แก้ปัญหา " ในหน้า Variables) ---
# บรรทัดนี้จะลบทั้งเครื่องหมายคำพูด และช่องว่างที่หลุดเข้ามาใน Raw Editor
API_KEY = os.getenv("BITKUB_KEY", "").replace('"', '').replace("'", "").strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").replace('"', '').replace("'", "").strip()
SYMBOL = "THB_XRP"  # กำหนดคู่เทรดให้ชัดเจนเพื่อแก้ปัญหาราคาผิด

# --- 3. ฟังก์ชันการทำงานของ Bitkub API ---
def generate_signature(payload):
    # ต้องใช้ separators=(',', ':') เพื่อให้ Signature ตรงกับเซิร์ฟเวอร์ Bitkub
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
    url = "https://api.bitkub.com/api/market/wallet"
    payload = {"ts": int(time.time())}
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=10)
        return res.json()
    except Exception as e:
        return {"error": 99, "message": str(e)}

def get_current_price():
    # ฟังก์ชันดึงราคา XRP ที่ถูกต้อง (ต้องได้ประมาณ 44-45 บาท)
    url = f"https://api.bitkub.com/api/market/ticker?sym={SYMBOL}"
    try:
        res =
