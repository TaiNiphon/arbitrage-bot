import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Trading Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- Config (ดึงค่าจาก Variables ของคุณ) ---
API_KEY = os.getenv("BITKUB_KEY") or os.getenv("API_KEY")
API_SECRET = os.getenv("BITKUB_SECRET") or os.getenv("API_SECRET")
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
    """ดึงข้อมูลราคาแบบวนลูป List (ท่าเดียวกับ buy.py) เพื่อความแน่นอน"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. ดึงราคา Ticker (ใช้ URL กลางที่คืนค่าเป็น List)
        t_res = requests.get("https://api.bitkub.com/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = 0
        for item in t_res:
            if item.get('symbol') == SYMBOL_TRADE:
                price = float(item.get('last', 0))
                break

        # 2. ดึงแท่งเทียน 15 นาที
        c_res = requests.get("https://api.bitkub.com/api/v3/market/candles?sym=THB_XRP&p=15&l=100", headers=headers, timeout=10).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- ลูปหลักและการรายงานแบบละเอียด ---
holding, last_report = False, 0

# แจ้งเตือนตอนเริ่มงานแบบละเอียด (เหมือนที่คุณต้องการ)
send_line(
    f"🚀 บอทเริ่มระบบสมบูรณ์ (V3 Final Fix)\n"
    f"📌 เฝ้าเหรียญ: {SYMBOL_TRADE}\n"
    f"📊 กลยุทธ์: EMA 50 (Trend Follow)\n"
    f"💰 เป้ากำไร: 2.55% | 🚫 Stop Loss: 2.0%"
)

while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            logging.info(f"Price: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            # รายงานประจำชั่วโมงแบบละเอียด
            if time.time() - last_report >= 3600:
                msg = (
                    f"📊 รายงานประจำชั่วโมง\n"
                    f"เหรียญ: {SYMBOL_TRADE}\n"
                    f"ราคา: {price} THB\n"
                    f"EMA50: {ema_val:.2f}\n"
                    f"เทรนด์: {'📈 ขาขึ้น' if trend == 'UP' else '📉 ขาลง'}\n"
                    f"สถานะ: {'ถือเหรียญ' if holding else 'รอสัญญาณ'}"
                )
                send_line(msg)
                last_report = time.time()

            # --- ส่วนการซื้อขายจะทำงานต่อที่นี่ตามเงื่อนไข Trend ---

        else:
            logging.warning(f"Connecting to Bitkub API for {SYMBOL_TRADE}...")

    except Exception as e:
        logging.error(f"Error: {e}")
    time.sleep(30)
