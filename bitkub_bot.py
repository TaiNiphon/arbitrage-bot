import os, requests, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Railway Keep-Alive) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร (ตามหน้า Variables ของคุณ) ---
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_TICKER = "XRP_THB" # สำหรับดึงราคาล่าสุด
SYMBOL_CANDLE = "THB_XRP" # สำหรับดึงข้อมูลกราฟ (V3 Requirement)

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึงราคาปัจจุบัน (Ticker) - พิสูจน์แล้วว่าดึงได้จากรูป 1000054369
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=15).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == SYMBOL_TICKER), 0)
        
        # ดึงข้อมูลกราฟ (Candles) - แก้ไขให้ใช้ชื่อ THB_XRP ตามที่ API V3 ต้องการ
        c_url = f"https://api.bitkub.com/api/v3/market/candles?sym={SYMBOL_CANDLE}&p=15&l=100"
        c_res = requests.get(c_url, headers=headers, timeout=15).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            # คำนวณค่า EMA 50
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"⚠️ กำลังรอข้อมูลกราฟ: ราคา={price}, จำนวนแท่ง={len(data)}")
        return None, None
    except Exception as e:
        print(f"❌ ระบบเชื่อมต่อขัดข้อง: {e}")
        return None, None

# --- 3. ลูปหลักและการรายงานที่สวยงาม ---
last_report = 0
send_line("🚀 [Bot Online]\nระบบเริ่มวิเคราะห์ XRP ด้วย EMA 50 แล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        # แสดงผลในหน้า Logs ของ Railway เพื่อความมั่นใจ
        print(f"✅ [{time.strftime('%H:%M:%S')}] {SYMBOL_TICKER}: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        # ส่งรายงานประจำชั่วโมงแบบสวยงาม
        if time.time() - last_report >= 3600:
            trend_icon = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            status_color = "🟢" if trend == "UP" else "🔴"
            
            report_msg = (
                f"{status_color} [XRP Market Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 สินทรัพย์: {SYMBOL_TICKER}\n"
                f"💵 ราคาปัจจุบัน: {price:,.2f} THB\n"
                f"📊 EMA 50 Line: {ema_val:,.2f}\n"
                f"🧭 เทรนด์ตลาด: {trend_icon}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}\n"
                "✅ ระบบทำงานปกติ"
            )
            send_line(report_msg)
            last_report = time.time()
    
    time.sleep(30) # ตรวจสอบทุก 30 วินาที
