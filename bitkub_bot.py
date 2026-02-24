import os, requests, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบประคองการเชื่อมต่อ (Railway Keep-Alive) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่าตัวแปร (ตามหน้า Variables ของคุณ) ---
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL = "THB_XRP" # ชื่อเหรียญสำหรับดึงกราฟ

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึงราคาปัจจุบัน (Ticker)
        t_res = requests.get("https://api.bitkub.com/api/market/ticker", headers=headers, timeout=10).json()
        price = float(t_res.get('THB_XRP', {}).get('last', 0))
        
        # 2. ดึงข้อมูลกราฟ (ใช้ URL สำรองที่เสถียรกว่า)
        # แก้ปัญหา CandleCount=0 โดยการใช้ API ชุดข้อมูลสาธารณะ
        c_url = f"https://api.bitkub.com/api/market/candles?sym={SYMBOL}&int=15&l=100"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        
        # ตรวจสอบโครงสร้างข้อมูล (Bitkub บางครั้งส่งมาใน result หรือ data)
        data = c_res.get('result', []) if isinstance(c_res, dict) else []
        if not data and 'data' in c_res: data = c_res['data']

        if price > 0 and len(data) >= 50:
            # คำนวณ EMA 50 จากราคาปิด (c)
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"⏳ กำลังประมวลผลข้อมูล... (ราคา={price}, แท่งเทียน={len(data)})")
        return None, None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None, None

# --- 3. ระบบรายงานผลแบบพรีเมียม ---
last_report = 0
send_line("✅ [System Reboot]\nบอทเปลี่ยนไปใช้ระบบดึงกราฟสำรองเพื่อแก้ปัญหาข้อมูล 0 แท่งแล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        print(f"✅ [{time.strftime('%H:%M:%S')}] {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        # ส่งรายงานสวยงามเข้า LINE ทันทีที่เชื่อมต่อได้ครั้งแรก และทุก 1 ชั่วโมง
        if time.time() - last_report >= 3600:
            status_color = "🟢" if trend == "UP" else "🔴"
            trend_text = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            
            msg = (
                f"{status_color} [XRP Market Analysis]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 สินทรัพย์: XRP / THB\n"
                f"💰 ราคาปัจจุบัน: {price:,.2f} บาท\n"
                f"📊 ค่าเฉลี่ย EMA 50: {ema_val:,.2f} บาท\n"
                f"🧭 วิเคราะห์เทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}\n"
                "✅ สถานะบอท: ทำงานปกติ (Active)"
            )
            send_line(msg)
            last_report = time.time()
            
    time.sleep(30) # ตรวจสอบทุก 30 วินาที
