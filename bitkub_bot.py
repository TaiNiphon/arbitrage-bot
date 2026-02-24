import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบจัดการ Log และ Server หลอกสำหรับ Railway ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร (Configuration) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

# แยกชื่อเหรียญตามที่ Bitkub ต้องการ
SYMBOL_TRADE = "XRP_THB"   # ใช้สำหรับซื้อขาย (V3)
SYMBOL_TICKER = "THB_XRP"  # ใช้สำหรับดึงราคา (Public)

EMA_PERIOD = 50
TIMEFRAME = "15"
API_HOST = "https://api.bitkub.com"

# --- 3. ฟังก์ชันการทำงานพื้นฐาน ---

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=5)
    except: pass

def bitkub_v3_request(method, path, body={}):
    """ระบบยืนยันตัวตนตามกฎ Bitkub V3 เป๊ะๆ"""
    try:
        # ดึงเวลา Server และเตรียมข้อมูล
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text.strip()
        body_str = json.dumps(body, separators=(',', ':'))
        # สร้าง Signature: Timestamp + Method + Path + Body
        payload = ts + method + path + body_str
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        
        headers = {
            'X-BTK-APIKEY': API_KEY,
            'X-BTK-TIMESTAMP': ts,
            'X-BTK-SIGN': sig,
            'Content-Type': 'application/json'
        }
        res = requests.post(f"{API_HOST}{path}", headers=headers, data=body_str, timeout=10)
        return res.json()
    except Exception as e:
        logging.error(f"Request Error: {e}")
        return {"error": 1}

def get_market_data():
    """ดึงข้อมูลราคาและแท่งเทียน (แก้ไขอาการค้างจากรูปแบบข้อมูล)"""
    try:
        # 1. ดึงราคาปัจจุบัน
        t_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL_TICKER}").json()
        # รองรับทั้งแบบ Dict และ List
        if isinstance(t_res, list) and len(t_res) > 0: price = float(t_res[0].get('last', 0))
        else: price = float(t_res.get(SYMBOL_TICKER, {}).get('last', 0))

        # 2. ดึงแท่งเทียนมาคำนวณ EMA
        c_res
