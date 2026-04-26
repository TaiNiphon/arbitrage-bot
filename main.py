import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanOmniV15_Pro:
    def __init__(self):
        # --- 1. CONFIGURATION (Environment Variables) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. STRATEGY & RISK CONTROL ---
        # แก้ไขการดึงค่ายอดเงินเริ่มต้นให้รองรับทั้งตัวเลขและข้อความ
        raw_equity = os.getenv("INITIAL_EQUITY", "1800")
        self.initial_equity = float(str(raw_equity).replace(',', ''))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "1.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "30.0"))
        self.daily_drawdown_limit = 5.0
        self.prev_rsi = 0.0

        # --- 3. DUAL-SLOT MEMORY ---
        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0, "max_pnl": 0.0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0, "max_pnl": 0.0}}
        
        self._init_db_v15()
        self._sync_slots_from_db()
        self.notify("<b>🔥 TITAN V.15.0 (FIXED-SIG) | DEPLOYED</b>\n<i>System: Dual-Engine & Wallet-Sync Active</i>")

    def _init_db_v15(self):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v15 (
                slot_id INTEGER PRIMARY KEY, last_action TEXT, avg_price FLOAT, 
                total_units FLOAT, dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"⚠️ DB Error: {e}")

    def _sync_slots_from_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT slot_id, avg_price, total_units, dynamic_sl, max_pnl FROM bot_state_v15")
            for row in cur.fetchall():
                if row[2] > 0:
                    self.slots[row[0]] = {"active": True, "price": row[1], "units": row[2], "sl": row[3], "max_pnl": row[4]}
            cur.close(); conn.close()
        except: pass

    def get_balance(self):
        """แก้ไขฟังก์ชันดึงยอดเงินเพื่อแก้ปัญหา Signature Error 6"""
        try:
            path = "/api/v3/market/wallet"
            ts = str(int(time.time() * 1000))
            # ปรับวิธีการสร้าง Signature ให้ตรงตาม Bitkub V3 (เหมือน V.17.2 ที่รันผ่าน)
            sig_data = ts + "POST" + path
            sig = hmac.new(self.api_secret.encode(), sig_data.encode(), hashlib.sha256).hexdigest()
            
            headers = {
                'X-BTK-APIKEY': self.api_key,
                'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': sig,
                'Content-Type': 'application/json'
            }
            
            res = requests.post(f"https://api.bitkub.com{path}", headers=headers, timeout=10).json()
            
            if res.get('error') == 0:
                thb = float(res['result'].get('THB', 0))
                coin_name = self.symbol.split('_')[0]
                coin = float(res['result'].get(coin_name, 0))
                return thb, coin
            else:
                # พ่น Error ลง Log เพื่อตรวจสอบได้ทันที
                print(f"❌ API Wallet Error: {res}")
                return 0.0, 0.0
        except Exception as e:
            print(f"⚠️ get_balance Exception: {e}")
            return 0.0, 0.0

    def get_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            if not res or 'c' not in res: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"price": c[-1], "rsi": rsi, "atr": np.mean(tr[-14:])}
        except: return None

    def place_smart_order(self, side, amt, price):
        try:
            path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
            ts = str(int(time.time() * 1000))
            payload = {"sym": self.symbol.lower(), "amt": amt, "rat": price, "typ": "limit"}
            sig_data = ts + "POST" + path + json.dumps(payload)
            sig = hmac.new(self.api_secret.encode(), sig_data.encode(), hashlib.sha256).hexdigest()
            
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            r = requests.post(f"https://api.bitkub.com{path}", headers=headers, data=json.dumps(payload), timeout=10)
            return r.json().get('error') == 0
        except: return False

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.get_indicators()
                if not d: 
                    time.sleep(10); continue
                
                p, rsi, atr = d['price'], d['rsi'], d['atr']
                thb, coin = self.get_balance()
                equity = thb + (coin * p)
                growth = ((equity - self.initial_equity) / self.initial_equity) * 100 if self.initial_equity > 0 else 0

                # --- BUY LOGIC (DUAL-SLOT) ---
                active_slots = [i for i in self.slots if self.slots[i]["active"]]
                if len(active_slots) < 2 and rsi <= self.rsi_buy_target and growth > -self.daily_drawdown_limit:
                    s_id = 1 if 1 not in active_slots else 2
                    # แบ่งครึ่งเงินสดสำหรับแต่ละ Slot
                    buy_amt = thb if len(active_slots) == 1 else thb / 2
                    if buy_amt >= 10:
                        if self.place_smart_order("buy", buy_amt, p):
                            self.slots[s_id] = {"active": True, "price": p, "units": buy_amt/p, "sl": p - (atr * 2.5), "max_pnl": 0.0}
                            self._save_state(s_id)
                            self.notify(f"🚀 <b>ENTRY SLOT {s_id} @ {p:,.2f}</b>\nRSI: {rsi:.2f}")

                # --- SELL LOGIC (TRAILING STOP & TAKE PROFIT) ---
                for s_id, s in self.slots.items():
                    if s["active"]:
                        curr_pnl = ((p - s['price']) / s['price']) * 100
                        if curr_pnl > s['max_pnl']: s['max_pnl'] = curr_pnl
                        # ขยับ Trailing Stop ตามราคาที่ขึ้นไป
                        if p - (atr * 2.5) > s['sl']: s['sl'] = p - (atr * 2.5)
                        
                        # เงื่อนไขการขาย: ชน SL หรือ กำไรถึงเป้า 10%
                        if p <= s['sl'] or curr_pnl >= 10.0:
                            if self.place_smart_order("sell", s['units'], p):
                                self.notify(f"📤 <b>CLOSE SLOT {s_id} @ {p:,.2f}</b>\nPnL: {curr_pnl:+.2f}%")
                                self.slots[s_id] = {"active": False, "price": 0, "units": 0, "sl": 0, "max_pnl": 0.0}
                                self._save_state(s_id)

                # --- REPORTING (EVERY 10 MINS) ---
                if time.time() - last_rep >= 600:
                    self._report(p, rsi, equity, growth, thb, coin)
                    last_rep = time.time(); self.prev_rsi = rsi
            except Exception as e: 
                print(f"Error in Main Loop: {e}")
            time.sleep(10)

    def _save_state(self, s_id):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            s = self.slots[s_id]
            cur.execute("""INSERT INTO bot_state_v15 (slot_id, last_action, avg_price, total_units, dynamic_sl, max_pnl, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (slot_id) 
                        DO UPDATE SET avg_price=EXCLUDED.avg_price, total_units=EXCLUDED.total_units, 
                        dynamic_sl=EXCLUDED.dynamic_sl, max_pnl=EXCLUDED.max_pnl, updated_at=EXCLUDED.updated_at""",
                        (s_id, "buy" if s['active'] else "sell", s['price'], s['units'], s['sl'], s['max_pnl'], datetime.now()))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _report(self, p, rsi, equity, growth, thb, coin):
        now = datetime.now(timezone(timedelta(hours=7)))
        status = "HOLDING" if any(s['active'] for s in self.slots.values()) else "MONITORING"
        msg = (f"🛡️ <b>TITAN V.15.0 PRO (FIXED) | {self.symbol}</b>\nStatus : {status}\nDate : {now.strftime('%d/%m/%Y')}\nTime : {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n📊 <b>MARKET INTELLIGENCE</b>\n• Price : {p:,.2f} THB\n• RSI : {rsi:.2f} (Prev:{self.prev_rsi:.2f})\n"
               f"━━━━━━━━━━━━━━━━━━\n💰 <b>PORTFOLIO ANALYSIS</b>\n• EQUITY : <b>{equity:,.2f} THB</b>\n• GROWTH : {growth:+.2f}%\n• Cash : {thb:,.2f} | Assets: {coin:.4f}\n"
               f"━━━━━━━━━━━━━━━━━━\n🎯 <b>STRATEGY DUAL-SLOT</b>\n")
        for i, s in self.slots.items():
            if s['active']:
                pnl = ((p - s['price']) / s['price']) * 100
                msg += f"<b>[SLOT {i}]</b> - {self.symbol}\n• SL : {s['sl']:,.2f} ({pnl:+.2f}%)\n• Max PnL : {s['max_pnl']:+.2f}%\n"
            else:
                msg += f"<b>[SLOT {i}]</b> - Waiting RSI ≤ {self.rsi_buy_target}\n"
        self.notify(msg)

    def notify(self, m):
        try:
            requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                         json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanOmniV15_Pro().run()
