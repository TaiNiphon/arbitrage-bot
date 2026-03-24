import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2, traceback
from datetime import datetime, timedelta, timezone

class TitanProMaxV12:
    def __init__(self):
        print("🛡️ Booting TITAN PRO MAX V.12 (Professional Career-Ready)...")
        # --- Config & Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "10000")).replace(',', ''))

        # --- Professional Risk Settings ---
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "3.0")) 
        self.rsi_buy_level = float(os.getenv("RSI_BUY_MAX", "32.0")) 
        self.tp_target = 10.0         
        self.ema_dist_limit = float(os.getenv("EMA_DIST_LIMIT", "1.8")) 
        self.risk_per_trade = 2.0     # 🛡️ ข้อที่ 1: ลงทุนตามความเสี่ยง 2% ของพอร์ต
        self.check_interval = 5       # 🏎️ เช็คทุก 5 วินาที

        # --- Tracking & State ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        self.rsi_prev = 50.0; self.rsi_rep_prev = 50.0; self.entry_rsi_val = 0.0      
        self.entry_time = None; self.max_pnl_val = 0.0; self.entry_trend = "Unknown"  

        self._init_db()
        self._load_state_db()
        self.notify(f"<b>💎 TITAN PRO MAX V.12 Active</b>\nMode: Full-Professional\nRisk: {self.risk_per_trade}% | SL: {self.stop_loss_pct}%")

    # --- 🤖 ข้อที่ 4: Infrastructure & Redundancy (ระบบ DB & Log สำรอง) ---
    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS bot_state (id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, entry_rsi FLOAT, entry_time TIMESTAMP, max_pnl FLOAT, entry_trend TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS trade_history (id SERIAL PRIMARY KEY, time TIMESTAMP, side TEXT, price FLOAT, pnl_pct FLOAT, pnl_thb FLOAT, reason TEXT, entry_rsi FLOAT, exit_rsi FLOAT, max_pnl_during_trade FLOAT, hold_time_min FLOAT, market_trend TEXT)")
            conn.commit(); cur.close(); conn.close()
        except: print("❌ DB Error: Check your DATABASE_URL in Railway")

    def _save_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("DELETE FROM bot_state")
            cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", 
                (str(self.last_action), float(self.avg_price), float(self.total_units), float(self.highest_price), float(self.dynamic_sl), float(self.entry_rsi_val), self.entry_time, float(self.max_pnl_val), str(self.entry_trend)))
            conn.commit(); cur.close(); conn.close()
        except Exception as e: self.notify(f"⚠️ Fail-safe: State save error {e}")

    def _load_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend FROM bot_state LIMIT 1")
            row = cur.fetchone()
            if row: self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_rsi_val, self.entry_time, self.max_pnl_val, self.entry_trend = row
            cur.close(); conn.close()
        except: pass

    # --- 📊 ข้อที่ 2 & 3: Market Analysis & Trend Filter ---
    def update_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            ema_short = self.calculate_ema(c, 20)
            ema_long = self.calculate_ema(c, 50) # 🛡️ คัดกรองภาพใหญ่ (Trend Filter)
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema": ema_short, "ema_long": ema_long, "rsi": rsi, "atr": atr}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.update_indicators()
                if not d: time.sleep(10); continue
                p, ema, ema_long, rsi, atr = d['price'], d['ema'], d['ema_long'], d['rsi'], d['atr']
                thb, coin = self.get_balance()
                total_equity = thb + (coin * p)
                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                # --- 🛡️ ข้อที่ 1 & 2: Professional Buy Logic (Risk Sizing + Trend) ---
                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 300:
                    dist_ema = ((p - ema) / ema) * 100
                    market_is_bear = p < ema_long # เช็คว่าขาลงรุนแรงไหม
                    
                    # ถ้าขาลงรุนแรง ให้บอทเข้มงวด RSI ขึ้น (Market State Detection)
                    active_rsi_limit = self.rsi_buy_level - 5 if market_is_bear else self.rsi_buy_level
                    
                    # Position Sizing (ทบต้นตามพอร์ตปัจจุบัน)
                    risk_amt = total_equity * (self.risk_per_trade / 100)
                    sl_dist_fixed = p * (self.stop_loss_pct / 100)
                    buy_thb = (risk_amt / (sl_dist_fixed / p)) if sl_dist_fixed > 0 else thb * 0.98
                    buy_thb = min(buy_thb, thb * 0.98)

                    # RSI Hook Logic (Fixed)
                    if rsi < active_rsi_limit and rsi > self.rsi_prev and abs(dist_ema) < self.ema_dist_limit:
                        if self.place_order("buy", buy_thb):
                            self.avg_price = p; self.total_units = (buy_thb * 0.9975) / p
                            self.last_action = "buy"; self.highest_price = p
                            self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                            self.entry_rsi_val, self.entry_time, self.max_pnl_val = rsi, datetime.now(timezone(timedelta(hours=7))), 0.0
                            self.entry_trend = "Bearish_Entry" if market_is_bear else "Bullish_Entry"
                            self._save_state_db()
                            self.notify(f"<b>🚀 PRO ENTRY: {p:,.2f}</b>\nAmt: {buy_thb:,.2f} THB\nTrend: {self.entry_trend}")

                # --- SELL LOGIC (Trailing Stop & Safety) ---
                elif self.last_action == "buy" and coin > 0:
                    self.max_pnl_val = max(self.max_pnl_val, pnl)
                    self.highest_price = max(self.highest_price, p)
                    trail_price = self.highest_price - (atr * 3.0)
                    if pnl >= 1.2: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.0065) # Lock Profit
                    self.dynamic_sl = max(self.dynamic_sl, trail_price)
                    self._save_state_db()

                    reason = None
                    if pnl >= self.tp_target: reason = "Take Profit 💰"
                    elif pnl <= -self.stop_loss_pct: reason = "Stop Loss 🔴"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop 🛡️"

                    if reason:
                        profit_thb = (coin * p * 0.9975) - (self.total_units * self.avg_price * 1.0025)
                        if self.place_order("sell", coin):
                            self._log_trade_db(p, pnl, profit_thb, reason, rsi)
                            self.notify(f"<b>💰 PRO EXIT: {p:,.2f}</b>\nReason: {reason}\nProfit: <b>{profit_thb:+.2f} THB</b>")
                            self.last_action = "sell"; self.avg_price = 0; self.last_sell_time = time.time()
                            self._save_state_db()

                if time.time() - last_rep >= 600:
                    self._report(p, rsi, pnl, thb, coin, total_equity)
                    self.rsi_rep_prev = rsi; last_rep = time.time()

                self.rsi_prev = rsi # Fixed Hook

            except Exception as e: 
                print(f"❌ Error: {e}")
                traceback.print_exc()
            time.sleep(self.check_interval)

    # --- 🛠️ ส่วนสนับสนุน (API & Helpers) ---
    def _report(self, price, rsi, pnl, thb, coin, total):
        growth = ((total - self.initial_equity) / self.initial_equity) * 100 if self.initial_equity > 0 else 0
        now_str = datetime.now(timezone(timedelta(hours=7))).strftime('%H:%M:%S')
        is_hooked = rsi > self.rsi_prev
        hook_icon = "🟢" if is_hooked else "🔴"
        msg = (f"<b>💎 TITAN PRO MAX V.12</b>\nTime: {now_str}\n"
               f"💰 Price: {price:,.2f} ({pnl:+.2f}%)\n"
               f"📊 RSI: {rsi:.2f} | Prev: {self.rsi_rep_prev:.2f}\n"
               f"🪝 Hook: {hook_icon} ({rsi:.2f} > {self.rsi_prev:.2f})\n"
               f"🏦 Equity: <b>{total:,.2f} THB</b>\n"
               f"📈 Growth: {growth:+.2f}%")
        self.notify(msg)

    def _log_trade_db(self, price, pnl_pct, pnl_thb, reason, current_rsi):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            now = datetime.now(timezone(timedelta(hours=7)))
            hold_min = (now - self.entry_time).total_seconds() / 60 if self.entry_time else 0
            cur.execute("INSERT INTO trade_history (time, side, price, pnl_pct, pnl_thb, reason, entry_rsi, exit_rsi, max_pnl_during_trade, hold_time_min, market_trend) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", 
                (now, "SELL", price, pnl_pct, pnl_thb, reason, self.entry_rsi_val, current_rsi, self.max_pnl_val, hold_min, self.entry_trend))
            conn.commit(); cur.close(); conn.close()
        except: pass

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
            ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode('utf-8'), (ts+method+path+(json.dumps(payload) if payload else "")).encode('utf-8'), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=json.dumps(payload) if payload else "").json()

    def calculate_ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanProMaxV12().run()
