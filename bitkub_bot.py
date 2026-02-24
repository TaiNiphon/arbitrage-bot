import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 1. ระบบรักษาการทำงาน (Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Trading Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร (Configuration) ---
API_KEY = os.getenv("BITKUB_KEY") or os.getenv("API_KEY")
API_SECRET = os.getenv("BITKUB_SECRET") or os.getenv("API_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

# จัดการชื่อเหรียญอัตโนมัติ
RAW_SYM = os.getenv("SYMBOL", "XRP_THB").upper().strip()
SYM_TRADE = "XRP_THB" if "XRP" in RAW_SYM else RAW_SYM
SYM_MARKET = "THB_XRP" if "XRP" in RAW_SYM else f"THB_{RAW_SYM.split('_')[0]}"

API_HOST = "https://api.bitkub.com"

# --- 3. ฟังก์ชันสนับสนุน (Helper Functions) ---

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def bitkub_v3_auth(method, path, body={}):
    """ฟังก์ชันยืนยันตัวตนสำหรับ ซื้อ/ขาย/เช็คยอดเงิน (V3)"""
    try:
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text.strip()
        json_body = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig
        }
        res = requests.post(f"{API_HOST}{path}", headers=headers, data=json_body, timeout=15)
        return res.json()
    except: return {"error": 1}

def get_market_data():
    """ดึงราคาและคำนวณ EMA โดยเน้นความเสถียร (Public API)"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึงราคา Ticker
        t_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYM_MARKET}", headers=headers).json()
        price = 0
        if isinstance(t_res, dict): price = float(t_res.get(SYM_MARKET, {}).get('last', 0))
        elif isinstance(t_res, list) and len(t_res) > 0:
            for item in t_res:
                if item.get('symbol') == SYM_TRADE:
                    price = float(item.get('last', 0))

        # ดึงแท่งเทียน 15 นาที จำนวน 100 แท่ง
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

# --- 4. ลูปการทำงานหลัก (Main Loop) ---

holding, last_buy, last_report = False, 0, 0

send_line(
    f"🚀 บอทเริ่มระบบสมบูรณ์ (V3 Full Strategy)\n"
    f"📌 เหรียญ: {SYM_TRADE}\n"
    f"📊 กลยุทธ์: EMA 50 | กำไร: 2.55% | Cut: 2.0%"
)



while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            logging.info(f"Price: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            # 1. รายงานประจำชั่วโมง
            if time.time() - last_report >= 3600:
                send_line(f"📊 รายงานประจำชั่วโมง\nราคา: {price} THB\nEMA50: {ema_val:.2f}\nสถานะ: {trend}")
                last_report = time.time()

            # 2. เงื่อนไขการซื้อ (ราคาตัดขึ้นเหนือ EMA + ยังไม่มีเหรียญ)
            if not holding and trend == "UP":
                wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                thb = float(wallet.get('result', {}).get('THB', 0)) if isinstance(wallet, dict) else 0
                
                if thb >= 10:
                    order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {
                        "sym": SYM_TRADE, "amt": round(thb, 2), "rat": 0, "typ": "market"
                    })
                    if order.get('error') == 0:
                        holding, last_buy = True, price
                        send_line(f"✅ ซื้อสำเร็จที่ {price}\n(ราคาตัดขึ้นเหนือ EMA50)")

            # 3. เงื่อนไขการขาย (ถึงเป้ากำไร หรือ Stop Loss)
            elif holding:
                profit = (price - last_buy) / last_buy
                if profit >= 0.0255 or profit <= -0.020:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin_name = SYM_TRADE.split('_')[0]
                    coin_bal = float(wallet.get('result', {}).get(coin_name, 0)) if isinstance(wallet, dict) else 0

                    if coin_bal > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {
                            "sym": SYM_TRADE, "amt": coin_bal, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding = False
                            msg = "💰 ขายทำกำไร!" if profit > 0 else "🚫 ขายตัดขาดทุน"
                            send_line(f"{msg}\nกำไร: {profit*100:.2f}%\nราคาขาย: {price}")
        else:
            logging.warning(f"Connecting to Bitkub API for {SYM_MARKET}...")

    except Exception as e:
        logging.error(f"Error: {e}")
    time.sleep(30)
