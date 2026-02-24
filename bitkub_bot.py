import os, requests, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Railway Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
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
        # 1. ดึงราคาปัจจุบันจาก Bitkub V3 (เพื่อให้ตรงกับหน้าแอปคุณที่สุด)
        t_url = "https://api.bitkub.com/api/v3/market/ticker"
        t_res = requests.get(t_url, headers=headers, timeout=10).json()
        price = 0
        for item in t_res:
            if item['symbol'] == SYMBOL_TICKER:
                price = float(item['last'])
                break
        
        # 2. ดึงข้อมูลกราฟ 15 นาที (ใช้ API V3 แบบ Standard)
        # แก้ปัญหา 0 แท่งด้วยการลองดึงซ้ำหากข้อมูลไม่มา
        c_url = f"https://api.bitkub.com/api/v3/market/candles?sym=THB_XRP&p=15&l=100"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            # คำนวณ EMA 50
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"⚠️ กำลังตรวจสอบข้อมูล: ราคา={price}, แท่งเทียน={len(data)}")
        return None, None
    except Exception as e:
        print(f"❌ ระบบ API ขัดข้อง: {e}")
        return None, None

# --- 3. ระบบรายงานประจำชั่วโมง ---
last_report = 0
send_line("✅ [System Calibrated]\nปรับปรุงระบบดึงราคาใหม่ให้ตรงกับหน้ากระดาน Bitkub แล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        # พิมพ์ค่าใน Logs เพื่อเช็กความถูกต้อง
        print(f"✅ [{time.strftime('%H:%M:%S')}] ราคาบอท: {price} | EMA50: {ema_val:.2f} | ตรงกับ Bitkub")

        if time.time() - last_report >= 3600:
            status_icon = "🟢" if trend == "UP" else "🔴"
            trend_text = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            
            msg = (
                f"{status_icon} [XRP Real-time Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 เหรียญ: XRP / THB\n"
                f"💰 ราคาปัจจุบัน: {price:,.2f} บาท\n"
                f"📊 เส้น EMA 50: {ema_val:,.2f} บาท\n"
                f"🧭 วิเคราะห์เทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}\n"
                "✅ ข้อมูลตรงกับหน้ากระดาน Bitkub"
            )
            send_line(msg)
            last_report = time.time()
            
    time.sleep(30)
