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

# --- 2. ตั้งค่าตัวแปรจาก Environment ของคุณ ---
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
# แยกสัญลักษณ์ตามมาตรฐาน API V3
SYMBOL_PRICE = "XRP_THB"   # สำหรับเช็กราคา (Ticker)
SYMBOL_CHART = "THB_XRP"   # สำหรับดึงกราฟ (Candles)

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึงราคาปัจจุบัน
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == SYMBOL_PRICE), 0)
        
        # 2. ดึงข้อมูลกราฟ (เพิ่มความพยายามดึงจนกว่าจะได้)
        c_url = f"https://api.bitkub.com/api/v3/market/candles?sym={SYMBOL_CHART}&p=15&l=100"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        
        # ตรวจสอบรูปแบบข้อมูลที่ได้รับ
        data = []
        if isinstance(c_res, list): data = c_res
        elif isinstance(c_res, dict): data = c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            # ใช้ค่า Close Price (c) มาคำนวณ EMA 50
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            multiplier = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * multiplier + ema
            return price, ema
        
        print(f"⚠️ รอดึงข้อมูลกราฟ... (ราคา={price}, แท่งเทียน={len(data)})")
        return None, None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None, None

# --- 3. ลูปการทำงานและรายงาน ---
last_report_time = 0
send_line("✅ [Trend Bot Online]\nเริ่มระบบวิเคราะห์ XRP ด้วย EMA 50 สำเร็จ!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        current_time = time.time()
        
        # พิมพ์สถานะใน Logs ของ Railway ทุกๆ รอบ
        print(f"[{time.strftime('%H:%M:%S')}] {SYMBOL_PRICE}: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        # ส่งรายงานเข้า LINE ทุกๆ 1 ชั่วโมง (3600 วินาที) หรือเมื่อมีสัญญาณรายงานครั้งแรก
        if current_time - last_report_time >= 3600:
            status_icon = "🟢" if trend == "UP" else "🔴"
            trend_text = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            
            report = (
                f"{status_icon} [XRP Trend Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 สินทรัพย์: {SYMBOL_PRICE}\n"
                f"💰 ราคาปัจจุบัน: {price:,.2f} THB\n"
                f"📊 เส้น EMA 50: {ema_val:,.2f} THB\n"
                f"🧭 สรุปเทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}\n"
                "🤖 บอททำงานปกติ"
            )
            send_line(report)
            last_report_time = current_time
            
    time.sleep(30) # ตรวจสอบข้อมูลทุก 30 วินาที
