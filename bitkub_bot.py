import os, requests, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบประคองการเชื่อมต่อ ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่าตัวแปร (ดึงจาก Environment) ---
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebkit/537.36'}
    try:
        # 1. ดึงราคาปัจจุบัน (ใช้ Market Ticker V3)
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == "XRP_THB"), 0)
        
        # 2. ดึงกราฟ (ใช้ TradingView Endpoint เพราะมักจะไม่โดนบล็อก)
        # resolution=15 (15 นาที), l=100 (100 แท่ง)
        c_url = "https://api.bitkub.com/tradingview/history?symbol=XRP_THB&resolution=15&from={}&to={}".format(
            int(time.time()) - 86400 * 2, int(time.time())
        )
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        
        # ดึงราคาปิดจาก 'c'
        data_c = c_res.get('c', [])

        if price > 0 and len(data_c) >= 50:
            # คำนวณ EMA 50 แบบ Exponential
            ema = data_c[0]
            m = 2 / (50 + 1)
            for p in data_c: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"⏳ กำลังพยายามดึงกราฟสำรอง... (ราคา={price}, แท่งเทียน={len(data_c)})")
        return None, None
    except Exception as e:
        print(f"❌ ระบบขัดข้อง: {e}")
        return None, None

# --- 3. ลูปรายงาน ---
last_report = 0
send_line("✅ [System Restart]\nบอทเปลี่ยนระบบดึงกราฟเป็น TradingView Mode เพื่อแก้ปัญหา 0 แท่ง!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        print(f"✅ [{time.strftime('%H:%M:%S')}] {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        if time.time() - last_report >= 3600:
            status_icon = "🟢" if trend == "UP" else "🔴"
            trend_text = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            
            report = (
                f"{status_icon} [Bitkub XRP Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💰 ราคาปัจจุบัน: {price:,.2f} บาท\n"
                f"📊 เส้น EMA 50: {ema_val:,.2f} บาท\n"
                f"🧭 วิเคราะห์เทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}\n"
                "✅ บอททำงานผ่านระบบสำรองแล้ว"
            )
            send_line(report)
            last_report = time.time()
            
    time.sleep(30)
