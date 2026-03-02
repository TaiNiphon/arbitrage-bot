import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubProBotV6_2:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Trading Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000.00))
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 5.0)) 
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.fee_pct = 0.0025 
        self.min_trade = 10.0 

        self.state_file = "bot_state_v6.json"
        self._load_state()
        self.last_report_time = 0

    def get_local_time(self):
        now = datetime.now(timezone.utc)
        return now + timedelta(hours=7)

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    self.last_action = d.get('last_action', 'sell')
                    self.avg_price = d.get('avg_price', 0.0)
                    self.current_stage = d.get('stage', 0) 
                    self.total_units = d.get('total_units', 0.0)
                    self.highest_price = d.get('highest_price', 0.0)
                    self.last_pnl = d.get('last_pnl', 0.0)
                    return
            except: pass
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price, self.last_pnl = "sell", 0.0, 0, 0.0, 0.0, 0.0

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price, "last_pnl": self.last_pnl
                }, f)
        except Exception as e: logger.error(f"Save State Error: {e}")

    def get_server_time(self):
        try:
            res = requests.get(f"{self.host}/api/v3/servertime", timeout=10)
            return res.text.strip()
        except: return str(int(time.time() * 1000))

    def _get_signature(self, ts, method, path, query="", body=""):
        payload = ts + method + path + query + body
        return hmac.new(self.api_secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, params=None, payload=None, private=False):
        url = f"{self.host}{path}"
        query_str = ""
        if params:
            query_str = "?" + "&".join([f"{k}={v}" for k, v in params.items()])
            url += query_str
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = self.get_server_time()
            headers.update({
                'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': self._get_signature(ts, method, path, query_str, body_str)
            })
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except: return {"error": 999}

    def notify(self, msg):
        if not self.tg_token or not self.tg_chat_id: return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=10)
        except: pass

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res and res.get('error') == 0:
            coin = self.symbol.split('_')[0]
            thb = float(res['result'].get('THB', 0))
            coin_bal = float(res['result'].get(coin, 0))
            return thb, coin_bal
        return 0.0, 0.0

    def clean_num(self, n, decimals=4):
        if n == 0: return 0
        return math.floor(n * (10**decimals)) / (10**decimals)

    def place_order(self, side, amt, rate=0, typ="market"):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        # ใช้ Market สำหรับซื้อเพื่อความชัวร์, ใช้ Market สำหรับขายตาม Logic Trailing
        payload = {
            "sym": self.symbol.lower(),
            "amt": self.clean_num(amt, 2 if side=="buy" else 4),
            "rat": 0 if typ == "market" else self.clean_num(rate, 2),
            "typ": typ
        }
        return self._request("POST", path, payload=payload, private=True)

    def calculate_ema(self, prices, period):
        if len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]:
            ema = (p * k) + (ema * (1 - k))
        return ema

    def send_detailed_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100

        now_th = self.get_local_time()
        is_holding = coin_value > self.min_trade
        status = "🚀 HOLDING" if is_holding else "💰 WAITING"

        display_pnl = pnl if is_holding else self.last_pnl
        pnl_label = "Net P/L" if is_holding else "Last Trade P/L"
        ema_str = f"{ema_val:,.2f}" if ema_val else "N/A"
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val else ""

        t_stop_price = "Waiting..."
        if self.current_stage == 3: 
            t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}"

        report = (
            f"<b>{status} | {self.symbol}</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_str} {diff_ema}\n"
            f"🕒 {pnl_label}: {display_pnl:+.2f}%\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Asset: {coin_bal:,.4f} ({coin_value:,.2f})\n"
            f"💎 Equity: {total_equity:,.2f} THB\n"
            "━━━━━━━━━━━━━━━\n"
            f"🚀 Growth: {growth_pct:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop_price}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"<b>🤖 Bitkub V6.2 Hybrid Started</b>\nMonitoring {self.symbol} (EMA {self.ema_period})")

        while True:
            try:
                # 1. Fetch Price
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                current_price = 0
                if isinstance(ticker, list):
                    for item in ticker:
                        if item['symbol'].upper() == self.symbol:
                            current_price = float(item['last']); break
                
                if current_price == 0: 
                    time.sleep(30); continue

                # 2. Indicators (Anti-Repaint)
                hist = self._request("GET", "/tradingview/history", params={
                    "symbol": self.symbol, "resolution": "15",
                    "from": int(time.time()) - 259200, "to": int(time.time())
                })
                prices = hist.get('c', [])
                ema_val = self.calculate_ema(prices[:-1], self.ema_period) if len(prices) > 1 else None
                ema_prev = self.calculate_ema(prices[:-2], self.ema_period) if len(prices) > 2 else None

                if not ema_val:
                    time.sleep(30); continue

                thb, coin_bal = self.get_balance()
                pnl = 0.0
                if self.avg_price > 0:
                    pnl = (((current_price * (1-self.fee_pct)) - (self.avg_price * (1+self.fee_pct))) / (self.avg_price * (1+self.fee_pct))) * 100

                # 3. Strategy Logic: Entry (Confirm 1% Gap)
                is_uptrend_confirmed = current_price > (ema_val * 1.01) and ema_val > (ema_prev or 0)

                if is_uptrend_confirmed and self.last_action == "sell" and thb > self.min_trade:
                    res = self.place_order("buy", thb * 0.45, 0, "market")
                    if res.get('error') == 0:
                        time.sleep(2)
                        _, new_coin = self.get_balance()
                        self.avg_price, self.total_units = current_price, new_coin
                        self.current_stage, self.last_action, self.highest_price = 1, "buy", current_price
                        self._save_state()
                        self.notify(f"<b>🟢 [BUY 1/2] Confirmed</b>\nPrice: {current_price:,.2f}")

                elif is_uptrend_confirmed and self.current_stage == 1 and pnl > 0.5 and thb > self.min_trade:
                    res = self.place_order("buy", thb * 0.95, 0, "market")
                    if res.get('error') == 0:
                        time.sleep(2)
                        _, new_total = self.get_balance()
                        added = new_total - self.total_units
                        self.avg_price = ((self.avg_price * self.total_units) + (current_price * added)) / new_total
                        self.total_units = new_total
                        self.current_stage = 2
                        self._save_state()
                        self.notify(f"<b>🟢 [BUY 2/2] Full Position</b>\nNew Avg: {self.avg_price:,.2f}")

                # 4. Strategy Logic: Exit
                if self.last_action == "buy" and self.total_units > 0:
                    self.highest_price = max(self.highest_price, current_price)

                    # ไม้ 1: Partial TP
                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        sell_amt = self.total_units * 0.5
                        res = self.place_order("sell", sell_amt, 0, "market")
                        if res.get('error') == 0:
                            self.total_units -= sell_amt
                            self.current_stage = 3
                            self._save_state()
                            self.notify(f"<b>🟠 [TP 50%]</b> Locked: {pnl:+.2f}%")

                    # ไม้ 2: Trailing / SL / Trend Exit
                    reason = None
                    if pnl <= -self.stop_loss: 
                        reason = f"Stop Loss ({pnl:.2f}%)"
                    elif self.current_stage == 3 and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop Exit"
                    elif current_price < (ema_val * 0.985): 
                        reason = "Trend Reversed (Below EMA)"

                    if reason:
                        res = self.place_order("sell", self.total_units, 0, "market")
                        if res.get('error') == 0:
                            self.last_pnl = pnl
                            self.notify(f"<b>🔴 [SELL ALL]</b>\nReason: {reason}\nNet P/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                            self._save_state()

                # Report (Every 30 Mins)
                if time.time() - self.last_report_time >= 1800:
                    self.send_detailed_report(current_price, pnl, ema_val)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

# --- Health Check Server (For Railway/Cloud) ---
def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200); self.end_headers(); self.wfile.write(b"Bot V6.2 Active")
        def log_message(self, *a): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), H)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubProBotV6_2().run()
