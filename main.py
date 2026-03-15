import os, requests, time, hmac, hashlib, json, threading, logging, math
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubUltimateV8_7_3_TITAN:
    def __init__(self):
        # 1. API & Telegram Setup
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # 2. Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB").strip().upper() 
        self.coin = self.symbol.split('_')[0]

        # Financial Parameters
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "5000")).replace(',', ''))
        self.ema_period = int(os.getenv("EMA_PERIOD", 20))
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 1.5))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.atr_multiplier = float(os.getenv("ATR_MULTIPLIER", 1.5))
        self.breakeven_pct = float(os.getenv("BREAKEVEN_PCT", 0.5))
        self.slope_threshold = float(os.getenv("SIDEWAYS_SLOPE_THRESHOLD", 0.05))

        # 🛡️ Advanced Filters
        self.adx_min = float(os.getenv("ADX_MIN", 20)) # ปรับตามที่เราคุยกันให้เข้าไวขึ้น
        self.buy_alloc_pct = float(os.getenv("SIDEWAYS_BUY_ALLOC", 50)) / 100

        # 3. Internal State
        self.state_file = "bot_state_v8_7_titan.json"
        self.last_action = "sell"
        self.avg_price, self.total_units, self.current_stage = 0.0, 0.0, 0
        self.highest_price, self.dynamic_sl, self.last_sell_time = 0.0, 0.0, 0 
        self.market_phase, self.big_trend = "INITIALIZING", "UNKNOWN"
        self.last_report_time = 0
        self.last_summary_date = ""

        self._load_state()
        self._init_db()

    # --- [Database & Reporting Intelligence] ---

    def _init_db(self):
        db_url = os.getenv("DATABASE_URL")
        if not db_url: return
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol TEXT,
                    side TEXT,
                    price FLOAT,
                    amount FLOAT,
                    pnl_pct FLOAT,
                    reason TEXT,
                    big_trend TEXT
                )
            ''')
            conn.commit(); cur.close(); conn.close()
            logger.info("✅ Database Table Ready")
        except Exception as e: logger.error(f"❌ DB Init Error: {e}")

    def _log_to_db(self, side, price, amount, pnl=0.0, reason=""):
        db_url = os.getenv("DATABASE_URL")
        if not db_url: return
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO trade_history (symbol, side, price, amount, pnl_pct, reason, big_trend)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (self.symbol, side, price, amount, pnl, reason, self.big_trend))
            conn.commit(); cur.close(); conn.close()
        except Exception as e: logger.error(f"❌ DB Logging Error: {e}")

    # --- [Core Functions] ---

    def get_big_trend(self):
        try:
            hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "60", "from": int(time.time())-432000, "to": int(time.time())})
            if not hist or 'c' not in hist or len(hist['c']) < 20: return "UNKNOWN"
            c = np.array(hist['c'], dtype=float)
            ema_big = self.calculate_ema(c, 20)
            return "BULLISH" if c[-1] > ema_big else "CAUTION"
        except: return "UNKNOWN"

    def update_indicators(self):
        try:
            hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
            if not hist or 'c' not in hist: return None
            c, h, l = np.array(hist['c'], dtype=float), np.array(hist['h'], dtype=float), np.array(hist['l'], dtype=float)
            ema = self.calculate_ema(c, self.ema_period)
            ema_prev = self.calculate_ema(c[:-1], self.ema_period)
            slope = (ema - ema_prev) / ema_prev * 100

            upmove = h[1:] - h[:-1]; downmove = l[:-1] - l[1:]
            dm_p = np.where((upmove > downmove) & (upmove > 0), upmove, 0)
            dm_m = np.where((downmove > upmove) & (downmove > 0), downmove, 0)
            tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            
            def smooth(x, p):
                out = np.zeros_like(x); out[p-1] = np.mean(x[:p])
                for i in range(p, len(x)): out[i] = (out[i-1] * (p - 1) + x[i]) / p
                return out

            adx = smooth(100 * abs(smooth(dm_p, 14) - smooth(dm_m, 14)) / (smooth(dm_p, 14) + smooth(dm_m, 14) + 1e-9), 14)[-1]
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            return {"ema": ema, "slope": slope, "adx": adx, "rsi": rsi, "atr": smooth(tr, 14)[-1], "price": c[-1]}
        except: return None

    def run(self):
        self.notify(f"<b>⚔️ TITAN V8.7.3 HYBRID</b>\n{self.symbol} | Online & Monitoring")
        while True:
            try:
                data = self.update_indicators()
                if not data: time.sleep(20); continue
                price, ema, slope, adx, rsi, atr = data['price'], data['ema'], data['slope'], data['adx'], data['rsi'], data['atr']
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0
                self.big_trend = self.get_big_trend()
                thb, coin_bal = self.get_balance()

                # --- 🔴 BUY LOGIC ---
                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 300:
                    if adx > self.adx_min and slope > self.slope_threshold:
                        buy_pct = 0.90 if self.big_trend == "BULLISH" else self.buy_alloc_pct
                        res = self.place_order("buy", thb * buy_pct)
                        if res.get('error') == 0:
                            self._update_buy_state(price, thb * buy_pct, (2 if self.big_trend == "BULLISH" else 1))
                            self.notify(f"<b>🚀 BUY ORDER EXECUTED!</b>\nPrice: {price:,.2f}\nAmt: {thb*buy_pct:,.2f} THB")

                # --- 🟢 SELL LOGIC ---
                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)
                    if pnl >= 1.0: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.003)
                    self.dynamic_sl = max(self.dynamic_sl, self.highest_price - (atr * (self.atr_multiplier if pnl < self.tp_stage_1 else 0.6)))
                    
                    reason = None
                    if pnl <= -self.stop_loss_pct: reason = "Stop Loss"
                    elif pnl >= self.breakeven_pct and price <= (self.avg_price * 1.0025): reason = "Breakeven"
                    elif price <= self.dynamic_sl: reason = "Trailing Stop"
                    
                    if reason:
                        res = self.place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            self.notify(f"<b>💰 SELL ORDER EXECUTED!</b>\nPrice: {price:,.2f}\nP/L: {pnl:+.2f}%\nReason: {reason}")
                            self._update_sell_state(pnl, reason)

                # --- 📅 REPORTING ---
                now_dt = datetime.now(timezone.utc) + timedelta(hours=7)
                if time.time() - self.last_report_time >= 600:
                    self._report_manager(price, pnl, ema, rsi, adx, slope, thb, coin_bal)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Main Error: {e}")
            time.sleep(15)

    def _report_manager(self, price, pnl, ema, rsi, adx, slope, thb, coin):
        try:
            total = thb + (coin * price)
            profit = total - self.initial_equity
            growth = (profit / self.initial_equity) * 100
            now = datetime.now(timezone.utc) + timedelta(hours=7)
            div = "━━━━━━━━━━━━━━━"
            
            report = (
                f"<b>{'🟢' if self.current_stage==2 else '🔵' if self.current_stage==1 else '⚪'} | TITAN V8.7.3</b>\n"
                f"📅 {now.strftime('%d/%m/%Y %H:%M:%S')}\n{div}\n"
                f"📊 <b>MARKET: {self.symbol}</b>\n"
                f"💰 Price: {price:,.2f} THB\n"
                f"📈 EMA: {ema:,.2f} ({slope:+.2f}%)\n"
                f"🕒 Net P/L: {pnl:+.2f}%\n"
                f"🔭 1H Trend: <b>{'🟢 BULLISH' if self.big_trend=='BULLISH' else '🟡 CAUTION'}</b>\n"
                f"🧩 ADX: {adx:.1f} | RSI: {rsi:.1f}\n{div}\n"
                f"🏛️ <b>PORTFOLIO</b>\n"
                f"💵 Cash: {thb:,.2f} THB\n"
                f"💎 {self.coin}: {coin:.4f} ({coin*price:,.2f} THB)\n"
                f"🛡️ Equity: {total:,.2f} THB\n{div}\n"
                f"📈 <b>PERFORMANCE</b>\n"
                f"💵 Profit: {profit:,.2f} THB\n"
                f"🚀 Growth: {growth:+.2f}%\n"
                f"🚨 SL @: {self.dynamic_sl:,.2f}\n{div}"
            )
            self.notify(report)
        except: pass

    def _update_buy_state(self, price, amt, stage):
        new_units = (amt * 0.9975) / price
        self.avg_price = ((self.avg_price * self.total_units) + (price * new_units)) / (self.total_units + new_units)
        self.total_units += new_units; self.last_action, self.current_stage, self.highest_price = "buy", stage, price
        self._save_state(); self._log_to_db("BUY", price, amt)

    def _update_sell_state(self, pnl, reason):
        self._log_to_db("SELL", self.avg_price, self.total_units, pnl, reason)
        self.last_action, self.avg_price, self.current_stage, self.total_units = "sell", 0, 0, 0.0
        self.dynamic_sl, self.highest_price = 0, 0; self.last_sell_time = time.time(); self._save_state()

    def _request(self, method, path, params=None, payload=None, private=False):
        url = f"{self.host}{path}"; query_str = "?" + "&".join([f"{k}={v}" for k, v in params.items()]) if params else ""
        if params: url += query_str
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = requests.get(f"{self.host}/api/v3/servertime").text.strip()
            sig = hmac.new(self.api_secret.encode('utf-8'), (ts+method+path+query_str+body_str).encode('utf-8'), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=body_str).json()

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res.get('error') == 0: return float(res['result'].get('THB', 0)), float(res['result'].get(self.coin, 0))
        return 0.0, 0.0

    def place_order(self, side, amt):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        clean_amt = math.floor(float(amt) * 100) / 100 if side == "buy" else math.floor(float(amt) * 10000) / 10000
        return self._request("POST", path, payload={"sym": self.symbol.lower(), "amt": clean_amt, "rat": 0, "typ": "market"}, private=True)

    def calculate_ema(self, prices, period):
        alpha = 2 / (period + 1); ema = prices[0]
        for p in prices[1:]: ema = (p * alpha) + (ema * (1 - alpha))
        return ema

    def notify(self, msg):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"})
        except: pass

    def _save_state(self):
        with open(self.state_file, "w") as f: json.dump({"last_action": self.last_action, "avg_price": self.avg_price, "stage": self.current_stage, "units": self.total_units}, f)

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f: 
                    d = json.load(f)
                    self.last_action, self.avg_price, self.current_stage, self.total_units = d['last_action'], d['avg_price'], d['stage'], d.get('units', 0.0)
            except: pass

def run_hc():
    try: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), type('H',(BaseHTTPRequestHandler,),{'do_GET':lambda s:(s.send_response(200),s.end_headers(),s.wfile.write(b"TITAN ACTIVE")),'log_message':lambda*a:None})).serve_forever()
    except: pass

if __name__ == "__main__":
    threading.Thread(target=run_hc, daemon=True).start()
    BitkubUltimateV8_7_3_TITAN().run()
