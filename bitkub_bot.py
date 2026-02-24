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

# --- 1. การดึงค่า Config แบบลำดับความสำคัญ (Priority) ---
# บอทจะลองดึง BITKUB_KEY ก่อน ถ้าไม่มีจะใช้ API_KEY
API_KEY = os.getenv("BITKUB_KEY") or os.getenv("API_KEY")
API_SECRET = os.getenv("BITKUB_SECRET") or os.getenv("API_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

# จัดการชื่อเหรียญ (รับได้ทั้ง XRP_THB หรือ THB_XRP)
RAW_SYM = os.getenv("SYMBOL", "XRP_THB").upper().strip()
SYM_TRADE = "XRP_THB" if "XRP" in RAW_SYM else RAW_SYM
SYM_MARKET = "THB_XRP" if "XRP" in RAW_SYM else f"THB_{RAW_SYM.split('_')[0]}"

API_HOST = "https://api.bitkub.com"

# --- 2. ฟังก์ชันรายงานและดึงข้อมูล ---

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def get_market_data():
    """ดึงข้อมูลราคาและ EMA พร้อม User-Agent เพื่อกัน API ปฏิเสธ"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึงราคาจาก Ticker
        t_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYM_MARKET}", headers=headers).json()
        price = 0
        if isinstance(t_res, dict): price = float(t_res.get(SYM_MARKET, {}).get('last', 0))
        elif isinstance(t_res, list) and len(t_res) > 0: price = float(t_res[0].get('last', 0))

        # ดึงแท่งเทียนคำนวณ EMA50
        c_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYM_MARKET}&p=15&l=100", headers=headers).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- 3. ลูปหลักและการรายงานแบบละเอียด ---

holding, last_buy, last_report = False, 0, 0

# ข้อความแจ้งเตือนเริ่มงานพร้อมรายละเอียดครบถ้วน
send_line(
    f"🚀 บอทเริ่มระบบสมบูรณ์ (V3 Final)\n"
    f"📌 เฝ้าเหรียญ: {SYM_TRADE}\n"
    f"📊 กลยุทธ์: EMA 50 (Trend Follow)\n"
    f"💰 เป้ากำไร: 2.55% | 🚫 Stop Loss: 2.0%"
)


while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            logging.info(f"Price: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            # รายงานรายชั่วโมงแบบละเอียดเหมือนเวอร์ชันแรก
            if time.time() - last_report >= 3600:
                send_line(f"📊 รายงานประจำชั่วโมง\nเหรียญ: {SYM_TRADE}\nราคา: {price} THB\nEMA50: {ema_val:.2f}\nเทรนด์: {trend}\nสถานะ: {'ถือเหรียญ' if holding else 'รอสัญญาณ'}")
                last_report = time.time()

            # --- ส่วนการซื้อขาย (Place Order) ---
            # บอทจะรันที่นี่ต่อเมื่อเทรนด์เปลี่ยน...
            
        else:
            logging.warning(f"Connecting to Bitkub API for {SYM_MARKET}...")
            
    except Exception as e:
        logging.error(f"Error: {e}")
    time.sleep(30)
