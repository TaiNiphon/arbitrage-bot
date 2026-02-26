import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
import datetime as dt

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB")
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.state_file = "/tmp/bot_state_v3.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        self.last_report_time = 0

    def notify(self, message):
        """ ระบบส่ง LINE พร้อมระบบลองใหม่เมื่อเน็ตหลุด """
        if not self.line_token: return
        url = "https://notify-api.line.me/api/notify"
        headers = {"Authorization": f"Bearer {self.line_token}"}
        for _ in range(3): # ลองใหม่สูงสุด 3 ครั้ง
            try:
                res = requests.post(url, headers=headers, data={"message": message}, timeout=15)
                if res.status_code == 200: return
            except: time.sleep(5)
        logger.error("Failed to send LINE Notify after 3 attempts")

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
            response = requests.request(method, url, headers=headers, data=body_str, timeout=20)
            return response.json()
        except: return {"error": 999}

    def calculate_ema(self, prices, period=50):
        if len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price * k) + (ema * (1 - k))
        return ema

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
        if res.get('error') == 0:
            coin = self.symbol.split('_')[0]
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def place_market_order(self, side, amount):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        payload = {"sym": self.symbol, "amt": amount, "typ": "market"}
        return self._request("POST", path, payload, private=True)

    def send_detailed_report(self, price, ema_val, pnl):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        all_time_pnl = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        ema_diff = ((price - ema_val) / ema_val * 100) if ema_val else 0
        thai_time = (dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))).strftime('%H:%M')
        t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.last_action == "buy" and pnl >= self.target_profit else "Wait for Target"

        report = (
            "📊 [PORTFOLIO INSIGHT]\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Market: {self.symbol}: {price:,.2f}\n"
            f"📈 EMA(50): {ema_val:,.2f} ({ema_diff:+.2f}%)\n"
            f"🕒 Time (TH): {thai_time}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📦 Position: Stage {self.current_stage}/2\n"
            f"📉 Avg Cost: {self.avg_price:,.2f}\n"
            f"✨ Current P/L: {pnl:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop_price}\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏦 Equity: {total_equity:,.2f} THB\n"
            f"💹 Growth: {all_time_pnl:+.2f}%\n"
            f"💵 Cash: {thb_bal:,.2f} | 💎 Coin: {coin_bal:,.4f}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        time.sleep(10) # รอให้ระบบ Network ของ Railway พร้อม
        self.notify(f"🚀 Bot Ultimate Edition Started\nSymbol: {self.symbol}")

        while True:
            try:
                ticker_res = self._request("GET", "/api/v3/market/ticker")
                current_price = 0
                if isinstance(ticker_res, dict) and self.symbol in ticker_res:
                    current_price = float(ticker_res[self.symbol].get('last', 0))
                elif isinstance(ticker_res, list):
                    for s in ticker_res:
                        if s.get('symbol') == self.symbol: current_price = float(s.get('last', 0)); break

                if current_price == 0:
                    time.sleep(20); continue

                history = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                ema_val = self.calculate_ema(history.get('c', []), 50)
                if not ema_val:
                    time.sleep(20); continue

                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                if current_price > ema_val:
                    thb, _ = self.get_balance()
                    if self.current_stage == 0 and thb > 50:
                        res = self.place_market_order("buy", thb * 0.98) # ใช้เกือบหมดพอร์ตในไม้เดียวหรือปรับตามใจชอบ
                        if res.get('error') == 0:
                            self.total_units = float(res['result'].get('rec', 0))
                            self.avg_price, self.current_stage, self.last_action, self.highest_price = current_price, 2, "buy", current_price
                            self._save_state()
                            self.notify(f"🟢 [BUY SUCCESS] Price: {current_price:,.2f}")

                if self.last_action == "buy":
                    if current_price > self.highest_price:
                        self.highest_price = current_price; self._save_state()
                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif current_price < (ema_val * 0.997): reason = "Trend Reversed"
                    
                    if reason:
                        _, coin = self.get_balance()
                        if coin > 0:
                            res = self.place_market_order("sell", coin)
                            if res.get('error') == 0:
                                self.notify(f"🔴 [SELL ALL]\nReason: {reason}\nP/L: {pnl:+.2f}%")
                                self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                                self._save_state()

                if time.time() - self.last_report_time >= 10800:
                    self.send_detailed_report(current_price, ema_val, pnl)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

def run_hc():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_hc, daemon=True).start()
    BitkubBot().run()
