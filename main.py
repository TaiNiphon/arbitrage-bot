import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # API & Notification Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        # State Persistence
        self.state_file = "bot_state_v5.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        self.last_report_time = 0

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def _get_signature(self, ts, method, path, body_str):
        payload = ts + method + path + body_str
        return hmac.new(self.api_secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, payload=None, private=False):
        url = f"{self.host}{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            try:
                ts = requests.get(f"{self.host}/api/v3/servertime", timeout=10).text.strip()
                headers.update({
                    'X-BTK-APIKEY': self.api_key,
                    'X-BTK-TIMESTAMP': ts,
                    'X-BTK-SIGN': self._get_signature(ts, method, path, body_str)
                })
            except: return {"error": 999}
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except: return {"error": 999}

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
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
        if res and res.get('error') == 0:
            coin = self.symbol.replace("THB_", "")
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def calculate_ema(self, prices, period=50):
        if not prices or len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        ema_list = [ema]
        for p in prices[period:]:
            ema = (p * k) + (ema * (1 - k))
            ema_list.append(ema)
        return ema_list

    def notify(self, msg):
        if not self.line_token: logger.info(msg); return
        try:
            headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
            payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: pass

    def run(self):
        self.notify(f"🚀 Bot V5.4 Fixed Started\nMonitoring {self.symbol}")
        # เตรียมชื่อ symbol สำหรับค้นหาใน List (เช่น XRP_THB)
        search_sym = f"{self.symbol.split('_')[1]}_{self.symbol.split('_')[0]}"

        while True:
            try:
                # 1. ข้อมูลราคา (Fix List Search)
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                current_price = None
                
                # ตรวจสอบว่าเป็น List หรือไม่ (ตามภาพ Log)
                if isinstance(ticker_res, list):
                    for item in ticker_res:
                        if item.get('symbol') == search_sym or item.get('symbol') == self.symbol:
                            current_price = float(item['last'])
                            break
                elif isinstance(ticker_res, dict):
                    res_data = ticker_res.get('result', ticker_res)
                    if self.symbol in res_data:
                        current_price = float(res_data[self.symbol]['last'])
                    elif search_sym in res_data:
                        current_price = float(res_data[search_sym]['last'])

                if current_price is None:
                    logger.error(f"❌ Could not find price for {self.symbol}. Response: {ticker_res}")
                    time.sleep(30); continue

                # 2. ข้อมูลกราฟ
                history = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                if not isinstance(history, dict) or 'c' not in history:
                    logger.error("❌ History data error"); time.sleep(30); continue
                
                prices = history.get('c', [])
                ema_series = self.calculate_ema(prices, 50)
                if not ema_series: time.sleep(30); continue

                ema_val = ema_series[-1]
                ema_prev = ema_series[-2]
                is_uptrend = current_price > (ema_val * 1.002) and ema_val > ema_prev

                # 3. Sync Wallet
                thb, coin_bal = self.get_balance()
                if coin_bal * current_price > 50:
                    if self.last_action == "sell" or self.current_stage == 0:
                        self.last_action, self.current_stage, self.total_units = "buy", 2, coin_bal
                        self.avg_price = current_price if self.avg_price == 0 else self.avg_price
                        self._save_state()

                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # --- BUY/SELL LOGIC (คงเดิม) ---
                if is_uptrend and self.current_stage < 2:
                    if self.current_stage == 0 and thb >= 10:
                        res = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": int(thb * 0.49), "rat": round(current_price, 4), "typ": "limit"}, private=True)
                        if res and res.get('error') == 0:
                            self.total_units, self.avg_price, self.current_stage, self.last_action = float(res['result']['rec']), float(res['result']['rat']), 1, "buy"
                            self.highest_price = self.avg_price
                            self._save_state()
                            self.notify(f"🟢 [BUY 1/2] @ {self.avg_price:,.2f}")

                if self.last_action == "buy" and self.total_units > 0:
                    if current_price > self.highest_price: self.highest_price = current_price; self._save_state()
                    reason = None
                    if pnl <= -self.stop_loss: reason = "Stop Loss"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))): reason = "Trailing Stop"
                    elif current_price < (ema_val * 0.998): reason = "Trend Reversed"

                    if reason:
                        res = self._request("POST", "/api/v3/market/place-ask", {"sym": self.symbol, "amt": round(self.total_units, 6), "rat": 0, "typ": "market"}, private=True)
                        if res and res.get('error') == 0:
                            self.notify(f"🔴 [SELL ALL]\nReason: {reason}\nP/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                            self._save_state()

                if time.time() - self.last_report_time >= 10800:
                    # ฟังก์ชันรายงานสรุป (ถ้าต้องการ)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"🔥 Error: {e}")
            time.sleep(30)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
