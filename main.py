import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubProBotV6_Final:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Trading Strategy Config (Logic จาก V6.0 Pro)
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.coin = self.symbol.split('_')[0]
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 2030.71)) 
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.fee_pct = 0.0025 
        self.min_trade = 10.0 

        self.state_file = f"bot_state_{self.symbol.lower()}.json"
        self.last_buy_time = 0
        self.last_report_time = 0
        
        # --- Boot Up: Sync With Wallet ---
        self._sync_with_wallet()

    def _sync_with_wallet(self):
        """ระบบตรวจสอบยอดจริงเพื่อป้องกันการหลงลืมสถานะ"""
        logger.info("🛠️ Syncing with Bitkub Wallet...")
        thb, coin_bal = self.get_balance()
        
        # ดึงราคาล่าสุดเพื่อประเมินมูลค่า
        ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
        price = 0
        if isinstance(ticker, list):
            for item in ticker:
                if item['symbol'].upper() == self.symbol: price = float(item['last'])

        # ถ้ามีเหรียญในกระเป๋าเกินขั้นต่ำ ให้ถือว่าสถานะคือ BUY (Stage 2)
        if coin_bal * price > self.min_trade:
            self.last_action = "buy"
            self.total_units = coin_bal
            self.avg_price = price # ใช้ราคาปัจจุบันเป็นทุนอ้างอิงถ้าหาไฟล์ state ไม่เจอ
            self.current_stage = 2
            self.highest_price = price
            self._load_state() # พยายามโหลดทุนเดิมจากไฟล์ถ้ามี
            logger.info(f"✅ Detected {coin_bal} {self.coin}. Setting status to HOLDING.")
        else:
            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price, self.last_pnl = "sell", 0.0, 0, 0.0, 0.0, 0.0
            logger.info("💰 No assets detected. Ready for new signals.")

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    self.avg_price = d.get('avg_price', self.avg_price)
                    self.current_stage = d.get('stage', self.current_stage)
                    self.highest_price = d.get('highest_price', self.highest_price)
            except: pass

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price, "last_pnl": getattr(self, 'last_pnl', 0.0)
                }, f)
        except Exception as e: logger.error(f"Save State Error: {e}")

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def get_server_time(self):
        try: return requests.get(f"{self.host}/api/v3/servertime").text.strip()
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
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except: return {"error": 999}

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res and res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get(self.coin, 0))
        return 0.0, 0.0

    def clean_num(self, n, decimals=4):
        return math.floor(n * (10**decimals)) / (10**decimals)

    def place_order(self, side, amt, typ="market"):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        payload = {
            "sym": self.symbol.lower(),
            "amt": self.clean_num(amt, 2 if side=="buy" else 4),
            "rat": 0, "typ": typ
        }
        return self._request("POST", path, payload=payload, private=True)

    def calculate_ema(self, prices, period):
        if len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]: ema = (p * k) + (ema * (1 - k))
        return ema

    def send_detailed_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100
        now_th = self.get_local_time()

        status = "🚀 HOLDING" if coin_value > self.min_trade else "💰 WAITING"
        t_stop = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.current_stage == 3 else "Waiting..."

        report = (
            f"<b>{status} | {self.symbol}</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_val:,.2f}\n"
            f"🕒 P/L: {pnl:+.2f}% (Fee Incl.)\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Asset: {coin_bal:,.4f} ({coin_value:,.2f})\n"
            f"💎 Equity: {total_equity:,.2f} THB\n"
            "━━━━━━━━━━━━━━━\n"
            f"🚀 Growth: {growth_pct:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"<b>🤖 Bot V6.0 Special Edition</b>\nStrict Mode: Pyramiding Only")

        while True:
            try:
                # 1. Fetch Price & Indicators
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                price = 0
                if isinstance(ticker, list):
                    for item in ticker:
                        if item['symbol'].upper() == self.symbol: price = float(item['last'])

                hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-259200, "to": int(time.time())})
                prices = hist.get('c', [])
                ema = self.calculate_ema(prices, self.ema_period)
                ema_prev = self.calculate_ema(prices[:-1], self.ema_period)

                thb, coin_bal = self.get_balance()
                
                # คำนวณ P/L แบบหัก Fee จริง
                pnl = 0.0
                if self.avg_price > 0:
                    buy_cost = self.avg_price * (1 + self.fee_pct)
                    sell_val = price * (1 - self.fee_pct)
                    pnl = ((sell_val - buy_cost) / buy_cost) * 100

                # --- 2. Entry Logic (V6.0 Pro) ---
                is_uptrend = ema and price > (ema * 1.01) and ema > ema_prev

                # ไม้ 1: ซื้อเมื่อ Trend Confirm
                if is_uptrend and self.last_action == "sell" and thb > self.min_trade:
                    buy_amt = thb * 0.45
                    res = self.place_order("buy", buy_amt)
                    if res.get('error') == 0:
                        self.avg_price = price
                        self.total_units = float(res['result'].get('rec', buy_amt/price * (1-self.fee_pct)))
                        self.current_stage, self.last_action, self.highest_price = 1, "buy", price
                        self.last_buy_time = time.time()
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 1/2] Confirmed</b>\nPrice: {price:,.2f}\nUnits: {self.total_units:,.4f}")

                # ไม้ 2: ซื้อเมื่อกำไรเขียว (Pyramiding)
                elif is_uptrend and self.current_stage == 1 and pnl > 0.5 and thb > self.min_trade:
                    buy_amt = thb * 0.95
                    res = self.place_order("buy", buy_amt)
                    if res.get('error') == 0:
                        new_units = float(res['result'].get('rec', buy_amt/price * (1-self.fee_pct)))
                        self.avg_price = ((self.avg_price * self.total_units) + (price * new_units)) / (self.total_units + new_units)
                        self.total_units += new_units
                        self.current_stage, self.last_buy_time = 2, time.time()
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 2/2] Pyramiding</b>\nAdded: {new_units:,.4f}\nNew Avg: {self.avg_price:,.2f}")

                # --- 3. Exit Logic (V6.0 Pro) ---
                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)
                    hold_time = time.time() - self.last_buy_time

                    # Partial TP 50%
                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        sell_amt = self.total_units * 0.5
                        res = self.place_order("sell", sell_amt)
                        if res.get('error') == 0:
                            self.total_units -= sell_amt
                            self.current_stage = 3
                            self._save_state()
                            self.notify(f"🟠 <b>[TP 50%] Locked</b>\nPNL: {pnl:+.2f}%")

                    # Exit Conditions (พร้อม Buffer ป้องกัน Noise)
                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif hold_time > 900: # ต้องถือเกิน 15 นาทีถึงจะยอมให้ EMA/Trailing ทำงาน
                        if self.current_stage == 3 and price < self.highest_price * (1 - self.trailing_pct/100):
                            reason = f"Trailing Stop ({pnl:.2f}%)"
                        elif price < (ema * 0.985): # หลุด EMA 1.5%
                            reason = "Trend Reversed"

                    if reason:
                        res = self.place_order("sell", self.total_units)
                        if res.get('error') == 0:
                            received = (self.total_units * price) * (1 - self.fee_pct)
                            self.notify(f"🔴 <b>[SELL ALL]</b>\nReason: {reason}\nReceived: {received:,.2f} THB")
                            self.last_action, self.current_stage, self.avg_price, self.total_units = "sell", 0, 0, 0
                            self.last_pnl = pnl
                            self._save_state()

                if time.time() - self.last_report_time >= 1800:
                    self.send_detailed_report(price, pnl, ema)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    BitkubProBotV6_Final().run()
