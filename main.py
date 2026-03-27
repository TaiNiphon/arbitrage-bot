import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone

class TitanOmniV13_1:
    def __init__(self):
        # --- Config & Variables (Railway Sync) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Strategy Settings ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "4.0"))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "5.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "30.0"))
        self.ema_dist_limit = float(os.getenv("EMA_DIST_LIMIT", "3.0"))
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "3"))
        self.report_interval = int(os.getenv("REPORT_INTERVAL", "600"))
        self.fee_pct = float(os.getenv("FEE_PCT", "0.25")) / 100

        # --- Tracking System ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.rsi_prev = None; self.rsi_memory = None 

        self._init_db()
        self._sync_with_wallet() 
        self.notify("<b>💎 TITAN OMNI V.13.1 PRO | ACTIVE</b>\n<i>Status: Full Reporting & Trend Filter Enabled</i>")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state (
                id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, 
                total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, 
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"DB Init Error: {e}")

    def _sync_with_wallet(self):
        try:
            thb, coin = self.get_balance()
            d = self.get_indicators("15")
            p = d['price'] if d else 46.18 
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("DELETE FROM bot_state") 
            if coin > 0.1: 
                self.last_action = "buy"; self.total_units = coin; self.avg_price = 46.18 # ต้นทุนไม้ที่ติดอยู่ของพี่
                self.highest_price = max(p, self.avg_price)
                self.dynamic_sl = self.avg_price * (1 - (self.stop_loss_pct/100))
                cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl) VALUES (%s, %s, %s, %s, %s)", 
                            (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl))
            else:
                self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
                cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl) VALUES (%s, %s, %s, %s, %s)", ("sell", 0, 0, 0, 0))
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"Sync Error: {e}")

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
        last_rep = 0
        while True:
            try:
                d15 = self.get_indicators("15")
                d60 = self.get_indicators("60")
                if not d15 or not d60: time.sleep(10); continue

                p, rsi, ema20, ema200_1h = d15['price'], d15['rsi'], d15['ema20'], d60['ema200']
                
                if self.rsi_prev is None: self.rsi_prev = rsi; self.rsi_memory = rsi
                elif abs(rsi - self.rsi_prev) > 0.01: self.rsi_memory = self.rsi_prev; self.rsi_prev = rsi

                thb, coin = self.get_balance()
                dist = ((p - ema20) / ema20) * 100
                pnl = (((p * (1 - self.fee_pct)) - (self.avg_price * (1 + self.fee_pct))) / (self.avg_price * (1 + self.fee_pct)) * 100) if self.avg_price > 0 else 0
                equity = thb + (coin * p)
                growth = ((equity - self.initial_equity) / self.initial_equity) * 100

                # --- BUY LOGIC ---
                if self.last_action == "sell" and rsi <= self.rsi_buy_target and rsi > (self.rsi_memory or 0):
                    if p > ema200_1h: # กรองเทรนด์ 1H
                        risk_amt = (equity * (self.risk_per_trade/100)) / (self.stop_loss_pct/100)
                        buy_amt = min(thb * 0.98, risk_amt)
                        if buy_amt >= 10 and self.place_order("buy", buy_amt):
                            self.last_action = "buy"; self.avg_price = p; self.total_units = buy_amt / p
                            self.highest_price = p; self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                            self.notify(f"🚀 <b>ENTRY BUY: {p:,.2f}</b>\nTrend: 1H BULLISH ✅")

                # --- SELL LOGIC ---
                elif self.last_action == "buy" and coin > 0.1:
                    self.highest_price = max(self.highest_price, p)
                    trail_dist = d15['atr'] * 2.5
                    if p - trail_dist > self.dynamic_sl: self.dynamic_sl = p - trail_dist
                    if pnl >= 1.0: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.003)

                    if pnl >= 10.0 or p <= self.dynamic_sl:
                        if self.place_order("sell", coin):
                            self.notify(f"💰 <b>EXIT SELL: {p:,.2f}</b>\nProfit: {pnl:+.2f}%")
                            self.last_action = "sell"; self.avg_price = 0

                if time.time() - last_rep >= self.report_interval:
                    trend_1h_txt = "BULLISH 📈" if p > ema200_1h else "BEARISH 📉"
                    status = "HOLDING" if self.last_action == "buy" else "MONITORING"
                    msg = (f"🛡️ <b>TITAN V.13.1 PRO | {self.symbol}</b>\n"
                           f"Status : {status}\n"
                           f"Date : {datetime.now().strftime('%d/%m/%m')}\n"
                           f"Time : {datetime.now().strftime('%H:%M:%S')}\n"
                           f"━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>MARKET INTELLIGENCE</b>\n"
                           f"• Price : {p:,.2f} THB\n"
                           f"• Trend 1H : {trend_1h_txt}\n"
                           f"• RSI : {rsi:.2f} (Prev:{self.rsi_memory:.2f})\n"
                           f"• Dist : {dist:+.2f}%\n"
                           f"━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>PORTFOLIO ANALYSIS</b>\n"
                           f"• EQUITY : {equity:,.2f} THB\n"
                           f"• GROWTH : {growth:+.2f}%\n"
                           f"• Cash : {thb:,.2f} | Assets: {coin:.4f}\n"
                           f"━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>STRATEGY</b>\n"
                           f"• Risk/Trade: {self.risk_per_trade}% (~{(equity*(self.risk_per_trade/100)):,.2f})\n"
                           f"• SL : {self.dynamic_sl:,.2f} ({pnl:+.2f}%)\n")
                    self.notify(msg); last_rep = time.time()

            except Exception as e: print(f"Error: {e}")
            time.sleep(self.check_interval)

    def get_balance(self):
        try:
            ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}).json()
            return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
        except: return 0.0, 0.0

    def place_order(self, side, amt):
        try:
            path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
            ts = str(int(time.time() * 1000)); payload = {"sym": self.symbol.lower(), "amt": amt, "rat": 0, "typ": "market"}
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload)).json()
            return res.get('error') == 0
        except: return False

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanOmniV13_1().run()
