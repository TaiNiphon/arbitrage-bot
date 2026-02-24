import os, requests, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Railway Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is Running")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่าตัวแปรจาก Railway ---
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_TICKER = "XRP_THB"

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึงราคาปัจจุบันผ่าน V3 (จุดนี้รันผ่านแล้ว)
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == SYMBOL_TICKER), 0)
        
        # ดึงกราฟผ่าน V2 (แก้ปัญหา CandleCount=0)
        # ใช้ URL: https://api.bitkub.com/api/market/candles
        c_url = f"https://api.bitkub.com/api/market/candles?sym=THB_XRP&int=15&l=100"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        
        # สำหรับ V2 ข้อมูลจะอยู่ใน 'data' หรือ 'result'
        data = c_res.get('result', []) if isinstance(c_res, dict) else []
        if not data and isinstance(c_res, list): data = c_res

        if price > 0 and len(data) >= 50:
            # คำนวณ EMA 50 จากราคาปิด
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"🔄 กำลังรอ API ตอบกลับ: ราคา={price}, แท่งเทียน={len(data)}")
        return None, None
    except Exception as e:
        print(f"❌ ระบบขัดข้อง: {e}")
        return None, None

# --- 3. ระบบรายงานพรีเมียม ---
last_report = 0
send_line("🤖 [System Start]\nเปลี่ยนระบบดึงกราฟเป็น V2 เพื่อความเสถียรแล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        print(f"✅ [{time.strftime('%H:%M:%S')}] {price} | EMA50: {ema_val:.2f}")

        # ส่งรายงานพรีเมียมทันทีที่เชื่อมต่อได้ครั้งแรก และทุก 1 ชม.
        if time.time() - last_report >= 3600:
            status_icon = "🟢" if trend == "UP" else "🔴"
            trend_text = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            
            report = (
                f"{status_icon} [Bitkub XRP Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 เหรียญ: {SYMBOL_TICKER}\n"
                f"💵 ราคาล่าสุด: {price:,.2f} THB\n"
                f"📊 ค่า EMA 50: {ema_val:,.2f} THB\n"
                f"🧭 วิเคราะห์เทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}\n"
                "✅ บอททำงานปกติ"
            )
            send_line(report)
            last_report = time.time()
            
    time.sleep(30)
