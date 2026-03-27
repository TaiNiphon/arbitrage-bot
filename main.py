import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanOmniV13_7:
    def __init__(self):
        # --- Config & Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Strategy Settings ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.0"))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "3.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "30.0"))
        self.fee_pct = 0.0025 

        # --- Safety Stars (ระบบดาว) ---
        self.daily_drawdown_limit = 5.0 # ดาวจะดับเมื่อลบเกิน 5%
        self.daily_pnl = 0.0
        self.last_pnl_reset = datetime.now(timezone(timedelta(hours=7))).date()
        self.is_bot_active = True # สถานะดาวทำงาน

        # --- Tracking ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.rsi_prev = None; self.rsi_memory = None 

        self._init_db()
        self._sync_with_wallet() 
        self.notify("<b>🛡️ TITAN OMNI V.13.7 | DRAWDOWN ACTIVE</b>\n<i>Status: ระบบดาวป้องกันพอร์ตทำงานสมบูรณ์แล้ว</i>")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state (
                id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, 
                total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, 
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                id SERIAL PRIMARY KEY, side TEXT, price FLOAT, units FLOAT, 
                pnl_thb FLOAT, traded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: self.notify(f"⚠️ <b>DB Error:</b> {e}")

    def _update_daily_pnl(self):
        try:
            now_ict = datetime.now(timezone(timedelta(hours=7)))
            today = now_ict.date()
            if today > self.last_pnl_reset:
                self.daily_pnl = 0.0; self.last_pnl_reset = today; self.is_bot_active = True
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT SUM(pnl_thb) FROM trade_history WHERE traded_at::date = %s", (today,))
            res = cur.fetchone()
            self.daily_pnl = float(res[0]) if res[0] else 0.0
            cur.close(); conn.close()
        except: pass

    def _sync_with_wallet(self):
        try:
            thb, coin = self.get_balance()
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT last_action, avg_price, total_units, dynamic_sl FROM bot_state ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if coin > 0.1: 
                self.last_action = "buy"; self.total_units = coin
                self.avg_price = row[1] if row and row[1] > 0 else 0.0
                self.dynamic_sl = row[3] if row and row[3] > 0 else self.avg_price * 0.97
            else:
                self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0; self.dynamic_sl = 0.0
            cur.close(); conn.close()
        except: pass

    def get_indicators(self, res_min):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution={res_min}&from={int(time.time())-172800}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            ema20 = self._ema(c, 20); ema200 = self._ema(c, 200)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema20": ema20, "ema200": ema200, "rsi": rsi, "atr": atr}
        except: return None

    def _ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def run(self):
        last_rep = 0; last_heartbeat = time.time()
        while True:
            try:
                self._update_daily_pnl()
                d15, d60 = self.get_indicators("15"), self.get_indicators("60")
                if not d15 or not d60: time.sleep(10); continue

                p, rsi, ema20, ema200_1h = d15['price'], d15['rsi'], d15['ema20'], d60['ema200']
                if self.rsi_prev is None: self.rsi_prev = rsi; self.rsi_memory = rsi
                elif abs(rsi - self.rsi_prev) > 0.01: self.rsi_memory = self.rsi_prev; self.rsi_prev = rsi

                thb, coin = self.get_balance()
                dist = ((p - ema20) / ema20) * 100
                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0
                equity = thb + (coin * p)
                growth = ((equity - self.initial_equity) / self.initial_equity) * 100

                # --- Drawdown Check ---
                dd_pct = (self.daily_pnl / self.initial_equity) * 100
                if dd_pct <= -self.daily_drawdown_limit and self.is_bot_active:
                    self.is_bot_active = False
                    self.notify(f"🚨 <b>DAILY DRAWDOWN ALERT:</b> พอร์ตหยุดเทรดฝั่งซื้ออัตโนมัติ (Loss: {dd_pct:.2f}%)")

                # --- BUY (Check is_bot_active) ---
                if self.is_bot_active and self.last_action == "sell" and rsi <= self.rsi_buy_target and rsi > (self.rsi_memory or 0):
                    if p > ema200_1h:
                        buy_amt = min(thb * 0.98, (equity * (self.risk_per_trade/100)) / (self.stop_loss_pct/100))
                        if buy_amt >= 10 and self.place_order("buy", buy_amt):
                            self.last_action = "buy"; self.avg_price = p; self.total_units = buy_amt / (p * 1.0025)
                            self.highest_price = p; self.dynamic_sl = p * (1 - (self.stop_loss_pct/100)); self._save_state()
                            self.notify(f"🚀 <b>ENTRY BUY: {p:,.2f}</b>")

                # --- SELL ---
                elif self.last_action == "buy" and coin > 0.1:
                    self.highest_price = max(self.highest_price, p)
                    if p - (d15['atr'] * 2.5) > self.dynamic_sl: self.dynamic_sl = p - (d15['atr'] * 2.5); self._save_state()
                    if pnl >= 1.0: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.003)

                    if pnl >= 10.0 or p <= self.dynamic_sl:
                        if self.place_order("sell", coin):
                            pnl_thb = (coin * (p * 0.9975)) - (coin * (self.avg_price * 1.0025))
                            self._save_trade_history("sell", p, coin, pnl_thb)
                            self.notify(f"💰 <b>EXIT SELL: {p:,.2f}</b>\nProfit: {pnl:+.2f}% ({pnl_thb:,.2f} THB)")
                            self.last_action = "sell"; self.avg_price = 0; self.total_units = 0; self._save_state()

                # --- Heartbeat Monitor (V.13.7) ---
                if time.time() - last_heartbeat >= 21600:
                    status_txt = "ACTIVE ✅" if self.is_bot_active else "PAUSED 🛑 (Drawdown)"
                    hb_msg = (f"💓 <b>TITAN Heartbeat</b>\nStatus: {status_txt}\nDaily PnL: {self.daily_pnl:,.2f} THB\nEquity: {equity:,.2f} THB")
                    self.notify(hb_msg); last_heartbeat = time.time()

                # --- REPORTING ---
                if time.time() - last_rep >= int(os.getenv("REPORT_INTERVAL", "600")):
                    msg = (f"🛡️ <b>TITAN V.13.7 | {self.symbol}</b>\n"
                           f"Status : {'HOLDING' if self.last_action == 'buy' else 'MONITORING'}\n"
                           f"━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>MARKET INTELLIGENCE</b>\n"
                           f"• Price : {p:,.2f} THB\n• RSI : {rsi:.2f}\n"
                           f"💰 <b>PORTFOLIO ANALYSIS</b>\n"
                           f"• EQUITY : {equity:,.2f} THB\n• GROWTH : {growth:+.2f}%\n"
                           f"━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>STRATEGY</b>\n"
                           f"• SL : {self.dynamic_sl:,.2f} ({pnl:+.2f}%)\n")
                    self.notify(msg); last_rep = time.time()

            except Exception as e: print(f"Loop Error: {e}")
            time.sleep(int(os.getenv("CHECK_INTERVAL", "3")))

    def _save_state(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl) VALUES (%s, %s, %s, %s, %s)", 
                        (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _save_trade_history(self, side, price, units, pnl_thb):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("INSERT INTO trade_history (side, price, units, pnl_thb) VALUES (%s, %s, %s, %s)", (side, price, units, pnl_thb))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def get_balance(self):
        try:
            ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            if res.get('error') == 0:
                thb = float(res['result'].get('THB', 0))
                coin = int(float(res['result'].get('XRP', 0)) * 10000) / 10000.0
                return thb, coin
            return 0.0, 0.0
        except: return 0.0, 0.0

    def place_order(self, side, amt):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        final_amt = int(amt * 10000) / 10000.0 if side == "sell" else amt
        ts = str(int(time.time() * 1000)); payload = {"sym": self.symbol.lower(), "amt": final_amt, "rat": 0, "typ": "market"}
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
        try:
            r = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload), timeout=10)
            res = r.json()
            if res.get('error') != 0:
                self.notify(f"❌ <b>Order Failed</b>\nReason: {res.get('description', 'Unknown Error')}")
                return False
            return True
        except Exception as e: self.notify(f"🚨 <b>Order Error:</b> {str(e)}"); return False

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanOmniV13_7().run()
