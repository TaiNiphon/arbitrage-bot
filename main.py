import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "xrp_thb").lower() 
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.state_file = "/tmp/bot_state_v3.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        self.last_report_time = 0

    def get_local_time(self):
        """ คืนค่าเวลาปัจจุบันของไทย (GMT+7) """
        return datetime.utcnow() + timedelta(hours=7)

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
            coin = self.symbol.split('_')[0].upper()
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def place_order_v3(self, side, amount, price):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        payload = {
            "sym": self.symbol,
            "amt": int(amount) if side == "buy" else amount,
            "rat": price,
            "typ": "limit"
        }
        return self._request("POST", path, payload, private=True)

    def calculate_ema(self, prices, period=50):
        if len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price * k) + (ema * (1 - k))
        return ema

    def notify(self, msg):
        if not self.line_token: logger.info(msg); return
        try:
            headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
            payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: logger.error("Line Notify Error")

    def send_detailed_report(self, price, ema_val, pnl):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        all_time_pnl = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        ema_diff = ((price - ema_val) / ema_val * 100) if ema_val else 0
        
        # ปรับเวลาเป็น GMT+7
        now_th = self.get_local_time()
        
        # หาราคา Trailing Stop
        t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.last_action == "buy" and pnl >= self.target_profit else "Wait for Target"

        # จัดรูปแบบรายงานตามรูปภาพที่คุณส่งมา
        report = (
            "📊 [PORTFOLIO INSIGHT]\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Market: {self.symbol.upper()}: {price:,.2f}\n"
            f"📈 EMA(50): {ema_val:,.2f} ({ema_diff:+.2f}%)\n"
            f"🕒 Time: {now_th.strftime('%H:%M')}\n"
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
        self.notify(f"🚀 Bot Fixed & Running\nSymbol: {self.symbol.upper()}")

        while True:
            try:
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                current_price = 0
                
                # ป้องกัน Error 'list' object has no attribute 'get'
                if isinstance(ticker_res, list):
                    symbol_data = next((item for item in ticker_res if item['symbol'] == self.symbol.upper()), None)
                    if symbol_data:
                        current_price = float(symbol_data.get('lowest_ask', 0))
                elif isinstance(ticker_res, dict) and ticker_res.get('error') == 0:
                    current_price = float(ticker_res['result'].get('lowest_ask', 0))

                if current_price == 0:
                    time.sleep(10); continue

                history = self._request("GET", f"/tradingview/history?symbol={self.symbol.upper()}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                ema_val = self.calculate_ema(history.get('c', []), 50)

                if not ema_val:
                    time.sleep(30); continue

                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # --- BUY LOGIC ---
                if current_price > ema_val:
                    thb, _ = self.get_balance()
                    if self.current_stage == 0 and thb >= 10:
                        res = self.place_order_v3("buy", thb * 0.49, current_price)
                        if res.get('error') == 0:
                            self.total_units = float(res['result'].get('rec', 0))
                            self.avg_price = float(res['result'].get('rat', current_price))
                            self.current_stage, self.last_action, self.highest_price = 1, "buy", current_price
                            self._save_state()
                            self.notify(f"🟢 [BUY 1/2] Price: {self.avg_price:,.2f}")

                    elif self.current_stage == 1 and pnl >= 0.5 and thb >= 10:
                        res = self.place_order_v3("buy", thb * 0.95, current_price)
                        if res.get('error') == 0:
                            new_units = float(res['result'].get('rec', 0))
                            new_price = float(res['result'].get('rat', current_price))
                            self.avg_price = ((self.avg_price * self.total_units) + (new_price * new_units)) / (self.total_units + new_units)
                            self.total_units += new_units
                            self.current_stage = 2
                            self._save_state()
                            self.notify(f"🟢 [BUY 2/2] New Avg: {self.avg_price:,.2f}")

                # --- SELL LOGIC ---
                if self.last_action == "buy" and self.total_units > 0:
                    if current_price > self.highest_price:
                        self.highest_price = current_price
                        self._save_state()

                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif current_price < (ema_val * 0.997): reason = "Trend Reversed"

                    if reason:
                        res = self.place_order_v3("sell", self.total_units, current_price)
                        if res.get('error') == 0:
                            self.notify(f"🔴 [SELL ALL]\nReason: {reason}\nP/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                            self._save_state()

                # Report ทุก 3 ชม.
                if time.time() - self.last_report_time >= 10800:
                    self.send_detailed_report(current_price, ema_val, pnl)
                    self.last_report_time = time.time()

            except Exception as e: 
                logger.error(f"Loop Error: {e}")
            time.sleep(30)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
