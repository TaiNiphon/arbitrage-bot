import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. Health Check สำหรับ Railway ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่า (เช็ค Variables ใน Railway ให้ตรงตามนี้) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
HOST = "https://api.bitkub.com"

# ทุนเริ่มต้นและเกณฑ์การเทรด
INITIAL_EQUITY = 1510.59 
TARGET_PROFIT = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", 2.0))
STATE_FILE = "bot_state.json"

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

# --- 3. ระบบ API V3 (อ้างอิงจากชุด buy2.py ที่คุณรันผ่าน) ---
def gen_sign(payload):
    return hmac.new(API_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

def get_server_time():
    return requests.get(HOST + "/api/v3/servertime").text.strip()

def get_wallet():
    path = "/api/v3/market/wallet"
    ts = get_server_time()
    body_str = json.dumps({}, separators=(',', ':'))
    sig = gen_sign(ts + "POST" + path + body_str)
    headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
    try:
        res = requests.post(HOST + path, headers=headers, data=body_str).json()
        if res.get('error') == 0:
            result = res.get('result', {})
            return float(result.get('THB', 0)), float(result.get('XRP', 0))
    except: pass
    return 0.0, 0.0

def place_order(side, amount, rate):
    path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
    ts = get_server_time()
    # ใช้ "THB_XRP" และระบุราคา 'rat' ตามชุดที่ผ่าน
    body = {
        "sym": "THB_XRP",
        "amt": float(amount),
        "rat": float(rate),
        "typ": "limit"
    }
    body_str = json.dumps(body, separators=(',', ':'))
    sig = gen_sign(ts + "POST" + path + body_str)
    headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
    return requests.post(HOST + path, headers=headers, data=body_str).json()

def get_market_data():
    try:
        # ใช้ V3 Ticker
        t_res = requests.get(HOST + "/api/v3/market/ticker?sym=THB_XRP").json()
        price = float(t_res['THB_XRP']['last'])
        # EMA 15 นาที
        c_url = f"{HOST}/tradingview/history?symbol=THB_XRP&resolution=15&from={int(time.time()) - 86400}&to={int(time.time())}"
        c_res = requests.get(c_url).json()
        closes = c_res.get('c', [])
        ema = sum(closes[-50:]) / 50 if len(closes) >= 50 else price
        return price, ema
    except: return None, None

# --- 4. Main Loop & รายงานแบบสวยงาม ---
last_report_time = 0
send_line("🤖 [Bot System Online]\nใช้ระบบ V3 (Limit Mode) ตามชุดทดสอบที่ผ่านแล้ว!")

while True:
    try:
        price, ema_val = get_market_data()
        if price:
            thb_bal, xrp_bal = get_wallet()
            trend_icon = "🟢 ขาขึ้น" if price > ema_val else "🔴 ขาลง"
            pnl = ((price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0

            # กลยุทธ์ซื้อ 2 ไม้
            if price > ema_val:
                if current_stage == 0 and thb_bal > 10:
                    res = place_order("buy", thb_bal * 0.5, price)
                    if res.get('error') == 0:
                        avg_price, last_action, current_stage = price, "buy", 1
                        save_state(last_action, avg_price, current_stage)
                        send_line(f"✅ [BUY SUCCESS ไม้ 1]\nราคา: {price:,.2f}")

                elif current_stage == 1 and pnl >= 0.5 and thb_bal > 10:
                    res = place_order("buy", thb_bal, price)
                    if res.get('error') == 0:
                        avg_price = (avg_price + price) / 2
                        current_stage = 2
                        save_state(last_action, avg_price, current_stage)
                        send_line(f"✅ [BUY SUCCESS ไม้ 2]\nราคาเฉลี่ย: {avg_price:,.2f}")

            # กลยุทธ์ขาย
            if last_action == "buy":
                if price < (ema_val * 0.998) or pnl >= TARGET_PROFIT or pnl <= -STOP_LOSS:
                    if xrp_bal > 0.1:
                        res = place_order("sell", xrp_bal, price)
                        if res.get('error') == 0:
                            send_line(f"🔴 [SELL ALL SUCCESS]\nกำไรรวม: {pnl:+.2f}%")
                            last_action, avg_price, current_stage = "sell", 0.0, 0
                            save_state(last_action, avg_price, current_stage)

            # --- [Full Portfolio Report] รายงานฉบับเต็ม ---
            if time.time() - last_report_time >= 10800:
                total_equity = thb_bal + (xrp_bal * price)
                total_profit_pct = ((total_equity - INITIAL_EQUITY) / INITIAL_EQUITY) * 100
                
                report = f"""📊 [Full Portfolio Report]
━━━━━━━━━━━━━━━
💰 ราคา: {price:,.2f} | EMA: {ema_val:,.2f}
🧭 เทรนด์: {trend_icon}
━━━━━━━━━━━━━━━
📦 สถานะ: ถือ {current_stage}/2 ไม้
📉 ต้นทุนเฉลี่ย: {avg_price:,.2f}
✨ P/L ปัจจุบัน: {pnl:+.2f}%
━━━━━━━━━━━━━━━
🏛️ พอร์ต: {total_equity:,.2f} THB
📈 กำไรสะสม: {total_profit_pct:+.2f}%
━━━━━━━━━━━━━━━
💵 เงินสด: {thb_bal:,.2f} | 💎 เหรียญ: {xrp_bal:,.4f}"""
                
                send_line(report)
                last_report_time = time.time()

    except Exception as e: print(f"Error: {e}")
    time.sleep(30)
