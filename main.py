import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubHybridV7:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.coin = self.symbol.split('_')[0] if '_' in self.symbol else self.symbol
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000)) 
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 5.0)) # ปรับตาม Variables ในรูป
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        
        # State Management
        self.state_file = "bot_state_v7_pro.json"
        self.last_report_time = 0
        self.report_interval = 1800 
        self.last_pnl = 0.0
        self._sync_setup()

    def _sync_setup(self):
        logger.info("🛠️ Initializing & Syncing Wallet...")
        thb, coin_bal = self.get_balance()
        ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
        price = 0
        if isinstance(ticker, list):
            for item in ticker:
                if item['symbol'].upper() == self.symbol: price = float(item['last'])

        if coin_bal * price > 50:
            self.last_action, self.total_units, self.current_stage = "buy", coin_bal, 2
            actual_avg, _ = self.get_actual_cost()
            self.avg_price = actual_avg if actual_avg > 0 else price
            self.highest_price = max(self.avg_price, price)
        else:
            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
        self._save_state()

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price
                }, f)
        except: pass

    def _request(self, method, path, params=None, payload=None, private=False):
        url = f"{self.host}{path}"
        query_str = "?" + "&".join([f"{k}={v}" for k, v in params.items()]) if params else ""
        if params: url += query_str
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = requests.get(f"{self.host}/api/v3/servertime").text.strip()
            headers.update({
                'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': hmac.new(self.api_secret.encode('utf-8'), (ts + method + path + query_str + body_str).encode('utf-8'), hashlib.sha256).hexdigest()
            })
        try:
            res = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return res.json()
        except: return {"error": 999}

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res and res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get(self.coin, 0))
        return 0.0, 0.0

    def get_actual_cost(self):
        res = self._request("GET", f"/api/v3/market/my-order-history?sym={self.symbol.lower()}&p=1&l=1", private=True)
        if res and res.get('error') == 0 and res.get('result'):
            for order in res['result']:
                if order['side'].lower() == 'buy':
                    return float(order['rat']), float(order['amount'])
        return 0.0, 0.0

    def place_order(self, side, amt, typ="market"):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        clean_amt = math.floor(amt * 100) / 100 if side == "buy" else math.floor(amt * 10000) / 10000
        payload = {"sym": self.symbol.lower(), "amt": clean_amt, "rat": 0, "typ": typ}
        return self._request("POST", path, payload=payload, private=True)

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    # --- รายงานผลถอดแบบจาก V6.0 PRO ---
    def send_detailed_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100
        now_th = datetime.now(timezone.utc) + timedelta(hours=7)

        status_icon = "🚀" if coin_value > 50 else "⚪️"
        status_text = "HOLDING COIN" if coin_value > 50 else "HOLDING CASH"
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val else ""

        report = (
            f"{status_icon} <b>{status_text} | Hybrid V7</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📊 <b>MARKET: {self.symbol}</b>\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_val:,.2f} {diff_ema}\n"
            f"🕒 Net P/L: {pnl:+.2f}% (Fee Incl.)\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>🏦 PORTFOLIO</b>\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Coin: {coin_bal:,.4f} ({coin_value:,.2f} THB)\n"
            f"💎 <b>Equity: {total_equity:,.2f} THB</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>📈 PERFORMANCE</b>\n"
            f"💵 Net Profit: {net_profit:,.2f} THB\n"
            f"🚀 Growth: {growth_pct:+.2f}%\n"
            f"🛡️ Trailing @: {f'{self.highest_price * (1-self.trailing_pct/100):,.2f}' if self.current_stage == 3 else 'Waiting...'}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)
        self.last_report_time = time.time()

    def run(self):
        self.notify(f"<b>🚀 Hybrid Bot V7.1 Pro Started</b>\nMonitoring {self.symbol}")
        while True:
            try:
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                price = 0
                if isinstance(ticker, list):
                    for item in ticker:
                        if item['symbol'].upper() == self.symbol: price = float(item['last'])

                hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
                prices = hist.get('c', [])
                if len(prices) < self.ema_period:
                    time.sleep(30); continue
                
                ema = sum(prices[-self.ema_period:]) / self.ema_period
                ema_prev = sum(prices[-(self.ema_period+1):-1]) / self.ema_period
                
                thb, coin_bal = self.get_balance()
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                # BUY Logic (Pyramiding + Slope)
                if self.last_action == "sell" and price > (ema * 1.005) and ema > (ema_prev * 1.001):
                    res = self.place_order("buy", thb * 0.48)
                    if res.get('error') == 0:
                        self.avg_price, self.last_action, self.current_stage = price, "buy", 1
                        self.total_units = float(res['result'].get('rec', 0))
                        self.highest_price = price
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 1/2] Confirmed</b>\nPrice: {price:,.2f}")

                elif self.current_stage == 1 and pnl > 0.5 and price > (ema * 1.005):
                    res = self.place_order("buy", thb * 0.95)
                    if res.get('error') == 0:
                        new_units = float(res['result'].get('rec', 0))
                        self.avg_price = ((self.avg_price * self.total_units) + (price * new_units)) / (self.total_units + new_units)
                        self.total_units += new_units
                        self.current_stage = 2
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 2/2] Pyramiding</b>\nNew Avg: {self.avg_price:,.2f}")

                # SELL Logic (Partial TP + Trailing)
                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)
                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        res = self.place_order("sell", coin_bal * 0.5)
                        if res.get('error') == 0:
                            self.total_units -= (coin_bal * 0.5); self.current_stage = 3
                            self._save_state()
                            self.notify(f"💰 <b>[TAKE PROFIT 50%]</b>\nPNL: {pnl:+.2f}%")

                    reason = None
                    if pnl <= -self.stop_loss: reason = "Stop Loss"
                    elif self.current_stage == 3 and price < self.highest_price * (1 - self.trailing_pct/100): reason = "Trailing Stop"
                    elif price < (ema * 0.99) and ema < ema_prev: reason = "Trend Reversed"

                    if reason:
                        res = self.place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            self.notify(f"🔴 <b>[SELL ALL]</b>\nReason: {reason}\nPNL: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units = "sell", 0, 0, 0
                            self._save_state()

                if time.time() - self.last_report_time >= self.report_interval:
                    self.send_detailed_report(price, pnl, ema)

            except Exception as e: logger.error(f"Error: {e}")
            time.sleep(30)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot V7.1 Active")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubHybridV7().run()
