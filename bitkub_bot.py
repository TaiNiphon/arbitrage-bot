import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ตั้งค่าระบบ Logging และ Server ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    """Server สำหรับป้องกัน Railway ตัดการทำงาน"""
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร (Configuration) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

# มาตรฐาน Bitkub V3: ใช้ XRP_THB สำหรับทุกส่วน
SYMBOL = "XRP_THB" 
EMA_PERIOD = 50
TIMEFRAME = "15"
API_HOST = "https://api.bitkub.com"

# --- 3. ฟังก์ชันพื้นฐาน (Core Functions) ---

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=5)
    except: pass

def bitkub_v3_auth(method, path, body={}):
    """สร้าง Signature ตามคู่มือ Bitkub V3 (Timestamp + Method + Path + Body)"""
    try:
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text.strip()
        body_str = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + body_str
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        return requests.post(f"{API_HOST}{path}", headers=headers, data=body_str, timeout=10).json()
    except: return {"error": 1}

def get_market_data():
    """ดึงข้อมูลราคาและแท่งเทียน พร้อมระบบกันค้าง"""
    try:
        # 1. ดึงราคาล่าสุด (Ticker)
        t_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL}").json()
        # ตรวจสอบรูปแบบข้อมูลว่าเป็น Dict หรือ List เพื่อดึงค่า 'last'
        if isinstance(t_res, list) and len(t_res) > 0: price = float(t_res[0].get('last', 0))
        else: price = float(t_res.get(SYMBOL, {}).get('last', 0))

        # 2. ดึงแท่งเทียน (Candles)
        c_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYMBOL}&p={TIMEFRAME}&l=100").json()
        # ป้องกันอาการ 'list' object has no attribute 'get'
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= EMA_PERIOD:
            closes = [float(d['c']) for d in data]
            # คำนวณ EMA50
            ema = closes[0]
            m = 2 / (EMA_PERIOD + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- 4. ลูปหลัก (Main Loop) ---

holding = False
last_buy = 0
last_report = 0

logging.info(f"--- BOT STARTED: {SYMBOL} ---")
send_line(f"🤖 บอทเฝ้าราคาเริ่มทำงานแล้ว (V3 Fix)\nเหรียญ: {SYMBOL}\nกลยุทธ์: EMA 50")


while True:
    try:
        price, ema_val = get_market_data()
        
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            logging.info(f"Price: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            # รายงานรายชั่วโมง
            if time.time() - last_report >= 3600:
                send_line(f"📊 รายงานประจำชั่วโมง\nราคา: {price} THB\nEMA50: {ema_val:.2f}\nเทรนด์: {trend}")
                last_report = time.time()

            # สัญญาณซื้อ (ขาขึ้น + ยังไม่มีเหรียญ)
            if not holding and trend == "UP":
                wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                bal = float(wallet.get('result', {}).get('THB', 0)) if isinstance(wallet, dict) else 0
                if bal >= 10:
                    order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {"sym": SYMBOL, "amt": round(bal, 2), "rat": 0, "typ": "market"})
                    if order.get('error') == 0:
                        holding, last_buy = True, price
                        send_line(f"✅ ซื้อสำเร็จที่ {price} THB")

            # สัญญาณขาย (ถึงเป้ากำไร 2.55% หรือ Stop Loss 2.0%)
            elif holding:
                profit = (price - last_buy) / last_buy
                if profit >= 0.0255 or profit <= -0.020:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin = float(wallet.get('result', {}).get('XRP', 0)) if isinstance(wallet, dict) else 0
                    if coin > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {"sym": SYMBOL, "amt": coin, "rat": 0, "typ": "market"})
                        if order.get('error') == 0:
                            holding = False
                            send_line(f"💰 ขายสำเร็จ! กำไร: {profit*100:.2f}%\nราคาขาย: {price} THB")
        else:
            # ถ้าข้อมูลไม่มา ให้แจ้งเตือนใน Log แทนการค้างเงียบ
            logging.warning("Waiting for market data from Bitkub...")

    except Exception as e:
        logging.error(f"System Error: {e}")

    time.sleep(30) # เช็คทุก 30 วินาที
