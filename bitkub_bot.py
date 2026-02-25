import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Health Check) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Trading Pro - Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่าตัวแปร (Configuration) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
HOST = "https://api.bitkub.com"

TARGET_PROFIT = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", 2.0))
STATE_FILE = "bot_state.json"
initial_equity = 1510.59  # ทุนเริ่มต้นอ้างอิงจากยอดเงินล่าสุดของคุณ

def save_state(action, buy_price, stage):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_action": action, "avg_price": buy_price, "stage": stage}, f)

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
    except: pass

# --- 3. ฟังก์ชัน API Bitkub (แก้ไข Error 10: 'rat' field is required) ---
def get_signature(ts, method, path, body_str):
    payload = ts + method + path + body_str
    return hmac.new(API_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

def get_wallet():
    path = "/api/v3/market/wallet"
    try:
        ts = requests.get(f"{HOST}/api/v3/servertime").text.strip()
        body_str = json.dumps({}, separators=(',', ':'))
        sig = get_signature(ts, "POST", path, body_str)
        headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
        res = requests.post(f"{HOST}{path}", headers=headers, data=body_str, timeout=10).json()
        if res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
    except: pass
    return 0.0, 0.0

def place_order(side, amount):
    path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
    try:
        # แก้ Error 10: ดึงราคาปัจจุบันมาใส่ในช่อง rat เสมอ
        t_res = requests.get(f"{HOST}/api/v3/market/ticker?sym=XRP_THB").json()
        current_rate = t_res['XRP_THB']['last']
        
        ts = requests.get(f"{HOST}/api/v3/servertime").text.strip()
        body = {
            "sym": "xrp_thb",
            "amt": int(amount) if side == "buy" else amount, # ซื้อปัดเป็นจำนวนเต็มตาม buy.py
            "rat": current_rate, # ใส่ราคาปัจจุบันเพื่อแก้ error
            "typ": "limit"       # ใช้ limit ตามที่รันใน buy.py สำเร็จ
        }
        body_str = json.dumps(body, separators=(',', ':'))
        sig = get_signature(ts, "POST", path, body_str)
        headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
        res = requests.post(f"{HOST}{path}", headers=headers, data=body_str, timeout=10).json()
        print(f"DEBUG: {side.upper()} Result -> {res}")
        return res
    except Exception as e:
        print(f"DEBUG Error: {e}")
        return {"error": 999}

def get_market_data():
    try:
        t_res = requests.get(f"{HOST}/api/v3/market/ticker?sym=XRP_THB", timeout=10).json()
        price = t_res['XRP_THB']['last']
        c_url = f"{HOST}/tradingview/history?symbol=XRP_THB&resolution=15&from={int(time.time()) - 86400}&to={int(time.time())}"
        c_res = requests.get(c_url, timeout=10).json()
        data_c = c_res.get('c', [])
        if price > 0 and len(data_c) >= 50:
            ema = data_c[0]
            m = 2 / (50 + 1)
            for p in data_c: ema = (p - ema) * m + ema
            return price, ema
    except: pass
    return None, None

# --- 4. ลูปการทำงานและรายงานแบบ Full Report ---
last_report_time = 0
send_line(f"🤖 [Bot Ready]\nสถานะปัจจุบัน: {last_action}\nถือครอง: {current_stage}/2 ไม้")

while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            trend_icon = "🟢 ขาขึ้น" if price > ema_val else "🔴 ขาลง"
            pnl = ((price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0

            # --- กลยุทธ์การซื้อ (Buy Logic) ---
            if price > ema_val and last_action == "sell":
                thb_bal, _ = get_wallet()
                if thb_bal > 20:
                    res = place_order("buy", thb_bal)
                    if res.get('error') == 0:
                        avg_price, current_stage, last_action = price, 1, "buy"
                        save_state(last_action, avg_price, current_stage)
                        send_line(f"📦 [BUY SUCCESS]\nราคา: {price:,.2f} THB")

            # --- กลยุทธ์การขาย (Sell Logic) ---
            if last_action == "buy":
                if price < (ema_val * 0.999) or pnl >= TARGET_PROFIT or pnl <= -STOP_LOSS:
                    _, xrp_bal = get_wallet()
                    if xrp_bal > 0.1:
                        res = place_order("sell", xrp_bal)
                        if res.get('error') == 0:
                            send_line(f"🔴 [SELL SUCCESS]\nราคา: {price:,.2f}\nกำไร/ขาดทุน: {pnl:+.2f}%")
                            last_action, avg_price, current_stage = "sell", 0.0, 0
                            save_state(last_action, avg_price, current_stage)

            # --- รายงานแบบ Full Report (ส่งทุก 3 ชม.) ---
            if time.time() - last_report_time >= 10800:
                thb_bal, xrp_bal = get_wallet()
                total_equity = thb_bal + (xrp_bal * price)
                total_profit_pct = ((total_equity - initial_equity) / initial_equity) * 100
                
                report = (
                    "📊 [Full Portfolio Report]\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💰 ราคา: {price:,.2f} | EMA: {ema_val:,.2f}\n"
                    f"🧭 เทรนด์ปัจจุบัน: {trend_icon}\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"📦 สถานะ: ถือ {current_stage}/2 ไม้\n"
                    f"📉 ต้นทุนเฉลี่ย: {avg_price:,.2f}\n"
                    f"✨ P/L ปัจจุบัน: {pnl:+.2f}%\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"🏛️ พอร์ต: {total_equity:,.2f} THB\n"
                    f"📈 กำไรสะสมทั้งหมด: {total_profit_pct:+.2f}%\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💵 เงินสด: {thb_bal:,.2f} THB\n"
                    f"💎 เหรียญ: {xrp_bal:,.4f} XRP"
                )
                send_line(report)
                last_report_time = time.time()

    except Exception as e:
        print(f"Loop Error: {e}")
    time.sleep(30)
