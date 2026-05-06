import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_20_Stable:
    def __init__(self):
        # --- [1] CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] STRATEGY SETTINGS (กู้คืนจาก V.18.15 ที่ดีอยู่แล้ว) ---
        self.initial_equity = 10000.28 
        self.fee_rate = 0.0025 
        self.current_tp = 2.0       
        self.current_rsi_buy = 40.0 
        self.last_alive_check = -1

        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}

        self._init_db() 
        self._load_state() 
        self.notify("🏛️ <b>TITAN V.18.20: DATABASE-FIX</b>\n<i>Status: Recovery & Sync Active</i>")

    def notify(self, message):
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}
            requests.post(url, json=payload, timeout=10)
        except: pass

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        """แก้ไข: ตรวจสอบชื่อ Table ให้ตรงกับในรูป Postgres ของคุณเสมอ"""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT)""")
                    conn.commit()
        except: pass

    def _load_state(self):
        """โหลดสถานะล่าสุดจาก DB (V.18 เท่านั้น)"""
        try:
            self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                          2: {"active": False, "price": 0, "units": 0, "sl": 0}}
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl FROM bot_state_v18")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3]}
        except: pass

    def get_indicator(self, symbol):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15).json()
            c = np.array(res['c'], dtype=float)
            def calc_rsi(prices, p_len):
                diff = np.diff(prices); up = diff.clip(min=0); down = -diff.clip(max=0)
                return 100 - (100 / (1 + (np.mean(up[-p_len:]) / (np.mean(down[-p_len:]) + 1e-9))))
            ema = np.mean(c[-200:])
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"p": c[-1], "r14": calc_rsi(c, 14), "r200": calc_rsi(c, 200), "ema": ema, "atr": np.mean(tr[-14:])}
        except: return None

    def execute_trade(self, side, slot_id, price, amt_units, atr):
        """แก้ไข: ปรับจังหวะบันทึก DB ให้แม่นยำขึ้น"""
        ts = str(int(time.time() * 1000)); path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"
        f_rat = round(float(price), 2); f_amt = int(float(amt_units)) if side == 'buy' else round(float(amt_units), 4)

        payload = {"sym": self.symbol.lower(), "amt": f_amt, "rat": f_rat, "typ": "limit"}
        payload_json = json.dumps(payload, separators=(',', ':'))
        sig = hmac.new(self.api_secret.encode(), (ts + "POST" + path + payload_json).encode(), hashlib.sha256).hexdigest()
        
        try:
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}, data=payload_json, timeout=15).json()
            if res.get('error') == 0:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            actual_units = round(f_amt / f_rat, 4); sl = round(f_rat - (atr * 2.5), 2)
                            cur.execute("INSERT INTO bot_state_v18 (slot_id, price, units, sl) VALUES (%s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl", (slot_id, f_rat, actual_units, sl))
                        else:
                            s = self.slots[slot_id]
                            net_pnl = (f_rat * s['units'] * (1-self.fee_rate)) - (s['price'] * s['units'] * (1+self.fee_rate))
                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb, status) VALUES (NOW(), 'SELL', %s, %s, %s, %s)", (f_rat, s['units'], net_pnl, 'WIN' if net_pnl > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (slot_id,))
                        conn.commit()
                self._load_state()
                return True
        except: pass
        return False

    def sync_wallet(self, coin_in_hand):
        """ฟีเจอร์ใหม่: ถ้าเหรียญหายไปจากกระเป๋า (ขายเอง) ให้ล้างไม้ใน DB ทันที"""
        if coin_in_hand < 1.0 and sum(1 for s in self.slots.values() if s['active']) > 0:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur: cur.execute("DELETE FROM bot_state_v18")
                conn.commit()
            self._load_state(); self.notify("🔄 <b>Auto-Sync:</b> เหรียญถูกขายออกนอกระบบ ล้างสถานะ DB แล้ว")

    def send_dashboard(self, dx, db, thb, coin):
        p, r14, r200 = dx['p'], dx['r14'], dx['r200']
        equity = thb + (coin * p); growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        msg = f"🏛️ <b>TITAN V.18.20: DASHBOARD</b>\n📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n📊 Price: {p:,.2f} | RSI 14: {r14:.1f}\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"📈 Growth: {growth:+.2f}%\n---------------------------------\n"

        for i, s in self.slots.items():
            if s['active']:
                pnl = (((p*(1-self.fee_rate)) - (s['price']*(1+self.fee_rate))) / (s['price']*(1+self.fee_rate))) * 100
                msg += f"🟢 SLOT {i}: {pnl:+.2f}% (Price: {s['price']})\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING (RSI ≤ {self.current_rsi_buy})\n"
        self.notify(msg)

    def run(self):
        last_dash = 0
        while True:
            try:
                dx, db = self.get_indicator(self.symbol), self.get_indicator("BTC_THB")
                if not dx or not db: time.sleep(20); continue

                # --- DYNAMIC FLOW (กู้คืน Logic ที่ดีที่สุดของคุณ) ---
                if dx['r200'] >= 48: 
                    self.current_tp = 2.0 if dx['r200'] < 60 else 10.0
                    self.current_rsi_buy = 40.0
                else: self.current_tp = 2.0; self.current_rsi_buy = 25.0

                ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                thb = float(wallet['result'].get('THB', 0)); coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                self.sync_wallet(coin) # ป้องกันไม้ค้างใน DB

                # SELL LOGIC
                for i, s in self.slots.items():
                    if s['active']:
                        if (((dx['p']*(1-self.fee_rate)) - (s['price']*(1+self.fee_rate))) / (s['price']*(1+self.fee_rate))) * 100 >= self.current_tp or dx['p'] <= s['sl']:
                            self.execute_trade('sell', i, dx['p'], s['units'], dx['atr'])

                # BUY LOGIC
                if sum(1 for s in self.slots.values() if s['active']) < 2 and dx['r14'] <= self.current_rsi_buy:
                    if dx['p'] > dx['ema'] and db['p'] > db['ema']:
                        buy_amt = int((thb + (coin * dx['p'])) * 0.45) 
                        if thb >= buy_amt >= 10:
                            s_id = 1 if not self.slots[1]['active'] else 2
                            self.execute_trade('buy', s_id, dx['p'], buy_amt, dx['atr'])

                if time.time() - last_dash > 3600:
                    self.send_dashboard(dx, db, thb, coin); last_dash = time.time()

            except Exception as e: print(f"Loop Error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    TitanV18_20_Stable().run()
