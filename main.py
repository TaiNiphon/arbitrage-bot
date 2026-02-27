import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000.00)) 
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        
        # เพิ่มตัวแปรค่าธรรมเนียม (Default 0.25%)
        self.fee_pct = float(os.getenv("FEE_PCT", 0.25)) / 100 
        self.min_trade = 50.0  # ขั้นต่ำ Bitkub

        self.state_file = "bot_state_v5.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        self.last_report_time = 0

    def get_local_time(self):
        now = datetime.now(timezone.utc)
        return now + timedelta(hours=7)

    def notify(self, msg):
        if not self.tg_token or not self.tg_chat_id:
            logger.info(f"Notification: {msg}")
            return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Notify Error: {e}")

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

    def get_actual_cost(self):
        res = self._request("GET", f"/api/v3/market/my-order-history?sym={self.symbol}&p=1&l=1", private=True)
        if res and res.get('error') == 0 and res.get('result'):
            last_order = res['result'][0]
            if last_order['side'].lower() == 'buy':
                return float(last_order['rat']), float(last_order['amount'])
        return 0.0, 0.0

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res and res.get('error') == 0:
            coin_key = self.symbol.replace("THB_", "").replace("_THB", "")
            thb_bal = float(res['result'].get('THB', 0))
            coin_bal = float(res['result'].get(coin_key, 0))
            return thb_bal, coin_bal
        return 0.0, 0.0

    def calculate_net_pnl(self, current_price):
        """ คำนวณกำไรสุทธิแบบหักค่าธรรมเนียม 0.25% ทั้งไปและกลับ """
        if self.avg_price <= 0: return 0.0
        buy_cost = self.avg_price * (1 + self.fee_pct)
        sell_value = current_price * (1 - self.fee_pct)
        return ((sell_value - buy_cost) / buy_cost) * 100

    def send_detailed_report(self, price, pnl, ema_val=None):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100

        now_th = self.get_local_time()
        status = "💰 HOLDING CASH" if coin_bal * price < self.min_trade else "🚀 HOLDING COIN"

        ema_str = f"{ema_val:,.2f}" if ema_val else "N/A"
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val else ""
        t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.last_action == "buy" and pnl >= self.target_profit else "Waiting..."

        report = (
            f"<b>{status}</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"<b>📊 MARKET: {self.symbol}</b>\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA(50): {ema_str} {diff_ema}\n"
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
            f"🛡️ Trailing @: {t_stop_price}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def place_order_v3(self, side, amount, price):
        if amount < (self.min_trade if side == "buy" else 0.0001): 
            logger.warning(f"Order too small: {amount}")
            return None
            
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        typ = "limit" if side == "buy" else "market"
        clean_amount = math.floor(amount * 10000) / 10000 if side == "sell" else int(amount)
        
        payload = {
            "sym": self.symbol, "amt": clean_amount,
            "rat": round(price, 4) if typ == "limit" else 0, "typ": typ
        }
        return self._request("POST", path, payload, private=True)

    def calculate_ema(self, prices, period=50):
        if not prices or len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        ema_list = [ema]
        for p in prices[period:]:
            ema = (p * k) + (ema * (1 - k))
            ema_list.append(ema)
        return ema_list

    def run(self):
        self.notify(f"<b>🚀 Bot V5.5 Ultimate Started</b>\nMonitoring {self.symbol}")
        search_sym = f"{self.symbol.split('_')[1]}_{self.symbol.split('_')[0]}" if "_" in self.symbol else self.symbol

        while True:
            try:
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                current_price = None
                if isinstance(ticker_res, list):
                    for item in ticker_res:
                        if item.get('symbol') in [search_sym, self.symbol]:
                            current_price = float(item['last']); break
                elif isinstance(ticker_res, dict):
                    res_data = ticker_res.get('result', ticker_res)
                    current_price = float(res_data.get(self.symbol, res_data.get(search_sym, {}))['last'])

                if current_price is None:
                    time.sleep(30); continue

                history = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                if not isinstance(history, dict) or 'c' not in history:
                    time.sleep(30); continue

                prices = history.get('c', [])
                ema_series = self.calculate_ema(prices, 50)
                if not ema_series: time.sleep(30); continue

                ema_val, ema_prev = ema_series[-1], ema_series[-2]
                is_uptrend = current_price > (ema_val * 1.005) and ema_val > (ema_prev * 1.001)

                thb, coin_bal = self.get_balance()
                
                # Sync State
                if coin_bal * current_price > self.min_trade: 
                    if self.last_action == "sell" or self.current_stage == 0:
                        cost_price, _ = self.get_actual_cost()
                        self.last_action, self.current_stage, self.total_units = "buy", 2, coin_bal
                        self.avg_price = cost_price if cost_price > 0 else current_price
                        self.highest_price = max(self.avg_price, current_price)
                        self._save_state()

                # คำนวณ PNL แบบหักค่าธรรมเนียม
                pnl = self.calculate_net_pnl(current_price)

                if is_uptrend and self.current_stage < 2:
                    res_open = self._request("GET", f"/api/v3/market/my-open-orders?sym={self.symbol}", private=True)
                    has_buy_order = any(o['side'].lower() == 'buy' for o in res_open.get('result', [])) if res_open.get('result') else False
                    
                    if not has_buy_order:
                        if self.current_stage == 0 and thb >= self.min_trade:
                            res = self.place_order_v3("buy", thb * 0.49, current_price)
                            if res and res.get('error') == 0:
                                self.total_units, self.avg_price, self.current_stage, self.last_action = float(res['result']['rec']), float(res['result']['rat']), 1, "buy"
                                self.highest_price = self.avg_price
                                self._save_state(); self.notify(f"<b>🟢 [BUY 1/2]</b> @ {self.avg_price:,.2f}")
                        elif self.current_stage == 1 and pnl >= 0.2 and thb >= self.min_trade:
                            res = self.place_order_v3("buy", thb * 0.95, current_price)
                            if res and res.get('error') == 0:
                                nq, nr = float(res['result']['rec']), float(res['result']['rat'])
                                self.avg_price = ((self.avg_price * self.total_units) + (nq * nr)) / (self.total_units + nq)
                                self.total_units += nq
                                self.current_stage = 2
                                self._save_state(); self.notify(f"<b>🟢 [BUY 2/2]</b> New Avg: {self.avg_price:,.2f}")

                if self.last_action == "buy" and self.total_units > 0:
                    if current_price > self.highest_price: 
                        self.highest_price = current_price
                        self._save_state()

                    reason = None
                    if pnl <= -self.stop_loss: 
                        reason = f"Stop Loss ({pnl:.2f}%)"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif current_price < (ema_val * 0.990):
                        reason = "Trend Reversed (Price below EMA 1%)"

                    if reason:
                        res = self.place_order_v3("sell", self.total_units, current_price)
                        if res and res.get('error') == 0:
                            self.notify(f"<b>🔴 [SELL ALL]</b>\nReason: {reason}\nNet P/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                            self._save_state()

                if time.time() - self.last_report_time >= 1800:
                    self.send_detailed_report(current_price, pnl, ema_val)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"🔥 Loop Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start() # คงฟังก์ชันเดิมไว้
    BitkubBot().run()
