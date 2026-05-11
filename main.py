import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2, sys
from datetime import datetime, timedelta, timezone

class TitanV18_Sync_Pro:
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
        self.order_timeout = 300 # 5 นาที

        self.slots = {1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None, "ts": 0}, 
                      2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None, "ts": 0}}

        self._init_db() 
        self._load_state() 
        self.notify("🏛️ <b>TITAN V.18.20: MASTER SYNC</b>\n<i>ระบบรายงานความสมบูรณ์และ Logic 45/95% พร้อมทำงานแล้ว</i>")

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
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, 
                        order_id TEXT, open_ts BIGINT, status TEXT)""")
                    conn.commit()
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, order_id, open_ts, status FROM bot_state_v18")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {
                            "status": r[6], "price": float(r[1]), "units": float(r[2]), 
                            "sl": float(r[3]), "oid": r[4], "ts": int(r[5])
                        }
        except Exception as e: self.notify(f"⚠️ <b>Load State Error:</b> {e}")

    def notify(self, message):
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}
            requests.post(url, json=payload, timeout=15)
        except: pass

    # --- [3] BITKUB API HELPERS ---
    def bt_request(self, method, path, payload=None):
        ts = str(int(time.time() * 1000))
        payload_json = json.dumps(payload, separators=(',', ':')) if payload else ""
        sig_msg = ts + method + path + payload_json
        sig = hmac.new(self.api_secret.encode(), sig_msg.encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        url = f"https://api.bitkub.com{path}"
        try:
            if method == "POST": return requests.post(url, headers=headers, data=payload_json, timeout=15).json()
            return requests.get(url, headers=headers, timeout=15).json()
        except: return None

    def get_wallet(self):
        res = self.bt_request("POST", "/api/v3/market/wallet")
        if res and 'result' in res:
            thb = float(res['result'].get('THB', 0))
            coin = float(res['result'].get(self.symbol.split('_')[0], 0))
            return thb, coin
        return 0.0, 0.0

    def get_order_info(self, oid):
        res = self.bt_request("POST", "/api/v3/market/order-info", {"sym": self.symbol.lower(), "id": oid, "sd": "buy"})
        return res['result'] if res and res.get('error') == 0 else None

    def cancel_order(self, oid):
        self.bt_request("POST", "/api/v3/market/cancel-order", {"sym": self.symbol.lower(), "id": oid, "sd": "buy"})

    # --- [4] CORE LOGIC ---
    def check_and_sync_orders(self):
        for i, s in self.slots.items():
            if s['status'] == 'WAITING' and s['oid']:
                if (int(time.time()) - s['ts']) > self.order_timeout:
                    self.cancel_order(s['oid'])
                    self.clear_slot(i)
                    self.notify(f"⏳ <b>TIMEOUT (Slot {i})</b>\nยกเลิกออเดอร์ค้าง 5 นาทีแล้ว")
                    continue
                
                info = self.get_order_info(s['oid'])
                if info and info.get('status') == 'filled':
                    self.update_status(i, 'MATCHED', float(info.get('amt', s['units'])))

    def update_status(self, slot_id, status, units):
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE bot_state_v18 SET status=%s, units=%s WHERE slot_id=%s", (status, units, slot_id))
                conn.commit()
        self._load_state()

    def clear_slot(self, slot_id):
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                conn.commit()
        self.slots[slot_id] = {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None, "ts": 0}

    def execute_trade(self, side, slot_id, price, amt_thb, atr):
        path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"
        # ใช้ Market Price เพื่อความรวดเร็วและราคาแมตช์จริง
        payload = {"sym": self.symbol.lower(), "amt": int(amt_thb), "rat": 0, "typ": "market"}
        res = self.bt_request("POST", path, payload)
        
        if res and res.get('error') == 0:
            result = res['result']
            real_p = float(result.get('rat', price))
            real_u = float(result.get('amt', amt_thb/price))
            sl_val = round(real_p - (atr * 2.5), 2)
            
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    if side == 'buy':
                        cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, order_id, open_ts, status)
                                       VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE 
                                       SET price=EXCLUDED.price, units=EXCLUDED.units, status='MATCHED'""",
                                    (slot_id, real_p, real_u, sl_val, str(result['id']), int(time.time()), 'MATCHED'))
                        self.notify(f"📥 <b>BUY SUCCESS (Slot {slot_id})</b>\nPrice: {real_p:,.2f}")
                    else:
                        s = self.slots[slot_id]
                        net_pnl = (real_p * real_u * 0.9975) - (s['price'] * s['units'] * 1.0025)
                        cur.execute("INSERT INTO trade_history (side, price, units, net_pnl_thb, status) VALUES ('SELL', %s, %s, %s, %s)",
                                    (real_p, real_u, net_pnl, 'WIN' if net_pnl > 0 else 'LOSS'))
                        cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                        self.notify(f"⚡ <b>SELL SUCCESS (Slot {slot_id})</b>\nPNL: {net_pnl:,.2f} THB")
                    conn.commit()
            self._load_state()
            return True
        return False

    # --- [5] DASHBOARD & INDICATORS ---
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
                return {"p": float(c[-1]), "r14": float(calc_rsi(c, 14)), "r200": float(calc_rsi(c, 200)), "ema": float(ema), "atr": float(np.mean(tr[-14:]))}
            except: time.sleep(2)
        return None

    def send_dashboard(self, dx, db, thb, coin):
        p = dx['p']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        msg = f"🏛️ <b>TITAN V.18.20: DASHBOARD</b>\n📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : <b>{p:,.2f} THB</b>\n"
        msg += f"📊 State : {'↔️ SIDEWAY' if abs(dx['r14']-50) < 15 else '📉 TREND'}\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
        msg += f"📉 RSI 14: {dx['r14']:.2f} | RSI 200: {dx['r200']:.2f}\n"
        msg += f"---------------------------------\n"
        msg += f"🛡️ <b>BTC-GUARD: {'🌕 BULL' if db['p'] > db['ema'] else '🌑 BEAR'}</b>\n"
        msg += f"💰 BTC P.: {db['p']:,.0f} THB\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f} THB\n"
        msg += f"🪙 Coin Value : {(coin*p):,.2f} THB\n"
        msg += f"📈 Total Growth: <b>{growth:+.2f}%</b>\n"
        msg += f"---------------------------------\n"
        
        for i, s in self.slots.items():
            if s['status'] == 'MATCHED':
                pnl = (((p*0.9975) - (s['price']*1.0025)) / (s['price']*1.0025)) * 100
                msg += f"🟢 SLOT {i}: {s['units']:.4f} XRP ({pnl:+.2f}%)\n"
                msg += f"🎯 TP: {s['price']*(1+(self.current_tp/100)):,.2f} | 🛡️ SL: {s['sl']:,.2f}\n"
            elif s['status'] == 'WAITING':
                elapsed = int((time.time() - s['ts']) / 60)
                msg += f"🟡 SLOT {i}: WAITING (ค้าง {elapsed} นาที)\n"
                msg += f"🎯 Bid Price: {s['price']:,.2f}\n"
            else:
                msg += f"⚪ SLOT {i}: FREE (RSI ≤ {self.current_rsi_buy})\n"
        self.notify(msg)

    def run(self):
        last_dash = 0
        while True:
            try:
                self.check_and_sync_orders()
                thb, coin = self.get_wallet()
                dx, db = self.get_indicator(self.symbol), self.get_indicator("BTC_THB")
                
                if not dx or not db: time.sleep(10); continue

                if time.time() - last_dash > 3600:
                    self.send_dashboard(dx, db, thb, coin)
                    last_dash = time.time()

                matched_count = sum(1 for s in self.slots.values() if s['status'] == 'MATCHED')
                waiting_count = sum(1 for s in self.slots.values() if s['status'] == 'WAITING')
                total_active = matched_count + waiting_count

                if total_active < 2 and dx['r14'] <= self.current_rsi_buy:
                    # Logic 45% / 95% + Micro Capital < 500
                    if thb < 500 or total_active == 1:
                        buy_amt = int(thb * 0.95)
                    else:
                        buy_amt = int((thb + (coin * dx['p'])) * 0.45)

                    if thb >= buy_amt >= 10 and dx['p'] > dx['ema'] and db['p'] > db['ema']:
                        s_id = 1 if self.slots[1]['status'] == 'FREE' else 2
                        self.execute_trade('buy', s_id, dx['p'], buy_amt, dx['atr'])

                for i, s in self.slots.items():
                    if s['status'] == 'MATCHED':
                        profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                        if profit >= self.current_tp or dx['p'] <= s['sl']:
                            self.execute_trade('sell', i, dx['p'], s['units'], dx['atr'])

            except Exception as e: print(f"Loop Error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    TitanV18_Sync_Pro().run()
