import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_15_OmniFlow:
    def __init__(self):
        # --- [1] CONFIGURATION (คงเดิมจาก V.18.12) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] STRATEGY SETTINGS (กู้คืนจาก 18.12 และอัปเกรดทุน) ---
        self.initial_equity = 11811.28 # ทุนล่าสุดตามรูปรายงาน
        self.fee_rate = 0.0025 
        self.last_alive_check = -1

        # Dynamic Parameters (ปรับอัตโนมัติตามตลาด)
        self.current_tp = 3.0       # เป้ากำไร (จะเปลี่ยนเป็น 10% เมื่อตลาดเป็น Uptrend)
        self.current_rsi_buy = 40.0 # จุดซื้อ (จะเปลี่ยนเป็น 35 เมื่อตลาดเป็น Uptrend)

        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}

        self._init_db() 
        self._load_state() # ดึง 2 ไม้ที่ค้างอยู่ออกมาทำงานต่อทันที
        self.notify("🏛️ <b>TITAN V.18.15: OMNI-FLOW</b>\n<i>Status: Full System Online | Omni-Flow Active</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT)""")
                    conn.commit()
        except: pass

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl FROM bot_state_v18")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3]}
        except: pass

    def get_indicator(self, symbol):
        for i in range(3):
            try:
                res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15).json()
                c = np.array(res['c'], dtype=float)
                def calc_rsi(prices, p_len):
                    diff = np.diff(prices); up = diff.clip(min=0); down = -diff.clip(max=0)
                    return 100 - (100 / (1 + (np.mean(up[-p_len:]) / (np.mean(down[-p_len:]) + 1e-9))))
                ema = np.mean(c[-200:])
                tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
                return {"p": c[-1], "r14": calc_rsi(c, 14), "r200": calc_rsi(c, 200), "ema": ema, "atr": np.mean(tr[-14:])}
            except: time.sleep(2)
        return None

    def execute_trade(self, side, slot_id, price, amt_units, atr):
        ts = str(int(time.time() * 1000))
        path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"
        final_rat = round(float(price), 2)
        
        # ป้องกัน Error 10: ใช้ Integer สำหรับซื้อ / Round 4 สำหรับขาย
        final_amt = int(float(amt_units)) if side == 'buy' else round(float(amt_units), 4)

        payload = {"sym": self.symbol.lower(), "amt": final_amt, "rat": final_rat, "typ": "limit"}
        payload_json = json.dumps(payload, separators=(',', ':'))
        sig = hmac.new(self.api_secret.encode(), (ts + "POST" + path + payload_json).encode(), hashlib.sha256).hexdigest()
        now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        try:
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}, data=payload_json, timeout=15).json()
            if res.get('error') == 0:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            actual_units = round(final_amt / final_rat, 4)
                            sl = round(final_rat - (atr * 2.5), 2)
                            cur.execute("INSERT INTO bot_state_v18 (slot_id, price, units, sl) VALUES (%s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl", (slot_id, final_rat, actual_units, sl))
                            msg = f"📥 <b>BUY COMPLETED (Slot {slot_id})</b>\n📅 <code>{now_str}</code>\nPrice: {final_rat:,.2f} | Amount: {final_amt:,} THB\n🛡️ SL: {sl:,.2f}"
                        else:
                            s = self.slots[slot_id]
                            net_pnl = (final_rat * s['units'] * (1-self.fee_rate)) - (s['price'] * s['units'] * (1+self.fee_rate))
                            msg = f"⚡ <b>TRADE COMPLETED ({'PROFIT' if net_pnl > 0 else 'LOSS'})</b>\n📅 <code>{now_str}</code>\nNET PROFIT: <b>{net_pnl:,.2f} THB</b> {'✅' if net_pnl > 0 else '❌'}"
                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb, status) VALUES (NOW(), 'SELL', %s, %s, %s, %s)", (final_rat, s['units'], net_pnl, 'WIN' if net_pnl > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (slot_id,))
                        conn.commit(); self.notify(msg); self._load_state()
                return True
        except: pass
        return False

    def send_dashboard(self, dx, db, thb, coin):
        # กู้คืนหน้าตา Dashboard 18.12 เป๊ะๆ ตามรูปที่คุณส่ง
        p, r14, r200 = dx['p'], dx['r14'], dx['r200']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        x_trend = "🌕 BULLISH" if p > dx['ema'] else "🌑 BEARISH"
        b_trend = "🌕 BULLISH" if db['p'] > db['ema'] else "🌑 BEARISH"

        msg = f"🏛️ <b>TITAN V.18.15: DASHBOARD</b>\n📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : {p:,.2f} THB\n📊 Trend : {x_trend}\n📉 RSI 14: {r14:.2f} | RSI 200: {r200:.2f}\n"
        msg += f"---------------------------------\n🛡️ <b>BTC-GUARD STATUS</b>\n"
        msg += f"📊 Trend : {b_trend}\n💰 BTC P.: {db['p']:,.0f} THB\n"
        msg += f"---------------------------------\n💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f} THB\n"
        msg += f"🪙 Coin Value : {(coin*p):,.2f} THB\n"
        msg += f"📦 Total Coins: {coin:.4f} XRP\n"
        msg += f"📈 Total Growth: {growth:+.2f}%\n"
        msg += f"---------------------------------\n"
        
        for i, s in self.slots.items():
            if s['active']:
                pnl = (((p*(1-self.fee_rate)) - (s['price']*(1+self.fee_rate))) / (s['price']*(1+self.fee_rate))) * 100
                tp_p = round(s['price'] * (1 + (self.current_tp / 100)), 2)
                msg += f"🟢 SLOT {i}: {s['units']:.4f} XRP ({pnl:+.2f}%)\n"
                msg += f"🎯 <b>TP:</b> {tp_p:,.2f} | 🛡️ <b>SL:</b> {s['sl']:,.2f}\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING (RSI ≤ {self.current_rsi_buy})\n"
        self.notify(msg)

    def send_periodic_report(self, days, title):
        start_date = self.get_thai_now() - timedelta(days=days)
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT net_pnl_thb, status FROM trade_history WHERE ts > %s AND side='SELL'", (start_date,))
                    rows = cur.fetchall()
                    total_pnl = sum(r[0] for r in rows); trades = len(rows)
                    wins = sum(1 for r in rows if r[1] == 'WIN'); win_rate = (wins/trades*100) if trades > 0 else 0
            msg = f"📅 <b>{title} SUMMARY</b>\n---------------------------------\nTrades: {trades} | Win Rate: {win_rate:.0f}%\n<b>Net Profit: {total_pnl:,.2f} THB</b>"
            self.notify(msg)
        except: pass

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def run(self):
        last_dash = 0; last_day = self.get_thai_now().day
        while True:
            try:
                thai_now = self.get_thai_now()
                dx, db = self.get_indicator(self.symbol), self.get_indicator("BTC_THB")
                if not dx or not db: time.sleep(20); continue

                # --- กลยุทธ์ OMNI-FLOW: ปรับตัวแปรตามตลาดอัตโนมัติ ---
                if dx['r200'] >= 48: # Sideway Up หรือ Uptrend
                    self.current_tp = 3.0 if dx['r200'] < 60 else 10.0
                    self.current_rsi_buy = 40.0 if dx['r200'] < 60 else 35.0
                else: # Downtrend
                    self.current_tp = 2.0; self.current_rsi_buy = 25.0

                ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                thb = float(wallet['result'].get('THB', 0)); coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                # รายงาน Alive Check (ทุก 6 ชม.)
                if thai_now.hour in [0, 6, 12, 18] and thai_now.hour != self.last_alive_check:
                    self.notify(f"📡 <b>TITAN ALIVE</b>\n📅 {thai_now.strftime('%H:%M')} | Status: OK ✅"); self.last_alive_check = thai_now.hour
                
                # รายงาน Dashboard (ทุก 1 ชม.)
                if time.time() - last_dash > 3600:
                    self.send_dashboard(dx, db, thb, coin); last_dash = time.time()
                
                # รายงาน Daily (08:00) และ Monthly (วันที่ 1)
                if thai_now.day != last_day and thai_now.hour == 8:
                    self.send_periodic_report(1, "DAILY"); last_day = thai_now.day
                if thai_now.day == 1 and thai_now.hour == 8 and thai_now.minute < 5:
                    self.send_periodic_report(30, "MONTHLY")

                # Logic การเข้าซื้อ
                if sum(1 for s in self.slots.values() if s['active']) < 2 and dx['r14'] <= self.current_rsi_buy:
                    if dx['p'] > dx['ema'] and db['p'] > db['ema']:
                        buy_amt = int((thb + (coin * dx['p'])) * 0.45) # ซื้อ 45% ของ Equity
                        if thb >= buy_amt >= 10:
                            s_id = 1 if not self.slots[1]['active'] else 2
                            self.execute_trade('buy', s_id, dx['p'], buy_amt, dx['atr'])

                # Logic การขาย
                for i, s in self.slots.items():
                    if s['active']:
                        e_cost = s['price'] * (1 + self.fee_rate); x_rev = dx['p'] * (1 - self.fee_rate)
                        if ((x_rev - e_cost) / e_cost) * 100 >= self.current_tp or dx['p'] <= s['sl']:
                            self.execute_trade('sell', i, dx['p'], s['units'], dx['atr'])
            except: pass
            time.sleep(20)

if __name__ == "__main__":
    TitanV18_15_OmniFlow().run()
