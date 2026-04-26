import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanHybridV15_Final:
    def __init__(self):
        # --- 1. CONFIGURATION (คงเดิมทั้งหมด) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        
        raw_eq = os.getenv("INITIAL_EQUITY", "1800")
        try: self.initial_equity = float(str(raw_eq).replace(',', ''))
        except: self.initial_equity = 1800.0
            
        self.last_known_equity = self.initial_equity
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "30.0"))
        
        # State Management
        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0, "max_pnl": 0.0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0, "max_pnl": 0.0}}
        self.prev_rsi = 0.0
        self.btc_status = "SYNCHRONIZING..." 

        self._setup_database()
        self._load_state_from_db()
        self.notify("<b>🛡️ TITAN V.15.3.1 | FIXED BTC TREND</b>\n<i>Status: กลยุทธ์เดิม เสถียรขึ้นด้วยระบบ Hybrid V3</i>")

    def _setup_database(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v15 (
                        slot_id INTEGER PRIMARY KEY, avg_price FLOAT, 
                        total_units FLOAT, dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, slot_id INTEGER, action TEXT, 
                        price FLOAT, units FLOAT, pnl FLOAT, timestamp TIMESTAMP)""")
                    conn.commit()
        except Exception as e: print(f"DB Error: {e}")

    def _load_state_from_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, avg_price, total_units, dynamic_sl, max_pnl FROM bot_state_v15")
                    for r in cur.fetchall():
                        if r[2] > 0:
                            self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3], "max_pnl": r[4]}
        except: pass

    def get_wallet_stable(self):
        for _ in range(3):
            try:
                ts = str(int(time.time() * 1000))
                sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                    headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                if res.get('error') == 0:
                    thb = float(res['result'].get('THB', 0))
                    asset = float(res['result'].get(self.symbol.split('_')[0], 0))
                    return thb, asset
            except: time.sleep(1)
        return None, None

    def get_btc_trend_optimized(self):
        """แก้ไขด่วน: ระบบดึงข้อมูล BTC แบบ Hybrid เพื่อให้สถานะเปลี่ยนแน่นอน"""
        try:
            # ใช้ Public Ticker API ที่เสถียรที่สุดของ Bitkub
            res = requests.get("https://api.bitkub.com/api/market/ticker?sym=THB_BTC", timeout=10).json()
            if 'THB_BTC' in res:
                data = res['THB_BTC']
                change = float(data.get('percentChange', 0))
                
                # ถ้าค่าเป็น 0 ให้คำนวณเองทันทีจากราคาปัจจุบันเทียบราคาเปิด 24 ชม.
                if change == 0:
                    last = float(data.get('last', 0))
                    open_p = float(data.get('open24h',  open_p if 'open_p' in locals() else last))
                    change = ((last - open_p) / open_p) * 100 if open_p > 0 else 0.0

                if change > 0.5: self.btc_status = f"BULLISH 📈 ({change:+.2f}%)"
                elif change < -2.0: self.btc_status = f"BEARISH 📉 ({change:+.2f}%)"
                else: self.btc_status = f"SIDEWAYS ↔️ ({change:+.2f}%)"
                
                return change > -3.5 # เงื่อนไขรักษาความเสี่ยงเดิม
            return True
        except:
            self.btc_status = "FETCH ERROR ⚠️"
            return True

    def get_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            if 'c' not in res or len(res['c']) < 15: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            m_up = np.mean(up[-14:]); m_down = np.mean(down[-14:])
            rsi = 100 - (100 / (1 + (m_up / (m_down + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"price": c[-1], "rsi": rsi, "atr": np.mean(tr[-14:])}
        except: return None

    def place_order(self, side, slot_id, price, amt_or_units, atr=0):
        try:
            path = f"/api/v3/market/place-{'bid' if side == 'buy' else 'ask'}"
            ts = str(int(time.time() * 1000))
            payload = {"sym": self.symbol.lower(), "amt": amt_or_units, "rat": price, "typ": "limit"}
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload), timeout=10).json()
            
            if res.get('error') == 0:
                pnl = 0
                if side == "sell":
                    pnl = ((price - self.slots[slot_id]['price']) / self.slots[slot_id]['price']) * 100
                
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO trade_history (slot_id, action, price, units, pnl, timestamp) VALUES (%s, %s, %s, %s, %s, %s)",
                                   (slot_id, side.upper(), price, amt_or_units if side == "sell" else amt_or_units/price, pnl, datetime.now()))
                        if side == "buy":
                            cur.execute("INSERT INTO bot_state_v15 (slot_id, avg_price, total_units, dynamic_sl, max_pnl, updated_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET avg_price=EXCLUDED.avg_price, total_units=EXCLUDED.total_units, dynamic_sl=EXCLUDED.dynamic_sl, max_pnl=EXCLUDED.max_pnl",
                                       (slot_id, price, amt_or_units/price, price - (atr*2.5), 0.0, datetime.now()))
                        else:
                            cur.execute("DELETE FROM bot_state_v15 WHERE slot_id = %s", (slot_id,))
                return True
            return False
        except: return False

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.get_indicators()
                if not d: time.sleep(10); continue
                    
                p, rsi, atr = d['price'], d['rsi'], d['atr']
                btc_allowed = self.get_btc_trend_optimized()
                
                thb, coin = self.get_wallet_stable()
                if thb is not None:
                    curr_equity = thb + (coin * p)
                    self.last_known_equity = curr_equity
                else:
                    curr_equity = self.last_known_equity
                    thb = 0 
                
                growth = ((curr_equity - self.initial_equity) / self.initial_equity) * 100

                # --- BUY LOGIC (คงเดิม) ---
                active_ids = [i for i in self.slots if self.slots[i]["active"]]
                if len(active_ids) < 2 and rsi <= self.rsi_buy_target:
                    if btc_allowed:
                        s_id = 1 if 1 not in active_ids else 2
                        if thb >= 10:
                            buy_amt = thb / (2 - len(active_ids))
                            if buy_amt >= 10 and self.place_order("buy", s_id, p, buy_amt, atr):
                                self.slots[s_id] = {"active": True, "price": p, "units": buy_amt/p, "sl": p - (atr*2.5), "max_pnl": 0.0}
                                self.notify(f"🚀 <b>BUY ENTRY SLOT {s_id}</b>\nPrice: {p:,.2f} | BTC: {self.btc_status}")

                # --- SELL LOGIC (Trailing Stop คงเดิม) ---
                for s_id, s in self.slots.items():
                    if s["active"]:
                        curr_pnl = ((p - s['price']) / s['price']) * 100
                        if curr_pnl > s['max_pnl']: s['max_pnl'] = curr_pnl
                        new_sl = p - (atr * 2.5)
                        if new_sl > s['sl']: s['sl'] = new_sl
                        if p <= s['sl'] or curr_pnl >= 10.0:
                            if self.place_order("sell", s_id, p, s['units']):
                                self.notify(f"📤 <b>SELL CLOSE SLOT {s_id}</b>\nPrice: {p:,.2f} | PnL: {curr_pnl:+.2f}%")
                                self.slots[s_id] = {"active": False, "price": 0, "units": 0, "sl": 0, "max_pnl": 0.0}

                # --- REPORTING (หน้าตาเดิมที่คุณต้องการ) ---
                if time.time() - last_rep >= 600:
                    self._send_full_report(p, rsi, curr_equity, growth, thb or 0, coin or 0)
                    last_rep = time.time(); self.prev_rsi = rsi
            except Exception as e: print(f"Error: {e}")
            time.sleep(20)

    def _send_full_report(self, p, rsi, equity, growth, thb, coin):
        now = datetime.now(timezone(timedelta(hours=7)))
        msg = (f"🛡️ <b>TITAN V.15.3.1 | {self.symbol}</b>\n"
               f"Status: ONLINE & MONITORING\n"
               f"📅 {now.strftime('%d/%m/%Y')} | ⏰ {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>MARKET INTELLIGENCE</b>\n"
               f"• Price : <b>{p:,.2f} THB</b>\n"
               f"• BTC Trend : {self.btc_status}\n"
               f"• RSI : {rsi:.2f} (Prev: {self.prev_rsi:.2f})\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
               f"• NET EQUITY : <b>{equity:,.2f} THB</b>\n"
               f"• TOTAL GROWTH : {growth:+.2f}%\n"
               f"• Cash : {thb:,.2f} | Assets: {coin:.4f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>STRATEGY DUAL-SLOT</b>\n")
        for i, s in self.slots.items():
            if s["active"]:
                pnl = ((p - s['price']) / s['price']) * 100
                msg += f"<b>[SLOT {i}]</b> - ACTIVE\n  └ SL: {s['sl']:,.2f} | PnL: {pnl:+.2f}%\n"
            else:
                msg += f"<b>[SLOT {i}]</b> - Waiting RSI ≤ {self.rsi_buy_target}\n"
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanHybridV15_Final().run()
