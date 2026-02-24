import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- Configuration ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_TRADE = "XRP_THB"

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึงราคาแบบ Ticker List ตาม buy.py
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=15).json()
        price = 0
        if isinstance(t_res, list):
            for item in t_res:
                if item.get('symbol') == SYMBOL_TRADE:
                    price = float(item.get('last', 0))
                    break
        
        # ดึงแท่งเทียน (ใช้ THB_XRP)
        c_res = requests.get("https://api.bitkub.com/api/v3/market/candles?sym=THB_XRP&p=15&l=100", headers=headers, timeout=15).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- Main Loop ---
holding, last_report = False, 0

# ข้อความเริ่มงานแบบสวยงาม
startup_msg = (
    "🤖 [System Start]\n"
    "━━━━━━━━━━━━━━━\n"
    "✅ บอทเริ่มระบบสมบูรณ์\n"
    f"📌 เฝ้าเหรียญ: {SYMBOL_TRADE}\n"
    "📊 กลยุทธ์: EMA 50 (Trend Follow)\n"
    "💰 เป้ากำไร: 2.55%\n"
    "🚫 Stop Loss: 2.0%\n"
    "━━━━━━━━━━━━━━━\n"
    "🚀 กำลังวิเคราะห์สัญญาณ..."
)
send_line(startup_msg)

while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            print(f"✅ Active: {price} | EMA: {ema_val:.2f} | Trend: {trend}")

            # รายงานประจำชั่วโมงแบบละเอียดและสวยงาม
            if time.time() - last_report >= 3600:
                trend_icon = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
                status_icon = "📦 ถือเหรียญอยู่ (Holding)" if holding else "⏳ รอสัญญาณ (Waiting)"
                
                report_msg = (
                    "📊 [Hourly Report]\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💎 เหรียญ: {SYMBOL_TRADE}\n"
                    f"💵 ราคาปัจจุบัน: {price} THB\n"
                    f"📉 ค่า EMA 50: {ema_val:.2f}\n"
                    f"🧭 เทรนด์: {trend_icon}\n"
                    f"⚙️ สถานะ: {status_icon}\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"⏰ เวลา: {time.strftime('%H:%M:%S')}"
                )
                send_line(report_msg)
                last_report = time.time()
        else:
            print(f"⏳ กำลังรอข้อมูลตลาด {SYMBOL_TRADE}...")

    except Exception as e:
        logging.error(f"Error: {e}")
    time.sleep(30)
