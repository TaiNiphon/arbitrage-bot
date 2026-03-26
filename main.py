import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanOmniV13:
    def __init__(self):
        # --- Config & Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Professional Settings ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        self.risk_per_trade = 2.0  # Risk Limit 2%
        self.stop_loss_pct = 4.0   # SL สำหรับ XRP
        self.rsi_buy_target = 35.0 # จุดเข้าช้อป
        self.check_interval = 5 
        self.report_interval = 600
        self.fee_pct = 0.25 / 100

        # --- Tracking System ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        self.rsi_prev = None; self.rsi_memory = None 
        self.entry_time = None; self.max_pnl_during_trade = 0.0

        self._init_db(); self._load_state_db()
        self.notify("<b>💎 TITAN OMNI V.13 ACTIVE</b>\nMode: Ultra-Professional Hybrid\nStatus: <i>System Synchronized</i>")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state (id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, entry_time TIMESTAMP, max_pnl FLOAT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _save_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("DELETE FROM bot_state")
            cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl, entry_time, max_pnl) VALUES (%s, %s, %s, %s, %s, %s, %s)", (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_time, self.max_pnl_during_trade))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _load_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT last_action, avg_price, total_units, highest_price, dynamic_sl, entry_time, max_pnl FROM bot_state LIMIT 1")
            row = cur.fetchone()
            if row: self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_time, self.max_pnl_during_trade = row
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

    def _report(self, price, ema20, ema50, rsi, pnl, thb, coin, status="MONITORING"):
        total_equity = thb + (coin * price)
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        now = datetime.now(timezone(timedelta(hours=7)))
        
        # --- รายงานแบบสมบูรณ์เหมือน V.12 แต่แม่นยำแบบ V.13 ---
        msg = (
            f"<b>🛡️ TITAN OMNI V.13 | {self.symbol}</b>\n"
            f"Status : {status}\n"
            f"Date   : {now.strftime('%d/%m/%Y')}\n"
            f"Time   : {now.strftime('%H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>📊 MARKET INTELLIGENCE</b>\n"
            f"• Price : <b>{price:,.2f} THB</b>\n"
            f"• Trend : {'BULLISH 📈' if price > ema50 else 'BEARISH 📉'}\n"
            f"• RSI   : {rsi:.2f} {'🟢' if rsi > (self.rsi_memory or 0) else '🔴'} (Prev:{self.rsi_memory or 0:.2f})\n"
            f"• Dist  : {((price-ema20)/ema20*100):+.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>💰 PORTFOLIO ANALYSIS</b>\n"
            f"• EQUITY : <b>{total_equity:,.2f} THB</b>\n"
            f"• GROWTH : {growth:+.2f}%\n"
            f"• Cash   : {thb:,.2f} | Assets: {coin:.4f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>🎯 STRATEGY</b>\n"
            f"• Risk/Trade: {self.risk_per_trade}% (~{(total_equity * (self.risk_per_trade/100)):,.2f})\n"
        )
        
        if self.last_action == "buy":
            msg += f"• Status: <b>Holding Profit: {pnl:+.2f}%</b>"
        else:
            msg += f"• Status: <i>Searching Entry... (Target RSI:{self.rsi_buy_target})</i>"
        
        self.notify(msg)

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.calculate_indicators()
                if not d: time.sleep(10); continue
                p, ema20, ema50, rsi, atr = d['price'], d['ema20'], d['ema50'], d['rsi'], d['atr']

                if self.rsi_prev is None: self.rsi_prev = rsi; self.rsi_memory = rsi
                elif abs(rsi - self.rsi_prev) > 0.01: self.rsi_memory = self.rsi_prev; self.rsi_prev = rsi

                thb, coin = self.get_balance()
                pnl = (((p * (1-self.fee_pct)) - (self.avg_price * (1+self.fee_pct))) / (self.avg_price * (1+self.fee_pct)) * 100) if self.avg_price > 0 else 0

                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 300:
                    dist_ema = ((p - ema20) / ema20) * 100
                    if rsi <= self.rsi_buy_target and rsi > (self.rsi_memory or 0) and abs(dist_ema) < 5.0:
                        total_equity = thb + (coin * p)
                        risk_amt = (total_equity * (self.risk_per_trade / 100)) / (self.stop_loss_pct / 100)
                        buy_amt = min(thb * 0.98, risk_amt)
                        if buy_amt >= 10 and self.place_order("buy", buy_amt):
                            self.avg_price = p; self.total_units = buy_amt / p; self.last_action = "buy"
                            self.highest_price = p; self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                            self.entry_time = datetime.now(timezone(timedelta(hours=7)))
                            self.max_pnl_during_trade = 0.0; self._save_state_db()
                            self.notify(f"🚀 <b>ENTRY BUY: {p:,.2f}</b>\nRSI: {rsi:.2f} | Risk Applied: {self.risk_per_trade}%")

                elif self.last_action == "buy" and coin > 0:
                    self.max_pnl_during_trade = max(self.max_pnl_during_trade, pnl)
                    self.highest_price = max(self.highest_price, p)
                    trail_dist = atr * 2.3
                    if p - trail_dist > self.dynamic_sl: self.dynamic_sl = p - trail_dist
                    if pnl >= 1.5: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.005)

                    reason = None
                    if pnl >= 15.0: reason = "🎯 Profit Target 15%"
                    elif p <= self.dynamic_sl: reason = "🛡️ Trailing Stop/SL"

                    if reason:
                        if self.place_order("sell", coin):
                            profit_thb = (coin * p * (1-self.fee_pct)) - (self.total_units * self.avg_price * (1+self.fee_pct))
                            self.notify(f"💰 <b>EXIT SELL: {p:,.2f}</b>\nProfit: <b>{profit_thb:+.2f} THB</b>\nReason: {reason}")
                            self.last_action = "sell"; self.last_sell_time = time.time(); self._save_state_db()

                if time.time() - last_rep >= self.report_interval:
                    self._report(p, ema20, ema50, rsi, pnl, thb, coin)
                    last_rep = time.time()

            except Exception as e: print(f"❌ Run Error: {e}")
            time.sleep(self.check_interval)

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
        url = f"https://api.bitkub.com{path}"; headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if private:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+method+path+(json.dumps(payload) if payload else "")).encode(), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=json.dumps(payload) if payload else "").json()

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanOmniV13().run()
