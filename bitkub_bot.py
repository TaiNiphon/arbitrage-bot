import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Dummy Server) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Trading Pro Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. ตั้งค่าตัวแปร (Environmental Variables) ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
HOST = "https://api.bitkub.com"

# มูลค่าพอร์ตเริ่มต้นสำหรับคำนวณกำไรสะสม
initial_equity = 497.29 

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

# --- 3. ฟังก์ชัน API ---
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
        ts = requests.get(f"{HOST}/api/v3/servertime").text.strip()
        body = {"sym": "THB_XRP", "amt": amount, "typ": "market"}
        body_str = json.dumps(body, separators=(',', ':'))
        sig = get_signature(ts, "POST", path, body_str)
        headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
        res = requests.post(f"{HOST}{path}", headers=headers, data=body_str, timeout=10).json()
        return res
    except: return {"error": 999}

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        t_res = requests.get(f"{HOST}/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == "XRP_THB"), 0)
        c_url = f"{HOST}/tradingview/history?symbol=XRP_THB&resolution=15&from={int(time.time()) - 86400}&to={int(time.time())}"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        data_c = c_res.get('c', [])
        if price > 0 and len(data_c) >= 50:
            ema = data_c[0]
            m = 2 / (50 + 1)
            for p in data_c: ema = (p - ema) * m + ema
            return price, ema
    except: pass
    return None, None

# --- 4. ลูปหลัก ---
last_report = 0
last_action = ""

# แจ้งเตือนเมื่อบอทเริ่มทำงาน
send_line("🚀 [Bot Started]\nบอทเริ่มทำงานแล้ว!\n- รายงานสรุปพอร์ตทุก 3 ชม.\n- แจ้งเตือน ซื้อ-ขาย ทันที")

while True:
    try:
        price, ema_val = get_market_data()

        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"

            # --- ตรรกะซื้อ (Buy) ---
            if trend == "UP" and last_action != "buy":
                thb_bal, xrp_bal = get_wallet()
                if thb_bal > 10:
                    res = place_order("buy", thb_bal)
                    if res.get('error') == 0:
                        # แจ้งเตือนซื้อทันที
                        send_line(f"🟢 [BUY ORDER SUCCESS]\nซี้อ XRP ที่ราคา: {price:,.2f} บาท\nยอดเงินที่ใช้: {thb_bal:,.2f} THB")
                        last_action = "buy"

            # --- ตรรกะขาย (Sell) ---
            elif trend == "DOWN" and last_action != "sell":
                thb_bal, xrp_bal = get_wallet()
                if xrp_bal > 0.1:
                    res = place_order("sell", xrp_bal)
                    if res.get('error') == 0:
                        # แจ้งเตือนขายทันที
                        send_line(f"🔴 [SELL ORDER SUCCESS]\nขาย XRP ที่ราคา: {price:,.2f} บาท\nเพื่อรักษากำไร/ทุน")
                        last_action = "sell"

            # --- รายงานพอร์ตทุก 3 ชั่วโมง (10800 วินาที) ---
            if time.time() - last_report >= 10800:
                thb_bal, xrp_bal = get_wallet()
                current_equity = thb_bal + (xrp_bal * price)
                profit_pct = ((current_equity - initial_equity) / initial_equity) * 100
                profit_icon = "📈" if profit_pct >= 0 else "📉"

                report = (
                    f"📊 [Bot Performance Report]\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💰 ราคา: {price:,.2f} / EMA: {ema_val:,.2f}\n"
                    f"🧭 เทรนด์: {'ขาขึ้น 🟢' if trend == 'UP' else 'ขาลง 🔴'}\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"🏦 มูลค่าพอร์ต: {current_equity:,.2f} THB\n"
                    f"{profit_icon} กำไรสะสม: {profit_pct:+.2f}%\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💵 เงินสด: {thb_bal:,.2f} THB\n"
                    f"💎 เหรียญ: {xrp_bal:,.4f} XRP\n"
                    "━━━━━━━━━━━━━━━"
                )
                send_line(report)
                last_report = time.time()

    except Exception as e:
        print(f"Error: {e}")
    
    time.sleep(30)
