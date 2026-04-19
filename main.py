import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanOmniV16_3_Final:
    def __init__(self):
        # --- 1. Load Environment Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. Risk Management Settings ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "4813.29")).replace(',', ''))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.5"))
        self.max_slots = int(os.getenv("MAX_SLOTS", "3"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "1000.0"))

        # --- 3. Internal State ---
        self.min_volume_thb = 1000000.0 
        self.positions = {} 
        self.latest_scan_results = []

        self._init_db_v16()
        self._sync_positions_from_db()
        self.notify("<b>🛡️ TITAN V.16.3 | FINAL AUDITED</b>\n<i>Status: Master Code Verified</i>")

    def _init_db_v16(self):
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=10)
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_positions_v16 (
                symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, 
                dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
            print("✅ Database: ONLINE")
        except Exception as e:
            print(f"⚠️ DB Error: {e}")

    def _sync_positions_from_db(self):
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT symbol, avg_price, total_units, dynamic_sl, max_pnl FROM bot_positions_v16")
            for row in cur.fetchall():
                self.positions[row[0]] = {"price": row[1], "units": row[2], "sl": row[3], "max_pnl": row[4]}
            cur.close(); conn.close()
        except: pass

    def get_indicators(self, symbol):
        try:
            # ใช้ API ตัวหลักเพื่อดึงข้อมูลแท่งเทียน 15 นาที
            res = requests.get(f"https://api.bitkub.com/api/market/candles?symbol={symbol}&resolution=15&limit=100", timeout=10)
            if res.status_code != 200: return None
            data = res.json()
            if not data or 'c' not in data: return None
            
            c = np.array(data['c'], dtype=float)
            h = np.array(data['h'], dtype=float)
            l = np.array(data['l'], dtype=float)
            
            # การคำนวณ RSI แบบมาตรฐาน
            diff = np.diff(c)
            gain = np.where(diff > 0, diff, 0)
            loss = np.where(diff < 0, -diff, 0)
            avg_gain = np.mean(gain[-14:])
            avg_loss = np.mean(loss[-14:])
            rsi = 100 - (100 / (1 + (avg_gain / (avg_loss + 1e-9))))
            
            # คำนวณ ATR เพื่อใช้ใน Trailing Stop
            tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            atr = np.mean(tr[-14:])
            
            trend = "BULLISH 📈" if c[-1] > np.mean(c[-20:]) else "BEARISH 📉"
            return {'price': c[-1], 'rsi': rsi, 'trend': trend, 'atr': atr}
        except: return None

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
            # เพิ่ม Header JSON เพื่อความชัวร์
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            r = requests.post(f"https://api.bitkub.com{path}", headers=headers, data=json.dumps(payload), timeout=10)
            return r.json().get('error') == 0
        except: return False

    def _save_state(self, symbol, data=None):
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=5)
            cur = conn.cursor()
            if data:
                cur.execute("""INSERT INTO bot_positions_v16 (symbol, avg_price, total_units, dynamic_sl, max_pnl, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (symbol) 
                               DO UPDATE SET avg_price=EXCLUDED.avg_price, total_units=EXCLUDED.total_units, 
                               dynamic_sl=EXCLUDED.dynamic_sl, max_pnl=EXCLUDED.max_pnl, updated_at=EXCLUDED.updated_at""",
                            (symbol, data['price'], data['units'], data['sl'], data['max_pnl'], datetime.now()))
            else:
                cur.execute("DELETE FROM bot_positions_v16 WHERE symbol = %s", (symbol,))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def run(self):
        last_rep = 0
        while True:
            try:
                # ดึง Ticker และคัดเลือกเหรียญที่มี Volume เกิน 1 ล้านบาท
                ticker_res = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker_res.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                
                thb = self.get_wallet()
                current_scan_data = [] 

                # 1. จัดการฝั่งขาย (Sell Logic)
                for sym in list(self.positions.keys()):
                    ind = self.get_indicators(sym)
                    if not ind: continue
                    p, atr, pos = ind['price'], ind['atr'], self.positions[sym]
                    pnl = ((p - pos['price']) / pos['price']) * 100
                    if pnl > pos['max_pnl']: pos['max_pnl'] = pnl
                    
                    # Trailing Stop: ขยับจุดตัดขาดทุนขึ้นตามกำไร
                    new_sl = p - (atr * self.risk_per_trade)
                    if new_sl > pos['sl']: 
                        pos['sl'] = new_sl
                        self._save_state(sym, pos)

                    if p <= pos['sl'] or pnl >= 10.0:
                        if self.place_order("sell", sym, pos['units'], p):
                            self.notify(f"📤 <b>CLOSE {sym} @ {p:,.2f}</b>\nPnL: {pnl:+.2f}%")
                            del self.positions[sym]; self._save_state(sym, None)

                # 2. จัดการฝั่งซื้อ (Buy Logic)
                for sym in qualified:
                    if sym in self.positions: continue
                    ind = self.get_indicators(sym)
                    if ind:
                        current_scan_data.append({"sym": sym, "rsi": ind['rsi'], "price": ind['price'], "trend": ind['trend']})
                        if len(self.positions) < self.max_slots and ind['rsi'] <= self.rsi_buy_target and thb >= self.budget_per_slot:
                            if self.place_order("buy", sym, self.budget_per_slot, ind['price']):
                                new_pos = {"price": ind['price'], "units": self.budget_per_slot/ind['price'], 
                                           "sl": ind['price'] - (ind['atr'] * self.risk_per_trade), "max_pnl": 0.0}
                                self.positions[sym] = new_pos; self._save_state(sym, new_pos)
                                self.notify(f"🚀 <b>ENTRY {sym} @ {ind['price']:,.2f}</b>\nRSI: {ind['rsi']:.2f}")
                                thb -= self.budget_per_slot
                    time.sleep(0.5)

                if current_scan_data:
                    self.latest_scan_results = sorted(current_scan_data, key=lambda x: x['rsi'])[:5]

                # ส่งรายงานสรุปทุก 10 นาที
                if time.time() - last_rep >= 600:
                    self._report(thb)
                    last_rep = time.time()

            except Exception as e: 
                print(f"⚠️ Runtime Error: {e}"); time.sleep(15)
            time.sleep(15)

    def _report(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_asset_val, total_units, slot_details = 0, 0, ""
        best_watch = self.latest_scan_results[0] if self.latest_scan_results else None

        # เลือกข้อมูลมาโชว์ในส่วน Market Intelligence
        if self.positions:
            first_sym = list(self.positions.keys())[0]
            ind_main = self.get_indicators(first_sym)
            main_p, main_t, main_r = (ind_main['price'], ind_main['trend'], ind_main['rsi']) if ind_main else (0, "N/A", 0)
        elif best_watch:
            main_p, main_t, main_r = best_watch['price'], best_watch['trend'], best_watch['rsi']
        else:
            main_p, main_t, main_r = 0, "N/A", 0

        for i, (sym, pos) in enumerate(self.positions.items(), 1):
            ind = self.get_indicators(sym)
            p = ind['price'] if ind else pos['price']
            total_asset_val += (pos['units'] * p); total_units += pos['units']
            pnl = ((p - pos['price']) / pos['price']) * 100
            slot_details += f"<b>[SLOT {i} | {sym}]</b>\n• PnL : {pnl:+.2f}%\n• SL : {pos['sl']:,.2f}\n"

        for i in range(len(self.positions) + 1, self.max_slots + 1):
            slot_details += f"<b>[SLOT {i}]</b> - Waiting RSI ≤ {self.rsi_buy_target}\n"

        equity = thb + total_asset_val
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        
        msg = (f"🛡️ <b>TITAN V.16.3 | MASTER</b>\n"
               f"Status : {'HOLDING' if self.positions else 'MONITORING'}\n"
               f"Date : {now.strftime('%d/%m/%Y')} | Time : {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>MARKET INTELLIGENCE</b>\n"
               f"• Price : {main_p:,.2f} THB\n"
               f"• Trend 15M : {main_t}\n"
               f"• RSI : {main_r:.2f}\n"
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
    TitanOmniV16_3_Final().run()
