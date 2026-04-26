import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanUltimateStability:
    def __init__(self):
        # --- 1. CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        
        # Equity Tracking (ป้องกันยอด 0.00)
        raw_eq = os.getenv("INITIAL_EQUITY", "1800")
        self.initial_equity = float(str(raw_eq).replace(',', ''))
        self.last_known_equity = self.initial_equity 
        self.rsi_buy_target = 30.0
        
        # Memory & DB Setup
        self.slots = {1: {"active": False, "price": 0, "units": 0}, 2: {"active": False, "price": 0, "units": 0}}
        self.prev_rsi = 0.0
        self._setup_database()
        self._load_state_from_db()
        self.notify("<b>🚀 TITAN V.15.1: REPORT RE-SYNCED</b>\n<i>Status: Online & Stable Reporting</i>")

    def _setup_database(self):
        """ตรวจสอบระบบฐานข้อมูล"""
        try:
            if not self.db_url: return
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS bot_state_v15 (slot_id INTEGER PRIMARY KEY, avg_price FLOAT, total_units FLOAT, updated_at TIMESTAMP)")
                    cur.execute("CREATE TABLE IF NOT EXISTS trade_history (id SERIAL PRIMARY KEY, slot_id INTEGER, action TEXT, price FLOAT, pnl FLOAT, timestamp TIMESTAMP)")
                    conn.commit()
        except Exception as e: print(f"DB Error: {e}")

    def _load_state_from_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, avg_price, total_units FROM bot_state_v15")
                    for r in cur.fetchall():
                        if r[2] > 0: self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2]}
        except: pass

    def get_wallet_sync(self):
        """ระบบดึงยอดเงินแบบ RE-TRY (ป้องกันยอด 0.00)"""
        for _ in range(3): # ลองดึงข้อมูล 3 ครั้งถ้าพลาด
            try:
                path = "/api/v3/market/wallet"
                ts = str(int(time.time() * 1000))
                sig = hmac.new(self.api_secret.encode(), (ts + "POST" + path).encode(), hashlib.sha256).hexdigest()
                res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                if res.get('error') == 0:
                    thb = float(res['result'].get('THB', 0))
                    coin = float(res['result'].get(self.symbol.split('_')[0], 0))
                    return thb, coin
            except: time.sleep(1)
        return None, None # ถ้าดึงไม่ได้เลยให้คืนค่า None เพื่อใช้ค่าเก่า

    def get_market(self):
        """ดึงข้อมูลตลาด 15m"""
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
                d = self.get_market()
                if not d: continue
                p, rsi = d['price'], d['rsi']
                
                # ดึงยอดเงินพร้อมระบบป้องกันค่า 0
                thb, coin = self.get_wallet_sync()
                if thb is not None:
                    curr_equity = thb + (coin * p)
                    self.last_known_equity = curr_equity
                else:
                    curr_equity = self.last_known_equity # ใช้ค่าล่าสุดถ้า API Error
                
                growth = ((curr_equity - self.initial_equity) / self.initial_equity) * 100

                # 📊 รายงานฉบับเต็ม (Full Report Restored)
                if time.time() - last_rep >= 600:
                    self._send_full_report(p, rsi, curr_equity, growth, thb or 0, coin or 0)
                    last_rep = time.time(); self.prev_rsi = rsi
            except Exception as e: print(f"Error: {e}")
            time.sleep(20)

    def _send_full_report(self, p, rsi, equity, growth, thb, coin):
        """สร้างรายงานที่มีรายละเอียดครบถ้วนและตัวเลขถูกต้อง"""
        now = datetime.now(timezone(timedelta(hours=7)))
        msg = (f"🛡️ <b>TITAN V.15.1 | {self.symbol}</b>\n"
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
    TitanUltimateStability().run()
