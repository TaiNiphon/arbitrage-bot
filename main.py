import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class Titan_V17_8_Pro_Final:
    def __init__(self):
        # 1. Configuration (จาก Railway)
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # 2. Strategy Settings
        self.symbol = os.getenv("SYMBOL", "THB_XRP")
        self.btc_ref = "THB_BTC"
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", "1800"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "600"))
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.atr_mult = float(os.getenv("RISK_PER_TRADE", "2.5"))

        # 3. Timezone Setup (Thailand)
        self.tz = timezone(timedelta(hours=7))

        # 4. Memory & DB Initialization
        self.slots = {1: {"active": False}, 2: {"active": False}, 3: {"active": False}}
        self._init_db()
        self._sync_state()
        self.notify("<b>🏛️ TITAN V.17.8 PRO | FINAL PILLAR</b>\n<i>Status: Online & Fully Synchronized</i>")

    def _init_db(self):
        """เตรียมฐานข้อมูลสำหรับเก็บสถานะและประวัติเทรด"""
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS bot_slots_v17 (
                    slot_id INTEGER PRIMARY KEY, avg_price FLOAT, units FLOAT, 
                    sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS trade_history_final (
                    id SERIAL PRIMARY KEY, ts TIMESTAMP, side TEXT, 
                    price FLOAT, pnl_thb FLOAT, pnl_pct FLOAT)""")

    def _sync_state(self):
        """ดึงสถานะสล็อตจากฐานข้อมูลกรณีบอทรีสตาร์ท"""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, avg_price, units, sl, max_pnl FROM bot_slots_v17")
                    for r in cur.fetchall():
                        if r[2] > 0:
                            self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3], "max_pnl": r[4]}
        except: pass

    def get_balance(self):
        """ดึงยอดเงินสดและเหรียญ (แก้ไขให้ไม่ค้างกรณีเป็น 0)"""
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers=headers, timeout=15).json()
            
            thb = 0.0
            coin = 0.0
            
            if res.get('error') == 0:
                # ป้องกัน Error กรณีดึงค่าจากดิกชันนารีแล้วไม่เจอ key
                thb = float(res['result'].get('THB', 0.0))
                coin_key = self.symbol.split('_')[1]
                coin = float(res['result'].get(coin_key, 0.0))
                
            return thb, coin
        except Exception as e:
            print(f"Balance Error: {e}")
            return 0.0, 0.0

    def get_market_data(self, sym):
        """วิเคราะห์ RSI, ATR และ Trend"""
        try:
            url = f"https://api.bitkub.com/tradingview/history?symbol={sym}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}"
            res = requests.get(url, timeout=15).json()
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); g = np.where(diff > 0, diff, 0); lo = np.where(diff < 0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(lo[-14:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            trend = 1 if c[-1] > np.mean(c[-20:]) else 0
            return {'price': c[-1], 'rsi': rsi, 'atr': np.mean(tr[-14:]), 'trend': trend}
        except: return None

    def execute_trade(self, side, amt, price):
        """ส่งคำสั่งซื้อขายไปยัง Bitkub"""
        try:
            path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
            ts = str(int(time.time() * 1000))
            # ปรับทศนิยม XRP (ราคา 4 ตำแหน่ง, จำนวน 8 ตำแหน่ง)
            payload = {"sym": self.symbol.lower(), "amt": round(amt, 8), "rat": round(price, 4), "typ": "limit"}
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            res = requests.post(f"https://api.bitkub.com{path}", headers=headers, data=json.dumps(payload), timeout=15).json()
            return res.get('error') == 0
        except: return False

    def run(self):
        last_rep = 0
        while True:
            try:
                btc = self.get_market_data(self.btc_ref)
                xrp = self.get_market_data(self.symbol)
                if not btc or not xrp: 
                    time.sleep(10)
                    continue

                p, rsi, atr = xrp['price'], xrp['rsi'], xrp['atr']
                btc_safe = btc['trend'] == 1 and btc['rsi'] > 40

                # --- SELL LOGIC (Trailing Stop) ---
                for s_id, s in self.slots.items():
                    if s.get('active'):
                        pnl = ((p - s['price']) / s['price']) * 100
                        if pnl > s['max_pnl']: s['max_pnl'] = pnl

                        new_sl = p - (atr * self.atr_mult)
                        if new_sl > s['sl']: s['sl'] = new_sl

                        if p <= s['sl'] or (rsi >= 75 and pnl > 2.0):
                            if self.execute_trade("sell", s['units'], p):
                                pnl_thb = (p - s['price']) * s['units']
                                self._update_db(s_id, "sell", p, pnl_thb, pnl)
                                self.notify(f"📤 <b>TRADE EXIT: SLOT {s_id}</b>\nPrice: {p:,.4f} | ROI: {pnl:+.2f}%")
                                self.slots[s_id] = {"active": False}

                # --- BUY LOGIC (3-Slots) ---
                active_count = sum(1 for s in self.slots.values() if s.get('active'))
                if active_count < 3 and rsi <= self.rsi_buy_max and btc_safe:
                    for s_id in [1, 2, 3]:
                        if not self.slots[s_id].get('active'):
                            if self.execute_trade("buy", self.budget_per_slot, p):
                                # หักค่าธรรมเนียม Bitkub 0.25%
                                units = (self.budget_per_slot * 0.9975) / p
                                sl = p - (atr * self.atr_mult)
                                self.slots[s_id] = {"active": True, "price": p, "units": units, "sl": sl, "max_pnl": 0.0}
                                self._update_db(s_id, "buy", p)
                                self.notify(f"🚀 <b>TRADE ENTRY: SLOT {s_id}</b>\nPrice: {p:,.4f}\nBTC Filter: 🟢 Safe")
                            break

                # --- REPORTING (ทุก 10 นาที) ---
                if time.time() - last_rep >= 600:
                    self._send_pro_report(p, rsi, btc_safe)
                    last_rep = time.time()

                time.sleep(20) 
            except Exception as e:
                print(f"Loop Error: {e}")
                time.sleep(15)

    def _update_db(self, s_id, side, price, pnl_thb=0, pnl_pct=0):
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                now_th = datetime.now(self.tz)
                if side == "buy":
                    s = self.slots[s_id]
                    cur.execute("""INSERT INTO bot_slots_v17 (slot_id, avg_price, units, sl, max_pnl, updated_at) 
                        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (slot_id) 
                        DO UPDATE SET avg_price=EXCLUDED.avg_price, units=EXCLUDED.units, sl=EXCLUDED.sl, max_pnl=EXCLUDED.max_pnl, updated_at=EXCLUDED.updated_at""", 
                        (s_id, s['price'], s['units'], s['sl'], 0.0, now_th))
                else:
                    cur.execute("DELETE FROM bot_slots_v17 WHERE slot_id=%s", (s_id,))
                cur.execute("INSERT INTO trade_history_final (ts, side, price, pnl_thb, pnl_pct) VALUES (%s, %s, %s, %s, %s)", 
                            (now_th, side, price, pnl_thb, pnl_pct))

    def _send_pro_report(self, p, rsi, btc_safe):
        thb_cash, coin_units = self.get_balance()
        asset_value = coin_units * p
        total_equity = thb_cash + asset_value
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100

        btc_status = "🟢 BULLISH" if btc_safe else "🔴 BEARISH (Hold)"
        now = datetime.now(self.tz)

        msg = (f"💠 <b>TITAN V.17.8 | INSTITUTIONAL REPORT</b>\n━━━━━━━━━━━━━━━━━━━━\n"
               f"🌍 <b>GLOBAL MARKET</b>\n• BTC Status: {btc_status}\n"
               f"📊 <b>XRP ANALYTICS</b>\n• Price: {p:,.4f} | RSI: {rsi:.1f}\n━━━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>FINANCIAL SUMMARY</b>\n"
               f"• Total Equity: {total_equity:,.2f} ({growth:+.2f}%)\n"
               f"• Available Cash: 🟢 <b>{thb_cash:,.2f} THB</b>\n"
               f"• Active Assets: 🔵 {asset_value:,.2f} THB\n━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>STRATEGY (3-SLOTS STATUS)</b>\n")

        for i in [1, 2, 3]:
            s = self.slots[i]
            if s.get('active'):
                pnl = ((p - s['price']) / s['price']) * 100
                msg += f"• SLOT {i}: 🟢 ROI {pnl:+.2f}% | SL {s['sl']:,.2f}\n"
            else: msg += f"• SLOT {i}: ⚪ WAITING FOR ENTRY\n"

        msg += f"━━━━━━━━━━━━━━━━━━━━\n📅 <i>{now.strftime('%d/%m/%Y | %H:%M:%S')}</i>"
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    Titan_V17_8_Pro_Final().run()
