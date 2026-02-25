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

# --- 2. การตั้งค่า (อ้างอิงจากตัวที่ทดสอบผ่าน) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
HOST = "https://api.bitkub.com"
INITIAL_EQUITY = 1510.59  # ทุนเริ่มต้นของคุณ

def gen_sign(payload):
    return hmac.new(API_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

def get_server_time():
    return requests.get(HOST + "/api/v3/servertime").text.strip()

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

# --- 3. ฟังก์ชัน API V3 (ถอดแบบจากชุดที่ทดสอบผ่าน) ---
def get_wallet():
    path = "/api/v3/market/wallet"
    ts = get_server_time()
    body = {}
    js = json.dumps(body, separators=(',', ':'))
    sig = gen_sign(ts + "POST" + path + js)
    headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
    res = requests.post(HOST + path, headers=headers, data=js).json()
    if res.get('error') == 0:
        return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
    return 0.0, 0.0

def place_order(side, amount, rate):
    path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
    ts = get_server_time()
    # ใช้ "THB_XRP" ตามที่คุณทดสอบผ่าน
    body = {
        "sym": "THB_XRP",
        "amt": float(amount),
        "rat": float(rate),
        "typ": "limit"
    }
    js = json.dumps(body, separators=(',', ':'))
    sig = gen_sign(ts + "POST" + path + js)
    headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
    res = requests.post(HOST + path, headers=headers, data=js).json()
    return res

def get_market_data():
    try:
        # Ticker V3
        t_res = requests.get(HOST + "/api/v3/market/ticker?sym=THB_XRP").json()
        price = float(t_res['THB_XRP']['last'])
        # History สำหรับ EMA 50
        c_url = f"{HOST}/tradingview/history?symbol=THB_XRP&resolution=15&from={int(time.time()) - 86400}&to={int(time.time())}"
        c_res = requests.get(c_url).json()
        closes = c_res.get('c', [])
        if len(closes) >= 50:
            ema = sum(closes[-50:]) / 50
            return price, ema
    except: pass
    return None, None

# --- 4. ลูปการทำงานและรายงานแบบข้อมูลครบๆ ---
last_action = "sell"
avg_price = 0.0
last_report_time = 0

send_line("🤖 บอท V3 (ชุดทดสอบผ่าน) เริ่มทำงานแล้ว!")

while True:
    try:
        price, ema_val = get_market_data()
        if price and ema_val:
            thb_bal, xrp_bal = get_wallet()
            pnl = ((price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0
            
            # --- ตรรกะซื้อ ---
            if price > ema_val and last_action == "sell" and thb_bal >= 10:
                res = place_order("buy", thb_bal, price)
                if res.get('error') == 0:
                    last_action, avg_price = "buy", price
                    send_line(f"✅ [BUY SUCCESS]\nราคา: {price:,.2f} THB")

            # --- ตรรกะขาย ---
            if last_action == "buy":
                if price < (ema_val * 0.99) or pnl >= 3.0 or pnl <= -2.0:
                    if xrp_bal > 0.1:
                        res = place_order("sell", xrp_bal, price)
                        if res.get('error') == 0:
                            send_line(f"🔴 [SELL SUCCESS]\nราคา: {price:,.2f}\nกำไร: {pnl:+.2f}%")
                            last_action, avg_price = "sell", 0.0

            # --- รายงานแบบ Full Report (ข้อมูลครบๆ) ---
            if time.time() - last_report_time >= 10800: # ทุก 3 ชม.
                total_equity = thb_bal + (xrp_bal * price)
                profit_amt = total_equity - INITIAL_EQUITY
                profit_pct = (profit_amt / INITIAL_EQUITY) * 100
                
                report = (
                    "📊 [Full Portfolio Report]\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💰 ราคา: {price:,.2f} | EMA: {ema_val:,.2f}\n"
                    f"🧭 เทรนด์: {'🟢 ขาขึ้น' if price > ema_val else '🔴 ขาลง'}\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"📦 สถานะ: {'ถือเหรียญ' if last_action == 'buy' else 'รอสัญญาณ'}\n"
                    f"📉 ต้นทุน: {avg_price:,.2f} | P/L: {pnl:+.2f}%\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"🏛️ พอร์ต: {total_equity:,.2f} THB\n"
                    f"📈 กำไรสะสม: {profit_pct:+.2f}% ({profit_amt:+.2f})\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💵 เงินสด: {thb_bal:,.2f} THB\n"
                    f"💎 เหรียญ: {xrp_bal:,.4f} XRP"
                )
                send_line(report)
                last_report_time = time.time()

    except Exception as e:
        print(f"Error: {e}")
    time.sleep(30)
