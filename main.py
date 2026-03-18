import os, requests, time, hmac, hashlib, json, csv, math, psycopg2
import numpy as np
from datetime import datetime, timedelta, timezone

class TitanMasterV10:
    def __init__(self):
        print("🛠️ Initializing TITAN MASTER V.10.2 (Postgres Enabled)...")
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        
        # --- Database Connection (Railway Postgres) ---
        self.db_url = os.getenv("DATABASE_URL")

        # --- อ่านค่าจาก Variables หน้า Railway ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "10000")).replace(',', ''))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "2.0")) 
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", "30.0"))   

        self.tp_target = 10.0         
        self.ema_dist_limit = 0.5    

        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        
        self._init_db() # สร้างตารางถ้ายังไม่มี
        self._load_state_db() # โหลดข้อมูลจาก Postgres
        print(f"✅ Setup Complete. Symbol: {self.symbol}")

    def _init_db(self):
        """ สร้างตารางเก็บสถานะและประวัติใน Postgres """
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            # ตารางเก็บสถานะล่าสุด
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    id SERIAL PRIMARY KEY,
                    last_action TEXT,
                    avg_price FLOAT,
                    total_units FLOAT,
                    highest_price FLOAT,
                    dynamic_sl FLOAT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # ตารางเก็บประวัติการเทรด (Database Log)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trade_history (
                    id SERIAL PRIMARY KEY,
                    time TIMESTAMP,
                    side TEXT,
                    price FLOAT,
                    pnl_pct FLOAT,
                    pnl_thb FLOAT,
                    reason TEXT
                )
            """)
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e: print(f"❌ DB Init Error: {e}")

    def _save_state_db(self):
        """ บันทึกสถานะลง Database """
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute("DELETE FROM bot_state") # ลบของเก่าเก็บของใหม่
            cur.execute("""
                INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl)
                VALUES (%s, %s, %s, %s, %s)
            """, (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e: print(f"❌ DB Save Error: {e}")

    def _load_state_db(self):
        """ โหลดสถานะจาก Database """
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute("SELECT last_action, avg_price, total_units, highest_price, dynamic_sl FROM bot_state LIMIT 1")
            row = cur.fetchone()
            if row:
                self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl = row
                print(f"📦 Restored state from DB: {self.last_action} at {self.avg_price}")
            cur.close()
            conn.close()
        except Exception as e: print(f"❌ DB Load Error: {e}")

    def update_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}").json()
            c = np.array(res['c'], dtype=float)
            ema = self.calculate_ema(c, 20)
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema": ema, "rsi": rsi, "atr": atr}
        except Exception as e:
            return None

    def _report(self, price, pnl, thb, coin, rsi, status="MASTER_ACTIVE"):
        coin_val = coin * price; total = thb + coin_val
        growth = ((total - self.initial_equity) / self.initial_equity) * 100 if self.initial_equity > 0 else 0
        diff_thb = total - self.initial_equity
        be_price = self.avg_price * 1.0051 if self.avg_price > 0 else 0
        sl_dist = ((price - self.dynamic_sl) / self.dynamic_sl * 100) if self.dynamic_sl > 0 else 0

        div = "━━━━━━━━━━━━━━━"
        guard_status = "🟢 Safe" if rsi < self.rsi_buy_max else "🔴 Wait"
        msg = (
            f"<b>🏆 TITAN MASTER V.10.2 ({self.symbol})</b>\n"
            f"🕒 Status: {status}\n{div}\n"
            f"💰 Price: <b>{price:,.2f}</b> | P/L: <b>{pnl:+.2f}%</b>\n"
            f"📊 RSI: {rsi:.1f} | EMA Guard: {guard_status}\n"
            f"🛡️ Config: RSI &lt; {self.rsi_buy_max} | SL: {self.stop_loss_pct}%\n{div}\n"
            f"🏦 <b>LIVE PORTFOLIO</b>\n"
            f"💵 Cash: {thb:,.2f} THB\n"
            f"💠 {self.symbol.split('_')[0]}: {coin:.4f} ({coin_val:,.2f} THB)\n"
            f"💎 Equity: <b>{total:,.2f} THB</b>\n"
            f"🚀 Growth: {growth:+.2f}% (<b>{diff_thb:,.2f} THB</b>)\n{div}\n"
        )
        if self.last_action == "buy" and coin > 0:
            msg += f"🎯 BE Price: {be_price:,.2f}\n🛡️ SL: {self.dynamic_sl:,.2f} (<b>{sl_dist:+.2f}%</b>)\n💰 TP Goal: {self.avg_price*(1 + self.tp_target/100):,.2f}"
        else:
            msg += f"💤 Status: <b>Waiting for Entry...</b>"
        self.notify(msg)

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.update_indicators()
                if not d: time.sleep(20); continue
                p, ema, rsi, atr = d['price'], d['ema'], d['rsi'], d['atr']
                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0
                thb, coin = self.get_balance()

                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 900:
                    dist_ema = ((p - ema) / ema) * 100
                    if rsi < self.rsi_buy_max and dist_ema < self.ema_dist_limit:
                        if self.place_order("buy", thb * 0.98):
                            self.avg_price = p; self.total_units = (thb * 0.975) / p
                            self.last_action = "buy"; self.highest_price = p
                            self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                            self._save_state_db() # บันทึกเข้า DB ทันทีที่ซื้อ
                            self.notify(f"<b>🚀 ENTRY: {p:,.2f}</b>\nRSI: {rsi:.1f}\nTarget: {self.tp_target}%")

                elif self.last_action == "buy" and coin > 0:
                    self.highest_price = max(self.highest_price, p)
                    if pnl >= 2.5: 
                        self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.0025) 
                    
                    self.dynamic_sl = max(self.dynamic_sl, self.highest_price - (atr * 3.5))
                    self._save_state_db() # อัปเดตจุด Trailing Stop เข้า DB ตลอดเวลา

                    reason = None
                    if pnl >= self.tp_target: reason = "Take Profit 💰"
                    elif pnl <= -self.stop_loss_pct: reason = "Stop Loss 🔴"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop 🛡️"

                    if reason:
                        profit_thb = (coin * p * 0.9975) - (self.total_units * self.avg_price * 1.0025)
                        if self.place_order("sell", coin):
                            self._log_trade_db("SELL", p, pnl, profit_thb, reason)
                            self.notify(f"<b>💰 EXIT: {p:,.2f}</b>\nP/L: {pnl:+.2f}% (<b>{profit_thb:+.2f} THB</b>)\nReason: {reason}")
                            self.last_action = "sell"; self.avg_price = 0; self.last_sell_time = time.time()
                            self._save_state_db()

                if time.time() - last_rep >= 600:
                    self._report(p, pnl, thb, coin, rsi)
                    last_rep = time.time()
            except Exception as e: print(f"❌ Error: {e}")
            time.sleep(30)

    def _log_trade_db(self, side, price, pnl_pct, pnl_thb, reason):
        """ บันทึกประวัติการเทรดลง Postgres """
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            now = datetime.now(timezone(timedelta(hours=7)))
            cur.execute("""
                INSERT INTO trade_history (time, side, price, pnl_pct, pnl_thb, reason)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (now, side, price, pnl_pct, pnl_thb, reason))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e: print(f"❌ DB Log Error: {e}")

    def _request(self, method, path, payload=None, private=False):
        url = f"https://api.bitkub.com{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if private:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode('utf-8'), (ts+method+path+(json.dumps(payload) if payload else "")).encode('utf-8'), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=json.dumps(payload) if payload else "").json()

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res.get('error') == 0: return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
        return 0.0, 0.0

    def place_order(self, side, amt):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        res = self._request("POST", path, payload={"sym": self.symbol.lower(), "amt": amt, "rat": 0, "typ": "market"}, private=True)
        return res.get('error') == 0

    def calculate_ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"})
        except: pass

if __name__ == "__main__":
    TitanMasterV10().run()
