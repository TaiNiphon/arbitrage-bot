import os, requests, time, hmac, hashlib, json, threading, logging, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- บังคับให้ Python พ่น Log ทันที (Fix Railway Log) ---
sys.stdout.reconfigure(line_buffering=True)

# --- Configuration & Logging ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"

        self.symbol = os.getenv("SYMBOL", "THB_XRP") # API v3 มักใช้ THB_XRP
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.state_file = "/tmp/bot_state_v3.json"
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
            except Exception as e: 
                logger.error(f"Auth Error: {e}")
                return {"error": 999}

        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except Exception as e:
            logger.error(f"API Connection Error: {e}")
            return {"error": 999}

    def calculate_ema(self, prices, period=50):
        if len(prices) < period: 
            logger.warning(f"Not enough data for EMA: {len(prices)}/{period}")
            return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
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
        except Exception as e: logger.error(f"Save State Error: {e}")

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
            coin = self.symbol.split('_')[1] # v3: THB_XRP -> XRP
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def place_market_order(self, side, amount):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        payload = {"sym": self.symbol, "amt": amount, "typ": "market"}
        return self._request("POST", path, payload, private=True)

    def notify(self, msg):
        logger.info(f"Notification: {msg}") # พิมพ์ลง Log เสมอ
        if not self.line_token: return
        try:
            headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
            payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: logger.error("Line Notify Error")

    def run(self):
        start_msg = f"🚀 Bot Ultimate Edition Started\nSymbol: {self.symbol}\nTarget: +{self.target_profit}%"
        self.notify(start_msg)

        while True:
            try:
                # --- Get Price (v3 Fix) ---
                ticker_res = self._request("GET", "/api/v3/market/ticker")
                current_price = 0
                
                # ตรวจสอบรูปแบบ Dictionary (Key เป็นชื่อ Symbol)
                if isinstance(ticker_res, dict) and self.symbol in ticker_res:
                    current_price = float(ticker_res[self.symbol].get('last', 0))
                
                if current_price == 0:
                    logger.warning(f"Price for {self.symbol} not found. Retrying...")
                    time.sleep(10); continue

                # --- Get History for EMA ---
                history = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                ema_val = self.calculate_ema(history.get('c', []), 50)

                if not ema_val:
                    time.sleep(30); continue

                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0
                logger.info(f"Market: {current_price} | EMA: {ema_val:.2f} | PNL: {pnl:.2f}%")

                # --- BUY LOGIC ---
                if current_price > ema_val:
                    thb, _ = self.get_balance()
                    if self.current_stage == 0 and thb > 50:
                        res = self.place_market_order("buy", thb * 0.98) # ใช้เกือบหมดเผื่อค่าธรรมเนียม
                        if res.get('error') == 0:
                            self.total_units = float(res['result']['rec'])
                            self.avg_price, self.current_stage, self.last_action, self.highest_price = current_price, 1, "buy", current_price
                            self._save_state()
                            self.notify(f"🟢 [BUY] Price: {current_price:,.2f}")

                # --- SELL LOGIC ---
                if self.last_action == "buy":
                    if current_price > self.highest_price:
                        self.highest_price = current_price
                        self._save_state()

                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif current_price < (ema_val * 0.995): reason = "Trend Reversed"

                    if reason:
                        _, coin = self.get_balance()
                        if coin > 0:
                            res = self.place_market_order("sell", coin)
                            if res.get('error') == 0:
                                self.notify(f"🔴 [SELL]\nReason: {reason}\nP/L: {pnl:+.2f}%")
                                self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                                self._save_state()

                # Report Every 3 Hours
                if time.time() - self.last_report_time >= 10800:
                    logger.info("Sending scheduled report...")
                    # (ใส่ฟังก์ชัน send_detailed_report ตรงนี้ถ้าต้องการ)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
        def log_message(self, *a): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
