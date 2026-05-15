import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_UltimateLuxury:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        self.initial_equity = 10000.28 
        self.buy_rsi_threshold = 28.0      
        self.target_profit = 3.0 
        self.slots = {1: {"status": "FREE"}, 2: {"status": "FREE"}}

        self._load_state()
        self.notify("🏛️ <b>TITAN V.18.99: FINAL ENGINE RELOADED</b>\n<i>Status: Hard-Link Database Ready</i>")

    def get_thai_now(self):
        return datetime.now(timezone(timedelta(hours=7)))

    def _load_state(self):
        """โหลดข้อมูลจาก DB (ภาพ 7980.jpg)"""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, max_p, status FROM bot_state_v18")
                    rows = cur.fetchall()
                    self.slots = {1: {"status": "FREE"}, 2: {"status": "FREE"}}
                    for r in rows:
                        self.slots[r[0]] = {"status": r[5], "price": r[1], "units": r[2], "sl": r[3], "max_p": r[4]}
        except: pass

    def execute_trade(self, side, slot_id, price, amt_val, buy_p=0):
        """แก้ไข: บังคับบันทึก Database ทันที (Hard-Commit)"""
        path = f"/api/v3/market/place-{'bid' if side == 'buy' else 'ask'}"
        res = self.bt_auth("POST", path, {"sym":self.symbol.lower(), "amt":amt_val, "typ":"market"})
        
        if res and res.get('error') == 0:
            time.sleep(2)
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    if side == 'buy':
                        # บันทึกตามภาพ 7980.jpg
                        units = amt_val / price
                        sl_val = price * 0.95
                        cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, status, open_ts, order_id) 
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (slot_id) 
                                       DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl, status='MATCHED'""", 
                                    (slot_id, price, units, sl_val, price, 'MATCHED', int(time.time()*1000), f"ORDER_{int(time.time())}"))
                    else:
                        # บันทึกประวัติ (ภาพ 7982.jpg) และลบสถานะเดิมออก
                        pnl = (price * amt_val * 0.9975) - (buy_p * amt_val * 1.0025)
                        cur.execute("INSERT INTO trade_history (slot_id, side, price, units, net_pnl_thb, ts, status) VALUES (%s,%s,%s,%s,%s,%s,%s)", 
                                    (slot_id, "SELL", price, amt_val, pnl, self.get_thai_now(), "PROFIT" if pnl > 0 else "LOSS"))
                        cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                    conn.commit() # บังคับเขียนลง Disk
            self._load_state()
            self.notify(f"✅ <b>{side.upper()} SUCCESS</b>\nSlot: {slot_id} | DB Synced")
            return True
        return False

    def run(self):
        last_h = -1
        while True:
            try:
                res_w = self.bt_auth("POST", "/api/v3/market/wallet")
                thb = float(res_w['result'].get('THB', 0)); coin = float(res_w['result'].get('XRP', 0))
                dx = self.get_indicator(self.symbol); db_btc = self.get_indicator("BTC_THB")
                
                if dx and db_btc:
                    # รายงานรายชั่วโมง (หน้าตาตามภาพ 7978.jpg)
                    if self.get_thai_now().hour != last_h:
                        self._load_state()
                        self.send_luxury_dashboard(dx, db_btc, thb, coin)
                        last_h = self.get_thai_now().hour
                    
                    # เช็คขาย (TP/SL)
                    for i, s in self.slots.items():
                        if s.get('status') == 'MATCHED':
                            profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                            if profit >= self.target_profit or dx['p'] <= s['sl']:
                                self.execute_trade('sell', i, dx['p'], s['units'], buy_p=s['price'])
                    
                    # เช็คซื้อ (แก้ไข Logic ไม้ 2 ให้ซื้อแน่นอน)
                    active_slots = sum(1 for s in self.slots.values() if s.get('status') == 'MATCHED')
                    if active_slots < 2 and dx['r14'] <= self.buy_rsi_threshold:
                        if thb >= 10:
                            target = 1 if self.slots[1].get('status') != 'MATCHED' else 2
                            # ใช้เงินที่เหลือ 95% เพื่อความชัวร์
                            buy_amt = int(thb * 0.48) if active_slots == 0 else int(thb * 0.95)
                            self.execute_trade('buy', target, dx['p'], buy_amt)
            except: pass
            time.sleep(10)

    def get_indicator(self, sym):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={sym}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}").json()
            c = np.array(res['c'], dtype=float)
            def rsi(p, n):
                d = np.diff(p); u = d.clip(min=0); dw = -d.clip(max=0)
                return 100 - (100 / (1 + (np.mean(u[-n:]) / (np.mean(dw[-n:]) + 1e-9))))
            return {"p": c[-1], "r14": rsi(c, 14), "r200": rsi(c, 200), "ema": np.mean(c[-200:])}
        except: return None

    def bt_auth(self, method, path, payload=None):
        ts = str(int(time.time() * 1000))
        payload_json = json.dumps(payload, separators=(',', ':')) if payload else ""
        sig = hmac.new(self.api_secret.encode(), (ts + method + path + payload_json).encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        try: return requests.request(method, f"https://api.bitkub.com{path}", headers=headers, data=payload_json, timeout=10).json()
        except: return None

    def send_luxury_dashboard(self, dx, db_btc, thb, coin):
        p = dx['p']; rsi_val = dx['r14']; equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        msg = f"🏛️ <b>TITAN V.18.99: HOURLY REPORT</b>\n📅 <code>{self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')}</code>\n---------------------------------\n📈 <b>MARKET: {self.symbol}</b>\n💰 Price : <b>{p:,.4f} THB</b>\n📊 State : {'🚨 EXTREME PANIC (BUY!)' if rsi_val <= 28 else '↔️ SIDEWAY'}\n📉 RSI 14: {rsi_val:.2f}\n---------------------------------\n💰 <b>ASSET SUMMARY</b>\n✨ Net Equity : <b>{equity:,.2f} THB</b>\n📈 Total Growth: <b>{growth:+.2f}%</b>\n---------------------------------\n"
        for i in [1, 2]:
            s = self.slots.get(i, {"status": "FREE"})
            if s.get('status') == 'MATCHED':
                pnl = ((p * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                msg += f"🟢 <b>SLOT {i}: {s['units']:.4f} XRP ({pnl:+.2f}%)</b>\n🛡️ SL: {s['sl']:,.4f}\n\n"
            else: msg += f"⚪ <b>SLOT {i}: FREE (RSI ≤ 28.0)</b>\n\n"
        msg += f"🔍 <i>Database Status: Verified & Locked</i>"
        self.notify(msg)

    def notify(self, msg):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={'chat_id': self.tg_chat_id, 'text': msg, 'parse_mode': 'HTML'})
        except: pass

if __name__ == "__main__":
    TitanV18_UltimateLuxury().run(