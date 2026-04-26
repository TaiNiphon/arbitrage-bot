import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanOmniV15_Pro:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2578")).replace(',', ''))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "30.0"))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "1.0"))
        self.daily_drawdown_limit = 5.0
        self.prev_rsi = 0.0
        self.error_count = 0 # ตัวนับ Error กันค้าง

        self.slots = {1: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "max_pnl": 0.0}, 
                      2: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "max_pnl": 0.0}}
        
        self._init_db_v15()
        self._sync_slots_from_db()
        # ส่งสัญญาณทันทีที่เริ่มรันสำเร็จ
        self.notify("<b>🛡️ TITAN V.15.2 | STANDBY</b>\n<i>Engine: Anti-Freeze System Active</i>")

    def _init_db_v15(self):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v15 (
                slot_id INTEGER PRIMARY KEY, last_action TEXT, avg_price FLOAT, 
                total_units FLOAT, dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"DB Init Error: {e}")

    def _sync_slots_from_db(self):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute("SELECT slot_id, avg_price, total_units, dynamic_sl, max_pnl FROM bot_state_v15")
            for row in cur.fetchall():
                if row[2] > 0:
                    self.slots[row[0]] = {"active": True, "price": row[1], "units": row[2], "sl": row[3], "max_pnl": row[4]}
            cur.close(); conn.close()
        except: pass

    def get_balance(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            if res.get('error') == 0:
                coin = self.symbol.split('_')[0]
                return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        except: pass
        return 0.0, 0.0

    def get_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}", timeout=10).json()
            if 'c' not in res or not res['c']:
                self.error_count += 1
                return None
            
            self.error_count = 0 # Reset ถ้าข้อมูลมาปกติ
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"price": c[-1], "rsi": rsi, "atr": np.mean(tr[-14:])}
        except:
            self.error_count += 1
            return None

    def run(self):
        last_rep = 0
        while True:
            try:
                # 1. เช็ค Error สะสม
                if self.error_count >= 5:
                    self.notify("⚠️ <b>SYSTEM ALERT</b>\nBitkub API ไม่ส่งข้อมูลราคา (Error: 'c')\n<i>บอทกำลังพยายามเชื่อมต่อใหม่...</i>")
                    self.error_count = 0 # แจ้งเตือนแล้วเริ่มนับใหม่

                # 2. ดึงข้อมูล
                d = self.get_indicators()
                thb, coin = self.get_balance()
                
                if d:
                    p, rsi, atr = d['price'], d['rsi'], d['atr']
                    equity = thb + (coin * p)
                    growth = ((equity - self.initial_equity) / self.initial_equity) * 100

                    # --- BUY LOGIC ---
                    active_slots = [i for i in self.slots if self.slots[i]["active"]]
                    if len(active_slots) < 2 and rsi <= self.rsi_buy_target and growth > -self.daily_drawdown_limit:
                        s_id = 1 if 1 not in active_slots else 2
                        buy_amt = thb / (2 - len(active_slots))
                        if buy_amt >= 10:
                            self.notify(f"🎯 <b>RSI BUY SIGNAL</b>\nAttempting Slot {s_id}...")
                            # โค้ดส่งคำสั่งซื้อ (ข้ามเพื่อความกระชับ)
                            # ... (เหมือนเดิม) ...

                    # --- SELL LOGIC --- (ข้ามเพื่อความกระชับ)
                    # ... (เหมือนเดิม) ...

                    # 3. ส่ง Report (ย้ายออกมาให้ทำงานได้ชัวร์ขึ้น)
                    if time.time() - last_rep >= 600:
                        self._report(p, rsi, equity, growth, thb, coin)
                        last_rep = time.time(); self.prev_rsi = rsi
                else:
                    # กรณี Indicator ไม่มา แต่ถึงเวลา Report ก็ต้องส่งสถานะปัจจุบัน
                    if time.time() - last_rep >= 600:
                        self.notify(f"📊 <b>STATUS UPDATE</b>\nMarket API: Down ❌\nCash: {thb:,.2f} THB\nAssets: {coin:.4f}")
                        last_rep = time.time()

            except Exception as e:
                print(f"Global Error: {e}")
            
            time.sleep(20) # เพิ่มเวลาพักเพื่อลดความร้อนแรงของ API

    def _report(self, p, rsi, equity, growth, thb, coin):
        now = datetime.now(timezone(timedelta(hours=7)))
        msg = (f"🛡️ <b>TITAN V.15.2 | {self.symbol}</b>\nStatus: ONLINE\nTime: {now.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n• Price : {p:,.2f}\n• RSI : {rsi:.2f}\n• Equity : {equity:,.2f}\n• Growth : {growth:+.2f}%")
        self.notify(msg)

    def notify(self, m):
        try:
            requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanOmniV15_Pro().run()
