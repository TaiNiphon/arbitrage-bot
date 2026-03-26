import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanOmniV13:
    def __init__(self):
        # --- Config & Variables (ระบบพื้นฐาน) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Strategy Settings (ดึงจาก Railway ถ้าไม่มีให้ใช้ค่ามาตรฐาน) ---
        # 1. ทุนเริ่มต้น (Default: 2000)
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        # 2. ความเสี่ยงต่อไม้ (Default: 2.0)
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.0"))
        # 3. จุดตัดขาดทุน % (Default: 4.0)
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "4.0"))
        # 4. เป้าหมาย RSI สำหรับซื้อ (Default: 35.0)
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        # 5. ระยะห่าง EMA (Default: 3.0)
        self.ema_dist_limit = float(os.getenv("EMA_DIST_LIMIT", "3.0"))
        # 6. ความถี่ในการเช็คสัญญาณ (Default: 3 วินาทีตามที่พี่ต้องการ)
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "3"))
        # 7. ความถี่ในการส่งรายงาน (Default: 600 วินาที / 10 นาที)
        self.report_interval = int(os.getenv("REPORT_INTERVAL", "600"))
        # 8. ค่าธรรมเนียม Bitkub 
        self.fee_pct = float(os.getenv("FEE_PCT", "0.25"))/100
        # --- Tracking System (ระบบจำสถานะ) ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.rsi_prev = None; self.rsi_memory = None 

        self._init_db()
        self._sync_with_wallet() # บังคับจำไม้ค้างและเงินสดจาก Bitkub โดยตรง
        self.notify("<b>💎 TITAN OMNI V.13 | RAILWAY LIVE</b>\n<i>Status: Variables Synced & Wallet Connected</i>")

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
        """ ตรวจสอบสถานะเหรียญ/เงินสดจริง และอัปเดต Database ทันที """
        try:
            thb, coin = self.get_balance()
            d = self.get_indicators()
            p = d['price'] if d else 46.18 # ใช้ราคาทุนเดิมถ้าดึงราคาปัจจุบันไม่ได้

            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("DELETE FROM bot_state") # เคลียร์สถานะเก่า

            if coin > 0.1: # สถานะถือเหรียญ
                self.last_action = "buy"; self.total_units = coin; self.avg_price = 46.18 
                self.highest_price = max(p, self.avg_price)
                self.dynamic_sl = self.avg_price * (1 - (self.stop_loss_pct/100))
                cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl) VALUES (%s, %s, %s, %s, %s)", 
                            (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl))
            else: # สถานะถือเงินสด
                self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
                cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl) VALUES (%s, %s, %s, %s, %s)", 
                            ("sell", 0, 0, 0, 0))

            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"Sync Error: {e}")

    def _save_state(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("UPDATE bot_state SET last_action=%s, avg_price=%s, total_units=%s, highest_price=%s, dynamic_sl=%s, updated_at=CURRENT_TIMESTAMP", 
                        (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl))
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

        msg = (
            f"🛡️ <b>TITAN OMNI V.13 | {self.symbol}</b>\n"
            f"Status : MONITORING\n"
            f"Date   : {now.strftime('%d/%m/%Y')}\n"
            f"Time   : {now.strftime('%H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>MARKET INTELLIGENCE</b>\n"
            f"• Price : <b>{price:,.2f} THB</b>\n"
            f"• Trend : {'BULLISH 📈' if price > ema50 else 'BEARISH 📉'}\n"
            f"• RSI   : {rsi:.2f} {'🟢' if rsi > (self.rsi_memory or 0) else '🔴'} (Prev:{self.rsi_memory or 0:.2f})\n"
            f"• Dist  : {((price-ema20)/ema20*100):+.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>PORTFOLIO ANALYSIS</b>\n"
            f"• EQUITY : <b>{total:,.2f} THB</b>\n"
            f"• GROWTH : {growth:+.2f}%\n"
            f"• Cash   : {thb:,.2f} | Assets: {coin:.4f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>STRATEGY</b>\n"
            f"• Risk/Trade: {self.risk_per_trade}% (~{(total * (self.risk_per_trade/100)):,.2f})\n"
        )
        msg += f"• Status: <b>Holding Profit: {pnl:+.2f}%</b>" if self.last_action == "buy" else f"• Status: <i>Searching Entry... (Target RSI:{self.rsi_buy_target})</i>"
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
                        self._save_state(); self.notify(f"🚀 <b>ENTRY BUY: {p:,.2f}</b>\nRSI: {rsi:.2f}")

                elif self.last_action == "buy" and coin > 0:
                    self.highest_price = max(self.highest_price, p)
                    trail_dist = atr * 2.3
                    if p - trail_dist > self.dynamic_sl: self.dynamic_sl = p - trail_dist
                    if pnl >= 1.5: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.005)

                    if pnl >= 15.0 or p <= self.dynamic_sl:
                        if self.place_order("sell", coin):
                            self.notify(f"💰 <b>EXIT SELL: {p:,.2f}</b>\nProfit: {pnl:+.2f}%")
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
