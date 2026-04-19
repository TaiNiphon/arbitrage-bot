import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanUltimateV16_4_Full:
    def __init__(self):
        # --- 1. Load Settings (ดึงจาก Railway) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. Strategy Parameters (จุดที่พี่เน้น) ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "4813.29")).replace(',', ''))
        self.rsi_buy_target = 35.0
        self.risk_per_trade = 2.5 # ป้องกันขายหมูด้วย Trailing Stop
        self.max_slots = 3
        self.budget_per_slot = 1000.0
        self.min_volume_thb = 3000000.0 # คัดเกรดเหรียญ 3 ล้านบาทขึ้นไป

        self.positions = {} 
        self.latest_scan_results = []
        self._init_db()
        self._sync_positions()
        self.notify("<b>🛡️ TITAN V.16.4 | MASTER ONLINE</b>\n<i>Status: Full Report & 3M Filter Active</i>")

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
        # --- ระบบ Retry 3 รอบเพื่อแก้ปัญหาค่า 0.00 ---
        for _ in range(3):
            try:
                res = requests.get(f"https://api.bitkub.com/api/market/candles?symbol={symbol}&resolution=15&limit=100", timeout=10).json()
                if not res or 'c' not in res or len(res['c']) < 30:
                    time.sleep(1); continue
                
                c, h, l = np.array(res['c'], dtype=float), np.array(res['h'], dtype=float), np.array(res['l'], dtype=float)
                diff = np.diff(c)
                gain, loss = np.where(diff > 0, diff, 0), np.where(diff < 0, -diff, 0)
                rsi = 100 - (100 / (1 + (np.mean(gain[-14:]) / (np.mean(loss[-14:]) + 1e-9))))
                tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
                return {'price': c[-1], 'rsi': rsi, 'atr': np.mean(tr[-14:]), 
                        'trend': "BULLISH 📈" if c[-1] > np.mean(c[-20:]) else "BEARISH 📉"}
            except: time.sleep(1)
        return None

    # ... (ส่วน place_order และ get_wallet เหมือนเดิมที่พี่มี) ...

    def _report_full(self, thb):
        """ รายงานฉบับเต็มรูปแบบเดียวกับที่พี่เคยใช้ (สวยงามและครบถ้วน) """
        now = datetime.now(timezone(timedelta(hours=7)))
        total_asset_val, total_units, slot_details = 0, 0, ""
        
        # ดึงตัวที่ RSI ต่ำสุดมาโชว์ใน Market Intelligence
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
               f"• Trend 15M : {m_trend}\n"
               f"• RSI : {m_rsi:.2f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>PORTFOLIO ANALYSIS</b>\n"
               f"• EQUITY : {equity:,.2f} THB\n"
               f"• GROWTH : {growth:+.2f}%\n"
               f"• Cash : {thb:,.2f} | Assets : {total_units:.4f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>STRATEGY OMNI-SLOT</b>\n{slot_details}")
        self.notify(msg)

    # ... (ส่วน run() ที่เรียกใช้ _report_full) ...
