import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_Full_Strategic:
    def __init__(self):
        # --- [1] CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] STRATEGY SETTINGS ---
        self.initial_equity = 10000.28 
        self.fee_rate = 0.0025 
        self.current_tp = 3.0       
        self.current_rsi_buy = 35.0
        self.buy_distance = 1.5      # ระยะห่างขั้นต่ำ 1.5% ระหว่างไม้

        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}

        self._init_db() 
        self._load_state() 
        self.notify("🏛️ <b>TITAN V.18.15.3: FULL ONLINE</b>\n<i>Status: ระบบทำงานสมบูรณ์ แก้ไขปัญหา DB & แบ่งไม้เรียบร้อย</i>")

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
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        try:
            temp_slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                          2: {"active": False, "price": 0, "units": 0, "sl": 0}}
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl FROM bot_state_v18")
                    for r in cur.fetchall():
                        temp_slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3]}
            self.slots = temp_slots
        except Exception as e: self.notify(f"⚠️ <b>Load State Error:</b> {e}")

    def notify(self, message):
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}
            requests.post(url, json=payload, timeout=15)
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
        final_amt = int(float(amt_units)) if side == 'buy' else round(float(amt_units), 4)

        payload = {"sym": self.symbol.lower(), "amt": final_amt, "rat": final_rat, "typ": "limit"}
        payload_json = json.dumps(payload, separators=(',', ':'))
        sig = hmac.new(self.api_secret.encode(), (ts + "POST" + path + payload_json).encode(), hashlib.sha256).hexdigest()

        try:
            res = requests.post(f"https://api.bitkub.com{path}", 
                                headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}, 
                                data=payload_json, timeout=15).json()
            
            if res.get('error') == 0:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            actual_units = round(final_amt / final_rat, 4)
                            sl = round(final_rat - (atr * 2.5), 2)
                            cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl) VALUES (%s, %s, %s, %s)
                                           ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl""", 
                                        (slot_id, final_rat, actual_units, sl))
                            msg = f"📥 <b>BUY SUCCESS (Slot {slot_id})</b>\nPrice: {final_rat:,.2f} | SL: {sl:,.2f}"
                        else:
                            s = self.slots[slot_id]
                            net_pnl = (final_rat * s['units'] * (1-self.fee_rate)) - (s['price'] * s['units'] * (1+self.fee_rate))
                            # แก้ไขชื่อคอลัมน์ 'ts' และ 'net_pnl_thb' ตามภาพ DB จริง
                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb, status) VALUES (NOW(), 'SELL', %s, %s, %s, %s)", 
                                        (final_rat, s['units'], net_pnl, 'WIN' if net_pnl > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (slot_id,))
                            msg = f"⚡ <b>SELL SUCCESS (Slot {slot_id})</b>\nNET: <b>{net_pnl:,.2f} THB</b>"
                        conn.commit()
                self._load_state()
                self.notify(msg)
                return True
            else:
                self.notify(f"❌ <b>API ERROR:</b> {res.get('error')} | {side} Slot {slot_id}")
        except Exception as e: 
            self.notify(f"⚠️ <b>DB/API ERROR:</b> {e}")
            # กรณี Error DB หลังจากขายแล้ว ให้พยายามล้างสถานะในตัวแปรเพื่อไม่ให้บอทค้างวนลูปขายซ้ำ
            if side == 'sell': self.slots[slot_id]['active'] = False
        return False

    def send_dashboard(self, dx, db, thb, coin):
        p = dx['p']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        # --- รายงานแบบดั้งเดิมที่ครบถ้วน (ตามที่คุณต้องการ) ---
        msg = f"🏛️ <b>TITAN V.18.15.3: DASHBOARD</b>\n📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : {p:,.2f} THB\n"
        msg += f"📊 State : {'↔️ SIDEWAY (Market)' if abs(dx['r14']-50) < 15 else '📉 TREND'}\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
        msg += f"📉 RSI 14: {dx['r14']:.2f} | RSI 200: {dx['r200']:.2f}\n"
        msg += f"---------------------------------\n"
        msg += f"🛡️ <b>BTC-GUARD STATUS</b>\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if db['p'] > db['ema'] else '🌑 BEARISH'}\n"
        msg += f"💰 BTC P.: {db['p']:,.0f} THB\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f} THB\n"
        msg += f"🪙 Coin Value : {(coin*p):,.2f} THB\n"
        msg += f"📦 Total Coins: {coin:.4f} {self.symbol.split('_')[0]}\n"
        msg += f"📈 Total Growth: {growth:+.2f}%\n"
        msg += f"---------------------------------\n"
        
        for i, s in self.slots.items():
            if s['active']:
                pnl = (((p*0.9975) - (s['price']*1.0025)) / (s['price']*1.0025)) * 100
                msg += f"🟢 SLOT {i}: {s['units']:.4f} XRP ({pnl:+.2f}%)\n"
                msg += f"🎯 TP: {s['price']*(1+(self.current_tp/100)):,.2f} | 🛡️ SL: {s['sl']:,.2f}\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING (RSI ≤ {self.current_rsi_buy})\n"
        self.notify(msg)

    def run(self):
        last_dash = 0
        while True:
            try:
                dx, db = self.get_indicator(self.symbol), self.get_indicator("BTC_THB")
                if not dx or not db: time.sleep(20); continue

                ts = str(int(time.time() * 1000))
                sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                     headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=15).json()
                
                thb = float(wallet['result'].get('THB', 0))
                coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                if time.time() - last_dash > 3600:
                    self.send_dashboard(dx, db, thb, coin)
                    last_dash = time.time()

                # --- BUY LOGIC (ปิดปัญหา All-in ด้วยระยะห่าง 1.5%) ---
                active_count = sum(1 for s in self.slots.values() if s['active'])
                if active_count < 2 and dx['r14'] <= self.current_rsi_buy:
                    can_buy = True
                    if active_count == 1:
                        m1_p = next(s['price'] for s in self.slots.values() if s['active'])
                        dist = ((dx['p'] - m1_p) / m1_p) * 100
                        if dist > -self.buy_distance: can_buy = False # ต้องลงมาต่ำกว่าไม้ 1 อย่างน้อย 1.5%

                    if can_buy and dx['p'] > dx['ema'] and db['p'] > db['ema']:
                        buy_amt = int((thb + (coin * dx['p'])) * 0.45)
                        if thb >= buy_amt >= 10:
                            s_id = 1 if not self.slots[1]['active'] else 2
                            self.execute_trade('buy', s_id, dx['p'], buy_amt, dx['atr'])

                # --- SELL LOGIC ---
                for i, s in self.slots.items():
                    if s['active']:
                        profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                        if profit >= self.current_tp or dx['p'] <= s['sl']:
                            self.execute_trade('sell', i, dx['p'], s['units'], dx['atr'])

            except Exception as e: print(f"Loop Error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    TitanV18_Full_Strategic().run()
