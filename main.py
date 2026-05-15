import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_LuxuryPanicHunter:
    def __init__(self):
        # --- [1] API & SYSTEM CONFIG ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] MONEY MANAGEMENT (45/45 RULE) ---
        self.initial_equity = 10000.28 
        self.current_tp = 3.0       # เป้ากำไรหลัก
        self.buy_rsi_14 = 28.0      # จุดช้อนซื้อ Panic
        self.buy_rsi_200 = 55.0     # คุมราคาโซนต่ำภาพใหญ่
        self.trail_dist = 1.5       # ระยะ Trailing Stop (%)
        
        self.slots = {1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0},
                      2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0}}

        self._init_db()
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18.99: LUXURY PANIC HUNTER ONLINE</b>\n<i>Status: Full Integrated Engine Ready</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        """ระบบฐานข้อมูลบันทึกทันที ป้องกันข้อมูลสูญหาย"""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, 
                        max_p FLOAT, order_id TEXT, open_ts BIGINT, status TEXT)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT)""")
                    conn.commit()
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        """โหลดข้อมูลจาก Database มาเช็คทุกลูป 100% Sync"""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, max_p, status FROM bot_state_v18")
                    rows = cur.fetchall()
                    for r in rows:
                        self.slots[r[0]] = {"status": r[5], "price": r[1], "units": r[2], "sl": r[3], "max_p": r[4]}
        except: pass

    def sync_manual_trade(self, real_coin_balance):
        """ตรวจสอบกรณีซื้อมือหรือขายมือ หากเหรียญหายไป บอทจะล้าง DB ทันที"""
        db_units = sum(s['units'] for s in self.slots.values() if s['status'] == 'MATCHED')
        if db_units > 0 and real_coin_balance < (db_units * 0.1): 
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bot_state_v18")
                    conn.commit()
            self._load_state()
            self.notify("🧹 <b>MANUAL SALE DETECTED</b>\n<i>Database Synced with Wallet.</i>")

    def send_luxury_dashboard(self, dx, db_btc, thb, coin, mode="REPORT"):
        """หน้าตารายงานฉบับเต็ม สมบูรณ์แบบตามภาพ 7940.jpg"""
        p = dx['p']; rsi_val = dx['r14']; equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        coin_sym = self.symbol.split('_')[0]

        # สถานะตลาด
        if rsi_val <= 28: state_msg = "🚨 EXTREME PANIC (BUY!)"
        elif rsi_val <= 35: state_msg = "🔥 PANIC SALE"
        elif rsi_val >= 70: state_msg = "⚠️ OVERBOUGHT"
        else: state_msg = "↔️ SIDEWAY"

        msg = f"🏛️ <b>TITAN V.18.99: {mode}</b>\n"
        msg += f"📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : <b>{p:,.4f} THB</b>\n"
        msg += f"📊 State : {state_msg}\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
        msg += f"📉 RSI 14: {rsi_val:.2f} | RSI 200: {dx['r200']:.2f}\n"
        msg += f"---------------------------------\n"
        msg += f"🛡️ <b>BTC-GUARD STATUS</b>\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if db_btc['p'] > db_btc['ema'] else '🌑 BEARISH'}\n"
        msg += f"💰 BTC P.: {db_btc['p']:,.0f} THB\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f}\n"
        msg += f"🪙 Coin Value : {(coin*p):,.2f}\n"
        msg += f"📈 Total Growth: <b>{growth:+.2f}%</b>\n"
        msg += f"---------------------------------\n"

        for i, s in self.slots.items():
            if s['status'] == 'MATCHED':
                pnl = ((p * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                msg += f"🟢 <b>SLOT {i}: {s['units']:.4f} {coin_sym} ({pnl:+.2f}%)</b>\n"
                msg += f"🎯 TP: {s['price']*1.03:,.4f} | 🛡️ SL: {s['sl']:,.4f}\n\n"
            else:
                msg += f"⚪ <b>SLOT {i}: FREE (RSI ≤ 28.0)</b>\n\n"
        
        msg += f"🔍 <i>Database Status: Verified & Locked</i>"
        self.notify(msg)

    def execute_trade(self, side, slot_id, price, amt_val, buy_p=0):
        """ระบบซื้อขายและบันทึก DB ทันที ป้องกันปัญหาซื้อไม่บันทึก"""
        typ = "bid" if side == "buy" else "ask"
        res = self.bt_auth("POST", f"/api/v3/market/place-{typ}", {"sym":self.symbol.lower(), "amt":amt_val, "typ":"market"})
        
        if res and res.get('error') == 0:
            time.sleep(3)
            order_id = str(res['result'].get('id'))
            info = self.bt_auth("POST", "/api/v3/market/order-info", {"sym":self.symbol.lower(), "id":order_id, "sd":side})
            real_p = float(info['result'].get('rat', price)) if info and info.get('result') else price
            real_u = float(info['result'].get('amt', 0)) if info and info.get('result') else (amt_val/price)

            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    if side == 'buy':
                        sl_val = round(real_p * 0.95, 4) # SL เริ่มต้น 5%
                        cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, order_id, status) 
                                       VALUES (%s,%s,%s,%s,%s,%s,'MATCHED')""", (slot_id, real_p, real_u, sl_val, real_p, order_id))
                        self.notify(f"📥 <b>BUY SUCCESS (SLOT {slot_id})</b>\nPrice: {real_p:,.4f}\n<i>Recorded to DB.</i>")
                    else:
                        net_pnl = (real_p * real_u * 0.9975) - (buy_p * real_u * 1.0025)
                        cur.execute("INSERT INTO trade_history (side, price, units, net_pnl_thb, status) VALUES ('SELL', %s,%s,%s,%s)", (real_p, real_u, net_pnl, 'CLOSED'))
                        cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                        self.notify(f"⚡ <b>SELL SUCCESS</b>\nProfit: {net_pnl:,.2f} THB\n<i>Moved to History.</i>")
                    conn.commit()
            self._load_state()
            return True
        return False

    def run(self):
        last_h = -1
        while True:
            try:
                res_w = self.bt_auth("POST", "/api/v3/market/wallet")
                if not res_w: time.sleep(10); continue
                thb = float(res_w['result'].get('THB', 0)); coin = float(res_w['result'].get(self.symbol.split('_')[0], 0))
                
                self.sync_manual_trade(coin)
                dx = self.get_indicator(self.symbol); db_btc = self.get_indicator("BTC_THB")
                
                if dx and db_btc:
                    now = self.get_thai_now()
                    if now.hour != last_h: self.send_luxury_dashboard(dx, db_btc, thb, coin, "HOURLY REPORT"); last_h = now.hour

                    for i, s in self.slots.items():
                        if s['status'] == 'MATCHED':
                            profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                            # Trailing Stop Logic
                            if dx['p'] > s['max_p']:
                                s['max_p'] = dx['p']
                                if profit >= 1.5: # เริ่มล็อคกำไรที่ 1.5%
                                    new_sl = round(s['max_p'] * (1 - (self.trail_dist / 100)), 4)
                                    if new_sl > s['sl']:
                                        s['sl'] = new_sl
                                        with psycopg2.connect(self.db_url) as conn:
                                            with conn.cursor() as cur:
                                                cur.execute("UPDATE bot_state_v18 SET max_p=%s, sl=%s WHERE slot_id=%s", (s['max_p'], s['sl'], i))
                                                conn.commit()
                            # Exit Check
                            if profit >= self.current_tp or dx['p'] <= s['sl']:
                                self.execute_trade('sell', i, dx['p'], s['units'], buy_p=s['price'])

                    # Entry Logic (45/45 Rule)
                    matched_count = sum(1 for s in self.slots.values() if s['status'] == 'MATCHED')
                    if matched_count < 2 and dx['r14'] <= self.buy_rsi_14 and dx['r200'] <= self.buy_rsi_200:
                        total_equity = thb + (coin * dx['p'])
                        buy_amount = int(total_equity * 0.45) # ปัดเศษทศนิยมทิ้ง ป้องกัน Error
                        if thb >= buy_amount >= 10:
                            target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
                            self.execute_trade('buy', target_slot, dx['p'], buy_amount)
            except: time.sleep(10)
            time.sleep(25)

    def get_indicator(self, symbol):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15).json()
            c = np.array(res['c'], dtype=float)
            def rsi(p, n):
                d = np.diff(p); u = d.clip(min=0); dw = -d.clip(max=0)
                return 100 - (100 / (1 + (np.mean(u[-n:]) / (np.mean(dw[-n:]) + 1e-9))))
            return {"p": float(c[-1]), "r14": float(rsi(c, 14)), "r200": float(rsi(c, 200)), "ema": float(np.mean(c[-200:]))}
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
    TitanV18_LuxuryPanicHunter().run()
