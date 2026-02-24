import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 1. ระบบจัดการ Server (Railway Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่าตัวแปร (อ้างอิงจาก Railway Variables ของคุณ) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_TICKER = "XRP_THB"  # สำหรับดึงราคา
SYMBOL_CANDLE = "THB_XRP"  # สำหรับดึงกราฟ (มาตรฐาน V3)

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

# --- 3. ฟังก์ชันดึงข้อมูล (แก้ปัญหา CandleCount=0) ---
def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึงราคาแบบ Ticker (ท่าเดียวกับ buy.py ที่คุณรันผ่าน)
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=15).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == SYMBOL_TICKER), 0)
        
        # ดึงแท่งเทียน 15 นาที (ใช้ชื่อ THB_XRP ตามมาตรฐาน V3)
        c_url = f"https://api.bitkub.com/api/v3/market/candles?sym={SYMBOL_CANDLE}&p=15&l=100"
        c_res = requests.get(c_url, headers=headers, timeout=15).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            # คำนวณ EMA 50
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"⚠️ ข้อมูลไม่ครบ: ราคา={price}, จำนวนแท่งเทียน={len(data)}")
        return None, None
    except Exception as e:
        print(f"❌ API Error: {e}")
        return None, None

# --- 4. ลูปหลักและการรายงานผลสวยงาม ---
last_report = 0
send_line("🚀 [Bot Start] ระบบวิเคราะห์ XRP พร้อมทำงานแล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        # แสดงผลใน Logs ของ Railway
        print(f"✅ [{time.strftime('%H:%M:%S')}] {SYMBOL_TICKER}: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        # รายงานประจำชั่วโมงแบบสวยงามละเอียด
        if time.time() - last_report >= 3600:
            trend_icon = "📈 ขาขึ้น (Strong Buy)" if trend == "UP" else "📉 ขาลง (Wait/Sell)"
            color_bar = "🟢" if trend == "UP" else "🔴"
            
            report_msg = (
                f"{color_bar} [Bitkub Hourly Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 เหรียญ: {SYMBOL_TICKER}\n"
                f"💵 ราคาปัจจุบัน: {price:,.2f} THB\n"
                f"📉 เส้น EMA 50: {ema_val:,.2f}\n"
                f"🧭 วิเคราะห์เทรนด์: {trend_icon}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ เวลา: {time.strftime('%H:%M:%S')}\n"
                "✅ สถานะระบบ: ทำงานปกติ"
            )
            send_line(report_msg)
            last_report = time.time()
    else:
        print(f"⏳ กำลังพยายามเชื่อมต่อ API ใหม่อีกครั้ง...")
    
    time.sleep(30)
