import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- Config จาก Variables ของคุณ ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_TRADE = "XRP_THB" # ใช้สำหรับเปรียบเทียบใน List

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึงราคา Ticker แบบที่คุณใช้ใน buy.py
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = 0
        # วนลูปหา XRP_THB ใน List (เลียนแบบบรรทัดที่ 48 ใน buy.py)
        for item in t_res:
            if item.get('symbol') == SYMBOL_TRADE:
                price = float(item.get('last', 0))
                break

        # 2. ดึงแท่งเทียน (ใช้ THB_XRP สำหรับ Candles)
        c_res = requests.get("https://api.bitkub.com/api/v3/market/candles?sym=THB_XRP&p=15&l=100", headers=headers, timeout=10).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None, None

# --- เริ่มการทำงาน ---
send_line("🤖 บอท Reboot: ใช้ระบบดึงราคาแบบเดียวกับ buy.py แล้ว!")
last_report = 0

while True:
    price, ema = get_market_data()
    if price and ema:
        trend = "UP" if price > ema else "DOWN"
        # บรรทัดนี้จะแสดงผลในหน้า Logs
        print(f"✅ SUCCESS! | {SYMBOL_TRADE}: {price} | EMA50: {ema:.2f} | Trend: {trend}")
        
        if time.time() - last_report >= 3600:
            send_line(f"📊 รายงานประจำชั่วโมง\nราคา: {price} THB\nเทรนด์: {trend}")
            last_report = time.time()
    else:
        # ถ้ายังขึ้นตัวนี้ แสดงว่า API ไม่ตอบกลับ
        print(f"⏳ กำลังรอข้อมูลราคาจาก Bitkub API... (เหรียญ: {SYMBOL_TRADE})")
    
    time.sleep(30)
