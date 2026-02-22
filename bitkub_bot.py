import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่า Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ฟังก์ชันล้างขยะออกจากรหัส (แก้ปัญหาเครื่องหมาย " อัตโนมัติ) ---
def get_clean_env(key):
    val = os.getenv(key, "")
    # ล้างเครื่องหมายคำพูดทิ้ง ไม่ว่าจะเป็น " หรือ '
    return val.replace('"', '').replace("'", "").strip()

API_KEY = get_clean_env("BITKUB_KEY")
API_SECRET = get_clean_env("BITKUB_SECRET")
LINE_TOKEN = get_clean_env("LINE_ACCESS_TOKEN")
LINE_USER_ID = get_clean_env("LINE_USER_ID")
SYMBOL = "THB_XRP"

# --- 3. ฟังก์ชันส่ง Line Messaging API ---
def send_line_msg(text):
    if not LINE_TOKEN or not LINE_USER_ID: return
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_TOKEN}'}
    payload = {'to': LINE_USER_ID, 'messages': [{'type': 'text', 'text': text}]}
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except: pass

# --- 4. ฟังก์ชัน Bitkub API ---
def get_wallet():
    url = "https://api.bitkub.com/api/market/wallet"
    ts = int(time.time())
    payload = {"ts": ts}
    json_payload = json.dumps(payload, separators=(',', ':'))
    sig = hmac.new(API_SECRET.encode(), json_payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-BTK-APIKEY': API_KEY
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        return res.json()
    except: return None

def get_price():
    url = f"https://api.bitkub.com/api/market/ticker?sym={SYMBOL}"
    try:
        res = requests.get(url, timeout=10).json()
        return res.get(SYMBOL, {}).get('last')
    except: return None

# --- 5. ลูปการทำงาน ---
logging.info(f"--- บอทเริ่มทำงาน (Key: {API_KEY[:5]}...) ---")
send_line_msg("🤖 บอท Bitkub พร้อมทำงานแล้ว!")

while True:
    try:
        price = get_price()
        wallet = get_wallet()
        
        if wallet and wallet.get('error') == 0:
            bal = wallet['result'].get('THB', 0)
            logging.info(f"✅ สำเร็จ! XRP: {price} | Wallet: {bal} THB")
        else:
            # แจ้ง Error ให้ละเอียดขึ้นใน Log
            logging.error(f"❌ ราคา: {price} | Error: {wallet}")
            
    except Exception as e:
        logging.error(f"Error: {e}")
    
    time.sleep(30)
