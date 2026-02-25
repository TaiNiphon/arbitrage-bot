import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Health Check สำหรับ Railway) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Trading Pro - Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร (ดึงจาก Railway Variables) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
SYMBOL = os.getenv("SYMBOL", "XRP_THB") # เช่น XRP_THB
SYMBOL_STR = os.getenv("SYMBOL_STR", "XRP") # สำหรับดึงยอดใน Wallet เช่น XRP
HOST = "https://api.bitkub.com"

TARGET_PROFIT = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", 2.0))
STATE_FILE = "bot_state.json"
initial_equity = 1500.00 

# --- 3. ฟังก์ชันพื้นฐาน ---
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

# --- 4. ฟังก์ชัน API Bitkub V3 (ตามรูปที่ 1) ---
def bitkub_v3_request(method, path, body={}):
    try:
        # 1. ดึง Server Time
        ts = requests.get(f"{HOST}/api/v3/servertime").text.strip()
        json_body = json.dumps(body, separators=(',', ':'))
        
        # 2. สร้าง Signature
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY,
            'X-BTK-TIMESTAMP': ts,
            'X-BTK-SIGN': sig
        }
        
        url = f"{HOST}{path}"
        if method == "POST":
            res = requests.post(url, headers=headers, data=json_body, timeout=15)
        else:
            res = requests.get(url, headers=headers, timeout=15)
        return res.json()
    except Exception as e:
        return {"error": 999, "message": str(e)}

def get_wallet():
    res = bitkub_v3_request("POST", "/api/v3/market/wallet")
    if res.get('error') == 0:
        results = res.get('result', {})
        return float(results.get('THB', 0)), float(results.get(SYMBOL_STR, 0))
    return 0.0, 0.0

def place_order(side, amount):
    # side: 'buy' หรือ 'sell'
    path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
    body = {
        "sym": SYMBOL.lower(), 
        "amt": amount, 
        "rat": 0, # 0 คือราคาตลาด (Market Order)
        "typ": "market"
    }
    return bitkub_v3_request("POST", path, body)

def get_market_price():
    try:
        res = requests.get(f"{HOST}/api/v3/market/ticker?sym={SYMBOL}").json()
        if SYMBOL in res:
            return float(res[SYMBOL]['last'])
    except: pass
    return None

# --- 5. ลูปการทำงานหลัก ---
send_line(f"🤖 [Bot Started]\nเหรียญ: {SYMBOL}\nสถานะ: {last_action}\nไม้: {current_stage}/2")

while True:
    try:
        price = get_market_price()
        if price:
            # คำนวณกำไร/ขาดทุน (PNL)
            pnl = ((price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0

            # --- ตรรกะซื้อ (ตัวอย่าง: ซื้อเมื่อไม่มีของ) ---
            if last_action == "sell":
                thb_bal, _ = get_wallet()
                if thb_bal > 100: # มีเงินมากกว่า 100 บาท
                    buy_amount = thb_bal * 0.95 # ซื้อ 95% ของพอร์ต (เผื่อค่าธรรมเนียม)
                    res = place_order("buy", buy_amount)
                    if res.get('error') == 0:
                        avg_price = price
                        last_action = "buy"
                        current_stage = 1
                        save_state(last_action, avg_price, current_stage)
                        send_line(f"✅ [BUY SUCCESS]\nราคา: {price:,.2f}\nเงินที่ใช้: {buy_amount:,.2f} THB")

            # --- ตรรกะขาย (Take Profit / Stop Loss) ---
            elif last_action == "buy":
                is_sell = False
                reason = ""

                if pnl >= TARGET_PROFIT:
                    is_sell = True
                    reason = f"Take Profit ({pnl:+.2f}%)"
                elif pnl <= -STOP_LOSS:
                    is_sell = True
                    reason = f"Stop Loss ({pnl:+.2f}%)"

                if is_sell:
                    _, coin_bal = get_wallet()
                    if coin_bal > 0:
                        res = place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            send_line(f"🔴 [SELL SUCCESS]\nเหตุผล: {reason}\nราคาขาย: {price:,.2f}\nกำไร: {pnl:+.2f}%")
                            last_action, avg_price, current_stage = "sell", 0.0, 0
                            save_state(last_action, avg_price, current_stage)

    except Exception as e:
        print(f"Main Loop Error: {e}")
    
    time.sleep(20) # พัก 20 วินาทีต่อรอบ
