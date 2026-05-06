import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_15_OmniFlow:
    def __init__(self):
        # --- [1] CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] STRATEGY SETTINGS ---
        self.initial_equity = 10000.28 # ทุนเริ่มต้นจากรายงานล่าสุดของคุณ
        self.fee_rate = 0.0025 
        self.last_alive_check = -1
        self.current_tp = 3.0       
        self.current_rsi_buy = 40.0 

        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}

        self._init_db() 
        self._load_state() # ดึงไม้ค้างจากฐานข้อมูล Postgres ทันที
        self.notify("🏛️ <b>TITAN V.18.15: OMNI-FLOW (FULL)</b>\n<i>Status: System Online | Database Synced</i>")

    def notify(self, message):
        """แก้ไข Error: เพิ่มฟังก์ชัน notify เพื่อส่ง Telegram"""
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram Notify Error: {e}")

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
        """โหลดสถานะไม้ค้างจากตาราง bot_state_v18"""
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
        p, r14, r200 = dx['p'], dx['r14'], dx['r200']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        
        # --- MARKET STATE (แก้ความสับสนเรื่อง RSI 200) ---
        if p > dx['ema']:
            if 48 <= r200 <= 60: x_state = "↔️ SIDEWAY (Market)"
            elif r200 > 60: x_state = "🚀 UP TREND (Turn Up)"
            else: x_state = "⚠️ RECOVERING"
            x_trend = "🌕 BULLISH"
        else:
            x_state = "📉 DOWN TREND"; x_trend = "🌑 BEARISH"

        b_trend = "🌕 BULLISH" if db['p'] > db['ema'] else "🌑 BEARISH"

        msg = f"🏛️ <b>TITAN V.18.15: DASHBOARD</b>\n📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : {p:,.2f} THB\n📊 State : {x_state}\n📈 Trend : {x_trend}\n📉 RSI 14: {r14:.2f} | RSI 200: {r200:.2f}\n"
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

    def run(self):
        last_dash = 0
        while True:
            try:
                thai_now = self.get_thai_now()
                dx, db = self.get_indicator(self.symbol), self.get_indicator("BTC_THB")
                if not dx or not db: time.sleep(20); continue

                # --- OMNI-FLOW DYNAMIC ADJUSTMENT ---
                if dx['r200'] >= 48: 
                    self.current_tp = 3.0 if dx['r200'] < 60 else 10.0
                    self.current_rsi_buy = 40.0 if dx['r200'] < 60 else 35.0
                else: 
                    self.current_tp = 2.0; self.current_rsi_buy = 25.0

                ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                thb = float(wallet['result'].get('THB', 0)); coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                if thai_now.hour in [0, 6, 12, 18] and thai_now.hour != self.last_alive_check:
                    self.notify(f"📡 <b>TITAN ALIVE</b>\n📅 {thai_now.strftime('%H:%M')} | Status: OK ✅"); self.last_alive_check = thai_now.hour
                
                # ส่ง Dashboard ทุกชั่วโมง หรือตอนเริ่มรันครั้งแรก
                if time.time() - last_dash > 3600:
                    self.send_dashboard(dx, db, thb, coin); last_dash = time.time()
                
                # Buy Logic (45% per slot)
                if sum(1 for s in self.slots.values() if s['active']) < 2 and dx['r14'] <= self.current_rsi_buy:
                    if dx['p'] > dx['ema'] and db['p'] > db['ema']:
                        buy_amt = int((thb + (coin * dx['p'])) * 0.45) 
                        if thb >= buy_amt >= 10:
                            s_id = 1 if not self.slots[1]['active'] else 2
                            self.execute_trade('buy', s_id, dx['p'], buy_amt, dx['atr'])

                # Sell Logic (TP/SL)
                for i, s in self.slots.items():
                    if s['active']:
                        e_cost = s['price'] * (1 + self.fee_rate); x_rev = dx['p'] * (1 - self.fee_rate)
                        if ((x_rev - e_cost) / e_cost) * 100 >= self.current_tp or dx['p'] <= s['sl']:
                            self.execute_trade('sell', i, dx['p'], s['units'], dx['atr'])
            except Exception as e:
                print(f"Loop Error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    TitanV18_15_OmniFlow().run()
