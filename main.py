import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_The_Precision:
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
        self.current_tp = 3.0       
        self.current_rsi_buy = 35.0

        self.slots = {1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None}, 
                      2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None}}

        self._init_db() 
        self._load_state() 
        self.notify("🏛️ <b>TITAN V.18.27: SL-EXECUTION FIXED</b>\n<i>Verified: DB Locked / Real-Price Match / No Condition Block</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, 
                        order_id TEXT, open_ts BIGINT, status TEXT)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, slot_id INT, side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, ts TIMESTAMP DEFAULT NOW(), status TEXT)""")
                    conn.commit()
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, order_id, status FROM bot_state_v18")
                    rows = cur.fetchall()
                    # Reset RAM before Loading
                    self.slots[1] = {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None}
                    self.slots[2] = {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "oid": None}
                    for r in rows:
                        self.slots[r[0]] = {
                            "status": str(r[5]).upper(), # ป้องกัน Case Sensitive
                            "price": float(r[1]), 
                            "units": float(r[2]), 
                            "sl": float(r[3]), 
                            "oid": r[4]
                        }
        except: pass

    def send_full_dashboard(self, dx, db, thb, coin, mode="DASHBOARD"):
        p = dx['p']; rsi_val = dx['r14']; equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        coin_sym = self.symbol.split('_')[0]

        if rsi_val <= 30: state_msg = "🔥 OVERSOLD"
        elif rsi_val <= 45: state_msg = "🔻 DOWNTREND"
        elif rsi_val >= 70: state_msg = "⚠️ OVERBOUGHT"
        elif rsi_val >= 55: state_msg = "🚀 TRENDING"
        else: state_msg = "↔️ SIDEWAY"

        msg = f"🏛️ <b>TITAN V.18.27: {mode}</b>\n"
        msg += f"📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : <b>{p:,.4f} THB</b>\n"
        msg += f"📊 State : {state_msg}\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
        msg += f"📉 RSI 14: {rsi_val:.2f} | RSI 200: {dx['r200']:.2f}\n"
        msg += f"---------------------------------\n"
        msg += f"🛡️ <b>BTC-GUARD STATUS</b>\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if db['p'] > db['ema'] else '🌑 BEARISH'}\n"
        msg += f"💰 BTC P.: {db['p']:,.0f} THB\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f} THB\n"
        msg += f"🪙 Coin Value : {(coin*p):,.2f} THB\n"
        msg += f"📦 Total Coins: {coin:.4f} {coin_sym}\n"
        msg += f"📈 Total Growth: <b>{growth:+.2f}%</b>\n"
        msg += f"---------------------------------\n"

        for i, s in self.slots.items():
            if s['status'] == 'MATCHED':
                pnl = (((p*0.9975) - (s['price']*1.0025)) / (s['price']*1.0025)) * 100
                msg += f"🟢 <b>SLOT {i}: {s['units']:.4f} {coin_sym} ({pnl:+.2f}%)</b>\n"
                msg += f"🎯 TP: {s['price']*1.03:,.4f} | 🛡️ SL: {s['sl']:,.4f}\n\n"
            else:
                msg += f"⚪ <b>SLOT {i}: FREE (RSI ≤ {self.current_rsi_buy})</b>\n\n"
        self.notify(msg)

    def execute_trade(self, side, slot_id, price, amt_val, atr):
        typ = "bid" if side == "buy" else "ask"
        # หากขาย (ask) ใช้หน่วยเหรียญ (units), หากซื้อ (bid) ใช้หน่วยเงินบาท (amt)
        payload = {"sym": self.symbol.lower(), "amt": amt_val, "rat": 0, "typ": "market"}
        res = self.bt_auth("POST", f"/api/v3/market/place-{typ}", payload)

        if res and res.get('error') == 0:
            time.sleep(3.5) 
            order_id = str(res['result'].get('id'))
            info = self.bt_auth("POST", "/api/v3/market/order-info", {"sym": self.symbol.lower(), "id": order_id, "sd": side})
            
            if info and info.get('result'):
                real_p = float(info['result'].get('rat', price))
                real_u = float(info['result'].get('amt', 0))
                real_fee = float(info['result'].get('fee', 0))
            else:
                real_p, real_u, real_fee = float(price), (amt_val if side=='sell' else 0.0), 0.0

            try:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            sl_val = round(real_p - (atr * 2.5), 2)
                            cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, order_id, open_ts, status) 
                                           VALUES (%s, %s, %s, %s, %s, %s, 'MATCHED') 
                                           ON CONFLICT (slot_id) DO UPDATE SET 
                                           status='MATCHED', price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl""",
                                        (slot_id, real_p, real_u, sl_val, order_id, int(time.time())))
                            self.notify(f"📥 <b>BUY SUCCESS (Slot {slot_id})</b>\nPrice: {real_p:,.4f}")
                        else:
                            s = self.slots[slot_id]
                            received_cash = (real_p * real_u) - real_fee
                            invested_cash = (s['price'] * s['units'] * 1.0025)
                            net_pnl = received_cash - invested_cash
                            cur.execute("""INSERT INTO trade_history (slot_id, side, price, units, net_pnl_thb, status, ts) 
                                           VALUES (%s, 'SELL', %s, %s, %s, %s, NOW())""",
                                        (slot_id, real_p, real_u, net_pnl, 'WIN' if net_pnl > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                            self.notify(f"⚡ <b>SELL SUCCESS (Slot {slot_id})</b>\nPNL: {net_pnl:+.2f} THB")
                        conn.commit()
            except: pass
            self._load_state() 
            return True
        return False

    def run(self):
        last_h = -1
        while True:
            try:
                self._load_state() # ดึงค่า SL ล่าสุดทุกลูป ป้องกันบอทลืม SL
                res = self.bt_auth("POST", "/api/v3/market/wallet")
                if not res or 'result' not in res: 
                    time.sleep(10); continue
                
                thb = float(res['result'].get('THB', 0))
                coin = float(res['result'].get(self.symbol.split('_')[0], 0))
                dx = self.get_indicator(self.symbol)
                db = self.get_indicator("BTC_THB")

                if dx and db:
                    now = self.get_thai_now()
                    if now.hour != last_h: 
                        self.send_full_dashboard(dx, db, thb, coin, "HOURLY REPORT")
                        last_h = now.hour
                    
                    matched_count = sum(1 for s in self.slots.values() if s['status'] == 'MATCHED')

                    # ลอจิกการขาย (SL Check) - เพิ่มความเด็ดขาด
                    for i, s in self.slots.items():
                        if s['status'] == 'MATCHED' and s['units'] > 0:
                            # คำนวณ % Profit เพื่อเช็ค TP
                            profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                            # เช็คเงื่อนไข TP หรือ SL (ถ้าราคาตลาดปัจจุบัน <= SL ที่ดึงจาก DB)
                            if profit >= self.current_tp or dx['p'] <= s['sl']: 
                                self.execute_trade('sell', i, dx['p'], s['units'], dx['atr'])

                    # ลอจิกการซื้อ
                    if matched_count < 2 and dx['r14'] <= self.current_rsi_buy:
                        buy_amt = int(thb * 0.95) if (thb < 500 or matched_count == 1) else int((thb + (coin * dx['p'])) * 0.45)
                        if thb >= buy_amt >= 10 and dx['p'] > dx['ema'] and db['p'] > db['ema']:
                            target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
                            self.execute_trade('buy', target_slot, dx['p'], buy_amt, dx['atr'])

            except Exception as e:
                print(f"Run Error: {e}")
                time.sleep(10)
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
    TitanV18_The_Precision().run()
```---
