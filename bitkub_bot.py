import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่า ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
HOST = "https://api.bitkub.com"

TARGET_PROFIT = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", 2.0))
STATE_FILE = "/tmp/bot_state.json" 
initial_equity = 1500.00 # ทุนเริ่มต้น

def save_state(action, buy_price, stage):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"last_action": action, "avg_price": buy_price, "stage": stage}, f)
    except: pass

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                d = json.load(f)
                return d.get("last_action", "sell"), d.get("avg_price", 0.0), d.get("stage", 0)
        except: pass
    return "sell", 0.0, 0

last_action, avg_price, current_stage = load_state()

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: print("Line Error")

# --- 3. ฟังก์ชัน API Bitkub V4 ---
def get_server_time():
    res = requests.get(f"{HOST}/api/v3/servertime")
    return res.text.strip()

def get_signature(ts, method, path, body_str):
    payload = ts + method + path + body_str
    return hmac.new(API_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

def get_wallet():
    path = "/api/v3/market/wallet"
    try:
        ts = get_server_time()
        body_str = json.dumps({}, separators=(',', ':'))
        sig = get_signature(ts, "POST", path, body_str)
        headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
        res = requests.post(f"{HOST}{path}", headers=headers, data=body_str, timeout=10).json()
        if res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
    except Exception as e: print(f"Wallet Exception: {e}")
    return 0.0, 0.0

def place_order(side, amount):
    path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
    try:
        ts = get_server_time()
        ticker_res = requests.get(f"{HOST}/api/v3/market/ticker").json()
        current_rat = next((float(i['last']) for i in ticker_res if i['symbol'] == "XRP_THB"), 0)

        body = {"sym": "XRP_THB", "amt": amount, "typ": "market", "rat": current_rat}
        body_str = json.dumps(body, separators=(',', ':'))
        sig = get_signature(ts, "POST", path, body_str)
        headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
        res = requests.post(f"{HOST}{path}", headers=headers, data=body_str, timeout=10).json()
        print(f"✅ Order Result: {res}")
        return res
    except Exception as e: return {"error": 999}

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        t_res = requests.get(f"{HOST}/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == "XRP_THB"), 0)
        c_url = f"{HOST}/tradingview/history?symbol=XRP_THB&resolution=15&from={int(time.time()) - 86400}&to={int(time.time())}"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        data_c = c_res.get('c', [])
        if price > 0 and len(data_c) >= 50:
            ema = sum(data_c[-50:]) / 50 
            return price, ema
    except: pass
    return None, None

def send_full_report(price, ema_val, current_stage, avg_price, pnl):
    thb_bal, xrp_bal = get_wallet()
    total_equity = thb_bal + (xrp_bal * price)
    total_profit_pct = ((total_equity - initial_equity) / initial_equity) * 100
    trend_icon = "🟢 ขาขึ้น" if price > ema_val else "🔴 ขาลง"
    
    report = (
        "📊 [Full Portfolio Report]\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 ราคา: {price:,.2f} | EMA: {ema_val:,.2f}\n"
        f"🧭 เทรนด์: {trend_icon}\n"
        "━━━━━━━━━━━━━━━\n"
        f"📦 สถานะ: ถือ {current_stage}/2 ไม้\n"
        f"📉 ต้นทุนเฉลี่ย: {avg_price:,.2f}\n"
        f"✨ P/L ปัจจุบัน: {pnl:+.2f}%\n"
        "━━━━━━━━━━━━━━━\n"
        f"🏦 พอร์ต: {total_equity:,.2f} THB\n"
        f"📈 กำไรสะสม: {total_profit_pct:+.2f}%\n"
        "━━━━━━━━━━━━━━━\n"
        f"💵 เงินสด: {thb_bal:,.2f} | 💎 เหรียญ: {xrp_bal:,.4f}"
    )
    send_line(report) # แสดงผลตามภาพที่คุณต้องการ

# --- 4. Main Loop ---
send_line("🤖 Bot Ready | 2-Stage Mode | Report Active")
last_report_time = 0

while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            pnl = ((price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0

            # --- BUY LOGIC (แบ่ง 2 ไม้) ---
            if price > ema_val:
                thb_bal, _ = get_wallet()
                
                # ไม้ที่ 1: เมื่อราคาเริ่มอยู่เหนือ EMA และยังไม่มีของ
                if current_stage == 0 and thb_bal > 20:
                    buy_amt = thb_bal * 0.50 # ใช้เงิน 50%
                    res = place_order("buy", buy_amt)
                    if res.get('error') == 0:
                        avg_price, current_stage, last_action = price, 1, "buy"
                        save_state(last_action, avg_price, current_stage)
                        send_line(f"📦 [BUY ไม้ 1/2]\nราคา: {price:,.2f}")
                        send_full_report(price, ema_val, current_stage, avg_price, pnl)

                # ไม้ที่ 2: เมื่อราคาขึ้นไปแล้ว 0.5% (ยืนยันเทรนด์) และเงินสดยังเหลือ
                elif current_stage == 1 and pnl >= 0.5 and thb_bal > 10:
                    res = place_order("buy", thb_bal * 0.95) # ซื้อส่วนที่เหลือ
                    if res.get('error') == 0:
                        # คำนวณต้นทุนเฉลี่ยใหม่คร่าวๆ
                        avg_price = (avg_price + price) / 2
                        current_stage = 2
                        save_state(last_action, avg_price, current_stage)
                        send_line(f"📦 [BUY ไม้ 2/2]\nราคา: {price:,.2f}\nต้นทุนเฉลี่ยใหม่: {avg_price:,.2f}")
                        send_full_report(price, ema_val, current_stage, avg_price, pnl)

            # --- SELL LOGIC (เน้น Stop Loss 2%) ---
            if last_action == "buy":
                reason = ""
                trend_confirm = ema_val * 0.997 # Buffer กันขายเร็วเกินไป

                if pnl <= -STOP_LOSS: 
                    reason = f"Stop Loss ({pnl:.2f}%)" # จุด SL 2% ที่คุณตั้งไว้
                elif pnl >= TARGET_PROFIT: 
                    reason = f"Take Profit ({pnl:.2f}%)"
                elif price < trend_confirm: 
                    reason = "Trend Down (EMA Confirmed)"

                if reason:
                    _, xrp_bal = get_wallet()
                    if xrp_bal > 0.1:
                        res = place_order("sell", xrp_bal)
                        if res.get('error') == 0:
                            send_line(f"🔴 [SELL ALL SUCCESS]\nเหตุผล: {reason}\nกำไร/ขาดทุน: {pnl:.2f}%")
                            last_action, avg_price, current_stage = "sell", 0.0, 0
                            save_state(last_action, avg_price, current_stage)
                            send_full_report(price, ema_val, current_stage, avg_price, pnl)

            # --- รายงานพอร์ตทุก 1 ชม. ---
            if time.time() - last_report_time >= 10800:
                send_full_report(price, ema_val, current_stage, avg_price, pnl)
                last_report_time = time.time()

    except Exception as e: print(f"Loop Error: {e}")
    time.sleep(30)
