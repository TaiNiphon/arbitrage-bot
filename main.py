import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanOmniV13:
    def __init__(self):
        # --- Config & Variables จาก Railway ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Strategy Settings ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.0"))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "4.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.ema_dist_limit = float(os.getenv("EMA_DIST_LIMIT", "3.0"))
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "5"))
        self.report_interval = int(os.getenv("REPORT_INTERVAL", "600"))
        self.fee_pct = 0.25 / 100

        # --- Tracking System ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.rsi_prev = None; self.rsi_memory = None 

        self._init_db()
        self._sync_with_wallet() # บังคับให้บอทจำไม้ที่ค้างอยู่จาก Bitkub โดยตรง
        self.notify("<b>💎 TITAN OMNI V.13 | AUTO-SYNC</b>\n<i>Status: Database & Wallet Synchronized</i>")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state (
                id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, 
                total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, 
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                id SERIAL PRIMARY KEY, time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                side TEXT, price FLOAT, amount FLOAT, rsi_at_trade FLOAT, 
                ema_dist FLOAT, pnl_percent FLOAT, pnl_thb FLOAT)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"DB Init Error: {e}")

    def _sync_with_wallet(self):
        """ ฟังก์ชันพิเศษ: ตรวจสอบ Wallet และบังคับให้ Database จำสถานะจริง """
        try:
            thb, coin = self.get_balance()
            d = self.get_indicators()
            current_price = d['price'] if d else 0
            
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            
            # ถ้ามีเหรียญค้างในกระเป๋า (Asset > 0)
            if coin > 0.1: # กำหนดขั้นต่ำไว้ป้องกันเศษเหรียญ
                self.last_action = "buy"
                self.total_units = coin
                # ถ้าราคาปัจจุบันต่ำกว่าทุนเดิม ให้ใช้ราคาทุนที่พี่เคยเข้า (46.18) หรือใช้ราคาปัจจุบันถ้าหาไม่ได้
                self.avg_price = 46.18 if current_price > 0 else 0 
                self.highest_price = max(current_price, self.avg_price)
                self.dynamic_sl = self.avg_price * (1 - (self.stop_loss_pct/100))
                
                cur.execute("DELETE FROM bot_state")
                cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl) VALUES (%s, %s, %s, %s, %s)", 
                            (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl))
                print(f"✅ Sync Found Assets: {coin} XRP. Status updated to BUY.")
            else:
                self.last_action = "sell"; self.avg_price = 0; self.total_units = 0
                cur.execute("DELETE FROM bot_state")
                cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl) VALUES (%s, %s, %s, %s, %s)", 
                            ("sell", 0, 0, 0, 0))
                print("✅ Sync Found No Assets. Status updated to SELL.")
            
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"Sync Error: {e}")

    def _save_state(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("UPDATE bot_state SET last_action=%s, avg_price=%s, total_units=%s, highest_price=%s, dynamic_sl=%s, updated_at=CURRENT_TIMESTAMP", 
                        (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _log_trade(self, side, price, amount, rsi, ema_dist, pnl_pct=0, pnl_thb=0):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("INSERT INTO trade_history (side, price, amount, rsi_at_trade, ema_dist, pnl_percent, pnl_thb) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (side, price, amount, rsi, ema_dist, pnl_pct, pnl_thb))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def get_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            ema20 = self._ema(c, 20); ema50 = self._ema(c, 50)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema20": ema20, "ema50": ema50, "rsi": rsi, "atr": atr}
        except: return None

    def _ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def _report(self, price, rsi, ema20, ema50, pnl, thb, coin):
        total = thb + (coin * price); growth = ((total - self.initial_equity) / self.initial_equity) * 100
        now = datetime.now(timezone(timedelta(hours=7)))
        msg = (f"<b>🛡️ TITAN OMNI V.13 | {self.symbol}</b>\n"
               f"Date : {now.strftime('%d/%m/%Y')} | Time : {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"Price: <b>{price:,.2f}</b> | RSI: {rsi:.2f} {'🟢' if rsi > (self.rsi_memory or 0) else '🔴'}\n"
               f"Trend: {'BULLISH 📈' if price > ema50 else 'BEARISH 📉'} | Dist: {((price-ema20)/ema20*100):+.2f}%\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"Equity: <b>{total:,.2f}</b> ({growth:+.2f}%)\n"
               f"Cash: {thb:,.2f} | Assets: {coin:.4f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"Status: {'Holding (PNL: '+str(round(pnl,2))+'%)' if self.last_action=='buy' else 'Searching Entry...'}")
        self.notify(msg)

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.get_indicators()
                if not d: time.sleep(10); continue
                p, rsi, ema20, ema50, atr = d['price'], d['rsi'], d['ema20'], d['ema50'], d['atr']
                
                if self.rsi_prev is None: self.rsi_prev = rsi; self.rsi_memory = rsi
                elif abs(rsi - self.rsi_prev) > 0.01: self.rsi_memory = self.rsi_prev; self.rsi_prev = rsi

                thb, coin = self.get_balance()
                dist = ((p - ema20) / ema20) * 100
                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                if self.last_action == "sell" and rsi <= self.rsi_buy_target and rsi > (self.rsi_memory or 0) and abs(dist) <= self.ema_dist_limit:
                    risk_amt = ( (thb + (coin*p)) * (self.risk_per_trade/100) ) / (self.stop_loss_pct/100)
                    buy_amt = min(thb * 0.98, risk_amt)
                    if buy_amt >= 10 and self.place_order("buy", buy_amt):
                        self.last_action = "buy"; self.avg_price = p; self.total_units = buy_amt / p
                        self.highest_price = p; self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                        self._save_state(); self._log_trade("BUY", p, buy_amt, rsi, dist)
                        self.notify(f"🚀 <b>ENTRY BUY: {p:,.2f}</b>\nRSI: {rsi:.2f} | Risk: {self.risk_per_trade}%")

                elif self.last_action == "buy" and coin > 0:
                    self.highest_price = max(self.highest_price, p)
                    trail_dist = atr * 2.3
                    if p - trail_dist > self.dynamic_sl: self.dynamic_sl = p - trail_dist
                    if pnl >= 1.5: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.005)

                    reason = None
                    if pnl >= 15.0: reason = "🎯 Target 15%"
                    elif p <= self.dynamic_sl: reason = "🛡️ Trailing Stop/SL"

                    if reason:
                        if self.place_order("sell", coin):
                            pnl_thb = (coin * p * 0.9975) - (self.total_units * self.avg_price * 1.0025)
                            self._log_trade("SELL", p, coin*p, rsi, dist, pnl, pnl_thb)
                            self.notify(f"💰 <b>EXIT SELL: {p:,.2f}</b>\nProfit: {pnl_thb:+.2f} THB\nReason: {reason}")
                            self.last_action = "sell"; self.avg_price = 0; self._save_state()

                if time.time() - last_rep >= self.report_interval:
                    self._report(p, rsi, ema20, ema50, pnl, thb, coin)
                    last_rep = time.time()

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
    TitanOmniV13().run()
