import os, requests, time, hmac, hashlib, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. การตั้งค่าระบบ (Logging & Server) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def run_dummy_server():
    """สร้าง Server จำลองเพื่อให้ Railway ไม่ตัดการทำงาน"""
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Trading Bot is Active")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร (Configuration) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# ชื่อเหรียญแยกตามการใช้งาน
SYMBOL_TRADE = "XRP_THB"   # สำหรับซื้อขาย (API V3)
SYMBOL_TICKER = "THB_XRP"  # สำหรับดึงราคาและแท่งเทียน

# กลยุทธ์
PROFIT_TARGET = 0.0255     # กำไร 2.55%
STOP_LOSS = 0.020          # ขาดทุน 2.0%
EMA_PERIOD = 50            
TIMEFRAME = "15"           
API_HOST = "https://api.bitkub.com"

# --- 3. ฟังก์ชันเสริม (Helper Functions) ---

def send_line(text):
    """ส่งข้อความเข้า LINE Notify/Push"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try: requests.post(url, headers=headers, json=payload, timeout=10)
    except: pass

def bitkub_auth_v3(method, path, body={}):
    """ระบบยืนยันตัวตน Bitkub API V3"""
    try:
        ts = requests.get(f"{API_HOST}/api/v3/servertime").text.strip()
        json_body = json.dumps(body, separators=(',', ':'))
        # สร้าง Signature ตามกฎ V3
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY,
            'X-BTK-TIMESTAMP': ts,
            'X-BTK-SIGN': sig
        }
        res = requests.post(f"{API_HOST}{path}", headers=headers, data=json_body, timeout=15)
        return res.json()
    except Exception as e:
        logging.error(f"Auth Error: {e}")
        return {"error": 1}

def get_market_data():
    """ดึงราคาและคำนวณ EMA โดยรองรับข้อมูลทั้งแบบ List และ Dict"""
    try:
        # 1. ดึงราคาล่าสุด
        t_res = requests.get(f"{API_HOST}/api/v3/market/ticker?sym={SYMBOL_TICKER}").json()
        current_price = 0
        if isinstance(t_res, dict):
            current_price = float(t_res.get(SYMBOL_TICKER, {}).get('last', 0))
        elif isinstance(t_res, list) and len(t_res) > 0:
            current_price = float(t_res[0].get('last', 0))

        # 2. ดึงแท่งเทียนมาคำนวณ EMA
        c_res = requests.get(f"{API_HOST}/api/v3/market/candles?sym={SYMBOL_TICKER}&p={TIMEFRAME}&l=100").json()
        data_list = c_res if isinstance(c_res, list) else c_res.get('result', [])

        if len(data_list) >= EMA_PERIOD:
            closes = [float(c['c']) for c in data_list]
            # คำนวณ EMA แบบ Exponential
            ema = closes[0]
            multiplier = 2 / (EMA_PERIOD + 1)
            for price in closes:
                ema = (price - ema) * multiplier + ema
            return current_price, ema
        
        return None, None
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
        return None, None

# --- 4. ลูปการทำงานหลัก (Main Loop) ---

holding_token = False
last_buy_price = 0
last_report_time = 0



logging.info(f"--- BOT STARTING: {SYMBOL_TRADE} ---")
send_line(f"🚀 บอทเริ่มระบบสมบูรณ์\nเฝ้าเหรียญ: {SYMBOL_TRADE}\nกลยุทธ์: EMA {EMA_PERIOD}")

while True:
    try:
        price, ema_val = get_market_data()
        
        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            logging.info(f"Price: {price} | EMA: {ema_val:.2f} | Trend: {trend}")

            # รายงานสถานะทุก 1 ชม.
            if time.time() - last_report_time >= 3600:
                send_line(f"📊 รายงานประจำชั่วโมง\nราคา: {price} THB\nEMA50: {ema_val:.2f}\nเทรนด์: {trend}\nสถานะ: {'ถือเหรียญ' if holding_token else 'รอสัญญาณ'}")
                last_report_time = time.time()

            # เงื่อนไขการซื้อ (ราคาตัดขึ้น + ยังไม่มีเหรียญ)
            if not holding_token and trend == "UP":
                wallet = bitkub_auth_v3("POST", "/api/v3/market/wallet")
                # เช็คยอดเงินบาท
                thb_bal = float(wallet.get('result', {}).get('THB', 0)) if isinstance(wallet, dict) else 0
                
                if thb_bal >= 10: # ขั้นต่ำ 10 บาท
                    order = bitkub_auth_v3("POST", "/api/v3/market/place-bid", {
                        "sym": SYMBOL_TRADE, "amt": round(thb_bal, 2), "rat": 0, "typ": "market"
                    })
                    if order.get('error') == 0:
                        holding_token = True
                        last_buy_price = price
                        send_line(f"✅ ซื้อ {SYMBOL_TRADE} สำเร็จ\nราคา: {price} THB")

            # เงื่อนไขการขาย (มีเหรียญ + ถึงเป้ากำไร หรือ Stop Loss)
            elif holding_token:
                profit = (price - last_buy_price) / last_buy_price
                if profit >= PROFIT_TARGET or profit <= -STOP_LOSS:
                    wallet = bitkub_auth_v3("POST", "/api/v3/market/wallet")
                    # เช็คยอดเหรียญ (ดึงชื่อเหรียญ เช่น XRP จาก XRP_THB)
                    coin_name = SYMBOL_TRADE.split('_')[0]
                    coin_bal = float(wallet.get('result', {}).get(coin_name, 0)) if isinstance(wallet, dict) else 0

                    if coin_bal > 0:
                        order = bitkub_auth_v3("POST", "/api/v3/market/place-ask", {
                            "sym": SYMBOL_TRADE, "amt": coin_bal, "rat": 0, "typ": "market"
                        })
                        if order.get('error') == 0:
                            holding_token = False
                            msg = "💰 ขายทำกำไร!" if profit > 0 else "🚫 ขายตัดขาดทุน (Stop Loss)"
                            send_line(f"{msg}\nกำไร: {profit*100:.2f}%\nราคาขาย: {price} THB")

    except Exception as e:
        logging.error(f"Loop Error: {e}")

    time.sleep(30) # พัก 30 วินาทีก่อนเช็คใหม่
