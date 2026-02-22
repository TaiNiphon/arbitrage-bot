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

# --- 3. ฟังก์ชันล้างเครื่องหมาย " ออกจากรหัสอัตโนมัติ (แก้ปัญหาจากรูป 1000054021) ---
def get_clean_env(key, default=""):
    val = os.getenv(key, default)
    if val:
        # ลบเครื่องหมาย " และ ' และช่องว่างออกทั้งหมด
        return val.replace('"', '').replace("'", "").strip()
    return default

API_KEY = get_clean_env("BITKUB_KEY")
API_SECRET = get_clean_env("BITKUB_SECRET")
LINE_ACCESS_TOKEN = get_clean_env("LINE_ACCESS_TOKEN")
LINE_USER_ID = get_clean_env("LINE_USER_ID")

SYMBOL = "THB_XRP"
SYMBOL_STR = "XRP_THB"
PROFIT_TARGET = 0.0155 
API_HOST = "https://api.bitkub.com"

# --- 4. ระบบแจ้งเตือน LINE (Messaging API) ---
def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code != 200: logging.error(f"LINE Error: {res.text}")
    except: pass

# --- 5. Functions จัดการ API Bitkub (แก้ไขจุดที่ทำให้ติด Error 404) ---
def generate_signature(payload):
    # ต้องใช้ separators=(',', ':') เพื่อให้ Signature ตรงกับเซิร์ฟเวอร์ Bitkub 100%
    json_payload = json.dumps(payload, separators=(',', ':'))
    return hmac.new(API_SECRET.encode(), msg=json_payload.encode(), digestmod=hashlib.sha256).hexdigest()

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
            logging.error(f"Wallet API Error: {data}")
            return {}
    except: return {}

def place_order(side, amount, rate):
    url = f"{API_HOST}/api/market/place-{side}"
    payload = {
        "sym": SYMBOL, "amt": round(float(amount), 8), "rat": round(float(rate), 4),
        "typ": "limit", "ts": int(time.time())
    }
    payload["sig"] = generate_signature(payload)
    try:
        res = requests.post(url, headers=get_header(), json=payload, timeout=15)
        return res.json()
    except: return {"error": 1}

def get_market_data():
    now = int(time.time())
    url = f"https://api.bitkub.com/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15).json()
        if res.get('s') == 'ok':
            return max(res['h']), min(res['l']), res['c'][-1]
    except: return None, None, None

# --- 6. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT STARTED | Key: {API_KEY[:5]}... ---")
send_line_message(f"🚀 บอท Bitkub XRP เริ่มทำงานแล้ว!\nราคาเป้าหมายกำไร: {PROFIT_TARGET*100}%")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            wallet = get_wallet()
            thb_balance = float(wallet.get('THB', 0))
            
            # แสดงค่าที่ถูกต้องใน Logs
            logging.info(f"Price: {current_price} | Mid: {mid_price:.2f} | THB: {thb_balance} | Holding: {holding_token}")

            if not holding_token:
                if current_price <= mid_price:
                    if thb_balance >= 10:
                        logging.info(f">>> Buying {SYMBOL} at {current_price}")
                        order = place_order("bid", thb_balance, current_price)
                        if order.
