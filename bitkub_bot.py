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

# --- Config: ดึงค่าจาก Variables ของคุณ ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")

# ระบบจัดการชื่อเหรียญอัตโนมัติ (ไม่ต้องแก้ใน Dashboard)
RAW_SYMBOL = os.getenv("SYMBOL", "XRP_THB").upper() # รับค่า XRP_THB
SYMBOL_TRADE = RAW_SYMBOL                           # ใช้ XRP_THB ซื้อขาย
# สลับเป็น THB_XRP เพื่อดึงราคา
SYMBOL_MARKET = f"THB_{RAW_SYMBOL.split('_')[0]}" 

EMA_PERIOD = 50
TIMEFRAME = "15"
PROFIT_TARGET = 0.0255 # 2.55%
STOP_LOSS = 0.020      # 2.0%
API_HOST = "https://api.bitkub.com"

# --- Functions ---
def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

def bitkub_v3_auth(method, path, body={}):
    try:
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text.strip()
        body_str = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + body_str
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        return requests.post(f"{API_HOST}{path}", headers=headers, data=body_str, timeout=15).json()
    except: return {"error": 1}

def get_market_data():
    try:
        # ใช้ SYMBOL_MARKET (THB_XRP) ดึงราคา
        t_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL_MARKET}").json()
        price = 0
        if isinstance(t_res, list) and len(t_res) > 0: price = float(t_res[0].get('last', 0))
        elif isinstance(t_res, dict): price = float(t_res.get(SYMBOL_MARKET, {}).get('last', 0))

        # ดึงแท่งเทียน
        c_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYMBOL_MARKET}&p={TIMEFRAME}&l=100").json()
        data = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if price > 0 and len(data) >= EMA_PERIOD:
            closes = [float(d['c']) for d in data]
            ema = closes[0]
            m = 2 / (EMA_PERIOD + 1)
            for p in closes: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except: return None, None

# --- Main Loop ---
holding, last_buy, last_report = False, 0, 0

# รายงานเริ่มต้นพร้อมรายละเอียดครบถ้วน
welcome_msg = (
    f"🚀 บอทเริ่มระบบสมบูรณ์ (V3 Final)\n"
    f"📌 เฝ้าเหรียญ: {SYMBOL_TRADE}\n"
    f"📊 กลยุทธ์: EMA {EMA_PERIOD} (Trend Follow)\n"
    f"💰 เป้ากำไร: {PROFIT_TARGET*100}%\n"
    f"🚫 Stop Loss: {STOP_LOSS*100}%"
)
send_line(welcome_msg)
logging.info(f"--- BOT STARTED: {SYMBOL_TRADE} ---")


while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            logging.info(f"Price: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

            # รายงานประจำชั่วโมงพร้อมรายละเอียด
            if time.time() - last_report >= 3600:
                send_line(f"📊 รายงานประจำชั่วโมง\nราคา: {price} THB\nEMA50: {ema_val:.2f}\nเทรนด์: {trend}\nสถานะ: {'ถือเหรียญ' if holding else 'รอสัญญาณ'}")
                last_report = time.time()

            if not holding and trend == "UP":
                wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                bal = float(wallet.get('result', {}).get('THB', 0)) if isinstance(wallet, dict) else 0
                if bal >= 10:
                    order = bitkub_v3_auth("POST", "/api/v3/market/place-bid", {"sym": SYMBOL_TRADE, "amt": round(bal, 2), "rat": 0, "typ": "market"})
                    if order.get('error') == 0:
                        holding, last_buy = True, price
                        send_line(f"✅ ซื้อสำเร็จที่ {price} THB\n(ราคาตัดขึ้นเหนือ EMA50)")

            elif holding:
                profit = (price - last_buy) / last_buy
                if profit >= PROFIT_TARGET or profit <= -STOP_LOSS:
                    wallet = bitkub_v3_auth("POST", "/api/v3/market/wallet")
                    coin = float(wallet.get('result', {}).get(SYMBOL_TRADE.split('_')[0], 0)) if isinstance(wallet, dict) else 0
                    if coin > 0:
                        order = bitkub_v3_auth("POST", "/api/v3/market/place-ask", {"sym": SYMBOL_TRADE, "amt": coin, "rat": 0, "typ": "market"})
                        if order.get('error') == 0:
                            holding = False
                            msg = "💰 ขายทำกำไรสำเร็จ!" if profit > 0 else "🚫 ขายตัดขาดทุน (Stop Loss)"
                            send_line(f"{msg}\nกำไร: {profit*100:.2f}%\nราคาขาย: {price} THB")
        else:
            logging.warning(f"Connecting to Bitkub API for {SYMBOL_MARKET}...")
    except Exception as e:
        logging.error(f"Error: {e}")
    time.sleep(30)
