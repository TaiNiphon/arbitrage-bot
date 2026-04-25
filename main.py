import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMasterV17_2_Final_Fixed:
    def __init__(self):
        # --- 1. CONFIG ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. SETTINGS ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", "1800.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.max_slots = int(os.getenv("MAX_SLOTS", "3"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "600.0"))
        self.min_volume_thb = float(os.getenv("MIN_VOLUME_THB", "3000000.0")) 
        self.fee_rate = 0.0025

        # --- 3. PERSISTENT STATE (จุดสำคัญ: เก็บค่าแบบไม่ล้างทิ้ง) ---
        self.positions = {}                
        self.scan_storage = {} # เก็บค่า RSI รายเหรียญแบบถาวร
        self.latest_top_list = [] 
        self.market_stats = {"total_qualified": 0, "bullish_pct": 0, "btc_status": "N/A"}
        
        self._init_db()                    
        self._sync_positions()
        self.notify(f"<b>💠 TITAN V.17.2 | RE-FIXED</b>\n<i>Status: Memory Persist Active</i>")

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS bot_positions_v17 (symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)")
                    cur.execute("CREATE TABLE IF NOT EXISTS trade_log_v17 (id SERIAL PRIMARY KEY, symbol TEXT, side TEXT, price FLOAT, pnl_pct FLOAT, pnl_thb FLOAT, timestamp TIMESTAMP)")
        except: pass

    def _sync_positions(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol, avg_price, total_units, dynamic_sl, max_pnl FROM bot_positions_v17")
                    for r in cur.fetchall(): self.positions[r[0]] = {"price": r[1], "units": r[2], "sl": r[3], "max_pnl": r[4]}
        except: pass

    def get_indicators(self, symbol):
        try:
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-(15*60*100)}&to={int(time.time())}"
            res = requests.get(url, timeout=10).json()
            if not res or 'c' not in res or len(res['c']) < 30: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c)
            g, l = np.where(diff>0, diff, 0), np.where(diff<0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(l[-14:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:],float)-np.array(res['l'][1:],float), np.maximum(abs(np.array(res['h'][1:],float)-c[:-1]), abs(np.array(res['l'][1:],float)-c[:-1])))
            return {'price': c[-1], 'rsi': rsi, 'atr': np.mean(tr[-14:]), 'trend': 1 if c[-1] > np.mean(c[-20:]) else 0}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                self.market_stats['total_qualified'] = len(qualified)
                
                # ตรวจสอบ BTC
                btc = self.get_indicators("BTC_THB")
                btc_safe = btc['trend'] == 1 if btc else False
                self.market_stats['btc_status'] = "🟢 OK" if btc_safe else "⚠️ WEAK"

                # SCAN LOOP
                bullish_count = 0
                for sym in qualified:
                    ind = self.get_indicators(sym)
                    if ind:
                        if ind['trend'] == 1: bullish_count += 1
                        # เก็บค่าลง Storage ทันที (ไม่ Reset)
                        self.scan_storage[sym] = {"sym": sym, "rsi": round(ind['rsi'], 2), "price": ind['price']}
                        
                        # อัปเดตลิสต์อันดับ RSI สำหรับรายงาน (กรองเฉพาะที่ไม่ได้ถืออยู่)
                        wait_list = [v for k, v in self.scan_storage.items() if k not in self.positions]
                        self.latest_top_list = sorted(wait_list, key=lambda x: x['rsi'])[:5]

                        # ลอจิกการซื้อ
                        if sym not in self.positions and btc_safe and len(self.positions) < self.max_slots and ind['rsi'] <= self.rsi_buy_target:
                            # ... [Execute BUY - ข้ามเพื่อความสั้นแต่ในไฟล์จริงมีครบครับ]
                            pass
                    time.sleep(0.4)

                self.market_stats['bullish_pct'] = (bullish_count/len(qualified)*100) if qualified else 0
                
                if time.time() - last_rep >= 600:
                    self._report_full(self.get_wallet())
                    last_rep = time.time()
            except: time.sleep(10)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_val, slot_details = 0, ""
        current_pos = list(self.positions.keys())

        for i in range(1, self.max_slots + 1):
            if i <= len(current_pos):
                sym = current_pos[i-1]
                pos = self.positions[sym]
                ind = self.get_indicators(sym)
                p = ind['price'] if ind else pos['price']
                val = (pos['units'] * p) * (1 - self.fee_rate)
                total_val += val
                pnl = ((val - ((pos['price']*pos['units'])/(1-self.fee_rate)))/((pos['price']*pos['units'])/(1-self.fee_rate)))*100
                slot_details += f"🟢 <b>SLOT {i} | {sym.split('_')[1]}</b>: {pnl:+.2f}% (Trailing...)\n"
            else:
                s_idx = i - len(current_pos) - 1
                if s_idx < len(self.latest_top_list):
                    target = self.latest_top_list[s_idx]
                    rsi_v, name = target['rsi'], target['sym'].split('_')[1]
                else:
                    rsi_v, name = 50.0, "Wait.."

                # Visual Bar
                if rsi_v <= 35: bar = "▪️" * max(1, int((35-rsi_v)/2)) + "▫️" * 5
                else: 
                    p = max(0, min(4, int((rsi_v-35)/35*5)))
                    bar = "▫️"*p + "🔹" + "▫️"*(4-p)
                slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [{bar}] RSI {rsi_v:.1f} ({name})\n"

        equity = thb + total_val
        msg = f"💠 <b>TITAN V.17.2 | FINAL MASTER</b>\n━━━━━━━━━━━━━━━━━━━━\n🌍 <b>SENTIMENT</b>: {'🟥' if self.market_stats['bullish_pct']<50 else '🟦'} {self.market_stats['bullish_pct']:.0f}%\n• BTC: {self.market_stats['btc_status']} | Assets: {self.market_stats['total_qualified']}\n━━━━━━━━━━━━━━━━━━━━\n💰 <b>NET EQUITY</b>: {equity:,.2f} THB\n━━━━━━━━━━━━━━━━━━━━\n🎯 <b>SLOT EXECUTION</b>\n{slot_details}\n━━━━━━━━━━━━━━━━━━━━\n📅 {now.strftime('%H:%M:%S')}"
        self.notify(msg)

    def get_wallet(self):
        try:
            ts = str(int(time.time()*1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            return float(res['result'].get('THB', 0)) if res.get('error')==0 else 0.0
        except: return 0.0

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanMasterV17_2_Final_Fixed().run()
