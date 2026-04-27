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
        
        # --- 2. STRATEGY SETTINGS ---
        raw_eq = os.getenv("INITIAL_EQUITY", "3300")
        self.initial_equity = float(str(raw_eq).replace(',', ''))
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.target_profit = float(os.getenv("TARGET_PROFIT", "10.0"))
        self.fee_rate = float(os.getenv("FEE_RATE", "0.0025"))
        
        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}
        
        self._init_db_v18() # อัปเกรดระบบฐานข้อมูลเป็น V18
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18 PRO-MAX: ONLINE</b>\n<i>Database Schema: v18 | Reporting: Professional</i>")

    # --- 3. DATABASE SYSTEM (V18 MIGRATION) ---
    def _init_db_v18(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    # ตารางประวัติการเทรดแบบละเอียด
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT)""")
                    # ตารางสถานะบอท V18
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT)""")
                    
                    # Logic ย้ายข้อมูลจาก v15 (ถ้ามีข้อมูลค้างอยู่)
                    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'bot_state_v18'")
                    if cur.fetchone()[0] > 0:
                        cur.execute("INSERT INTO bot_state_v18 (slot_id, price, units, sl) SELECT slot_id, price, units, sl FROM bot_state_v15 ON CONFLICT DO NOTHING")
                    conn.commit()
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl FROM bot_state_v18")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3]}
        except: pass

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

    # --- 4. PROFESSIONAL REPORTING (Section A: Dashboard) ---
    def send_dashboard(self, data, thb, coin):
        p, rsi, ema = data['p'], data['rsi'], data['ema']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        trend = "🌕 BULLISH" if p > ema else "🌑 BEARISH"
        rsi_stat = " (OVERSOLD)" if rsi <= 30 else " (OVERBOUGHT)" if rsi >= 70 else ""

        msg = f"🏛️ <b>TITAN PRO-MAX: PORTFOLIO STATUS</b>\n"
        msg += f"Market: {self.symbol} | Trend: {trend}\n"
        msg += f"Price: {p:,.2f} THB | RSI: {rsi:.1f}{rsi_stat}\n"
        msg += "---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"Total Growth: {growth:+.2f}% (From {self.initial_equity:,.0f})\n"
        msg += f"Available  : {thb:,.2f} THB\n"
        msg += "---------------------------------\n"
        for i, s in self.slots.items():
            if s['active']:
                e_cost = s['price'] * (1 + self.fee_rate); x_rev = p * (1 - self.fee_rate)
                pnl = ((x_rev - e_cost) / e_cost) * 100
                msg += f"🟢 SLOT {i}: IN TRADE (PnL: {pnl:+.2f}%)\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING (Target RSI ≤ {self.rsi_buy_max})\n"
        self.notify(msg)

    # --- 5. EXECUTION LOGIC (Section B: Transaction Reports) ---
    def execute_trade(self, side, slot_id, price, amt_units, atr):
        ts = str(int(time.time() * 1000)); path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"
        payload = {"sym": self.symbol.lower(), "amt": amt_units, "rat": price, "typ": "limit"}
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
        
        try:
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload), timeout=15).json()
            if res.get('error') == 0:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            sl = price - (atr * 2.5)
                            cur.execute("INSERT INTO bot_state_v18 (slot_id, price, units, sl) VALUES (%s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl", (slot_id, price, amt_units/price, sl))
                            self.notify(f"📥 <b>BUY COMPLETED</b>\nSlot: {slot_id} | Price: {price:,.2f}\nUnits: {amt_units/price:,.2f} {self.symbol.split('_')[0]}\nSL: {sl:,.2f}")
                        else:
                            s = self.slots[slot_id]
                            gross_pnl = ((price - s['price']) / s['price']) * 100
                            fee_thb = (price * amt_units * self.fee_rate) + (s['price'] * amt_units * self.fee_rate)
                            net_pnl_thb = (price * amt_units * (1-self.fee_rate)) - (s['price'] * amt_units * (1+self.fee_rate))
                            
                            msg = f"⚡ <b>TRADE COMPLETED ({'PROFIT' if net_pnl_thb > 0 else 'LOSS'})</b>\n"
                            msg += f"Action: SELL {self.symbol.split('_')[0]} | Slot: {slot_id}\n"
                            msg += f"Price : {price:,.2f} THB\n"
                            msg += "---------------------------------\n"
                            msg += f"Gross Profit: {gross_pnl:+.2f}%\n"
                            msg += f"Fee ({self.fee_rate*200}%): -{fee_thb:,.2f} THB\n"
                            msg += f"<b>NET PROFIT : {net_pnl_thb:,.2f} THB</b> {'✅' if net_pnl_thb > 0 else '❌'}\n"
                            self.notify(msg)

                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb, status) VALUES (NOW(), 'SELL', %s, %s, %s, %s)", (price, amt_units, net_pnl_thb, 'WIN' if net_pnl_thb > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (slot_id,))
                        conn.commit()
                return True
        except: pass
        return False

    # --- 6. MONTHLY ANALYTICS (Section C: Win Rate & Growth) ---
    def send_monthly_report(self):
        last_month = datetime.now() - timedelta(days=30)
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT net_pnl_thb, status FROM trade_history WHERE ts > %s AND side='SELL'", (last_month,))
                    rows = cur.fetchall()
                    total_pnl = sum(r[0] for r in rows); trades = len(rows)
                    wins = sum(1 for r in rows if r[1] == 'WIN')
                    win_rate = (wins / trades * 100) if trades > 0 else 0
            
            msg = f"📅 <b>MONTHLY PERFORMANCE ({datetime.now().strftime('%B %Y').upper()})</b>\n"
            msg += f"Starting Equity: {self.initial_equity:,.2f} THB\n"
            msg += f"Ending Equity  : {self.initial_equity + total_pnl:,.2f} THB\n"
            msg += "---------------------------------\n"
            msg += f"Total Trades : {trades} Trades\n"
            msg += f"Win Rate     : {win_rate:.0f}% (W:{wins} / L:{trades-wins})\n"
            msg += f"<b>NET MONTHLY : {total_pnl:,.2f} THB ({((total_pnl/self.initial_equity)*100):+.2f}%)</b>\n"
            self.notify(msg)
        except: pass

    def run(self):
        last_dash = 0
        while True:
            try:
                d = self.get_market_data()
                if not d: time.sleep(20); continue
                
                ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                thb = float(wallet['result'].get('THB', 0)); coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                if time.time() - last_dash > 3600:
                    self.send_dashboard(d, thb, coin); last_dash = time.time()
                
                now = datetime.now()
                if now.day == 1 and now.hour == 8 and now.minute == 0: self.send_monthly_report()

                active_count = sum(1 for s in self.slots.values() if s['active'])
                if active_count < 2 and d['rsi'] <= self.rsi_buy_max and d['p'] > d['ema']:
                    buy_amt = (thb + (coin * d['p'])) * 0.45
                    if thb >= buy_amt:
                        s_id = 1 if not self.slots[1]['active'] else 2
                        if self.execute_trade('buy', s_id, d['p'], buy_amt, d['atr']): self._load_state()

                for i, s in self.slots.items():
                    if s['active']:
                        e_cost = s['price'] * (1 + self.fee_rate); x_rev = d['p'] * (1 - self.fee_rate)
                        if ((x_rev - e_cost) / e_cost) * 100 >= self.target_profit or d['p'] <= s['sl']:
                            if self.execute_trade('sell', i, d['p'], s['units'], d['atr']): self.slots[i]['active'] = False
            except Exception as e: print(f"Error: {e}")
            time.sleep(20)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanUltimate_V18().run()
