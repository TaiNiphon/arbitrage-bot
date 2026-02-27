import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # API Keys & TG Settings
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Trading Parameters
        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000.00)) 
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        # Anti-Whipsaw Parameters
        self.buy_buffer = 1.008  # Price > EMA 0.8%
        self.sell_buffer = 0.992 # Price < EMA 0.8%

        # State Management
        self.state_file = "bot_state_v6.json"
        self._init_state()
        
        # Report Timer (ตั้งค่าเป็น 0 เพื่อให้รายงานส่งทันทีที่เริ่มรัน)
        self.last_report_time = 0 
        self.report_interval = 1800 # 30 minutes (in seconds)

    def _init_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    self.last_action = d.get('last_action', 'sell')
                    self.avg_price = d.get('avg_price', 0.0)
                    self.current_stage = d.get('stage', 0)
                    self.total_units = d.get('total_units', 0.0)
                    self.highest_price = d.get('highest_price', 0.0)
            except: self._reset_state_vars()
        else: self._reset_state_vars()

    def _reset_state_vars(self):
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price
                }, f)
        except Exception as e: logger.error(f"Save State Error: {e}")

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def notify(self, msg):
        if not self.tg_token or not self.tg_chat_id:
            logger.info(f"Telegram Log: {msg}")
            return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=10)
        except Exception as e: logger.error(f"Notify Error: {e}")

    # --- API Helper Methods ---
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
                    'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                    'X-BTK-SIGN': self._get_signature(ts, method, path, body_str)
                })
            except: return {"error": 999}
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except: return {"error": 999}

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res and res.get('error') == 0:
            coin_key = self.symbol.split('_')[1] if '_' in self.symbol else self.symbol
            thb_bal = float(res['result'].get('THB', 0))
            coin_bal = float(res['result'].get(coin_key, 0))
            return thb_bal, coin_bal
        return 0.0, 0.0

    # --- Trading Core ---
    def calculate_ema(self, prices, period=50):
        if not prices or len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        ema_list = [ema]
        for p in prices[period:]:
            ema = (p * k) + (ema * (1 - k))
            ema_list.append(ema)
        return ema_list

    def place_order_v3(self, side, amount, price):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        typ = "limit" if side == "buy" else "market"
        payload = {
            "sym": self.symbol, "amt": int(amount) if side == "buy" else round(amount, 6),
            "rat": round(price, 4) if typ == "limit" else 0, "typ": typ
        }
        return self._request("POST", path, payload, private=True)

    # --- Reporting Logic ---
    def send_detailed_report(self, price, ema_val=None):
        try:
            thb_bal, coin_bal = self.get_balance()
            coin_value = coin_bal * price
            total_equity = thb_bal + coin_value
            net_profit = total_equity - self.initial_equity
            growth_pct = (net_profit / self.initial_equity) * 100
            pnl = ((price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

            now_th = self.get_local_time()
            status_icon = "🟢" if coin_bal * price > 50 else "⚪️"
            status_text = "HOLDING COIN" if coin_bal * price > 50 else "HOLDING CASH"

            ema_info = f"{ema_val:,.2f} ({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val else "Calculating..."
            ts_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.last_action == "buy" and pnl >= self.target_profit else "N/A"

            report = (
                f"<b>{status_icon} SYSTEM STATUS: {status_text}</b>\n"
                f"⏰ <i>{now_th.strftime('%d/%m/%Y | %H:%M:%S')} (GMT+7)</i>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>📊 MARKET DATA</b>\n"
                f"• Symbol:  <code>{self.symbol}</code>\n"
                f"• Price:   <b>{price:,.4f} THB</b>\n"
                f"• EMA(50): {ema_info}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>🏦 PORTFOLIO</b>\n"
                f"• Net Equity: <b>{total_equity:,.2f} THB</b>\n"
                f"• Growth:     <pre>{growth_pct:+.2f}%</pre>\n"
                f"• Profit:     {net_profit:,.2f} THB\n"
                f"• THB: {thb_bal:,.2f} | Coin: {coin_bal:,.4f}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>🎯 STRATEGY</b>\n"
                f"• Entry Avg:  {self.avg_price:,.4f}\n"
                f"• Current PL: <b>{pnl:+.2f}%</b>\n"
                f"• Trailing:   {ts_price}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            self.notify(report)
            self.last_report_time = time.time()
        except Exception as e:
            logger.error(f"Report Generation Error: {e}")

    def run(self):
        welcome_msg = (
            f"<b>🤖 Bitkub Bot V6.1 Active</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• Strategy: EMA Cross + Whipsaw Protection\n"
            f"• Target: +{self.target_profit}% | SL: -{self.stop_loss}%\n"
            f"• Monitoring: <b>{self.symbol}</b>"
        )
        self.notify(welcome_msg)
        
        # Search symbol naming for different API responses
        search_sym = f"{self.symbol.split('_')[1]}_{self.symbol.split('_')[0]}" if "_" in self.symbol else self.symbol

        while True:
            try:
                # 1. Fetch Current Price
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                current_price = None
                if isinstance(ticker_res, dict):
                    res_data = ticker_res.get('result', ticker_res)
                    current_price = float(res_data.get(self.symbol, res_data.get(search_sym, {}))['last'])

                if current_price is None:
                    time.sleep(30); continue

                # 2. Handle Reporting (Check time before processing indicator)
                if time.time() - self.last_report_time >= self.report_interval:
                    # พยายามหา EMA มาใส่ในรายงาน (ถ้ามี)
                    self.send_detailed_report(current_price)

                # 3. Fetch Technical Data
                history = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                if not isinstance(history, dict) or 'c' not in history:
                    time.sleep(30); continue

                prices = history.get('c', [])
                ema_series = self.calculate_ema(prices, 50)
                if not ema_series:
                    time.sleep(30); continue

                ema_val, ema_prev = ema_series[-1], ema_series[-2]
                is_uptrend = current_price > (ema_val * self.buy_buffer) and ema_val > ema_prev
                
                thb, coin_bal = self.get_balance()
                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # Auto-sync state if manual trades were made
                if coin_bal * current_price > 50 and self.last_action == "sell":
                    self.last_action, self.current_stage, self.total_units = "buy", 2, coin_bal
                    self.avg_price = current_price if self.avg_price == 0 else self.avg_price
                    self._save_state()

                # --- BUY LOGIC ---
                if is_uptrend and self.current_stage < 2:
                    if prices[-2] > ema_prev: # Confirmation candle
                        if self.current_stage == 0 and thb >= 10:
                            res = self.place_order_v3("buy", thb * 0.98, current_price) # Buy 98% to cover fees
                            if res and res.get('error') == 0:
                                self.total_units = float(res['result']['rec'])
                                self.avg_price = float(res['result']['rat'])
                                self.current_stage, self.last_action = 2, "buy"
                                self.highest_price = self.avg_price
                                self._save_state()
                                self.notify(f"<b>✅ [BUY SUCCESS]</b>\nPrice: {self.avg_price:,.4f}\nStage: Full Entry")

                # --- SELL LOGIC ---
                if self.last_action == "buy" and self.total_units > 0:
                    if current_price > self.highest_price: 
                        self.highest_price = current_price
                        self._save_state()

                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif current_price < (ema_val * self.sell_buffer):
                        reason = "Trend Reversed (Price < EMA Buffer)"

                    if reason:
                        res = self.place_order_v3("sell", self.total_units, current_price)
                        if res and res.get('error') == 0:
                            exit_price = current_price
                            self.notify(f"<b>🚨 [SELL ACTION]</b>\nReason: {reason}\nPrice: {exit_price:,.4f}\nNet P/L: {pnl:+.2f}%")
                            self._reset_state_vars()
                            self._save_state()

            except Exception as e:
                logger.error(f"Main Loop Error: {e}")
            
            time.sleep(30)

# --- Health Check Server ---
def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running...")
        def log_message(self, *a): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), H).serve_forever()

if __name__ == "__main__":
    # Start Health Check in Background
    threading.Thread(target=run_health_check, daemon=True).start()
    # Start Trading Bot
    BitkubBot().run()
