import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_Survival:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        self.initial_equity = 10000.28 
        self.buy_rsi = 28.0      
        self.target_tp = 3.0 # กำไร 3% ขายทันที
        self.slots = {1: {"status": "FREE"}, 2: {"status": "FREE"}}

        self._init_db()
        self._load_state()
        self.notify("🏛️ <b>TITAN: SURVIVAL MODE ONLINE</b>\n<i>Status: Execution Priority - Zero Delay</i>")

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS bot_state_v18 (slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, max_p FLOAT, status TEXT)")
                    cur.execute("CREATE TABLE IF NOT EXISTS trade_history (id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, price FLOAT, units FLOAT, net_pnl_thb FLOAT)")
                    conn.commit()
        except: pass

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, max_p, status FROM bot_state_v18")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {"status": r[5], "price": r[1], "units": r[2], "sl": r[3], "max_p": r[4]}
        except: pass

    def send_luxury_dashboard(self, dx, db_btc, thb, coin):
        """คืนค่าหน้าตา Luxury 100% ตามภาพ 7971.jpg"""
        p = dx['p']; rsi_val = dx['r14']; equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = datetime.now(timezone(timedelta(hours=7))).strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        
        msg = f"🏛️ <b>TITAN V.18.99: REPORT</b>\n"
        msg += f"📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : <b>{p:,.4f} THB</b>\n"
        msg += f"📊 State : {'🚨 EXTREME PANIC (BUY!)' if rsi_val <= 28 else '↔️ SIDEWAY'}\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
        msg += f"📉 RSI 14: {rsi_val:.2f} | RSI 200: {dx['r200']:.2f}\n"
        msg += f"---------------------------------\n"
        msg += f"🛡️ <b>BTC-GUARD STATUS</b>\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if db_btc['p'] > db_btc['ema'] else '🌑 BEARISH'}\n"
        msg += f"💰 BTC P.: {db_btc['p']:,.0f} THB\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f}\n"
        msg += f"📈 Total Growth: <b>{growth:+.2f}%</b>\n"
        msg += f"---------------------------------\n"
        for i, s in self.slots.items():
            if s.get('status') == 'MATCHED':
                pnl = ((p * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                msg += f"🟢 <b>SLOT {i}: {s['units']:.4f} XRP ({pnl:+.2f}%)</b>\n"
                msg += f"🎯 TP: {s['price']*1.03:,.4f} | 🛡️ SL: {s['sl']:,.4f}\n\n"
            else:
                msg += f"⚪ <b>SLOT {i}: FREE (RSI ≤ 28.0)</b>\n\n"
        msg += f"🔍 <i>Verified & Locked</i>"
        self.notify(msg)

    def execute_trade(self, side, slot_id, price, amt_val, buy_p=0):
        # ยิงคำสั่งทันที ไม่ต้องรอเงื่อนไขอื่น
        path = f"/api/v3/market/place-{'bid' if side == 'buy' else 'ask'}"
        res = self.bt_auth("POST", path, {"sym":self.symbol.lower(), "amt":amt_val, "typ":"market"})
        if res and res.get('error') == 0:
            time.sleep(1)
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    if side == 'buy':
                        cur.execute("INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, status) VALUES (%s,%s,%s,%s,%s,'MATCHED') ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, status='MATCHED'", (slot_id, price, (amt_val/price), (price*0.95), price))
                    else:
                        pnl = (price * amt_val * 0.9975) - (buy_p * amt_val * 1.0025)
                        cur.execute("INSERT INTO trade_history (side, price, units, net_pnl_thb) VALUES ('SELL', %s, %s, %s)", (price, amt_val, pnl))
                        cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                    conn.commit()
            self._load_state()
            self.notify(f"⚡ <b>{side.upper()} SUCCESS</b>: {price} THB")
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
                    if datetime.now().hour != last_h:
                        self.send_luxury_dashboard(dx, db_btc, thb, coin)
                        last_h = datetime.now().hour
                    
                    # Logic ขาย (เช็คกำไร)
                    for i, s in self.slots.items():
                        if s.get('status') == 'MATCHED':
                            profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                            if profit >= self.target_tp or dx['p'] <= s['sl']:
                                self.execute_trade('sell', i, dx['p'], s['units'], buy_p=s['price'])
                    
                    # Logic ซื้อ (บังคับยิงเมื่อ RSI ถึง)
                    m_count = sum(1 for s in self.slots.values() if s.get('status') == 'MATCHED')
                    if m_count < 2 and dx['r14'] <= self.buy_rsi and thb >= 10:
                        target = 1 if self.slots[1].get('status') != 'MATCHED' else 2
                        self.execute_trade('buy', target, dx['p'], int(thb * 0.98))
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

    def notify(self, msg):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={'chat_id': self.tg_chat_id, 'text': msg, 'parse_mode': 'HTML'})
        except: pass

if __name__ == "__main__":
    TitanV18_Survival().run()
