import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubProBot:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Trading Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() # แนะนำใช้ XRP_THB ตาม V3
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000.00)) 
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    # ขายไม้แรก 50% ที่กำไรเท่าไหร่
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 5.0)) # เป้าหมายหลัก
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.fee_pct = 0.0025 # 0.25%
        self.min_trade = 10.0 # Bitkub V3 min quote size is usually 10 THB

        self.state_file = "bot_state_v6.json"
        self._load_state()
        self.last_report_time = 0

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    self.last_action = d.get('last_action', 'sell')
                    self.avg_price = d.get('avg_price', 0.0)
                    self.current_stage = d.get('stage', 0) # 0=cash, 1=half_buy, 2=full_buy, 3=tp1_done
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
        # Bitkub V3 Signature: ts + method + path + query + body
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
                'X-BTK-APIKEY': self.api_key,
                'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': self._get_signature(ts, method, path, query_str, body_str)
            })
        
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except Exception as e:
            logger.error(f"Request Error: {e}")
            return {"error": 999}

    def notify(self, msg):
        if not self.tg_token or not self.tg_chat_id: return
        try:
            requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res and res.get('error') == 0:
            coin = self.symbol.split('_')[0]
            thb = float(res['result'].get('THB', 0))
            coin_bal = float(res['result'].get(coin, 0))
            return thb, coin_bal
        return 0.0, 0.0

    def clean_num(self, n, step=0.01):
        # ปรับเลขให้ไม่มีทศนิยมเกินที่ Bitkub กำหนด (Trailing zero fix)
        if n == 0: return 0
        decimals = abs(int(math.log10(step))) if step < 1 else 0
        return math.floor(n * (10**decimals)) / (10**decimals)

    def place_order(self, side, amt, rate=0, typ="market"):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        # Bitkub V3: amt ต้องเป็นตัวเลข (float) ที่ไม่มี trailing zero
        payload = {
            "sym": self.symbol.lower(),
            "amt": self.clean_num(amt, 0.01 if side=="buy" else 0.0001),
            "rat": self.clean_num(rate, 0.01) if typ == "limit" else 0,
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
        total_equity = thb_bal + (coin_bal * price)
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        
        status = "🟢 ACTIVE HOLD" if coin_bal * price > 10 else "⚪️ SCANNING"
        tp_line = f"🎯 TP1: {self.avg_price * (1 + self.tp_stage_1/100):,.2f}" if self.current_stage in [1,2] else "N/A"

        report = (
            f"<b>{status} | {self.symbol}</b>\n"
            f"💰 Price: {price:,.2f} | EMA: {ema_val:,.2f}\n"
            f"📊 P/L: {pnl:+.2f}% | Growth: {growth:+.2f}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💵 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Asset: {coin_bal:,.4f} ({coin_bal*price:,.2f})\n"
            f"💎 Equity: {total_equity:,.2f} THB\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🛡️ SL: {self.avg_price * (1 - self.stop_loss/100):,.2f}\n"
            f"{tp_line}\n"
            f"🛰️ Mode: 2-Step Profit Taker"
        )
        self.notify(report)

    def run(self):
        self.notify(f"<b>🤖 Bitkub Bot Pro V6.0</b>\nSystem initialized for {self.symbol}")
        
        while True:
            try:
                # 1. Get Market Data
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                if not ticker or 'error' in ticker: 
                    time.sleep(10); continue
                
                # หาตัวที่ตรงกับ symbol (V3 คืนค่าเป็น List)
                current_price = 0
                for item in ticker:
                    if item['symbol'].upper() == self.symbol:
                        current_price = float(item['last'])
                        break
                
                # 2. Get Chart & EMA
                hist = self._request("GET", "/tradingview/history", params={
                    "symbol": self.symbol, "resolution": "15",
                    "from": int(time.time()) - (86400 * 3), "to": int(time.time())
                })
                prices = hist.get('c', [])
                ema_val = self.calculate_ema(prices, self.ema_period)
                ema_prev = self.calculate_ema(prices[:-1], self.ema_period)

                if not ema_val or not ema_prev: continue

                # Strategy Logic
                thb, coin_bal = self.get_balance()
                pnl = 0.0
                if self.avg_price > 0:
                    # Net P/L หักค่าธรรมเนียมขาไป-กลับ
                    pnl = (((current_price * (1-self.fee_pct)) - (self.avg_price * (1+self.fee_pct))) / (self.avg_price * (1+self.fee_pct))) * 100

                # --- BUY LOGIC (2 ไม้) ---
                is_uptrend = current_price > ema_val and ema_val > ema_prev
                
                if is_uptrend and self.last_action == "sell" and thb > 10:
                    # ไม้ที่ 1 (50% ของเงินสด)
                    res = self.place_order("buy", thb * 0.5, current_price, "limit")
                    if res.get('error') == 0:
                        self.avg_price = float(res['result']['rat'])
                        self.total_units = float(res['result']['rec'])
                        self.current_stage = 1
                        self.last_action = "buy"
                        self.highest_price = self.avg_price
                        self._save_state()
                        self.notify(f"<b>🟢 BUY Step 1/2</b>\nPrice: {self.avg_price}")

                elif is_uptrend and self.current_stage == 1 and pnl > 0.5 and thb > 10:
                    # ไม้ที่ 2 (เข้าเมื่อไม้แรกกำไรแล้ว)
                    res = self.place_order("buy", thb * 0.95, current_price, "limit")
                    if res.get('error') == 0:
                        new_units = float(res['result']['rec'])
                        new_rate = float(res['result']['rat'])
                        self.avg_price = ((self.avg_price * self.total_units) + (new_units * new_rate)) / (self.total_units + new_units)
                        self.total_units += new_units
                        self.current_stage = 2
                        self._save_state()
                        self.notify(f"<b>🟢 BUY Step 2/2 (Full)</b>\nAvg: {self.avg_price:,.2f}")

                # --- SELL LOGIC (2 ไม้) ---
                if self.last_action == "buy" and self.total_units > 0:
                    self.highest_price = max(self.highest_price, current_price)
                    
                    # 1. ขายไม้แรก (50%) เมื่อถึงเป้ากำไรแรก (ล็อคกำไร)
                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        sell_amt = self.total_units * 0.5
                        res = self.place_order("sell", sell_amt, 0, "market")
                        if res.get('error') == 0:
                            self.total_units -= sell_amt
                            self.current_stage = 3 # Stage 3: Partial Profit Taken
                            self._save_state()
                            self.notify(f"<b>🟠 TAKE PROFIT (50%)</b>\nLocked: {pnl:+.2f}%\nRunning rest with Trailing Stop...")

                    # 2. ขายทั้งหมด (Trailing Stop หรือ Stop Loss)
                    sell_reason = None
                    if pnl <= -self.stop_loss:
                        sell_reason = f"Stop Loss ({pnl:.2f}%)"
                    elif self.current_stage == 3 and current_price <= (self.highest_price * (1 - self.trailing_pct/100)):
                        sell_reason = f"Trailing Stop Exit @ {pnl:.2f}%"
                    elif current_price < ema_val * 0.985: # เทรนด์เปลี่ยนชัดเจน
                        sell_reason = "Trend Reversed"

                    if sell_reason:
                        res = self.place_order("sell", self.total_units, 0, "market")
                        if res.get('error') == 0:
                            self.last_pnl = pnl
                            self.notify(f"<b>🔴 SELL ALL (Exit)</b>\nReason: {sell_reason}\nFinal P/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                            self._save_state()

                # Report ทุก 1 ชม.
                if time.time() - self.last_report_time >= 3600:
                    self.send_detailed_report(current_price, pnl, ema_val)
                    self.last_report_time = time.time()

            except Exception as e:
                logger.error(f"Main Loop Error: {e}")
            time.sleep(30)

# --- Health Check Server ---
def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200); self.end_headers(); self.wfile.write(b"Bot V6 Active")
        def log_message(self, *a): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubProBot().run()
