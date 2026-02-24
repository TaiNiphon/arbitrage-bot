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

# --- Config ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_TRADE = "XRP_THB" # ใช้ชื่อเดียวกับใน buy.py

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึง Ticker (ใช้ URL กลางตาม buy.py)
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = 0
        
        # วนลูปหาเหรียญแบบเดียวกับ buy.py
        for item in t_res:
            if item.get('symbol') == SYMBOL_TRADE:
                price = float(item.get('last', 0))
                break
        
        # 2. ดึงแท่งเทียน (ใช้ THB_XRP ตามเอกสาร Bitkub)
        c_res = requests.get("https://api.bitkub.com/api/v3/market/candles?sym=THB_XRP&p=15&l=100", headers=headers, timeout=10).json()
        
        # เช็คว่า c_res มาเป็น list หรือ dict
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            # คำนวณ EMA50 แบบพื้นฐาน
            ema = sum(closes[:50]) / 50 
            return price, ema
        
        print(f"⚠️ ข้อมูลไม่ครบ: Price={price}, CandleCount={len(data)}")
        return None, None
    except Exception as e:
        print(f"❌ Error ใน get_market_data: {e}")
        return None, None

# --- ลูปหลัก ---
send_line("🤖 บอท Reboot: ปรับระบบ Log และการดึงข้อมูลตาม buy.py")
last_report = 0

while True:
    print(f"🔍 กำลังตรวจสอบราคา {SYMBOL_TRADE}...")
    price, ema = get_market_data()
    
    if price and ema:
        trend = "UP" if price > ema else "DOWN"
        # บรรทัดนี้จะยืนยันใน Railway ว่ารันผ่านแล้ว
        print(f"✅ สำเร็จ! | ราคา: {price} | EMA50: {ema:.2f} | เทรนด์: {trend}")
        
        if time.time() - last_report >= 3600:
            send_line(f"📊 รายงานประจำชั่วโมง\nราคา: {price} THB\nEMA50: {ema:.2f}\nเทรนด์: {trend}")
            last_report = time.time()
    else:
        print(f"⏳ ยังดึงข้อมูลไม่ได้... จะลองใหม่ใน 30 วินาที")
    
    time.sleep(30)
