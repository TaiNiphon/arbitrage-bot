import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2, sys
from datetime import datetime, timedelta, timezone

class TitanV18_Final_Fixed:
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
        self.buy_distance = 1.5      
        self.cancel_timeout = 300 

        self.slots = {1: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "ts": 0}, 
                      2: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "ts": 0}}

        self._init_db() 
        self._load_state() 
        self.notify("🏛️ <b>TITAN V.18.15.8: REPAIR COMPLETE</b>\n<i>Status: ซ่อมฐานข้อมูลและกู้คืนรายงานฉบับสมบูรณ์แล้ว</i>")

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
                    # --- AUTO MIGRATION: เพิ่มคอลัมน์ที่ขาด ---
                    cur.execute("ALTER TABLE bot_state_v18 ADD COLUMN IF NOT EXISTS open_ts BIGINT DEFAULT 0")
                    conn.commit()
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        try:
            temp_slots = {1: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "ts": 0}, 
                          2: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "ts": 0}}
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, open_ts FROM bot_state_v18")
                    for r in cur.fetchall():
                        temp_slots[r[0]] = {"active": True, "price": float(r[1]), "units": float(r[2]), "sl": float(r[3]), "ts": int(r[4])}
            self.slots = temp_slots
        except Exception as e: self.notify(f"⚠️ <b>Load Error:</b> {e}")

    def notify(self, message):
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}
            requests.post(url, json=payload, timeout=15)
        except: pass

    def get_open_orders(self):
        try:
            ts = str(int(time.time() * 1000))
            query = f"sym={self.symbol.lower()}"
            sig = hmac.new(self.api_secret.encode(), (ts + "GET" + "/api/v3/market/my-open-orders" + query).encode(), hashlib.sha256).hexdigest()
            res = requests.get(f"https://api.bitkub.com/api/v3/market/my-open-orders?{query}", 
                               headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=15).json()
            return res.get('result', [])
        except: return []

    def cancel_order(self, order_id, side):
        try:
            ts = str(int(time.time() * 1000))
            payload = {"sym": self.symbol.lower(), "id": order_id, "sd": side}
            payload_json = json.dumps(payload, separators=(',', ':'))
            sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/cancel-order" + payload_json).encode(), hashlib.sha256).hexdigest()
            requests.post("https://api.bitkub.com/api/v3/market/cancel-order", 
                          headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}, 
                          data=payload_json, timeout=15)
            return True
        except: return False

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
                return {"p": float(c[-1]), "r14": float(calc_rsi(c, 14)), "ema": float(ema), "atr": float(np.mean(tr[-14:]))}
            except: time.sleep(2)
        return None

    def execute_trade(self, side, slot_id, price, amt_units, atr, market=False):
        ts = str(int(time.time() * 1000))
        path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"
        typ = "market" if market else "limit"
        final_rat = 0 if typ == "market" else round(float(price), 2)
        final_amt = int(float(amt_units)) if side == 'buy' else round(float(amt_units), 4)

        payload = {"sym": self.symbol.lower(), "amt": final_amt, "rat": final_rat, "typ": typ}
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
                            actual_units = round(float(final_amt) / float(price), 4)
                            sl_val = round(float(price) - (float(atr) * 2.5), 2)
                            cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, open_ts) VALUES (%s, %s, %s, %s, %s)
                                           ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl, open_ts=EXCLUDED.open_ts""", 
                                        (int(slot_id), float(price), float(actual_units), float(sl_val), int(time.time())))
                        else:
                            s = self.slots[slot_id]
                            sell_p = float(res['result'].get('rat', price)) if typ == "market" else float(price)
                            net_pnl = (sell_p * float(s['units']) * (1-self.fee_rate)) - (float(s['price']) * float(s['units']) * (1+self.fee_rate))
                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb, status) VALUES (NOW(), 'SELL', %s, %s, %s, %s)", (float(sell_p), float(s['units']), float(net_pnl), 'WIN' if net_pnl > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (int(slot_id),))
                        conn.commit()
                self._load_state()
                self.notify(f"⚡ <b>{'BUY' if side=='buy' else 'SELL'} SUCCESS (Slot {slot_id})</b>")
                return True
        except: pass
        return False

    def send_dashboard(self, dx, db, thb, coin):
        p = dx['p']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        orders = self.get_open_orders()

        msg = f"🏛️ <b>TITAN V.18.15.8: DASHBOARD</b>\n"
        msg += f"📅 <code>{self.get_thai_now().strftime('%d/%m/%Y | %H:%M:%S')}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {p:,.2f} THB</b>\n"
        msg += f"📉 RSI: {dx['r14']:.2f} | EMA: {dx['ema']:,.2f}\n"
        msg += f"🛡️ <b>BTC-GUARD:</b> {'🌕 BULL' if db['p'] > db['ema'] else '🌑 BEAR'}\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity: {equity:,.2f} THB\n"
        msg += f"💵 Cash: {thb:,.2f} | Growth: {growth:+.2f}%\n"
        msg += f"---------------------------------\n"
        
        for i, s in self.slots.items():
            if s['active']:
                is_pending = any(abs(float(o['rat']) - s['price']) < 0.01 for o in orders if o['sd'] == 'buy')
                if is_pending:
                    msg += f"🟡 SLOT {i}: (WAIT MATCH) @ {s['price']:,.2f}\n"
                else:
                    pnl = (((p*0.9975) - (s['price']*1.0025)) / (s['price']*1.0025)) * 100
                    msg += f"🟢 SLOT {i}: {s['units']} XRP ({pnl:+.2f}%)\n"
                    msg += f"🎯 TP: {s['price']*1.03:,.2f} | 🛡️ SL: {s['sl']:,.2f}\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING (RSI ≤ 35)\n"
        self.notify(msg)

    def run(self):
        last_dash = 0
        while True:
            try:
                dx, db = self.get_indicator(self.symbol), self.get_indicator("BTC_THB")
                if not dx or not db: time.sleep(20); continue

                ts_btk = str(int(time.time() * 1000))
                sig = hmac.new(self.api_secret.encode(), (ts_btk + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts_btk, 'X-BTK-SIGN': sig}, timeout=15).json()
                thb = float(wallet['result'].get('THB', 0))
                coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                if time.time() - last_dash > 3600:
                    self.send_dashboard(dx, db, thb, coin); last_dash = time.time()

                # --- AUTO CANCEL LOGIC ---
                orders = self.get_open_orders()
                for i, s in self.slots.items():
                    if s['active'] and s['ts'] > 0 and (time.time() - s['ts']) > self.cancel_timeout:
                        m_order = next((o for o in orders if abs(float(o['rat']) - s['price']) < 0.01), None)
                        if m_order:
                            self.cancel_order(m_order['id'], 'buy')
                            with psycopg2.connect(self.db_url) as conn:
                                with conn.cursor() as cur:
                                    cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (i,))
                                    conn.commit()
                            self._load_state()
                            self.notify(f"⏱️ <b>CANCELLED (Slot {i})</b>: ไม่ Match ใน 5 นาที")

                active_count = sum(1 for s in self.slots.values() if s['active'])
                if active_count < 2 and dx['r14'] <= self.current_rsi_buy:
                    if dx['p'] > dx['ema'] and db['p'] > db['ema']:
                        buy_amt = int((thb + (coin * dx['p'])) * 0.45) if active_count == 0 else int(thb * 0.95)
                        if thb >= buy_amt >= 10:
                            s_id = 1 if not self.slots[1]['active'] else 2
                            self.execute_trade('buy', s_id, dx['p'], buy_amt, dx['atr'])

                for i, s in self.slots.items():
                    if s['active']:
                        is_pending = any(abs(float(o['rat']) - s['price']) < 0.01 for o in orders if o['sd'] == 'buy')
                        if not is_pending:
                            profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                            if profit >= self.current_tp or dx['p'] <= s['sl']:
                                self.execute_trade('sell', i, dx['p'], s['units'], 0)
            except Exception as e: print(f"Loop Error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    TitanV18_Final_Fixed().run()
