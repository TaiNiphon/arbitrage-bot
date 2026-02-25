import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Railway Health Check) ---
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
initial_equity = 1500.00

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

# --- 3. ฟังก์ชัน API มาตรฐาน V4 ---
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
        print(f"✅ Order Attempt ({side}): {res}")
        return res
    except Exception as e: 
        print(f"❌ Order Exception: {e}")
        return {"error": 999}

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

# --- 4. Main Loop ---
send_line("🤖 Bot Started | All-in Mode | StopLoss Active")
last_report_time = 0

while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            pnl = ((price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0

            # --- BUY LOGIC (All-in ไม่แบ่งไม้) ---
            if price > ema_val and last_action == "sell":
                thb_bal, _ = get_wallet()
                if thb_bal > 15:
                    buy_amt = thb_bal * 0.95 # ใช้เงินทั้งหมด 95% ในครั้งเดียว
                    res = place_order("buy", buy_amt)
                    if res.get('error') == 0:
                        avg_price, current_stage, last_action = price, 1, "buy"
                        save_state(last_action, avg_price, current_stage)
                        send_line(f"🟢 BUY ALL-IN SUCCESS\nราคา: {price}")

            # --- SELL LOGIC (TP, SL, Trend) ---
            if last_action == "buy":
                reason = ""
                # ให้ราคาต่ำกว่า EMA อย่างน้อย 0.3% ถึงจะถือว่าเป็นขาลงจริง (ลดการตกใจขาย)
                trend_confirm = ema_val * 0.997 

                if pnl <= -STOP_LOSS: 
                    reason = f"Stop Loss ({pnl:.2f}%)"
                elif pnl >= TARGET_PROFIT: 
                    reason = f"Take Profit ({pnl:.2f}%)"
                elif price < trend_confirm: 
                    reason = "Trend Down (EMA Confirmed)"

                if reason:
                    _, xrp_bal = get_wallet()
                    if xrp_bal > 0.1:
                        res = place_order("sell", xrp_bal)
                        if res.get('error') == 0:
                            send_line(f"🔴 SELL SUCCESS\nเหตุผล: {reason}\nกำไร/ขาดทุน: {pnl:.2f}%")
                            last_action, avg_price, current_stage = "sell", 0.0, 0
                            save_state(last_action, avg_price, current_stage)

    except Exception as e: print(f"Loop Error: {e}")
    time.sleep(30)
