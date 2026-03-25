import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanProMaxV12:
    def __init__(self):
        print("🛡️ Booting TITAN PRO MAX V.12.3 (Final Polish)...")
        # --- Config & Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Professional Settings (Auto-Synced with Railway) ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.0")) 
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "3.0"))
        self.rsi_buy_base = float(os.getenv("RSI_BUY_MAX", "38.0"))
        self.ema_dist_limit = float(os.getenv("EMA_DIST_LIMIT", "5.0")) 
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "3"))
        self.report_interval = int(os.getenv("REPORT_INTERVAL", "600"))
        self.fee_pct = 0.25 / 100

        # --- Tracking System ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        self.rsi_history = [] # ใช้ List เก็บค่า RSI ย้อนหลังแทน Memory ตัวเดียว
        self.entry_rsi_val = 0.0; self.entry_time = None
        self.max_pnl_val = 0.0; self.entry_trend = "Unknown"

        self._init_db(); self._load_state_db()
        self.notify(f"<b>💎 TITAN V.12.3 ACTIVE</b>\nMode: Precision Entry\nRSI Target: {self.rsi_buy_base}")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state (id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, entry_rsi FLOAT, entry_time TIMESTAMP, max_pnl FLOAT, entry_trend TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (id SERIAL PRIMARY KEY, time TIMESTAMP, side TEXT, price FLOAT, pnl_pct FLOAT, pnl_thb FLOAT, reason TEXT, entry_rsi FLOAT, exit_rsi FLOAT, max_pnl_during_trade FLOAT, hold_time_min FLOAT, market_trend TEXT)""")
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _save_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("DELETE FROM bot_state")
            cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_rsi_val, self.entry_time, self.max_pnl_val, self.entry_trend))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _load_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend FROM bot_state LIMIT 1")
            row = cur.fetchone()
            if row: self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_rsi_val, self.entry_time, self.max_pnl_val, self.entry_trend = row
            cur.close(); conn.close()
        except: pass

    def calculate_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            ema20 = self._ema(c, 20); ema50 = self._ema(c, 50)
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema20": ema20, "ema50": ema50, "rsi": rsi, "atr": atr}
        except: return None

    def _ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.calculate_indicators()
                if not d: time.sleep(10); continue
                p, ema20, ema50, rsi, atr = d['price'], d['ema20'], d['ema50'], d['rsi'], d['atr']

                # เก็บประวัติ RSI เพื่อเช็คการงัดหัว (Hook)
                self.rsi_history.append(rsi)
                if len(self.rsi_history) > 2: self.rsi_history.pop(0)
                
                prev_rsi = self.rsi_history[0] if len(self.rsi_history) > 1 else rsi
                buy_fee = 1 + self.fee_pct; sell_fee = 1 - self.fee_pct
                pnl = (((p * sell_fee) - (self.avg_price * buy_fee)) / (self.avg_price * buy_fee) * 100) if self.avg_price > 0 else 0
                thb, coin = self.get_balance()

                # --- BUY LOGIC (Precision Edition) ---
                if self.last_action == "sell":
                    dist_ema = ((p - ema20) / ema20) * 100
                    
                    # เช็คเงื่อนไข: RSI ต่ำ + RSI เริ่มนิ่งหรือเงย (>=) + ระยะ EMA ไม่ห่างจนน่ากลัว
                    if rsi <= self.rsi_buy_base and rsi >= prev_rsi and abs(dist_ema) <= self.ema_dist_limit:
                        total_equity = thb + (coin * p)
                        risk_amt = (total_equity * (self.risk_per_trade / 100)) / (self.stop_loss_pct / 100)
                        buy_amt = min(thb * 0.98, risk_amt) # หักเผื่อไว้ 2% กันเงินไม่พอค่าธรรมเนียม

                        if buy_amt >= 10 and self.place_order("buy", buy_amt):
                            self.avg_price = p; self.total_units = buy_amt / p
                            self.last_action = "buy"; self.highest_price = p
                            self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                            self.entry_rsi_val = rsi; self.entry_time = datetime.now(timezone(timedelta(hours=7)))
                            self.entry_trend = "BULLISH" if p > ema50 else "BEARISH"
                            self.max_pnl_val = 0.0; self._save_state_db()
                            self.notify(f"<b>🚀 ENTRY: {p:,.2f}</b>\nRSI: {rsi:.2f} | Trend: {self.entry_trend}")

                # --- SELL LOGIC (Trailing Edition) ---
                elif self.last_action == "buy" and coin > 0:
                    self.max_pnl_val = max(self.max_pnl_val, pnl)
                    self.highest_price = max(self.highest_price, p)
                    
                    # ขยับ Stop Loss ตามราคา (Trailing Stop)
                    trail_dist = atr * 2.2 # ปรับให้ไวขึ้นเพื่อล็อคกำไร
                    if p - trail_dist > self.dynamic_sl: self.dynamic_sl = p - trail_dist
                    
                    # ถ้ากำไรพ้น 1% ให้ยกจุด SL มากันทุนทันที (+0.5% กันค่าธรรมเนียม)
                    if pnl >= 1.0: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.005)

                    reason = None
                    if pnl >= 12.0: reason = "Take Profit 💰"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop/SL 🛡️"

                    if reason:
                        if self.place_order("sell", coin):
                            profit_thb = (coin * p * sell_fee) - (self.total_units * self.avg_price * buy_fee)
                            self._log_trade(p, pnl, profit_thb, reason, rsi)
                            self.notify(f"<b>💰 EXIT: {p:,.2f}</b>\nProfit: <b>{profit_thb:+.2f} THB</b>\nReason: {reason}")
                            self.last_action = "sell"; self._save_state_db()

                if time.time() - last_rep >= self.report_interval:
                    self._report(p, ema20, ema50, rsi, pnl, thb, coin, prev_rsi)
                    last_rep = time.time()

            except Exception as e: print(f"❌ Error: {e}")
            time.sleep(self.check_interval)

    def _report(self, price, ema20, ema50, rsi, pnl, thb, coin, prev_rsi):
        total_equity = thb + (coin * price)
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        dist_ema = ((price - ema20) / ema20) * 100
        trend = "BULLISH 📈" if price > ema50 else "BEARISH 📉"
        hook = "🟢" if rsi >= prev_rsi else "🔴"

        msg = (
            f"<b>⚙️ TITAN V.12.3 | {self.symbol}</b>\n"
            f"<code>Price : {price:,.2f} ({trend})</code>\n"
            f"<code>RSI   : {rsi:.2f} {hook} (Prev:{prev_rsi:.2f})</code>\n"
            f"<code>Dist  : {dist_ema:+.2f}%</code>\n"
            f"<b>🏦 PORT</b>\n"
            f"<code>Equity: {total_equity:,.2f} ({growth:+.2f}%)</code>"
        )
        self.notify(msg)

    def get_balance(self):
        try:
            res = self._request("POST", "/api/v3/market/wallet", private=True)
            if res.get('error') == 0: return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
        except: pass
        return 0.0, 0.0

    def place_order(self, side, amt):
        try:
            path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
            res = self._request("POST", path, payload={"sym": self.symbol.lower(), "amt": amt, "rat": 0, "typ": "market"}, private=True)
            return res.get('error') == 0
        except: return False

    def _request(self, method, path, payload=None, private=False):
        url = f"https://api.bitkub.com{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if private:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+method+path+(json.dumps(payload) if payload else "")).encode(), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=json.dumps(payload) if payload else "").json()

    def _log_trade(self, p, pnl, thb, reason, rsi):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            hold_min = (datetime.now(timezone(timedelta(hours=7))) - self.entry_time).total_seconds()/60 if self.entry_time else 0
            cur.execute("INSERT INTO trade_history (time, side, price, pnl_pct, pnl_thb, reason, entry_rsi, exit_rsi, max_pnl_during_trade, hold_time_min, market_trend) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (datetime.now(timezone(timedelta(hours=7))), "SELL", p, pnl, thb, reason, self.entry_rsi_val, rsi, self.max_pnl_val, hold_min, self.entry_trend))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanProMaxV12().run()
