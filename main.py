import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanMasterV11:
    def __init__(self):
        print("🛡️ Booting TITAN MASTER V.11.2 (Turbo Edition)...")
        # --- Environment Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "10000")).replace(',', ''))

        # --- Turbo Strategy Settings (Dynamic from Railway) ---
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "2.0")) 
        self.rsi_buy_level = float(os.getenv("RSI_BUY_MAX", "28.0")) 
        self.tp_target = 10.0         
        self.ema_dist_limit = float(os.getenv("EMA_DIST_LIMIT", "1.2")) 
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "5")) 

        # --- Tracking Variables ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        self.rsi_prev = 50.0       # สำหรับเช็ค Hook (5 วินาที)
        self.rsi_rep_prev = 50.0   # สำหรับโชว์ใน Report (10 นาที)
        self.entry_rsi_val = 0.0      
        self.entry_time = None        
        self.max_pnl_val = 0.0        
        self.entry_trend = "Unknown"  

        self._init_db()
        self._load_state_db()
        self.notify(f"<b>🚀 TITAN MASTER V.11.2 TURBO Active</b>\nInterval: {self.check_interval}s | RSI Buy: {self.rsi_buy_level}")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, total_units FLOAT, 
                    highest_price FLOAT, dynamic_sl FLOAT, entry_rsi FLOAT, entry_time TIMESTAMP, 
                    max_pnl FLOAT, entry_trend TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trade_history (
                    id SERIAL PRIMARY KEY, time TIMESTAMP, side TEXT, price FLOAT, 
                    pnl_pct FLOAT, pnl_thb FLOAT, reason TEXT, 
                    entry_rsi FLOAT, exit_rsi FLOAT, max_pnl_during_trade FLOAT, 
                    hold_time_min FLOAT, market_trend TEXT
                )""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"❌ DB Init Error: {e}")

    def _save_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("DELETE FROM bot_state")
            cur.execute("""
                INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                (str(self.last_action), float(self.avg_price), float(self.total_units), float(self.highest_price), 
                 float(self.dynamic_sl), float(self.entry_rsi_val), self.entry_time, float(self.max_pnl_val), str(self.entry_trend)))
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"❌ DB Save Error: {e}")

    def _load_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend FROM bot_state LIMIT 1")
            row = cur.fetchone()
            if row: 
                self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, \
                self.entry_rsi_val, self.entry_time, self.max_pnl_val, self.entry_trend = row
            cur.close(); conn.close()
        except Exception as e: print(f"❌ DB Load Error: {e}")

    def update_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            ema = self.calculate_ema(c, 20)
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema": ema, "rsi": rsi, "atr": atr}
        except: return None

    def _report(self, price, pnl, thb, coin, rsi, status="TURBO_ACTIVE"):
        coin_val = coin * price; total = thb + coin_val
        growth = ((total - self.initial_equity) / self.initial_equity) * 100 if self.initial_equity > 0 else 0
        diff_thb = total - self.initial_equity

        now_str = datetime.now(timezone(timedelta(hours=7))).strftime('%Y-%m-%d %H:%M:%S')
        div = "━━━━━━━━━━━━━━━"

        msg = (
            f"<b>🏆 TITAN MASTER V.11.2 ({self.symbol})</b>\n"
            f"🕒 Status: {status}\n"
            f"⏰ Time: <code>{now_str}</code>\n{div}\n"
            f"💰 Price: <b>{price:,.2f}</b> | P/L: <b>{pnl:+.2f}%</b>\n"
            f"📊 RSI: {rsi:.2f} | Prev(10m): {self.rsi_rep_prev:.2f}\n"
            f"🛡️ Config: RSI &lt; {self.rsi_buy_level} | SL: {self.stop_loss_pct}%\n{div}\n"
            f"🏦 <b>LIVE PORTFOLIO</b>\n"
            f"💵 Cash: {thb:,.2f} THB\n"
            f"💠 {self.symbol.split('_')[0]}: {coin:.4f} ({coin_val:,.2f} THB)\n"
            f"💎 Equity: <b>{total:,.2f} THB</b>\n"
            f"🚀 Growth: {growth:+.2f}% (<b>{diff_thb:,.2f} THB</b>)\n{div}\n"
        )

        if self.last_action == "buy" and coin > 0:
            be_price = self.avg_price * 1.0065
            sl_dist = ((price - self.dynamic_sl) / self.dynamic_sl * 100) if self.dynamic_sl > 0 else 0
            msg += (
                f"🎯 BE Price: {be_price:,.2f}\n"
                f"🛡️ SL: {self.dynamic_sl:,.2f} (<b>{sl_dist:+.2f}%</b>)\n"
                f"📈 Max P/L: <b>{self.max_pnl_val:+.2f}%</b>"
            )
        else:
            msg += f"💤 Status: <b>Searching for Entry...</b>"

        self.notify(msg)

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.update_indicators()
                if not d: time.sleep(10); continue
                p, ema, rsi, atr = d['price'], d['ema'], d['rsi'], d['atr']

                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0
                thb, coin = self.get_balance()

                # --- BUY LOGIC ---
                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 300:
                    dist_ema = ((p - ema) / ema) * 100
                    # ใช้ rsi_prev (5 วินาที) เพื่อเช็คจังหวะ Hook งัดขึ้น
                    if self.rsi_prev < self.rsi_buy_level and rsi > self.rsi_prev and dist_ema < self.ema_dist_limit:
                        if self.place_order("buy", thb * 0.98):
                            self.avg_price = p; self.total_units = (thb * 0.975) / p
                            self.last_action = "buy"; self.highest_price = p
                            self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                            self.entry_rsi_val = rsi
                            self.entry_time = datetime.now(timezone(timedelta(hours=7)))
                            self.max_pnl_val = 0.0
                            self.entry_trend = "UpTrend" if p > ema else "DownTrend"
                            self._save_state_db()
                            self.notify(f"<b>🚀 ENTRY (TURBO): {p:,.2f}</b>\n📊 RSI: {rsi:.2f} | EMA Dist: {dist_ema:.2f}%")

                # --- SELL LOGIC ---
                elif self.last_action == "buy" and coin > 0:
                    self.max_pnl_val = max(self.max_pnl_val, pnl)
                    self.highest_price = max(self.highest_price, p)
                    trail_price = self.highest_price - (atr * 3.0)

                    if pnl >= 1.2: 
                        self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.0065)

                    self.dynamic_sl = max(self.dynamic_sl, trail_price)
                    self._save_state_db()

                    reason = None
                    if pnl >= self.tp_target: reason = "Take Profit 💰"
                    elif pnl <= -self.stop_loss_pct: reason = "Stop Loss 🔴"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop 🛡️"

                    if reason:
                        profit_thb = (coin * p * 0.9975) - (self.total_units * self.avg_price * 1.0025)
                        if self.place_order("sell", coin):
                            self._log_trade_db_v11("SELL", p, pnl, profit_thb, reason, rsi)
                            self.notify(f"<b>💰 EXIT (TURBO): {p:,.2f}</b>\nReason: {reason}\nProfit: <b>{profit_thb:+.2f} THB</b>\nMax P/L: {self.max_pnl_val:+.2f}%")
                            self.last_action = "sell"; self.avg_price = 0; self.last_sell_time = time.time()
                            self.entry_rsi_val = 0.0; self.max_pnl_val = 0.0
                            self._save_state_db()

                # --- Report Cycle (ทุก 10 นาที) ---
                if time.time() - last_rep >= 600:
                    self._report(p, pnl, thb, coin, rsi)
                    last_rep = time.time()
                    # อัปเดตเฉพาะค่าสำหรับรายงาน เพื่อให้รอบหน้าเห็นผลต่าง 10 นาที
                    self.rsi_rep_prev = rsi 

                # อัปเดตค่า RSI เดิมสำหรับเช็คจังหวะซื้อ (5 วินาที)
                self.rsi_prev = rsi 

            except Exception as e: print(f"❌ Run Error: {e}")
            time.sleep(self.check_interval)

    def _get_hold_time(self):
        if self.entry_time:
            now = datetime.now(timezone(timedelta(hours=7)))
            diff = now - self.entry_time
            return round(diff.total_seconds() / 60, 2)
        return 0.0

    def _log_trade_db_v11(self, side, price, pnl_pct, pnl_thb, reason, current_rsi):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            hold_min = self._get_hold_time()
            cur.execute("""
                INSERT INTO trade_history (
                    time, side, price, pnl_pct, pnl_thb, reason, 
                    entry_rsi, exit_rsi, max_pnl_during_trade, hold_time_min, market_trend
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                (datetime.now(timezone(timedelta(hours=7))), str(side), float(price), float(pnl_pct), float(pnl_thb), str(reason),
                 float(self.entry_rsi_val), float(current_rsi), float(self.max_pnl_val), float(hold_min), str(self.entry_trend)))
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"❌ DB Log Error: {e}")

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
            sig = hmac.new(self.api_secret.encode('utf-8'), (ts+method+path+(json.dumps(payload) if payload else "")).encode('utf-8'), hashlib.sha256).hexdigest()
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
    TitanMasterV11().run()
