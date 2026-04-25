import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanHybrid_Final:
    def __init__(self):
        # 1. การตั้งค่าตัวแปร (อิงจากโครงสร้าง V.15 ที่รันผ่าน)
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")
        self.symbol = os.getenv("SYMBOL", "THB_XRP") # รูปแบบ Bitkub มาตรฐาน

        # 2. กลยุทธ์และการเงิน (3 สล็อต)
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", "1800"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "600"))
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.atr_mult = 2.5 # ค่ามาตรฐานสำหรับการคุมความเสี่ยง
        
        self.tz = timezone(timedelta(hours=7))
        self.slots = {1: {"active": False}, 2: {"active": False}, 3: {"active": False}}
        
        self._init_db()
        self._sync_slots()
        self.notify("<b>🏛️ TITAN V.17.9 HYBRID | ONLINE</b>\n<i>ระบบพร้อมทำงาน 3 สล็อต และรายงานผลแบบมืออาชีพแล้วครับ</i>")

    def _init_db(self):
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS bot_slots_v17_9 (
                    slot_id INTEGER PRIMARY KEY, price FLOAT, units FLOAT, 
                    sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")

    def _sync_slots(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, max_pnl FROM bot_slots_v17_9")
                    for r in cur.fetchall():
                        if r[2] > 0:
                            self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3], "max_pnl": r[4]}
        except: pass

    def get_balance(self):
        ts = str(int(time.time() * 1000))
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
        try:
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=15).json()
            if res.get('error') == 0:
                thb = float(res['result'].get('THB', 0.0))
                coin_key = self.symbol.split('_')[1]
                coin = float(res['result'].get(coin_key, 0.0))
                return thb, coin
        except: pass
        return 0.0, 0.0

    def get_market_data(self):
        try:
            url = f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}"
            res = requests.get(url, timeout=15).json()
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"price": c[-1], "rsi": rsi, "atr": np.mean(tr[-14:])}
        except: return None

    def execute_order(self, side, amt, price):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        ts = str(int(time.time() * 1000))
        payload = {"sym": self.symbol.lower(), "amt": round(amt, 8), "rat": round(price, 4), "typ": "limit"}
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
        try:
            r = requests.post(f"https://api.bitkub.com{path}", 
                             headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, 
                             data=json.dumps(payload), timeout=15)
            return r.json().get('error') == 0
        except: return False

    def run(self):
        last_rep = 0
        while True:
            try:
                m = self.get_market_data()
                if not m: continue
                p, rsi, atr = m['price'], m['rsi'], m['atr']
                thb, coin = self.get_balance()

                # --- 🚀 LOGIC ซื้อ (3-Slots) ---
                active_count = sum(1 for s in self.slots.values() if s.get('active'))
                if active_count < 3 and rsi <= self.rsi_buy_max and thb >= self.budget_per_slot:
                    for s_id in [1, 2, 3]:
                        if not self.slots[s_id].get('active'):
                            if self.execute_order("buy", self.budget_per_slot, p):
                                units = (self.budget_per_slot * 0.9975) / p
                                self.slots[s_id] = {"active": True, "price": p, "units": units, "sl": p - (atr * self.atr_mult), "max_pnl": 0.0}
                                self._update_db(s_id)
                                self.notify(f"🚀 <b>TRADE ENTRY | SLOT {s_id}</b>\nPrice: {p:,.4f}\nStatus: คุมความเสี่ยงเรียบร้อย")
                            break

                # --- 📤 LOGIC ขาย (Trailing Stop) ---
                for s_id, s in self.slots.items():
                    if s.get('active'):
                        pnl = ((p - s['price']) / s['price']) * 100
                        if pnl > s['max_pnl']: s['max_pnl'] = pnl
                        if p - (atr * self.atr_mult) > s['sl']: s['sl'] = p - (atr * self.atr_mult)
                        
                        if p <= s['sl'] or (rsi >= 75 and pnl > 2.0):
                            if self.execute_order("sell", s['units'], p):
                                self.notify(f"📤 <b>TRADE EXIT | SLOT {s_id}</b>\nPrice: {p:,.4f}\nROI: {pnl:+.2f}%")
                                self.slots[s_id] = {"active": False}
                                self._update_db(s_id)

                # --- 📊 รายงานระดับมืออาชีพ (ทุก 10 นาที) ---
                if time.time() - last_rep >= 600:
                    self._send_pro_report(p, rsi, thb, coin)
                    last_rep = time.time()

            except Exception as e: print(f"Runtime Error: {e}")
            time.sleep(5) # รักษาสถานะให้ Railway ไม่ค้าง

    def _update_db(self, s_id):
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                s = self.slots[s_id]
                if s['active']:
                    cur.execute("""INSERT INTO bot_slots_v17_9 (slot_id, price, units, sl, max_pnl, updated_at)
                        VALUES (%s,%s,%s,%s,%s,NOW()) ON CONFLICT (slot_id) DO UPDATE SET 
                        price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl, max_pnl=EXCLUDED.max_pnl, updated_at=NOW()""",
                        (s_id, s['price'], s['units'], s['sl'], s['max_pnl']))
                else:
                    cur.execute("DELETE FROM bot_slots_v17_9 WHERE slot_id=%s", (s_id,))

    def _send_pro_report(self, p, rsi, thb, coin):
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = datetime.now(self.tz)
        
        msg = (f"💠 <b>TITAN V.17.9 | PORTFOLIO REPORT</b>\n━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>MARKET</b>\n• Price: {p:,.4f} | RSI: {rsi:.2f}\n"
               f"💰 <b>FINANCIALS</b>\n"
               f"• Total Equity: {equity:,.2f} ({growth:+.2f}%)\n"
               f"• Cash: 🟢 <b>{thb:,.2f} THB</b>\n"
               f"• Assets: 🔵 {coin * p:,.2f} THB\n━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>SLOTS STATUS</b>\n")
        for i in [1, 2, 3]:
            s = self.slots[i]
            if s.get('active'):
                pnl = ((p - s['price']) / s['price']) * 100
                msg += f"• SLOT {i}: 🟢 ROI {pnl:+.2f}% | SL {s['sl']:,.2f}\n"
            else: msg += f"• SLOT {i}: ⚪ WAITING\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n📅 <i>{now.strftime('%d/%m/%Y | %H:%M:%S')}</i>"
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanHybrid_Final().run()
