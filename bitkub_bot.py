import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is Running")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- Configuration (อ้างอิงจาก Railway Variables ของคุณ) ---
BITKUB_KEY = os.getenv("BITKUB_KEY")
BITKUB_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_STR = "XRP_THB" # อ้างอิงจากรูป 1000054337.jpg

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึงราคา Ticker แบบวนลูป (วิธีเดียวกับ buy.py ที่รันผ่าน)
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=15).json()
        price = 0
        if isinstance(t_res, list):
            for item in t_res:
                if item.get('symbol') == SYMBOL_STR:
                    price = float(item.get('last', 0))
                    break
        
        # 2. ดึงแท่งเทียน (แก้ปัญหา CandleCount=0 โดยลอง 2 ชื่อ)
        # แบบแรก: THB_XRP (มาตรฐาน Bitkub V3)
        c_res = requests.get(f"https://api.bitkub.com/api/v3/market/candles?sym=THB_XRP&p=15&l=100", headers=headers, timeout=15).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        # แบบสอง: ถ้าแบบแรกไม่ได้ ให้ลอง XRP_THB
        if not data or len(data) == 0:
            c_res = requests.get(f"https://api.bitkub.com/api/v3/market/candles?sym=XRP_THB&p=15&l=100", headers=headers, timeout=15).json()
            data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            # คำนวณ EMA 50
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        
        # รายงานสถานะลง Logs กรณีข้อมูลไม่ครบ
        print(f"⚠️ ข้อมูลขัดข้อง: ราคา={price}, จำนวนแท่งเทียน={len(data)}")
        return None, None
    except Exception as e:
        print(f"❌ API Error: {e}")
        return None, None

# --- Main Logic ---
last_report = 0
send_line("🤖 [Reboot Success]\nบอทเริ่มดึงข้อมูลด้วยระบบ Fallback แล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        # แสดงผลใน Logs ให้คุณมั่นใจว่ารันผ่าน
        print(f"✅ [{time.strftime('%H:%M:%S')}] {SYMBOL_STR}: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        # รายงานประจำชั่วโมงแบบสวยงามละเอียด
        if time.time() - last_report >= 3600:
            trend_msg = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            msg = (
                "📊 [Bitkub Bot Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 Asset: {SYMBOL_STR}\n"
                f"💵 Current Price: {price} THB\n"
                f"📉 EMA 50 Line: {ema_val:.2f}\n"
                f"🧭 Market Trend: {trend_msg}\n"
                "━━━━━━━━━━━━━━━\n"
                "✅ ระบบทำงานปกติ (Active)"
            )
            send_line(msg)
            last_report = time.time()
    else:
        print(f"⏳ กำลังพยายามดึงข้อมูล {SYMBOL_STR} ใหม่ใน 30 วินาที...")
    
    time.sleep(30)
