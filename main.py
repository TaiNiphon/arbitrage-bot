import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta

class TitanUltimate_V18:
    def __init__(self):
        # --- 1. CONFIGURATION & AUTH ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        
        # --- 2. STRATEGY SETTINGS (Sync with Railway Variables) ---
        raw_eq = os.getenv("INITIAL_EQUITY", "3300")
        self.initial_equity = float(str(raw_eq).replace(',', ''))
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", "35.0"))
        
        # ดึงค่าเป้าหมายกำไรและค่าธรรมเนียมจาก Variables
        self.target_profit = float(os.getenv("TARGET_PROFIT", "10.0"))
        self.fee_rate = float(os.getenv("FEE_RATE", "0.0025")) # 0.25% per side
        
        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}
        
        self._init_db()
        self._load_state()
        self.notify(f"🚀 <b>TITAN V.18 ULTIMATE ACTIVE</b>\n<i>Target: {self.target_profit}% | Fee: {self.fee_rate*100}%</i>\nReady for Professional Trading")

    # --- 3. DATABASE SYSTEM (Mapped to your Postgres Tables) ---
    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    # ตารางประวัติการเทรด (สำหรับรายงานรายเดือน)
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP, side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT)""")
                    # ตารางสถานะบอท (แมพตามชื่อที่คุณมีในรูป 6228)
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v15 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT)""")
                    conn.commit()
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl FROM bot_state_v15")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3]}
        except: pass

    # --- 4. INDICATORS & MARKET DATA (EMA 200 + RSI + ATR) ---
    def get_market_data(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            ema200 = np.mean(c[-200:])
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"p": c[-1], "rsi": rsi, "ema": ema200, "atr": np.mean(tr[-14:])}
        except: return None

    # --- 5. REPORTING SYSTEM ---
    def send_dashboard(self, data, thb, coin):
        p, rsi, ema = data['p'], data['rsi'], data['ema']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        trend = "🌕 BULL" if p > ema else "🌑 BEAR"
        
        msg = f"<b>🏦 TITAN DASHBOARD</b>\n"
        msg += f"<code>Status: {trend} | RSI: {rsi:.1f}</code>\n"
        msg += f"<code>Equity: {equity:,.2f} | Growth: {growth:+.2f}%</code>\n"
        msg += "---------------------------\n"
        for i, s in self.slots.items():
            if s['active']:
                # คำนวณ Net PnL หักค่าธรรมเนียมจริง
                entry_cost = s['price'] * (1 + self.fee_rate)
                current_val = p * (1 - self.fee_rate)
                pnl = ((current_val - entry_cost) / entry_cost) * 100
                msg += f"🟢 SLOT {i}: {pnl:+.2f}% ({s['price']:,.2f})\n"
            else:
                msg += f"⚪ SLOT {i}: WAIT RSI ≤ {self.rsi_buy_max}\n"
        self.notify(msg)

    def send_monthly_report(self):
        last_month = datetime.now() - timedelta(days=30)
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT SUM(net_pnl_thb), COUNT(*) FROM trade_history WHERE ts > %s AND side='SELL'", (last_month,))
                    res = cur.fetchone()
                    total_pnl = res[0] or 0
                    count = res[1] or 0
            msg = f"<b>📅 MONTHLY SUMMARY</b>\n<code>Net Profit: {total_pnl:,.2f} THB | Completed Trades: {count}</code>"
            self.notify(msg)
        except: pass

    # --- 6. EXECUTION LOGIC ---
    def execute_trade(self, side, slot_id, price, amt_units, atr):
        ts = str(int(time.time() * 1000))
        path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"
        payload = {"sym": self.symbol.lower(), "amt": amt_units, "rat": price, "typ": "limit"}
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
        
        try:
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload), timeout=15).json()
            
            if res.get('error') == 0:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            sl = price - (atr * 2.5) # ATR Stop Loss
                            cur.execute("INSERT INTO bot_state_v15 (slot_id, price, units, sl) VALUES (%s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl", (slot_id, price, amt_units/price, sl))
                            self.notify(f"📥 <b>BUY COMPLETED</b>\nSlot: {slot_id} | Price: {price:,.2f}")
                        else:
                            s = self.slots[slot_id]
                            # บันทึก Net Profit เป็นบาท (หัก Fee ไป-กลับ)
                            pnl_thb = (price * amt_units * (1 - self.fee_rate)) - (s['price'] * amt_units * (1 + self.fee_rate))
                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb) VALUES (NOW(), 'SELL', %s, %s, %s)", (price, amt_units, pnl_thb))
                            cur.execute("DELETE FROM bot_state_v15 WHERE slot_id = %s", (slot_id,))
                            self.notify(f"📤 <b>SELL COMPLETED</b>\nProfit: {pnl_thb:,.2f} THB")
                        conn.commit()
                return True
        except: pass
        return False

    def run(self):
        last_dash = 0
        while True:
            try:
                d = self.get_market_data()
                if not d: time.sleep(20); continue
                
                # Update Wallet
                ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                thb = float(wallet['result'].get('THB', 0)); coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                # Dashboard ทุก 1 ชม.
                if time.time() - last_dash > 3600:
                    self.send_dashboard(d, thb, coin); last_dash = time.time()
                
                # Monthly Report ทุกวันที่ 1
                now = datetime.now()
                if now.day == 1 and now.hour == 8 and now.minute == 0: self.send_monthly_report()

                # --- TRADING LOGIC ---
                active_count = sum(1 for s in self.slots.values() if s['active'])
                
                # BUY: เงื่อนไข RSI และกะขนาดไม้
                if active_count < 2 and d['rsi'] <= self.rsi_buy_max:
                    # กรองเฉพาะขาขึ้น (EMA200) เพื่อความปลอดภัยของเงินแสน
                    if d['p'] > d['ema']:
                        buy_amt = (thb + (coin * d['p'])) * 0.45
                        s_id = 1 if not self.slots[1]['active'] else 2
                        if thb >= buy_amt and self.execute_trade('buy', s_id, d['p'], buy_amt, d['atr']):
                            self._load_state()

                # SELL: เงื่อนไขเป้ากำไรหรือ Stop Loss
                for i, s in self.slots.items():
                    if s['active']:
                        entry_cost = s['price'] * (1 + self.fee_rate)
                        exit_revenue = d['p'] * (1 - self.fee_rate)
                        net_pnl = ((exit_revenue - entry_cost) / entry_cost) * 100
                        
                        if net_pnl >= self.target_profit or d['p'] <= s['sl']:
                            if self.execute_trade('sell', i, d['p'], s['units'], d['atr']):
                                self.slots[i]['active'] = False

            except Exception as e: print(f"Error: {e}")
            time.sleep(20)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanUltimate_V18().run()
