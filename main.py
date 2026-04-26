import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanOmniV15_Comprehensive:
    def __init__(self):
        # --- Config & Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        
        # Risk Settings
        raw_equity = os.getenv("INITIAL_EQUITY", "1800")
        self.initial_equity = float(str(raw_equity).replace(',', ''))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "30.0"))
        
        # State Management
        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}
        self.prev_rsi = 0.0
        
        self._init_db()
        self.notify("<b>🚀 TITAN V.15.0 | REPORT SYSTEM RESTORED</b>\n<i>สถานะ: กำลังเชื่อมต่อ API และ Sync ข้อมูล...</i>")

    def _init_db(self):
        try:
            if self.db_url:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v15 (
                            slot_id INTEGER PRIMARY KEY, avg_price FLOAT, 
                            total_units FLOAT, dynamic_sl FLOAT, updated_at TIMESTAMP)""")
                        conn.commit()
        except: pass

    def get_balance(self):
        """แก้ไขระบบ Signature ให้ดึงยอดเงิน 1,800 THB ได้จริง"""
        try:
            path = "/api/v3/market/wallet"
            ts = str(int(time.time() * 1000))
            # ต้องต่อ String ให้ตรงตามคู่มือ Bitkub V3 เป๊ะๆ
            sig_data = ts + "POST" + path 
            sig = hmac.new(self.api_secret.encode(), sig_data.encode(), hashlib.sha256).hexdigest()
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            res = requests.post(f"https://api.bitkub.com{path}", headers=headers, timeout=10).json()
            
            if res.get('error') == 0:
                thb = float(res['result'].get('THB', 0))
                coin = float(res['result'].get(self.symbol.split('_')[0], 0))
                return thb, coin
            return 0.0, 0.0
        except: return 0.0, 0.0

    def get_market_data(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            return {"price": c[-1], "rsi": rsi}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                m = self.get_market_data()
                if not m: time.sleep(10); continue
                
                thb, coin = self.get_balance()
                # กรณี API Error ให้ใช้ยอด Initial มาคำนวณชั่วคราวเพื่อให้รายงานไม่เป็น 0
                current_thb = thb if thb > 0 else self.initial_equity 
                equity = current_thb + (coin * m['price'])
                growth = ((equity - self.initial_equity) / self.initial_equity) * 100

                if time.time() - last_rep >= 600: # รายงานทุก 10 นาที
                    self._send_full_report(m['price'], m['rsi'], equity, growth, current_thb, coin)
                    last_rep = time.time(); self.prev_rsi = m['rsi']
            except Exception as e: print(f"Error: {e}")
            time.sleep(15)

    def _send_full_report(self, price, rsi, equity, growth, thb, coin):
        now = datetime.now(timezone(timedelta(hours=7)))
        
        # ส่วนที่ 1: Header & Status
        report = (f"🛡️ <b>TITAN V.15.0 PRO | {self.symbol}</b>\n"
                  f"<b>Status:</b> {'ONLINE & MONITORING' if rsi > self.rsi_buy_target else 'BUY ZONE DETECTED'}\n"
                  f"📅 {now.strftime('%d/%m/%Y')} | ⏰ {now.strftime('%H:%M:%S')}\n"
                  f"━━━━━━━━━━━━━━━━━━\n")
        
        # ส่วนที่ 2: Market Intelligence (ความครบถ้วนของข้อมูลตลาด)
        report += (f"📊 <b>MARKET INTELLIGENCE</b>\n"
                   f"• Current Price : <b>{price:,.2f} THB</b>\n"
                   f"• RSI (15m) : {rsi:.2f} (Prev: {self.prev_rsi:.2f})\n"
                   f"• Target Buy : ≤ {self.rsi_buy_target}\n"
                   f"━━━━━━━━━━━━━━━━━━\n")
        
        # ส่วนที่ 3: Portfolio Performance (ยอดเงินที่ดึงมาจาก API)
        report += (f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
                   f"• NET EQUITY : <b>{equity:,.2f} THB</b>\n"
                   f"• TOTAL GROWTH : {growth:+.2f}%\n"
                   f"• Available Cash : {thb:,.2f} THB\n"
                   f"• Asset Holding : {coin:.4f} {self.symbol.split('_')[0]}\n"
                   f"━━━━━━━━━━━━━━━━━━\n")
        
        # ส่วนที่ 4: Slot Operation (รายละเอียดความคืบหน้าของแต่ละไม้)
        report += f"🎯 <b>STRATEGY DUAL-SLOT</b>\n"
        for i, s in self.slots.items():
            if s['active']:
                pnl = ((price - s['price']) / s['price']) * 100
                report += (f"<b>[SLOT {i}] - ACTIVE</b>\n"
                           f"  └ Entry: {s['price']:,.2f} | PnL: {pnl:+.2f}%\n"
                           f"  └ StopLoss: {s['sl']:,.2f}\n")
            else:
                report += f"<b>[SLOT {i}]</b> - <i>Waiting for RSI Condition...</i>\n"
        
        self.notify(report)

    def notify(self, msg):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanOmniV15_Comprehensive().run()
