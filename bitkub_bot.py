import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่า Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

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

# --- 2. ฟังก์ชันดึงค่า Config แบบ "ทำความสะอาดรหัส" (แก้ปัญหาเครื่องหมาย ") ---
def get_safe_env(key):
    # ดึงค่ามาแล้วลบเครื่องหมาย " และช่องว่างทิ้งทันที
    val = os.getenv(key, "")
    return val.replace('"', '').replace("'", "").strip()

API_KEY = get_safe_env("BITKUB_KEY")
API_SECRET = get_safe_env("BITKUB_SECRET")
LINE_ACCESS_TOKEN = get_safe_env("LINE_ACCESS_TOKEN")
LINE_USER_ID = get_safe_env("LINE_USER_ID")

SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155 
API_HOST = "https://api.bitkub.com"

# --- 3. ระบบแจ้งเตือน LINE ---
def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except: pass

# --- 4. Functions จัดการ API Bitkub (แก้ Signature ให้ Bitkub ยอมรับ) ---
def generate_signature(payload):
    # สำคัญมาก: separators=(',', ':') ป้องกัน Wallet API Error 404
    json_payload = json.dumps(payload, separators=(',', ':'))
    return hmac.new(API_SECRET.encode(), msg=json_payload.encode(), hashlib.sha256).hexdigest()

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

# --- 5. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT STARTED | Pair: {SYMBOL} ---")
send_line_message(f"🚀 บอทเริ่มทำงานแล้ว\nเหรียญ: {SYMBOL}\nเป้ากำไร: {PROFIT_TARGET*100}%")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            wallet = get_wallet()
            thb_balance = float(wallet.get('THB', 0))
            
            # เมื่อแก้สำเร็จ THB จะไม่เป็น 0.0 แต่จะเป็น 201.0
            logging.info(f"Price: {current_price} | Mid: {mid_price:.4f} | THB: {thb_balance} | Holding: {holding_token}")

            if not holding_token:
                if current_price <= mid_price:
                    if thb_balance >= 10:
                        logging.info(f">>> Buying {SYMBOL} at {current_price}")
                        order = place_order("bid", thb_balance, current_price)
                        if order.get('error') == 0:
                            last_buy_price = current_price
                            holding_token = True
                            send_line_message(f"✅ ซื้อสำเร็จ! (BUY)\nราคา: {current_price} THB\nใช้เงิน: {thb_balance} THB")
            else:
                sell_target = last_buy_price * (1 + PROFIT_TARGET)
                if current_price >= sell_target:
                    coin_ticker = SYMBOL.split('_')[1]
                    coin_balance = float(wallet.get(coin_ticker, 0))
                    if coin_balance > 0:
                        logging.info(f">>> Selling {SYMBOL} at {current_price}")
                        order = place_order("ask", coin_balance, current_price)
                        if order.get('error') == 0:
                            holding_token = False
                            send_line_message(f"💰 ขายสำเร็จ! (SELL)\nราคาขาย: {current_price} THB")
    except Exception as e:
        logging.error(f"Loop error: {e}")
    time.sleep(30)
