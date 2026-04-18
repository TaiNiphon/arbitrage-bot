import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanOmniV16_1_Final:
    def __init__(self):
        # --- ดึงค่าจาก Railway (อ้างอิงตามรูป 5363.jpg ที่พี่ติ๊กยืนยัน) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- Settings & Risk Management ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "5433.29")).replace(',', ''))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.5"))
        self.max_slots = int(os.getenv("MAX_SLOTS", "3"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "1500.0"))
        
        # --- Filters (ป้องกันเหรียญผี/เหรียญไม่มีวอลลุ่ม) ---
        self.min_volume_thb = 3000000.0
        self.positions = {} 

        self._init_db_v16()
        self._sync_positions_from_db()
        self.notify("<b>🔥 TITAN V.16.1 OMNI | DEPLOYED</b>\n<i>Status: Scanner Mode Active (100+ Coins)</i>")

    def _init_db_v16(self):
        """สร้างตารางเก็บข้อมูลไม้เทรดถ้ายังไม่มี"""
        conn = psycopg2.connect(self.db_url); cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS bot_positions_v16 (
            symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, 
            dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
        conn.commit(); cur.close(); conn.close()

    def _sync_positions_from_db(self):
        """ดึงสถานะจาก Database มาใส่ในตัวแปรบอท"""
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT symbol, avg_price, total_units, dynamic_sl, max_pnl FROM bot_positions_v16")
            for row in cur.fetchall():
                self.positions[row[0]] = {"price": row[1], "units": row[2], "sl": row[3], "max_pnl": row[4]}
            cur.close(); conn.close()
        except: pass

    def get_indicators(self, symbol):
        """คำนวณ RSI, ATR และ Trend"""
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            if res.get('s') != 'ok': return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            atr = np.mean(tr[-14:])
            trend = "BULLISH 📈" if c[-1] > np.mean(c[-20:]) else "BEARISH 📉"
            return {"price": c[-1], "rsi": rsi, "atr": atr, "trend": trend}
        except: return None

    def get_wallet(self):
        """เช็กยอดเงินบาทคงเหลือจริงใน Bitkub"""
        ts = str(int(time.time() * 1000))
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
        res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                            headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
        return float(res['result'].get('THB', 0)) if res.get('error') == 0 else 0.0

    def place_order(self, side, symbol, amt, price):
        """ส่งคำสั่งซื้อ/ขาย"""
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        ts = str(int(time.time() * 1000))
        payload = {"sym": symbol.lower(), "amt": amt, "rat": price, "typ": "limit"}
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
        r = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload), timeout=10)
        return r.json().get('error') == 0

    def _save_state(self, symbol, data=None):
        """บันทึกหรือลบข้อมูลไม้ลง Database"""
        conn = psycopg2.connect(self.db_url); cur = conn.cursor()
        if data:
            cur.execute("""INSERT INTO bot_positions_v16 (symbol, avg_price, total_units, dynamic_sl, max_pnl, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (symbol) 
                           DO UPDATE SET avg_price=EXCLUDED.avg_price, total_units=EXCLUDED.total_units, 
                           dynamic_sl=EXCLUDED.dynamic_sl, max_pnl=EXCLUDED.max_pnl, updated_at=EXCLUDED.updated_at""",
                        (symbol, data['price'], data['units'], data['sl'], data['max_pnl'], datetime.now()))
        else:
            cur.execute("DELETE FROM bot_positions_v16 WHERE symbol = %s", (symbol,))
        conn.commit(); cur.close(); conn.close()

    def run(self):
        last_rep = 0
        while True:
            try:
                # 1. Scanner: กรองเหรียญที่มีโวลุ่ม > 1 ล้านบาท
                ticker = requests.get("https://api.bitkub.com/api/market/ticker").json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                thb = self.get_wallet()
                
                # 2. Sell Logic (Trailing Stop จาก ATR ป้องกันขายหมู)
                for sym in list(self.positions.keys()):
                    ind = self.get_indicators(sym)
                    if not ind: continue
                    p, atr, pos = ind['price'], ind['atr'], self.positions[sym]
                    pnl = ((p - pos['price']) / pos['price']) * 100
                    
                    if pnl > pos['max_pnl']: pos['max_pnl'] = pnl # เก็บสถิติกำไรสูงสุด
                    
                    # ขยับ SL ตามราคา (Trailing Stop)
                    new_sl = p - (atr * self.risk_per_trade)
                    if new_sl > pos['sl']: pos['sl'] = new_sl

                    # เงื่อนไขขาย: ราคาหลุด SL หรือ กำไรกระโดดถึง 10%
                    if p <= pos['sl'] or pnl >= 10.0:
                        if self.place_order("sell", sym, pos['units'], p):
                            self.notify(f"📤 <b>CLOSE {sym} @ {p:,.2f}</b>\nPnL: {pnl:+.2f}%")
                            del self.positions[sym]; self._save_state(sym, None)

                # 3. Buy Logic (Scanner ค้นหาของถูก)
                if len(self.positions) < self.max_slots:
                    for sym in qualified:
                        if sym in self.positions or len(self.positions) >= self.max_slots: continue
                        ind = self.get_indicators(sym)
                        if ind and ind['rsi'] <= self.rsi_buy_target and thb >= self.budget_per_slot:
                            if self.place_order("buy", sym, self.budget_per_slot, ind['price']):
                                # จุด SL เริ่มต้น = ราคาซื้อ - (ความผันผวน * 2.5)
                                new_pos = {"price": ind['price'], "units": self.budget_per_slot/ind['price'], 
                                           "sl": ind['price'] - (ind['atr'] * self.risk_per_trade), "max_pnl": 0.0}
                                self.positions[sym] = new_pos; self._save_state(sym, new_pos)
                                self.notify(f"🚀 <b>ENTRY {sym} @ {ind['price']:,.2f}</b>\nRSI: {ind['rsi']:.2f}")
                                thb -= self.budget_per_slot
                        time.sleep(1.2)

                # 4. Report (สรุปผลตามรูป 5364.jpg ทุก 10 นาที)
                if time.time() - last_rep >= 600:
                    self._report(thb)
                    last_rep = time.time()

            except Exception as e: 
                print(f"Error: {e}")
                time.sleep(30)
            time.sleep(30)

    def _report(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_asset_val, total_units, slot_details, main_p, main_t, main_r = 0, 0, "", 0, "N/A", 0
        
        for i, (sym, pos) in enumerate(self.positions.items(), 1):
            ind = self.get_indicators(sym)
            p = ind['price'] if ind else pos['price']
            total_asset_val += (pos['units'] * p); total_units += pos['units']
            pnl = ((p - pos['price']) / pos['price']) * 100
            sl_pct = ((pos['sl'] - p) / p) * 100
            if i == 1: main_p, main_t, main_r = p, ind['trend'], ind['rsi']
            
            slot_details += (f"<b>[SLOT {i} | {sym}]</b>\n"
                             f"• SL : {pos['sl']:,.2f} ({sl_pct:+.2f}%)\n"
                             f"• Max PnL : {pos['max_pnl']:+.2f}%\n")

        for i in range(len(self.positions) + 1, self.max_slots + 1):
            slot_details += f"<b>[SLOT {i}]</b> - Waiting RSI ≤ {self.rsi_buy_target}\n"

        equity = thb + total_asset_val
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        
        msg = (f"🛡️ <b>TITAN V.16.1 OMNI | SCANNER</b>\n"
               f"Status : {'HOLDING' if self.positions else 'MONITORING'}\n"
               f"Date : {now.strftime('%d/%m/%Y')}\nTime : {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>MARKET INTELLIGENCE</b>\n"
               f"• Price : {main_p:,.2f} THB\n"
               f"• Trend 1H : {main_t}\n"
               f"• RSI : {main_r:.2f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>PORTFOLIO ANALYSIS</b>\n"
               f"• EQUITY : {equity:,.2f} THB\n"
               f"• GROWTH : {growth:+.2f}%\n"
               f"• Cash : {thb:,.2f} | <b>Assets : {total_units:.4f}</b>\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>STRATEGY OMNI-SLOT</b>\n{slot_details}")
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"})
        except: pass

if __name__ == "__main__":
    TitanOmniV16_1_Final().run()
