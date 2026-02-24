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
        c_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYMBOL_TICKER}&p={TIMEFRAME}&l=100").json()
        # ป้องกันอาการค้าง: ตรวจสอบข้อมูลก่อนดึงค่า
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if len(data) >= EMA_PERIOD:
            closes = [float(day['c']) for day in data]
            # คำนวณ EMA
            ema = closes[0]
            m = 2 / (EMA_PERIOD + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- 4. ลูปการทำงานหลัก ---

holding = False
last_buy = 0
last_report = 0

logging.info(f"--- BOT STARTED: {SYMBOL_TRADE} ---")
send_line(f"🤖 บอททำงานแล้ว!\nเหรียญ: {SYMBOL_TRADE}\nกลยุทธ์: EMA50")


while True:
    try:
        current_price, ema_val = get_market_data()
        
        if current_price and ema_val:
            trend = "UP" if current_price > ema_val else "DOWN"
            logging.info(f"Price: {current_price} | EMA: {ema_val:.2f} | Trend: {trend}")

            # รายงานรายชั่วโมง
            if time.time() - last_report >= 3600:
                send_line(f"📊 รายงาน: {current_price} THB\nEMA50: {ema_val:.2f}\nสถานะ: {trend}")
                last_report = time.time()

            # ตัดสินใจซื้อ
            if not holding and trend == "UP":
                wallet = bitkub_v3_request("POST", "/api/v3/market/wallet")
                bal = float(wallet.get('result', {}).get('THB', 0)) if isinstance(wallet, dict) else 0
                if bal >= 10:
                    order = bitkub_v3_request("POST", "/api/v3/market/place-bid", {
                        "sym": SYMBOL_TRADE, "amt": round(bal, 2), "rat": 0, "typ": "market"
                    })
                    if order.get('error') == 0:
                        holding, last_buy = True, current_price
                        send_line(f"✅ ซื้อสำเร็จที่ {current_price}")

            # ตัดสินใจขาย
            elif holding:
                profit = (current_price - last_buy) / last_buy
                if profit >= 0.0255 or profit <= -0.020:
                    wallet = bitkub_v3_request("POST", "/api/v3/market/wallet")
                    coin = float(wallet.get('result', {}).get('XRP', 0)) if isinstance(wallet, dict) else 0
                    if coin > 0:
                        order = bitkub_v3_request("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL_TRADE, "amt": coin, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding = False
                            send_line(f"💰 ขายแล้ว! กำไร: {profit*100:.2f}%")
        else:
            logging.warning("Waiting for market data...")

    except Exception as e:
        logging.error(f"System Error: {e}")

    time.sleep(30)
