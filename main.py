import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"
        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000.0))
        self.target_profit, self.stop_loss, self.trailing_pct = 3.0, 2.0, 1.0
        self.buy_buffer, self.sell_buffer = 1.008, 0.992
        self.state_file = "bot_state_v6.json"
        self._init_state()
        self.last_report_time, self.report_interval = 0, 1800

    def _init_state(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                d = json.load(f)
                self.last_action, self.avg_price = d.get('last_action', 'sell'), d.get('avg_price', 0.0)
                self.current_stage, self.total_units = d.get('stage', 0), d.get('total_units', 0.0)
                self.highest_price = d.get('highest_price', 0.0)
        else: self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump({"last_action": self.last_action, "avg_price": self.avg_price, "stage": self.current_stage, "total_units": self.total_units, "highest_price": self.highest_price}, f)

    def notify(self, msg):
        if self.tg_token: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"})

    def _request(self, method, path, payload=None, private=False):
        url, body_str = f"{self.host}{path}", json.dumps(payload, separators=(',', ':')) if payload else ""
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if private:
            ts = requests.get(f"{self.host}/api/v3/servertime").text.strip()
            sig = hmac.new(self.api_secret.encode(), (ts + method + path + body_str).encode(), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=body_str).json()

    def send_detailed_report(self, price, ema=None):
        try:
            res = self._request("POST", "/api/v3/market/wallet", {}, True)
            thb = float(res['result'].get('THB', 0))
            coin = float(res['result'].get(self.symbol.split('_')[1], 0))
            eq = thb + (coin * price)
            pnl = ((price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0
            status = "🚀 <b>HOLDING COIN</b>" if coin * price > 50 else "💰 <b>HOLDING CASH</b>"
            report = (f"{status}\n📅 {(datetime.now(timezone.utc)+timedelta(hours=7)).strftime('%d/%m/%Y %H:%M')}\n"
                      f"━━━━━━━━━━━━━━━\n📊 MARKET: {self.symbol}\n💵 Price: {price:,.2f}\n📈 EMA(50): {ema:,.2f if ema else 0}\n🕒 P/L: {pnl:+.2f}%\n"
                      f"━━━━━━━━━━━━━━━\n🏦 PORTFOLIO\n💰 Cash: {thb:,.2f}\n🪙 Coin: {coin:,.4f}\n💎 Equity: {eq:,.2f}\n"
                      f"━━━━━━━━━━━━━━━\n📈 PERFORMANCE\n💵 Profit: {eq-self.initial_equity:,.2f}\n🚀 Growth: {((eq-self.initial_equity)/self.initial_equity*100):+.2f}%\n━━━━━━━━━━━━━━━")
            self.notify(report); self.last_report_time = time.time()
        except: pass

    def run(self):
        self.notify(f"<b>🚀 Bot Started</b>\nMonitoring {self.symbol}")
        while True:
            try:
                t_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                price = float(t_res.get('result', t_res).get(self.symbol, {}).get('last', 0))
                hist = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                ema_val = None
                if 'c' in hist:
                    prices = hist['c']
                    k = 2 / 51
                    ema = sum(prices[:50]) / 50
                    for p in prices[50:]: ema = (p * k) + (ema * (1 - k))
                    ema_val, ema_prev = ema, (prices[-2] * k) + (ema * (1 - k)) # Simple prev approximation

                if time.time() - self.last_report_time >= self.report_interval or self.last_report_time == 0:
                    self.send_detailed_report(price, ema_val)

                if not ema_val: continue
                
                # Logic
                res_w = self._request("POST", "/api/v3/market/wallet", {}, True)
                thb, coin = float(res_w['result'].get('THB', 0)), float(res_w['result'].get(self.symbol.split('_')[1], 0))
                pnl = ((price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                if coin * price > 50 and self.last_action == "sell":
                    self.last_action, self.current_stage, self.total_units, self.avg_price = "buy", 2, coin, price
                    self._save_state()

                if price > (ema_val * self.buy_buffer) and self.current_stage < 2 and thb >= 10:
                    r = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": int(thb*0.98), "rat": round(price, 4), "typ": "limit"}, True)
                    if r.get('error') == 0:
                        self.total_units, self.avg_price, self.current_stage, self.last_action, self.highest_price = float(r['result']['rec']), float(r['result']['rat']), 2, "buy", price
                        self._save_state(); self.notify(f"✅ BUY at {price}")

                if self.last_action == "buy" and self.total_units > 0:
                    if price > self.highest_price: self.highest_price = price; self._save_state()
                    reason = None
                    if pnl <= -self.stop_loss: reason = "Stop Loss"
                    elif pnl >= self.target_profit and price <= (self.highest_price * 0.99): reason = "Trailing Stop"
                    elif price < (ema_val * self.sell_buffer): reason = "Trend Reversed"
                    
                    if reason:
                        r = self._request("POST", "/api/v3/market/place-ask", {"sym": self.symbol, "amt": self.total_units, "typ": "market"}, True)
                        if r.get('error') == 0:
                            self.notify(f"🚨 SELL: {reason}\nP/L: {pnl:+.2f}%"); self.last_action, self.current_stage, self.avg_price, self.total_units = "sell", 0, 0, 0
                            self._save_state()
            except: pass
            time.sleep(30)

def run_hc():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_hc, daemon=True).start()
    BitkubBot().run()
