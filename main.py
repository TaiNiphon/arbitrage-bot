import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanDefinitiveEdition:
    def __init__(self):
        # --- 1. CONFIGURATION (จากหน้า Variables ของคุณ) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        
        # Risk & Equity Management
        raw_equity = os.getenv("INITIAL_EQUITY", "1800")
        self.initial_equity = float(str(raw_equity).replace(',', ''))
        self.rsi_buy_target = 30.0
        
        # Memory State
        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}
        self.prev_rsi = 0.0
        
        # Initialize System
        self._setup_database()
        self._load_state_from_db()
        self.notify("<b>✅ TITAN V.15.0 PRO: REPORT RESTORED</b>\n<i>Status: Database & Full Reporting Active</i>")

    def _setup_database(self):
        """ตรวจสอบและสร้างตารางฐานข้อมูลรวมถึง History"""
        try:
            if not self.db_url: return
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v15 (
                        slot_id INTEGER PRIMARY KEY, avg_price FLOAT, 
                        total_units FLOAT, dynamic_sl FLOAT, updated_at TIMESTAMP)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, slot_id INTEGER, action TEXT, 
                        price FLOAT, units FLOAT, pnl FLOAT, timestamp TIMESTAMP)""")
                    conn.commit()
            print("✅ Database System Ready")
        except Exception as e: print(f"⚠️ DB Error: {e}")

    def _load_state_from_db(self):
        try:
            if not self.db_url: return
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, avg_price, total_units, dynamic_sl FROM bot_state_v15")
                    for row in cur.fetchall():
                        if row[2] > 0:
                            self.slots[row[0]] = {"active": True, "price": row[1], "units": row[2], "sl": row[3]}
        except: pass

    def get_balance(self):
        """แก้ไข Signature V3 ให้ดึงยอดเงิน 1,800 THB ได้เสถียร"""
        try:
            path = "/api/v3/market/wallet"
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts + "POST" + path).encode(), hashlib.sha256).hexdigest()
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            res = requests.post(f"https://api.bitkub.com{path}", headers=headers, timeout=10).json()
            if res.get('error') == 0:
                thb = float(res['result'].get('THB', 0))
                coin = float(res['result'].get(self.symbol.split('_')[0], 0))
                return thb, coin
            return 0.0, 0.0
        except: return 0.0, 0.0

    def get_market_data(self):
        """ดึงราคาและคำนวณ RSI (15m)"""
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"price": c[-1], "rsi": rsi, "atr": np.mean(tr[-14:])}
        except: return None

    def execute_trade(self, side, slot_id, price, amt_or_units, atr=0):
        """ซื้อขายและบันทึกประวัติลงฐานข้อมูลทันที"""
        try:
            path = f"/api/v3/market/place-{'bid' if side == 'buy' else 'ask'}"
            ts = str(int(time.time() * 1000))
            payload = {"sym": self.symbol.lower(), "amt": amt_or_units, "rat": price, "typ": "limit"}
            sig = hmac.new(self.api_secret.encode(), (ts + "POST" + path + json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            
            res = requests.post(f"https://api.bitkub.com{path}", headers=headers, data=json.dumps(payload), timeout=10).json()
            
            if res.get('error') == 0:
                pnl = 0
                if side == "sell":
                    pnl = ((price - self.slots[slot_id]['price']) / self.slots[slot_id]['price']) * 100
                
                # UPDATE DATABASE & HISTORY
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO trade_history (slot_id, action, price, units, pnl, timestamp) VALUES (%s, %s, %s, %s, %s, %s)",
                                   (slot_id, side.upper(), price, amt_or_units if side == "sell" else amt_or_units/price, pnl, datetime.now()))
                        if side == "buy":
                            cur.execute("INSERT INTO bot_state_v15 (slot_id, avg_price, total_units, dynamic_sl, updated_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET avg_price=EXCLUDED.avg_price, total_units=EXCLUDED.total_units, dynamic_sl=EXCLUDED.dynamic_sl",
                                       (slot_id, price, amt_or_units/price, price - (atr*2.5), datetime.now()))
                        else:
                            cur.execute("DELETE FROM bot_state_v15 WHERE slot_id = %s", (slot_id,))
                        conn.commit()
                return True
            return False
        except: return False

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.get_market_data()
                if not d: continue
                p, rsi, atr = d['price'], d['rsi'], d['atr']
                thb, coin = self.get_balance()
                equity = thb + (coin * p)
                growth = ((equity - self.initial_equity) / self.initial_equity) * 100 if self.initial_equity > 0 else 0

                # 🎯 STRATEGY LOGIC: RSI-SLOTS
                active_ids = [i for i in self.slots if self.slots[i]["active"]]
                if len(active_ids) < 2 and rsi <= self.rsi_buy_target:
                    s_id = 1 if 1 not in active_ids else 2
                    buy_amt = thb / (2 - len(active_ids))
                    if buy_amt >= 10 and self.execute_trade("buy", s_id, p, buy_amt, atr):
                        self.slots[s_id] = {"active": True, "price": p, "units": buy_amt/p, "sl": p - (atr*2.5)}
                        self.notify(f"🚀 <b>BUY ORDER EXECUTED</b>\nSlot: {s_id} | Price: {p:,.2f}")

                # 📤 SELL LOGIC: TP/SL
                for s_id, s in self.slots.items():
                    if s["active"]:
                        curr_pnl = ((p - s['price']) / s['price']) * 100
                        if p <= s['sl'] or curr_pnl >= 10.0:
                            if self.execute_trade("sell", s_id, p, s['units']):
                                self.notify(f"📤 <b>SELL ORDER EXECUTED</b>\nSlot: {s_id} | PnL: {curr_pnl:+.2f}%")
                                self.slots[s_id] = {"active": False, "price": 0, "units": 0, "sl": 0}

                # 📊 REPORTING SYSTEM (แก้ไขให้ละเอียดตามที่คุณต้องการ)
                if time.time() - last_rep >= 600:
                    self._generate_full_report(p, rsi, equity, growth, thb, coin)
                    last_rep = time.time(); self.prev_rsi = rsi
            except Exception as e: print(f"Main Loop Error: {e}")
            time.sleep(15)

    def _generate_full_report(self, p, rsi, equity, growth, thb, coin):
        """สร้างรายงานที่ละเอียดและครบถ้วน"""
        now = datetime.now(timezone(timedelta(hours=7)))
        msg = (f"🛡️ <b>TITAN V.15.0 PRO | {self.symbol}</b>\n"
               f"Status: ONLINE & MONITORING\n"
               f"📅 {now.strftime('%d/%m/%Y')} | ⏰ {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>MARKET INTELLIGENCE</b>\n"
               f"• Current Price : <b>{p:,.2f} THB</b>\n"
               f"• RSI (15m) : {rsi:.2f} (Prev: {self.prev_rsi:.2f})\n"
               f"• Target Buy : ≤ {self.rsi_buy_target}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
               f"• NET EQUITY : <b>{equity:,.2f} THB</b>\n"
               f"• TOTAL GROWTH : {growth:+.2f}%\n"
               f"• Available Cash : {thb:,.2f} THB\n"
               f"• Asset Holding : {coin:.4f} {self.symbol.split('_')[0]}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>STRATEGY DUAL-SLOT</b>\n")
        
        for i in [1, 2]:
            s = self.slots[i]
            if s["active"]:
                pnl = ((p - s['price']) / s['price']) * 100
                msg += f"<b>[SLOT {i}]</b> - ACTIVE (PnL {pnl:+.2f}%)\n"
            else:
                msg += f"<b>[SLOT {i}]</b> - <i>Waiting for RSI Condition...</i>\n"
        
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanDefinitiveEdition().run()
