import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบจัดการ Log และ Health Check ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Trading Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่า (Configuration) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

SYMBOL = "XRP_THB" 
EMA_PERIOD = 50
TIMEFRAME = "15"
API_HOST = "https://api.bitkub.com"

# เป้าหมายกำไรและตัดขาดทุน
PROFIT_TARGET = 0.0255 # 2.55%
STOP_LOSS = 0.020      # 2.0%

# --- 3. ฟังก์ชันหลัก ---

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def bitkub_v3_auth(method, path, body={}):
    try:
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text.strip()
        body_str = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + body_str
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        return requests.post(f"{API_HOST}{path}", headers=headers, data=body_str, timeout=15).json()
    except: return {"error": 1}

def get_market_data():
    try:
        # ดึงราคาล่าสุดจาก Ticker
        t_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL}").json()
        price = 0
        if isinstance(t_res, list) and len(t_res) > 0:
            price = float(t_res[0].get('last', 0))
        elif isinstance(t_res, dict):
            price = float(t_res.get(SYMBOL, {}).get('last', 0))

        # ดึงแท่งเทียนเพื่อคำนวณ EMA
        c_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYMBOL}&p={TIMEFRAME}&l=100").json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= EMA_PERIOD:
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (EMA_PERIOD + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- 4. ลูปการทำงาน (Main Loop) ---

holding = False
last_buy = 0
last_report = 0

# แจ้งเตือนเริ่มต้นพร้อมรายละเอียดครบถ้วน
start_msg = (
    f"🤖 บอทเริ่มระบบสมบูรณ์ (V3 Final)\n"
    f"📌 เหรียญ: {SYMBOL}\n"
    f"📈 กลยุทธ์: EMA {EMA_PERIOD} (Trend Follow)\n"
    f"💰 เป้ากำไร: {PROFIT_TARGET*100}%\n"
    f"🚫 Stop Loss: {STOP_LOSS*100}%"
)
logging.info(f"--- BOT STARTED: {SYMBOL} ---")
send_line(start_msg)

while True:
    try:
        price, ema_val = get_market_data()
        
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            # แสดงราคาใน Log เพื่อยืนยันว่าไม่ค้าง
            logging.info(f"Price: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            if time.time() - last_report >= 3600:
                send_line(f"📊 รายงานประจำชั่วโมง\nราคา: {price} THB\nEMA50: {ema_val:.2f}\nสถานะ: {trend}")
                last_report = time.time()

            if not holding and trend == "UP":
                wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                bal = float(wallet.get('result', {}).get('THB', 0)) if isinstance(wallet, dict) else 0
                if bal >= 10:
                    order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {"sym": SYMBOL, "amt": round(bal, 2), "rat": 0, "typ": "market"})
                    if order.get('error') == 0:
                        holding, last_buy = True, price
                        send_line(f"✅ ซื้อสำเร็จที่ {price} THB")

            elif holding:
                profit = (price - last_buy) / last_buy
                if profit >= PROFIT_TARGET or profit <= -STOP_LOSS:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin = float(wallet.get('result', {}).get('XRP', 0)) if isinstance(wallet, dict) else 0
                    if coin > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {"sym": SYMBOL, "amt": coin, "rat": 0, "typ": "market"})
                        if order.get('error') == 0:
                            holding = False
                            send_line(f"💰 ขายแล้ว! กำไร: {profit*100:.2f}%\nราคาขาย: {price} THB")
        else:
            # ปรับ Log ให้ชัดเจนว่ารอส่วนไหน
            logging.warning(f"Fetching {SYMBOL} data from Bitkub API...")

    except Exception as e:
        logging.error(f"Error: {e}")

    time.sleep(30)
