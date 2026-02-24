import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot Trading Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_ID = os.getenv("LINE_USER_ID")
HOST = "https://api.bitkub.com"

def send_line(msg):
    if not LINE_TOKEN or not LINE_ID: return
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_ID, "messages": [{"type": "text", "text": str(msg)}]}
    try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
    except: pass

# --- 3. ฟังก์ชัน API (Private V3) ---
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
    """ส่งคำสั่งซื้อขายแบบ Market Order"""
    path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
    try:
        ts = requests.get(f"{HOST}/api/v3/servertime").text.strip()
        # สำหรับ Buy ใช้ 'amt' (บาท), สำหรับ Sell ใช้ 'rat' (จำนวนเหรียญ)
        body = {"sym": "THB_XRP", "amt": amount, "typ": "market"} if side == "buy" else {"sym": "THB_XRP", "amt": amount, "typ": "market"}
        body_str = json.dumps(body, separators=(',', ':'))
        sig = get_signature(ts, "POST", path, body_str)
        headers = {'Accept': 'application/json','Content-Type': 'application/json','X-BTK-APIKEY': API_KEY,'X-BTK-TIMESTAMP': ts,'X-BTK-SIGN': sig}
        res = requests.post(f"{HOST}{path}", headers=headers, data=body_str, timeout=10).json()
        return res
    except Exception as e:
        return {"error": 999, "msg": str(e)}

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

# --- 4. ลูปหลักระบบเทรดอัตโนมัติ ---
last_report = 0
last_action = "" # เก็บสถานะล่าสุดเพื่อป้องกันการสั่งซ้ำ

send_line("🤖 บอทเทรด XRP (EMA 50 Full Auto) เริ่มทำงานแล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        print(f"✅ [{time.strftime('%H:%M:%S')}] ราคา: {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        # --- ตรรกะการซื้อขาย (Trade Logic) ---
        if trend == "UP" and last_action != "buy":
            thb_bal, xrp_bal = get_wallet()
            if thb_bal > 10: # ขั้นต่ำ Bitkub
                res = place_order("buy", thb_bal)
                if res.get('error') == 0:
                    send_line(f"🟢 [BUY ORDER]\nราคาตัดขึ้นเหนือ EMA 50\nซื้อสำเร็จด้วยเงิน {thb_bal:,.2f} บาท")
                    last_action = "buy"

        elif trend == "DOWN" and last_action != "sell":
            thb_bal, xrp_bal = get_wallet()
            if xrp_bal > 0.1: # มีเหรียญพอให้ขาย
                res = place_order("sell", xrp_bal)
                if res.get('error') == 0:
                    send_line(f"🔴 [SELL ORDER]\nราคาหลุดเส้น EMA 50\nขายเหรียญออกทั้งหมดเพื่อรักษากำไร")
                    last_action = "sell"

        # --- รายงานประจำชั่วโมง ---
        if time.time() - last_report >= 3600:
            thb_bal, xrp_bal = get_wallet()
            total_value = thb_bal + (xrp_bal * price)
            trend_text = "📈 ขาขึ้น" if trend == "UP" else "📉 ขาลง"
            status_icon = "🟢" if trend == "UP" else "🔴"
            
            report = (
                f"{status_icon} [Auto-Trade Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💰 ราคา: {price:,.2f} / EMA: {ema_val:,.2f}\n"
                f"🧭 เทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"💵 เงินสด: {thb_bal:,.2f} THB\n"
                f"💎 เหรียญ: {xrp_bal:,.4f} XRP\n"
                f"🏦 รวม: {total_value:,.2f} THB\n"
                "━━━━━━━━━━━━━━━"
            )
            send_line(report)
            last_report = time.time()
            
    time.sleep(30)
