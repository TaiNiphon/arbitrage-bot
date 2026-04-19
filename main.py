import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMasterV16_4:
    def __init__(self):
        # --- 1. ดึงค่าจากหน้า Variables ใน Railway (ต้องสะกดตัวใหญ่ตามรูปที่พี่ส่งมา) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. ตั้งค่ากลยุทธ์ตามที่พี่ตั้งไว้ใน Railway ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", "4813.29"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.5"))
        self.max_slots = int(os.getenv("MAX_SLOTS", "3"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "1000.0"))
        self.min_volume_thb = float(os.getenv("MIN_VOLUME_THB", "3000000.0"))
        self.atr_period = int(os.getenv("ATR_PERIOD", "14"))

        # --- 3. ระบบจัดการสถานะและความจำ ---
        self.positions = {}                
        self.latest_scan_results = []      

        self._init_db()                    
        self._sync_positions()             
        self.notify(f"<b>🛡️ TITAN V.16.4 | MASTER ONLINE</b>\n<i>ระบบดึงค่าจาก Railway สำเร็จ พร้อมกรองโวลุ่ม {self.min_volume_thb:,.0f} THB</i>")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=10)
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_positions_v16 (
                symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, 
                dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
            print("✅ Database: ONLINE")
        except Exception as e: print(f"⚠️ DB Error: {e}")

    def _sync_positions(self):
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT symbol, avg_price, total_units, dynamic_sl, max_pnl FROM bot_positions_v16")
            for row in cur.fetchall():
                self.positions[row[0]] = {"price": row[1], "units": row[2], "sl": row[3], "max_pnl": row[4]}
            cur.close(); conn.close()
        except: pass

    def get_indicators_deep(self, symbol):
        """ ระบบ Deep Scan ป้องกันค่า 0.00: ดึงข้อมูลซ้ำจนกว่าจะได้ค่าจริง """
        for attempt in range(3):
            try:
                res = requests.get(f"https://api.bitkub.com/api/market/candles?symbol={symbol}&resolution=15&limit=100", timeout=10).json()
                if not res or 'c' not in res or len(res['c']) < 30:
                    time.sleep(1); continue

                c, h, l = np.array(res['c'], dtype=float), np.array(res['h'], dtype=float), np.array(res['l'], dtype=float)
                diff = np.diff(c)
                gain, loss = np.where(diff > 0, diff, 0), np.where(diff < 0, -diff, 0)
                rsi = 100 - (100 / (1 + (np.mean(gain[-self.atr_period:]) / (np.mean(loss[-self.atr_period:]) + 1e-9))))
                tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
                atr = np.mean(tr[-self.atr_period:])

                return {'price': c[-1], 'rsi': rsi, 'atr': atr, 
                        'trend': "BULLISH 📈" if c[-1] > np.mean(c[-20:]) else "BEARISH 📉"}
            except: time.sleep(1)
        return None

    def get_wallet(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            return float(res['result'].get('THB', 0)) if res.get('error') == 0 else 0.0
        except: return 0.0

    def place_order(self, side, symbol, amt, price):
        try:
            path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
            ts = str(int(time.time() * 1000))
            payload = {"sym": symbol.lower(), "amt": amt, "rat": price, "typ": "limit"}
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            r = requests.post(f"https://api.bitkub.com{path}", headers=headers, data=json.dumps(payload), timeout=10)
            return r.json().get('error') == 0
        except: return False

    def _save_state(self, symbol, data=None):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            if data:
                cur.execute("""INSERT INTO bot_positions_v16 (symbol, avg_price, total_units, dynamic_sl, max_pnl, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (symbol) 
                               DO UPDATE SET dynamic_sl=EXCLUDED.dynamic_sl, max_pnl=EXCLUDED.max_pnl, updated_at=EXCLUDED.updated_at""",
                            (symbol, data['price'], data['units'], data['sl'], data['max_pnl'], datetime.now()))
            else:
                cur.execute("DELETE FROM bot_positions_v16 WHERE symbol = %s", (symbol,))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def run(self):
        last_rep = 0
        while True:
            try:
                # 1. ดึง Ticker และกรองโวลุ่มคุณภาพ
                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                
                thb = self.get_wallet()
                current_scan_data = []

                # 2. จัดการเหรียญที่ถืออยู่ (Trailing Stop)
                for sym in list(self.positions.keys()):
                    ind = self.get_indicators_deep(sym)
                    if not ind: continue
                    p, pos = ind['price'], self.positions[sym]
                    pnl = ((p - pos['price']) / pos['price']) * 100
                    if pnl > pos['max_pnl']: pos['max_pnl'] = pnl
                    
                    new_sl = p - (ind['atr'] * self.risk_per_trade)
                    if new_sl > pos['sl']: 
                        pos['sl'] = new_sl
                        self._save_state(sym, pos)

                    if p <= pos['sl']: 
                        if self.place_order("sell", sym, pos['units'], p):
                            self.notify(f"📤 <b>SELL {sym} @ {p:,.2f}</b>\nPnL: {pnl:+.2f}%")
                            del self.positions[sym]; self._save_state(sym, None)

                # 3. สแกนหาจุดซื้อ RSI ต่ำ
                for sym in qualified:
                    if sym in self.positions: continue
                    ind = self.get_indicators_deep(sym)
                    if ind:
                        current_scan_data.append({"sym": sym, "rsi": ind['rsi'], "price": ind['price'], "trend": ind['trend']})
                        if len(self.positions) < self.max_slots and ind['rsi'] <= self.rsi_buy_target and thb >= self.budget_per_slot:
                            if self.place_order("buy", sym, self.budget_per_slot, ind['price']):
                                new_pos = {"price": ind['price'], "units": self.budget_per_slot/ind['price'], 
                                           "sl": ind['price'] - (ind['atr'] * self.risk_per_trade), "max_pnl": 0.0}
                                self.positions[sym] = new_pos; self._save_state(sym, new_pos)
                                self.notify(f"🚀 <b>BUY {sym} @ {ind['price']:,.2f}</b>\nRSI: {ind['rsi']:.2f}")
                                thb -= self.budget_per_slot
                    time.sleep(0.5)

                if current_scan_data:
                    self.latest_scan_results = sorted(current_scan_data, key=lambda x: x['rsi'])[:5]

                # 4. ส่งรายงานฉบับสมบูรณ์ (ไม่มี 0.00)
                if time.time() - last_rep >= 600:
                    self._report_full(thb)
                    last_rep = time.time()

            except Exception as e: print(f"Runtime Error: {e}"); time.sleep(15)
            time.sleep(15)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_asset_val, total_units, slot_details = 0, 0, ""
        
        best = self.latest_scan_results[0] if self.latest_scan_results else None
        m_price, m_trend, m_rsi = (best['price'], best['trend'], best['rsi']) if best else (0, "SCANNING", 0)

        for i, (sym, pos) in enumerate(self.positions.items(), 1):
            ind = self.get_indicators_deep(sym)
            p = ind['price'] if ind else pos['price']
            total_asset_val += (pos['units'] * p); total_units += pos['units']
            pnl = ((p - pos['price']) / pos['price']) * 100
            slot_details += f"<b>[SLOT {i} | {sym}]</b>\n• PnL : {pnl:+.2f}%\n• SL : {pos['sl']:,.2f}\n"

        for i in range(len(self.positions) + 1, self.max_slots + 1):
            slot_details += f"<b>[SLOT {i}]</b> - Waiting RSI ≤ {self.rsi_buy_target}\n"

        equity = thb + total_asset_val
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        
        msg = (f"🛡️ <b>TITAN V.16.4 | MASTER</b>\n"
               f"Status : {'HOLDING' if self.positions else 'MONITORING'}\n"
               f"Date : {now.strftime('%d/%m/%Y')} | Time : {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>MARKET INTELLIGENCE</b>\n"
               f"• Price : {m_price:,.2f} THB\n"
               f"• Trend : {m_trend}\n"
               f"• RSI : {m_rsi:.2f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>PORTFOLIO ANALYSIS</b>\n"
               f"• EQUITY : {equity:,.2f} THB\n"
               f"• GROWTH : {growth:+.2f}%\n"
               f"• Cash : {thb:,.2f} | Assets : {total_units:.4f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>STRATEGY OMNI-SLOT</b>\n{slot_details}")
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanMasterV16_4().run()
