import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubHybridBotV7:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config (จากชุดที่ 2)
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.coin = self.symbol.split('_')[0] if '_' in self.symbol else self.symbol
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 2688)) 
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        self.ema_period = 50
        
        # State Management
        self.state_file = "bot_state_v7_hybrid.json"
        self.last_report_time = 0
        self.report_interval = 1800 # 30 mins
        self._sync_setup()

    def _sync_setup(self):
        logger.info("🛠️ Initializing & Syncing Wallet...")
        thb, coin_bal = self.get_balance()
        # ดึงราคาปัจจุบันเพื่อเช็คสถานะ
        ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
        price = 0
        if isinstance(ticker, list):
            for item in ticker:
                if item['symbol'].upper() == self.symbol: price = float(item['last'])

        if coin_bal * price > 50: # ถ้ามีเหรียญค้างอยู่
            self.last_action, self.total_units, self.current_stage = "buy", coin_bal, 2
            self.avg_price, self.highest_price = price, price
            if os.path.exists(self.state_file):
                try:
                    with open(self.state_file, "r") as f:
                        d = json.load(f)
                        self.avg_price = d.get('avg_price', price)
                        self.current_stage = d.get('stage', 2)
                        self.highest_price = d.get('highest_price', price)
                except: pass
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

    # --- API Helper Methods ---
    def get_server_time(self):
        try: return requests.get(f"{self.host}/api/v3/servertime", timeout=10).text.strip()
        except: return str(int(time.time() * 1000))

    def _get_signature(self, ts, method, path, query="", body=""):
        payload = ts + method + path + query + body
        return hmac.new(self.api_secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, params=None, payload=None, private=False):
        url = f"{self.host}{path}"
        query_str = "?" + "&".join([f"{k}={v}" for k, v in params.items()]) if params else ""
        if params: url += query_str
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = self.get_server_time()
            headers.update({
                'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': self._get_signature(ts, method, path, query_str, body_str)
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

    def place_order(self, side, amt, typ="market"):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        # ปรับทศนิยมให้ Bitkub ยอมรับ
        clean_amt = math.floor(amt * 100) / 100 if side == "buy" else round(amt, 6)
        payload = {"sym": self.symbol.lower(), "amt": clean_amt, "rat": 0, "typ": typ}
        return self._request("POST", path, payload=payload, private=True)

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def send_detailed_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100
        now_th = datetime.now(timezone.utc) + timedelta(hours=7)

        status = "🟢 HOLDING COIN" if coin_value > 50 else "⚪️ HOLDING CASH"
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val else ""

        report = (
            f"<b>{status} | Hybrid V7</b>\n"
            f"⏰ {now_th.strftime('%H:%M:%S')} (GMT+7)\n"
            "━━━━━━━━━━━━━━━\n"
            f"📊 <b>{self.symbol}</b>: <b>{price:,.2f}</b>\n"
            f"📈 EMA(50): {ema_val:,.2f} {diff_ema}\n"
            f"🕒 Net P/L: <b>{pnl:+.2f}%</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Cash: {thb_bal:,.2f}\n"
            f"💎 <b>Equity: {total_equity:,.2f}</b>\n"
            f"🚀 Growth: {growth_pct:+.2f}%\n"
            f"🛡️ TS: {f'{self.highest_price * (1-self.trailing_pct/100):,.2f}' if self.current_stage == 3 else 'Wait...'}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)
        self.last_report_time = time.time()

    def run(self):
        self.notify(f"<b>🚀 Hybrid Bot V7 Started</b>\nEMA + Pyramiding + Partial TP\nMonitoring: {self.symbol}")
        
        while True:
            try:
                # 1. ดึงราคาปัจจุบัน
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                price = 0
                if isinstance(ticker, list):
                    for item in ticker:
                        if item['symbol'].upper() == self.symbol: price = float(item['last'])

                # 2. ดึงข้อมูลกราฟและคำนวณ EMA (จากชุดที่ 3)
                hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
                prices = hist.get('c', [])
                if len(prices) < self.ema_period:
                    time.sleep(30); continue
                
                ema = sum(prices[-self.ema_period:]) / self.ema_period
                ema_prev = sum(prices[-(self.ema_period+1):-1]) / self.ema_period
                
                thb, coin_bal = self.get_balance()
                # คำนวณ Net P/L (หัก Fee 0.25% x 2)
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                # --- กลยุทธ์การซื้อ (Pyramiding + Slope Filter) ---
                # เงื่อนไขไม้ 1: ราคา > EMA 0.5% และเส้น EMA เริ่มชันขึ้น (Slope Filter จากชุดที่ 3)
                if self.last_action == "sell" and price > (ema * 1.005) and ema > (ema_prev * 1.001):
                    res = self.place_order("buy", thb * 0.48) # ซื้อไม้แรก 48%
                    if res.get('error') == 0:
                        self.avg_price, self.last_action, self.current_stage = price, "buy", 1
                        self.total_units = float(res['result'].get('rec', 0))
                        self.highest_price = price
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 1/2]</b>\nPrice: {price:,.2f}\nEMA confirms Uptrend")

                # เงื่อนไขไม้ 2: กำไรเดินแล้ว และยังเป็นเทรนด์ขาขึ้น
                elif self.current_stage == 1 and pnl > 0.5 and price > (ema * 1.005):
                    res = self.place_order("buy", thb * 0.95)
                    if res.get('error') == 0:
                        new_units = float(res['result'].get('rec', 0))
                        self.avg_price = ((self.avg_price * self.total_units) + (price * new_units)) / (self.total_units + new_units)
                        self.total_units += new_units
                        self.current_stage = 2
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 2/2] Pyramiding</b>\nNew Avg: {self.avg_price:,.2f}")

                # --- กลยุทธ์การขาย (Partial TP + Stop Loss + Trailing) ---
                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)
                    
                    # 1. แบ่งขาย 50% เมื่อถึงเป้าแรก (จากชุดที่ 2)
                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        res = self.place_order("sell", coin_bal * 0.5)
                        if res.get('error') == 0:
                            self.total_units -= (coin_bal * 0.5)
                            self.current_stage = 3 # เข้าสู่ช่วงรันเทรนด์ด้วยครึ่งที่เหลือ
                            self._save_state()
                            self.notify(f"💰 <b>[TAKE PROFIT 50%]</b>\nLocked profit at {pnl:+.2f}%\nRunning rest with Trailing Stop")

                    # 2. เงื่อนไขขายล้างพอร์ต (Stop Loss / Trailing / Trend Reverse)
                    reason = None
                    if pnl <= -self.stop_loss: 
                        reason = f"Stop Loss ({pnl:.2f}%)"
                    elif self.current_stage == 3 and price < self.highest_price * (1 - self.trailing_pct/100):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif price < (ema * 0.99) and ema < ema_prev: # Trend Reverse (จากชุดที่ 3)
                        reason = "Trend Reversed (EMA Down)"

                    if reason:
                        res = self.place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            self.notify(f"🔴 <b>[SELL ALL]</b>\nReason: {reason}\nNet P/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units = "sell", 0, 0, 0
                            self._save_state()

                # 3. ส่งรายงาน
                if time.time() - self.last_report_time >= self.report_interval:
                    self.send_detailed_report(price, pnl, ema)

            except Exception as e:
                logger.error(f"Main Loop Error: {e}")
            
            time.sleep(30)

# --- Health Check Server (จากชุดที่ 3) ---
def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200); self.end_headers(); self.wfile.write(b"Bot V7 Hybrid Active")
        def log_message(self, *a): return
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubHybridBotV7().run()
