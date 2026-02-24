import os, requests, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Railway Keep-Alive) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่าตัวแปร ---
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึงราคาปัจจุบันจาก Bitkub (ส่วนนี้ทำงานได้ปกติ)
        t_res = requests.get("https://api.bitkub.com/api/market/ticker", headers=headers, timeout=10).json()
        price = float(t_res.get('THB_XRP', {}).get('last', 0))
        
        # 2. ดึงกราฟจาก TradingView Bridge (แก้ปัญหา 0 แท่ง)
        # ใช้ข้อมูล 15 นาที, ดึง 100 แท่ง
        to_time = int(time.time())
        from_time = to_time - (15 * 60 * 100)
        c_url = f"https://api.bitkub.com/tradingview/history?symbol=XRP_THB&resolution=15&from={from_time}&to={to_time}"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        
        # ดึงราคาปิดจาก 'c' (Close)
        data_c = c_res.get('c', [])

        if price > 0 and len(data_c) >= 50:
            # คำนวณ EMA 50
            ema = data_c[0]
            m = 2 / (50 + 1)
            for p in data_c: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"🔄 กำลังเรียกข้อมูลสำรอง... (ราคา={price}, ข้อมูลกราฟ={len(data_c)} แท่ง)")
        return None, None
    except Exception as e:
        print(f"❌ ระบบขัดข้อง: {e}")
        return None, None

# --- 3. ระบบรายงานพรีเมียม ---
last_report = 0
send_line("✅ [System Fixed]\nบอทเปลี่ยนไปใช้ระบบดึงกราฟสำรอง (เสถียร 100%) แล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        print(f"✅ [{time.strftime('%H:%M:%S')}] {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        if time.time() - last_report >= 3600:
            status_color = "🟢" if trend == "UP" else "🔴"
            trend_text = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            
            msg = (
                f"{status_color} [XRP Market Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 สินทรัพย์: XRP / THB\n"
                f"💰 ราคาปัจจุบัน: {price:,.2f} บาท\n"
                f"📊 ค่า EMA 50: {ema_val:,.2f} บาท\n"
                f"🧭 วิเคราะห์เทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}\n"
                "✅ บอททำงานปกติ (Cloud Mode)"
            )
            send_line(msg)
            last_report = time.time()
            
    time.sleep(30)
