import os, requests, time, hmac, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- 1. ระบบรักษาการเชื่อมต่อ ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Bot is Active")
        def log_message(self, *args): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 2. การตั้งค่าตัวแปร (API Key/Secret จาก Railway) ---
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

# --- 3. ฟังก์ชันดึงยอดเงินในกระเป๋า (Private API V3) ---
def get_wallet():
    """ดึงยอดเงินคงเหลือจากกระเป๋า Bitkub [อ้างอิงจาก buy.py]"""
    path = "/api/v3/market/wallet"
    try:
        ts = requests.get(f"{HOST}/api/v3/servertime").text.strip()
        body = {} # สำหรับกระเป๋าเงินใช้ Body ว่าง
        json_body = json.dumps(body, separators=(',', ':'))
        payload = ts + "POST" + path + json_body
        sig = hmac.new(API_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        
        headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig
        }
        res = requests.post(f"{HOST}{path}", headers=headers, data=json_body, timeout=10).json()
        
        if res.get('error') == 0:
            result = res.get('result', {})
            return result.get('THB', 0), result.get('XRP', 0)
        return 0, 0
    except:
        return 0, 0

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # ดึงราคาปัจจุบัน
        t_res = requests.get(f"{HOST}/api/v3/market/ticker", headers=headers, timeout=10).json()
        price = next((float(i['last']) for i in t_res if i['symbol'] == "XRP_THB"), 0)
        
        # ดึงกราฟจาก TradingView (ระบบสำรองที่เสถียรที่สุดที่คุณใช้ผ่าน)
        c_url = f"{HOST}/tradingview/history?symbol=XRP_THB&resolution=15&from={int(time.time()) - 86400}&to={int(time.time())}"
        c_res = requests.get(c_url, headers=headers, timeout=10).json()
        data_c = c_res.get('c', [])

        if price > 0 and len(data_c) >= 50:
            ema = data_c[0]
            m = 2 / (50 + 1)
            for p in data_c: ema = (p - ema) * m + ema
            return price, ema
        return None, None
    except:
        return None, None

# --- 4. ลูปรายงานพร้อมยอดเงิน ---
last_report = 0
send_line("🚀 บอทอัปเกรดระบบ: ตรวจสอบยอดเงินในกระเป๋าได้แล้ว!")

while True:
    price, ema_val = get_market_data()
    
    if price and ema_val:
        trend = "UP" if price > ema_val else "DOWN"
        print(f"✅ [{time.strftime('%H:%M:%S')}] {price} | EMA50: {ema_val:.2f} | Trend: {trend}")

        if time.time() - last_report >= 3600:
            # ดึงยอดเงินในกระเป๋ามาแสดง
            thb_bal, xrp_bal = get_wallet()
            total_value = thb_bal + (xrp_bal * price)
            
            trend_text = "📈 ขาขึ้น (Bullish)" if trend == "UP" else "📉 ขาลง (Bearish)"
            status_icon = "🟢" if trend == "UP" else "🔴"
            
            report = (
                f"{status_icon} [XRP Premium Report]\n"
                "━━━━━━━━━━━━━━━\n"
                f"💵 ราคาปัจจุบัน: {price:,.2f} บาท\n"
                f"📊 เส้น EMA 50: {ema_val:,.2f} บาท\n"
                f"🧭 เทรนด์: {trend_text}\n"
                "━━━━━━━━━━━━━━━\n"
                f"💰 เงินสด (THB): {thb_bal:,.2f} บาท\n"
                f"💎 เหรียญ (XRP): {xrp_bal:,.4f} XRP\n"
                f"🏦 มูลค่ารวม: {total_value:,.2f} บาท\n"
                "━━━━━━━━━━━━━━━\n"
                f"⏰ อัปเดตเมื่อ: {time.strftime('%H:%M:%S')}"
            )
            send_line(report)
            last_report = time.time()
            
    time.sleep(30)
