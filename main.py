import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta

class TitanUltimate_V18_Full_Stable:
    def __init__(self):
        # --- CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- STRATEGY SETTINGS ---
        raw_eq = os.getenv("INITIAL_EQUITY", "7500") # ปรับตามยอดล่าสุดของคุณ
        self.initial_equity = float(str(raw_eq).replace(',', ''))
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.target_profit = float(os.getenv("TARGET_PROFIT", "10.0"))
        self.fee_rate = 0.0025 

        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}

        self._init_db_v18() 
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18: MILLIONAIRE EDITION ACTIVE</b>\n<i>Status: BTC-Guard | Thai Time | Robust Data Fetching</i>")

    def get_thai_now(self):
        return datetime.utcnow() + timedelta(hours=7)

    def _init_db_v18(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT)""")
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

    def get_indicator(self, symbol, period=200):
        # --- เพิ่มระบบ RETRY 3 รอบ เพื่อความเสถียรสำหรับพอร์ตใหญ่ ---
        for i in range(3):
            try:
                res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15).json()
                c = np.array(res['c'], dtype=float)
                
                def calc_rsi(prices, p_len):
                    diff = np.diff(prices)
                    up = diff.clip(min=0); down = -diff.clip(max=0)
                    return 100 - (100 / (1 + (np.mean(up[-p_len:]) / (np.mean(down[-p_len:]) + 1e-9))))

                ema = np.mean(c[-period:])
                tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
                return {"p": c[-1], "rsi14": calc_rsi(c, 14), "ema": ema, "atr": np.mean(tr[-14:])}
            except Exception as e:
                print(f"Fetch Error ({symbol}) trial {i+1}: {e}")
                time.sleep(2) # รอ 2 วินาทีก่อนลองใหม่
        return None

    def send_dashboard(self, data_xrp, data_btc, thb, coin):
        p, rsi14, ema = data_xrp['p'], data_xrp['rsi14'], data_xrp['ema']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        xrp_trend = "🌕 BULLISH" if p > ema else "🌑 BEARISH"
        btc_trend = "🌕 BULLISH" if data_btc['p'] > data_btc['ema'] else "🌑 BEARISH"
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        msg = f"📊 <b>TITAN STATUS (Hourly)</b>\n📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"🔸 <b>{self.symbol}</b>: {p:,.2f} | {xrp_trend}\n"
        msg += f"🔸 <b>BTC_THB</b>: {data_btc['p']:,.0f} | {btc_trend}\n"
        msg += f"📊 RSI 14: {rsi14:.2f} | Equity: {equity:,.2f}\n"
        msg += f"📈 Growth: {growth:+.2f}% | Cash: {thb:,.2f}\n"
        msg += f"---------------------------------\n"
        for i, s in self.slots.items():
            if s['active']:
                e_cost = s['price'] * (1 + self.fee_rate); x_rev = p * (1 - self.fee_rate)
                pnl = ((x_rev - e_cost) / e_cost) * 100
                msg += f"🟢 SLOT {i}: IN TRADE ({pnl:+.2f}%)\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING\n"
        self.notify(msg)

    def execute_trade(self, side, slot_id, price, amt_units, atr):
        ts = str(int(time.time() * 1000)); path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"
        payload = {"sym": self.symbol.lower(), "amt": amt_units, "rat": price, "typ": "limit"}
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
        now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        try:
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload), timeout=15).json()
            if res.get('error') == 0:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            sl = price - (atr * 2.5) 
                            cur.execute("INSERT INTO bot_state_v18 (slot_id, price, units, sl) VALUES (%s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl", (slot_id, price, amt_units/price, sl))
                            msg = f"📥 <b>BUY COMPLETED</b>\n📅 <code>{now_str}</code>\nSlot: {slot_id} | Price: {price:,.2f} | 🛡️ SL: {sl:,.2f}"
                        else:
                            s = self.slots[slot_id]
                            net_pnl_thb = (price * s['units'] * (1-self.fee_rate)) - (s['price'] * s['units'] * (1+self.fee_rate))
                            msg = f"⚡ <b>TRADE COMPLETED ({'PROFIT' if net_pnl_thb > 0 else 'LOSS'})</b>\n📅 <code>{now_str}</code>\n"
                            msg += f"Net PnL: <b>{net_pnl_thb:,.2f} THB</b> {'✅' if net_pnl_thb > 0 else '❌'}"
                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb, status) VALUES (NOW(), 'SELL', %s, %s, %s, %s)", (price, s['units'], net_pnl_thb, 'WIN' if net_pnl_thb > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (slot_id,))
                        conn.commit()
                        self.notify(msg)
                return True
            else:
                self.notify(f"⚠️ <b>Trade Execution Failed:</b> {res.get('error')}")
        except Exception as e: print(f"Trade Error: {e}")
        return False

    def send_periodic_report(self, days, title):
        start_date = self.get_thai_now() - timedelta(days=days)
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT net_pnl_thb, status FROM trade_history WHERE ts > %s AND side='SELL'", (start_date,))
                    rows = cur.fetchall()
                    total_pnl = sum(r[0] for r in rows); trades = len(rows)
                    wins = sum(1 for r in rows if r[1] == 'WIN'); win_rate = (wins/trades*100) if trades > 0 else 0
            msg = f"📅 <b>{title} SUMMARY</b>\n---------------------------------\n"
            msg += f"Trades: {trades} | Win Rate: {win_rate:.0f}%\n<b>Net: {total_pnl:,.2f} THB</b>"
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
                d_xrp = self.get_indicator(self.symbol)
                d_btc = self.get_indicator("BTC_THB")
                
                if not d_xrp or not d_btc: 
                    print("Data missing, retrying in 20s..."); time.sleep(20); continue
                
                ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                thb = float(wallet['result'].get('THB', 0)); coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))
                
                # รายงาน Dashboard (ส่งทุกชั่วโมง)
                if time.time() - last_dash > 3600:
                    self.send_dashboard(d_xrp, d_btc, thb, coin); last_dash = time.time()
                
                # รายงาน Daily/Monthly
                if thai_now.day != last_day and thai_now.hour == 8:
                    self.send_periodic_report(1, "DAILY"); last_day = thai_now.day
                if thai_now.day == 1 and thai_now.hour == 8 and thai_now.minute < 5:
                    self.send_periodic_report(30, "MONTHLY")

                # --- กลยุทธ์การเทรด ---
                active_count = sum(1 for s in self.slots.values() if s['active'])
                btc_is_safe = d_btc['p'] > d_btc['ema']
                
                if active_count < 2 and d_xrp['rsi14'] <= self.rsi_buy_max and d_xrp['p'] > d_xrp['ema'] and btc_is_safe:
                    buy_amt = (thb + (coin * d_xrp['p'])) * 0.45 
                    if thb >= buy_amt:
                        s_id = 1 if not self.slots[1]['active'] else 2
                        if self.execute_trade('buy', s_id, d_xrp['p'], buy_amt, d_xrp['atr']): self._load_state()
                
                for i, s in self.slots.items():
                    if s['active']:
                        e_cost = s['price'] * (1 + self.fee_rate); x_rev = d_xrp['p'] * (1 - self.fee_rate)
                        if ((x_rev - e_cost) / e_cost) * 100 >= self.target_profit or d_xrp['p'] <= s['sl']:
                            if self.execute_trade('sell', i, d_xrp['p'], s['units'], d_xrp['atr']): self.slots[i]['active'] = False
            except Exception as e: print(f"Main Loop Error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    TitanUltimate_V18_Full_Stable().run()
