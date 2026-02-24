import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 1. ระบบรักษาการทำงาน (Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่า API และตัวแปร (ดึงจาก Variables ของคุณ) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL_TRADE = "XRP_THB" # ใช้ XRP_THB ตามที่คุณรันผ่าน
HOST = "https://api.bitkub.com"

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

# --- 3. ฟังก์ชัน API V3 (อ้างอิงจาก buy.py และ sale.py ที่คุณส่งมา) ---

def bitkub_v3_request(method, path, body={}):
    """ฟังก์ชันส่งคำสั่ง V3 ตามโครงสร้างที่คุณรันผ่าน"""
    try:
        ts = requests.get(f"{HOST}/api/v3/servertime").text.strip()
        json_body = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig
        }
        res = requests.post(f"{HOST}{path}", headers=headers, data=json_body, timeout=15)
        return res.json()
    except Exception as e:
        return {"error": 1, "message": str(e)}

def get_market_data():
    """ดึงราคาแบบวนลูปจาก List ตามบรรทัดที่ 48 ใน buy.py"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึง Ticker ทั้งหมดมาวนหาเหรียญ
        t_res = requests.get(f"{HOST}/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = 0
        for item in t_res:
            if item.get('symbol') == SYMBOL_TRADE:
                price = float(item.get('last', 0))
                break

        # ดึงแท่งเทียน 15 นาที สำหรับ EMA50
        c_res = requests.get(f"{HOST}/api/v3/market/candles?sym=THB_XRP&p=15&l=100", headers=headers, timeout=10).json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= 50:
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (50 + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- 4. ลูปหลักการทำงาน ---
holding, last_buy, last_report = False, 0, 0

send_line(
    f"🚀 บอท V3 สมบูรณ์แบบ (Full Auto)\n"
    f"📌 เหรียญ: {SYMBOL_TRADE}\n"
    f"📊 กลยุทธ์: EMA 50 | กำไร: 2.55% | Cut: 2.0%"
)

while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            print(f"✅ Active | Price: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            # รายงานชั่วโมง
            if time.time() - last_report >= 3600:
                send_line(f"📊 รายงานประจำชั่วโมง\nเหรียญ: {SYMBOL_TRADE}\nราคา: {price}\nเทรนด์: {trend}")
                last_report = time.time()

            # --- เงื่อนไขการซื้อ (Trend UP + ไม่มีของ) ---
            if not holding and trend == "UP":
                wallet = bitkub_v3_request("POST", "/api/v3/market/wallet")
                thb = float(wallet.get('result', {}).get('THB', 0))
                if thb >= 10:
                    # ใช้คำสั่ง Bid แบบเดียวกับ buy.py
                    order = bitkub_v3_request("POST", "/api/v3/market/place-bid", {
                        "sym": SYMBOL_TRADE.lower(), "amt": int(thb), "rat": 0, "typ": "market"
                    })
                    if order.get('error') == 0:
                        holding, last_buy = True, price
                        send_line(f"✅ ซื้อสำเร็จที่ {price} THB (ตามเทรนด์ EMA)")

            # --- เงื่อนไขการขาย (ถึงเป้ากำไร หรือ Cut Loss) ---
            elif holding:
                profit = (price - last_buy) / last_buy
                if profit >= 0.0255 or profit <= -0.020:
                    wallet = bitkub_v3_request("POST", "/api/v3/market/wallet")
                    coin_bal = float(wallet.get('result', {}).get('XRP', 0))
                    if coin_bal > 0:
                        # ใช้คำสั่ง Ask แบบเดียวกับ sale.py
                        order = bitkub_v3_request("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL_TRADE.lower(), "amt": coin_bal, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding = False
                            msg = "💰 ขายทำกำไร!" if profit > 0 else "🚫 ขายตัดขาดทุน"
                            send_line(f"{msg}\nกำไร: {profit*100:.2f}%\nราคาขาย: {price}")
        else:
            print(f"⏳ กำลังรอข้อมูลตลาดจาก API (เหรียญ: {SYMBOL_TRADE})...")

    except Exception as e:
        logging.error(f"Main Error: {e}")
    time.sleep(30)
