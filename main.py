import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_Absolute:
    def __init__(self):
        # --- [1] CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        self.initial_equity = 10000.28  # ทุนตั้งต้นสำหรับการคำนวณ Growth
        self.current_rsi_buy = 35.0
        self.tp_threshold = 1.5   
        self.trail_distance = 1.5 

        self.slots = {1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None, "max_p": 0.0}, 
                      2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None, "max_p": 0.0}}

        self._init_db() 
        self._load_state() 
        self.notify("🏛️ <b>TITAN V.18.55: ABSOLUTE ONLINE</b>\n<i>Status: Monitoring all lines with 100% Precision</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, 
                        order_id TEXT, open_ts BIGINT, status TEXT, max_p FLOAT)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, slot_id INT, side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, ts TIMESTAMP DEFAULT NOW(), status TEXT)""")
                    conn.commit()
        except: pass

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, order_id, status, max_p FROM bot_state_v18")
                    rows = cur.fetchall()
                    for i in [1, 2]: self.slots[i] = {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None, "max_p": 0.0}
                    for r in rows:
                        self.slots[r[0]] = {
                            "status": str(r[5]).upper(), "price": float(r[1]), "units": float(r[2]), 
                            "sl": float(r[3]), "oid": r[4], "max_p": float(r[6] if r[6] else r[1])
                        }
        except: pass

    def send_full_dashboard(self, dx, db, thb, coin, mode="HOURLY REPORT"):
        """เช็คแล้ว: บรรทัดครบถ้วนตามภาพ 7881.jpg และ 7885.jpg"""
        p = dx['p']; rsi_val = dx['r14']; rsi_200 = dx['r200']; equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        coin_sym = self.symbol.split('_')[0]

        if rsi_val <= 30: state_msg = "🔥 OVERSOLD"
        elif rsi_val >= 55: state_msg = "🚀 TRENDING"
        else: state_msg = "↔️ SIDEWAY"

        msg = f"🏛️ <b>TITAN V.18.55: {mode}</b>\n"
        msg += f"📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : <b>{p:,.4f} THB</b>\n"
        msg += f"📊 State : {state_msg}\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
        msg += f"📉 RSI 14: {rsi_val:.2f} | RSI 200: {rsi_200:.2f}\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f}\n"
        msg += f"🪙 Coin Value : {(coin*p):,.2f}\n"
        msg += f"📦 Total Coins: {coin:.4f} {coin_sym}\n"
        msg += f"📈 Total Growth: <b>{growth:+.2f}%</b>\n"
        msg += f"---------------------------------\n"

        for i in [1, 2]:
            s = self.slots[i]
            if s['status'] == 'MATCHED':
                pnl = (((p*0.9975) - (s['price']*1.0025)) / (s['price']*1.0025)) * 100
                msg += f"🟢 <b>SLOT {i}: {pnl:+.2f}%</b>\n"
                msg += f"🎯 T-SL: {s['sl']:,.4f} | 🔝 Max: {s['max_p']:,.4f}\n\n"
            else:
                msg += f"⚪ <b>SLOT {i}: FREE (RSI ≤ {self.current_rsi_buy})</b>\n\n"
        
        self.notify(msg)

    def execute_trade(self, side, slot_id, price, amt_val, atr, buy_p=0):
        typ = "bid" if side == "buy" else "ask"
        payload = {"sym": self.symbol.lower(), "amt": amt_val, "rat": 0, "typ": "market"}
        res = self.bt_auth("POST", f"/api/v3/market/place-{typ}", payload)
        
        if res and res.get('error') == 0:
            time.sleep(5) 
            order_id = str(res['result'].get('id'))
            info = self.bt_auth("POST", "/api/v3/market/order-info", {"sym": self.symbol.lower(), "id": order_id, "sd": side})
            
            real_p = float(info['result'].get('rat', price)) if info and info.get('result') else price
            real_u = float(info['result'].get('amt', 0)) if info and info.get('result') else (amt_val / price if side == 'buy' else amt_val)
            
            try:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            sl_val = round(real_p - (atr * 2.5), 2)
                            cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, order_id, open_ts, status) 
                                VALUES (%s, %s, %s, %s, %s, %s, %s, 'MATCHED') 
                                ON CONFLICT (slot_id) DO UPDATE SET status='MATCHED', price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl, max_p=EXCLUDED.max_p""", 
                                (slot_id, real_p, real_u, sl_val, real_p, order_id, int(time.time())))
                            self.notify(f"🟢 <b>[BUY SUCCESS]</b>\nSlot: {slot_id}\nPrice: {real_p:,.4f}\nUnits: {real_u:,.4f}")
                        else:
                            net_pnl = (real_p * real_u * 0.9975) - (buy_p * real_u * 1.0025)
                            cur.execute("INSERT INTO trade_history (slot_id, side, price, units, net_pnl_thb, status) VALUES (%s, %s, %s, %s, %s, 'CLOSED')", 
                                (slot_id, 'SELL', real_p, real_u, net_pnl))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                            self.notify(f"🔴 <b>[SELL SUCCESS]</b>\nSlot: {slot_id}\nPNL: {net_pnl:+.2f} THB")
                        conn.commit()
            except: pass
            self._load_state() 
            return True
        return False

    def run(self):
        last_h = -1
        while True:
            try:
                self._load_state() 
                res = self.bt_auth("POST", "/api/v3/market/wallet")
                if res and 'result' in res:
                    thb = float(res['result'].get('THB', 0))
                    coin_sym = self.symbol.split('_')[0]
                    coin = float(res['result'].get(coin_sym, 0))
                    
                    # ตรวจสอบการซื้อมือ/ขายมือ (Manual Sync)
                    if coin < 0.0001 and any(s['status'] == 'MATCHED' for s in self.slots.values()):
                        with psycopg2.connect(self.db_url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("DELETE FROM bot_state_v18")
                                conn.commit()
                        self.notify("🧹 <b>MANUAL SELL SYNC</b>\nตรวจพบการขายด้วยมือ บอทล้างข้อมูลสล็อตแล้ว")
                        self._load_state()

                    dx = self.get_indicator(self.symbol)
                    db = self.get_indicator("BTC_THB")
                    if dx and db:
                        now = self.get_thai_now()
                        if now.hour != last_h: 
                            self.send_full_dashboard(dx, db, thb, coin, "HOURLY REPORT")
                            last_h = now.hour
                        
                        for i, s in self.slots.items():
                            if s['status'] == 'MATCHED' and s['units'] > 0:
                                if dx['p'] > s['max_p']:
                                    s['max_p'] = dx['p']
                                    with psycopg2.connect(self.db_url) as conn:
                                        with conn.cursor() as cur:
                                            cur.execute("UPDATE bot_state_v18 SET max_p = %s WHERE slot_id = %s", (dx['p'], i))
                                            conn.commit()

                                current_pnl = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                                if current_pnl >= self.tp_threshold:
                                    dynamic_sl = round(s['max_p'] * (1 - (self.trail_distance / 100)), 2)
                                    if dynamic_sl > s['sl']:
                                        s['sl'] = dynamic_sl
                                        with psycopg2.connect(self.db_url) as conn:
                                            with conn.cursor() as cur:
                                                cur.execute("UPDATE bot_state_v18 SET sl = %s WHERE slot_id = %s", (dynamic_sl, i))
                                                conn.commit()

                                if dx['p'] <= s['sl']: 
                                    self.execute_trade('sell', i, dx['p'], s['units'], dx['atr'], buy_p=s['price'])
                        
                        matched_count = sum(1 for s in self.slots.values() if s['status'] == 'MATCHED')
                        if matched_count < 2 and dx['r14'] <= self.current_rsi_buy:
                            total_asset = thb + (coin * dx['p'])
                            buy_amt = int(total_asset * 0.48)
                            if thb >= buy_amt >= 10 and dx['p'] > dx['ema'] and db['p'] > db['ema']:
                                target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
                                self.execute_trade('buy', target_slot, dx['p'], buy_amt, dx['atr'])
            except Exception as e: print(f"Error: {e}"); time.sleep(10)
            time.sleep(25)

    def get_indicator(self, symbol):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            def rsi(p, n):
                d = np.diff(p); u = d.clip(min=0); dw = -d.clip(max=0)
                return 100 - (100 / (1 + (np.mean(u[-n:]) / (np.mean(dw[-n:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"p": float(c[-1]), "r14": float(rsi(c, 14)), "r200": float(rsi(c, 200)), "ema": float(np.mean(c[-200:])), "atr": float(np.mean(tr[-14:]))}
        except: return None

    def bt_auth(self, method, path, payload=None):
        ts = str(int(time.time() * 1000))
        payload_json = json.dumps(payload, separators=(',', ':')) if payload else ""
        sig = hmac.new(self.api_secret.encode(), (ts + method + path + payload_json).encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        try: return requests.request(method, f"https://api.bitkub.com{path}", headers=headers, data=payload_json, timeout=15).json()
        except: return None

    def notify(self, message):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanV18_Absolute().run()
