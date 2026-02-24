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

# --- Configuration (ดึงตามชื่อใน Variables ของคุณเป๊ะๆ) ---
BITKUB_KEY = os.getenv("BITKUB_KEY")
BITKUB_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL = "XRP_THB" # ตามหน้า Variables

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึงราคา Ticker แบบเดียวกับ buy.py
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=15).json()
        price = 0
        for item in t_res:
            if item.get('symbol') == SYMBOL:
                price = float(item.get('last', 0))
                break
        
        # 2. ดึงแท่งเทียน (ลองทั้งสองแบบเพื่อแก้ปัญหา CandleCount=0)
        # ลองใช้ sym=THB_XRP (มาตรฐาน V3)
        c_res = requests.get("https://api.bitkub.com/api/v3/market/candles?sym=THB_XRP&p=15&l=100", headers=headers, timeout=15).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        # ถ้ายังไม่ได้ข้อมูล ให้ลองอีกชื่อ (XRP_THB)
        if not data:
            c_res = requests.get("https://api.bitkub.com/api/v3/market/candles?sym=XRP_THB&p=15&l=100", headers=headers, timeout=15).json()
            data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            # คำนวณ EMA50
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        
        print(f"⚠️ ข้อมูลไม่ครบ: Price={price}, CandleCount={len(data)}")
        return None, None
    except Exception as e:
        print(f"❌ API Error: {e}")
        return None, None

# --- ลูปหลักและการรายงาน ---
holding, last_report = False, 0

# ข้อความเริ่มงานสวยงาม
send_line(
    "🤖 [System Reboot Success]\n"
    "━━━━━━━━━━━━━━━\n"
    f"📌 เฝ้าเหรียญ: {SYMBOL}\n"
    "📊 กลยุทธ์: EMA 50 (V3 Optimized)\n"
    "💰 เป้ากำไร: 2.55% | 🚫 Cut: 2.0%\n"
    "━━━━━━━━━━━━━━━"
)

while True:
    price, ema_val = get_market_data()
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        print(f"✅ [{time.strftime('%H:%M:%S')}] {SYMBOL}: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        if time.time() - last_report >= 3600:
            trend_icon = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            report_msg = (
                "📊 [Hourly Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💎 เหรียญ: {SYMBOL}\n"
                f"💵 ราคา: {price} THB\n"
                f"📉 EMA 50: {ema_val:.2f}\n"
                f"🧭 เทรนด์: {trend_icon}\n"
                "━━━━━━━━━━━━━━━"
            )
            send_line(report_msg)
            last_report = time.time()
    else:
        print(f"⏳ กำลังพยายามดึงข้อมูล {SYMBOL} ใหม่อีกครั้ง...")
    
    time.sleep(30)
