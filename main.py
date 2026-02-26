import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # API Config (ดึงจากตัวแปรที่คุณรันได้ปกติ)
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB")
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        # ระบบจำสถานะ (State)
        self.state_file = "bot_state_v3.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        self.last_report_time = 0

    def _get_signature(self, ts, method, path, body_str):
        payload = ts + method + path + body_str
        return hmac.new(self.api_secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, payload=None, private=False):
        url = f"{self.host}{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            try:
                ts = requests.get(f"{self.host}/api/v3/servertime", timeout=5).text.strip()
                headers.update({
                    'X-BTK-APIKEY': self.api_key,
                    'X-BTK-TIMESTAMP': ts,
                    'X-BTK-SIGN': self._get_signature(ts, method, path, body_str)
                })
            except: return {"error": 999}
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except Exception as e:
            logger.error(f"API Error: {e}")
            return {"error": 999}

    def calculate_ema(self, prices, period=50):
        if len(prices) < 10: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / len(prices[:period])
        for price in prices[period:]:
            ema = (price * k) + (ema * (1 - k))
        return ema

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action,
                    "avg_price": self.avg_price,
                    "stage": self.current_stage,
                    "total_units": self.total_units,
                    "highest_price": self.highest_price
                }, f)
        except: pass

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    return d['last_action'], d['avg_price'], d['stage'], d.get('total_units', 0.0), d.get('highest_price', 0.0)
            except: pass
        return "sell", 0.0, 0, 0.0, 0.0

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res.get('error') == 0:
            coin = self.symbol.split('_')[0]
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def place_market_order(self, side, amount):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        payload = {"sym": self.symbol, "amt": amount, "typ": "market"}
        return self._request("POST", path, payload, private=True)

    def notify(self, msg):
        if not self.line_token: return
        try:
            headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
            payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: pass

    def run(self):
        self.notify(f"🚀 บอทอัปเกรดสำเร็จ!\nสถานะปัจจุบัน: {self.last_action.upper()}\nเป้าหมายกำไร: {self.target_profit}%")
        while True:
            try:
                # 1. ดึงข้อมูลตลาด
                ticker = self._request("GET", "/api/v3/market/ticker")
                current_price = 0
                if isinstance(ticker, dict) and self.symbol in ticker:
                    current_price = float(ticker[self.symbol].get('last', 0))
                
                # 2. ดึงข้อมูล EMA
                history = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                ema_val = self.calculate_ema(history.get('c', []), 50)
                
                thb, coin = self.get_balance()
                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # --- BUY LOGIC (2 ไม้) ---
                if self.last_action == "sell" and thb > 50:
                    # ซื้อไม้แรกถ้า ราคา > EMA หรือไม่มีข้อมูล EMA
                    if ema_val is None or current_price >= (ema_val * 0.998):
                        buy_amt = thb * 0.49
                        res = self.place_market_order("buy", buy_amt)
                        if res.get('error') == 0:
                            self.total_units = float(res['result'].get('rec', 0))
                            self.avg_price = current_price
                            self.current_stage = 1
                            self.last_action = "buy"
                            self.highest_price = current_price
                            self._save_state()
                            self.notify(f"🟢 BUY 1/2 สำเร็จ!\nราคา: {current_price:,.2f}")

                elif self.current_stage == 1 and thb > 50 and pnl >= 0.5:
                    # ซื้อไม้สองถ้ากำไรมาแล้ว 0.5% (Confirm เทรนด์)
                    buy_amt = thb * 0.95
                    res = self.place_market_order("buy", buy_amt)
                    if res.get('error') == 0:
                        new_units = float(res['result'].get('rec', 0))
                        self.avg_price = ((self.avg_price * self.total_units) + (current_price * new_units)) / (self.total_units + new_units)
                        self.total_units += new_units
                        self.current_stage = 2
                        self._save_state()
                        self.notify(f"🟢 BUY 2/2 สำเร็จ!\nต้นทุนเฉลี่ย: {self.avg_price:,.2f}")

                # --- SELL LOGIC ---
                elif self.last_action == "buy" and coin > 0:
                    if current_price > self.highest_price:
                        self.highest_price = current_price
                        self._save_state()

                    reason = None
                    if pnl <= -self.stop_loss: reason = "Stop Loss"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = "Trailing Stop"
                    elif ema_val and current_price < (ema_val * 0.995): reason = "Trend Reversed"

                    if reason:
                        res = self.place_market_order("sell", coin)
                        if res.get('error') == 0:
                            self.notify(f"🔴 SELL ALL เรียบร้อย\nเหตุผล: {reason}\nP/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage = "sell", 0.0, 0
                            self._save_state()

                # รายงานพอร์ตทุก 3 ชม.
                if time.time() - self.last_report_time >= 10800:
                    self.notify(f"📊 รายงานสถานะ\nราคา: {current_price:,.2f}\nPNL: {pnl:+.2f}%")
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Error: {e}")
            time.sleep(30)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
