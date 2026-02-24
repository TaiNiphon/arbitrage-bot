import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ (Dummy Server) ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Trading Pro Active with TP/SL")
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

# ค่าที่เพิ่มเข้ามาใหม่สำหรับ TP/SL
TARGET_PROFIT = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", 2.0))

initial_equity = 1500.00
last_buy_price = 0.0 # ตัวแปรเก็บราคาต้นทุน

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
last_action = "sell" # เริ่มต้นด้วยสถานะว่าง (ขายแล้ว)

send_line(f"🚀 [Bot Started]\n- Target Profit: {TARGET_PROFIT}%\n- Stop Loss: {STOP_LOSS}%\n- รายงานพอร์ตทุก 3 ชม.")

while True:
    try:
        price, ema_val = get_market_data()

        if price and ema_val:
            trend = "UP" if price > ema_val else "DOWN"
            
            # คำนวณกำไร/ขาดทุนปัจจุบันของไม้ที่ถืออยู่
            current_pnl = 0.0
            if last_buy_price > 0:
                current_pnl = ((price - last_buy_price) / last_buy_price) * 100

            # --- ตรรกะซื้อ (Buy) ---
            if trend == "UP" and last_action == "sell":
                thb_bal, xrp_bal = get_wallet()
                if thb_bal > 10:
                    res = place_order("buy", thb_bal)
                    if res.get('error') == 0:
                        last_buy_price = price
                        last_action = "buy"
                        send_line(f"🟢 [BUY ORDER]\nราคา: {price:,.2f} THB\nทุนที่ใช้: {thb_bal:,.2f} THB")

            # --- ตรรกะขาย (Sell) ---
            elif last_action == "buy":
                reason = ""
                # 1. ขายตามเทรนด์เปลี่ยน
                if trend == "DOWN":
                    reason = "Trend Change (EMA)"
                # 2. ขายทำกำไร (Take Profit)
                elif current_pnl >= TARGET_PROFIT:
                    reason = f"Take Profit ({current_pnl:+.2f}%)"
                # 3. ขายตัดขาดทุน (Stop Loss)
                elif current_pnl <= -STOP_LOSS:
                    reason = f"Stop Loss ({current_pnl:+.2f}%)"

                if reason:
                    thb_bal, xrp_bal = get_wallet()
                    if xrp_bal > 0.1:
                        res = place_order("sell", xrp_bal)
                        if res.get('error') == 0:
                            send_line(f"🔴 [SELL ORDER]\nเหตุผล: {reason}\nราคาขาย: {price:,.2f}\nกำไรไม้หน้า: {current_pnl:+.2f}%")
                            last_action = "sell"
                            last_buy_price = 0.0

            # --- รายงานพอร์ต (ทุก 3 ชั่วโมง) ---
            if time.time() - last_report >= 10800:
                thb_bal, xrp_bal = get_wallet()
                current_equity = thb_bal + (xrp_bal * price)
                total_profit_pct = ((current_equity - initial_equity) / initial_equity) * 100
                
                report = (
                    f"📊 [Performance Report]\n"
                    f"💰 ราคาปัจจุบัน: {price:,.2f}\n"
                    f"📈 ต้นทุนไม้ล่าสุด: {'-' if last_buy_price == 0 else f'{last_buy_price:,.2f}'}\n"
                    f"📉 P/L ไม้ปัจจุบัน: {current_pnl:+.2f}%\n"
                    f"🏦 รวมมูลค่าพอร์ต: {current_equity:,.2f} THB\n"
                    f"✨ กำไรสะสมทั้งหมด: {total_profit_pct:+.2f}%"
                )
                send_line(report)
                last_report = time.time()

    except Exception as e:
        print(f"Error: {e}")

    time.sleep(30)
