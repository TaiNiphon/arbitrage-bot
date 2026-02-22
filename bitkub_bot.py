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

# --- 3. ดึงค่า Config และล้างเครื่องหมาย " (ป้องกัน Error 404) ---
API_KEY = os.getenv("BITKUB_KEY", "").replace('"', '').strip()
API_SECRET = os.getenv("BITKUB_SECRET", "").replace('"', '').strip() # เป็น String ก่อน
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "").replace('"', '').strip()
LINE_USER_ID = os.getenv("LINE_USER_ID", "").replace('"', '').strip()

SYMBOL = os.getenv("SYMBOL", "THB_XRP")
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP_THB")
PROFIT_TARGET = 0.0155 
API_HOST = "https://api.bitkub.com"

# --- 4. ระบบแจ้งเตือน LINE ---
def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code != 200: logging.error(f"LINE API Error: {res.text}")
    except Exception as e: logging.error(f"LINE Connection Error: {e}")

# --- 5. Functions จัดการ API Bitkub (แก้ไข Signature ให้ถูกต้อง) ---
def generate_signature(payload):
    # Bitkub ต้องการ JSON ที่ไม่มีช่องว่างระหว่างตัวคั่น เพื่อสร้าง Signature ที่ถูกต้อง
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
        if data.get('error') != 0:
            logging.error(f"Wallet API Error: {data}")
            return {}
        return data.get('result', {})
    except Exception as e:
        logging.error(f"Wallet Connection Error: {e}")
        return {}

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
    except: return {"error": 1}

def get_market_data():
    now = int(time.time())
    url = f"https://api.bitkub.com/tradingview/history?symbol={SYMBOL_STR}&resolution=1&from={now-86400}&to={now}"
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('s') == 'ok':
            return max(data['h']), min(data['l']), data['c'][-1]
    except: return None, None, None

# --- 6. Main Loop ---
holding_token = False
last_buy_price = 0

logging.info(f"--- BITKUB BOT STARTED | Key: {API_KEY[:5]}... ---")
send_line_message(f"🚀 บอทเริ่มทำงานแล้ว\nเหรียญ: {SYMBOL}\nเป้ากำไร: {PROFIT_TARGET*100}%")

while True:
    try:
        high_24h, low_24h, current_price = get_market_data()
        if current_price is not None:
            mid_price = (high_24h + low_24h) / 2
            # ดึง Wallet มาโชว์ใน Log เพื่อยืนยันว่าเชื่อมต่อ Bitkub ได้แล้ว
            wallet = get_wallet()
            thb_balance = float(wallet.get('THB', 0))
            
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
                            profit_pct = ((current_price - last_buy_price) / last_buy_price) * 100
                            send_line_message(f"💰 ขายสำเร็จ! (SELL)\nราคาขาย: {current_price} THB\nกำไร: {profit_pct:.2f}%")
    except Exception as e:
        logging.error(f"Loop error: {e}")
    time.sleep(30)
