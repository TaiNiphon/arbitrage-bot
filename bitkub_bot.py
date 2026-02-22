import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่า Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 2. ระบบ Dummy Server สำหรับ Railway ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. ฟังก์ชันดึงค่า Config แบบสะอาด (แก้ปัญหาเครื่องหมาย " ในรูป 1000054021) ---
def get_clean_env(key):
    val = os.getenv(key, "")
    # ลบเครื่องหมาย " และช่องว่างที่อาจติดมาจาก Raw Editor
    return val.replace('"', '').replace("'", "").strip()

API_KEY = get_clean_env("BITKUB_KEY")
API_SECRET = get_clean_env("BITKUB_SECRET")
LINE_ACCESS_TOKEN = get_clean_env("LINE_ACCESS_TOKEN")
LINE_USER_ID = get_clean_env("LINE_USER_ID")

SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155 
API_HOST = "https://api.bitkub.com"

# --- 4. ระบบแจ้งเตือน LINE (Messaging API) ---
def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try:
        res = requests.post(url, headers=headers
