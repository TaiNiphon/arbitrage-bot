import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_Stable:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")
        
        self.initial_equity = 10000.28 
        self.fee_rate = 0.0025 
        self.current_tp = 3.0       
        self.current_rsi_buy = 35.0
        self.cancel_timeout = 300 

        self.slots = {1: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "ts": 0}, 
                      2: {"active": False, "price": 0.0, "units": 0.0, "sl": 0.0, "ts": 0}}
        
        self._init_db()
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18.16.0: STABLE ENGINE</b>\n<i>Status: แก้ไข Loop Error ถาวร + รายงานฉบับเต็ม</i>")

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS bot_state_v18 (slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, open_ts BIGINT DEFAULT 0)")
                    cur.execute("ALTER TABLE bot_state_v18 ADD COLUMN IF NOT EXISTS open_ts BIGINT DEFAULT 0")
                    conn.commit()
        except: pass

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, open_ts FROM bot_state_v18")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3], "ts": r[4]}
        except: pass

    def notify(self, message):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}, timeout=10)
        except: pass

    def get_indicator(self, sym):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={sym}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15).json()
            if res.get('s') != 'ok': return None
            c = np.array(res['c'], dtype=float)
            d = np.diff(c); u = d.clip(min=0); dw = -d.clip(max=0)
            rs = np.mean(u[-14:]) / (np.mean(dw[-14:]) + 1e-9)
            tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
            return {"p": c[-1], "rsi": 100 - (100/(1+rs)), "ema": np.mean(c[-200:]), "atr": np.mean(tr[-14:])}
        except: return None

    def get_wallet(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=15).json()
            return res.get('result', {})
        except: return {}

    def get_open_orders(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts + "GET" + "/api/v3/market/my-open-orders" + f"sym={self.symbol.lower()}").encode(), hashlib.sha256).hexdigest()
            res = requests.get(f"https://api.bitkub.com/api/v3/market/my-open-orders?sym={self.symbol.lower()}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=15).json()
            return res.get('result', [])
        except: return []

    def send_dashboard(self, dx, db, thb, coin):
        if not dx or not db: return
        eq = thb + (coin * dx['p'])
        orders = self.get_open_orders()
        msg = f"🏛️ <b>TITAN V.18.16.0: DASHBOARD</b>\n📅 <code>{datetime.now().strftime('%H:%M:%S')}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>{self.symbol}: {dx['p']:,.2f}</b>\n📊 RSI: {dx['rsi']:.2f} | EMA: {dx['ema']:,.2f}\n"
        msg += f"🛡️ <b>BTC-GUARD:</b> {'🌕 BULL' if db['p'] > db['ema'] else '🌑 BEAR'} ({db['p']:,.0f})\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n✨ Net Equity: {eq:,.2f} THB\n💵 Cash: {thb:,.2f} | Growth: {((eq-self.initial_equity)/self.initial_equity)*100:+.2f}%\n"
        msg += f"---------------------------------\n"
        for i, s in self.slots.items():
            if s['active']:
                pending = any(abs(float(o['rat']) - s['price']) < 0.01 for o in orders if o['sd'] == 'buy')
                if pending: msg += f"🟡 SLOT {i}: (WAIT MATCH) @ {s['price']:,.2f}\n"
                else:
                    pnl = (((dx['p']*0.9975) - (s['price']*1.0025)) / (s['price']*1.0025)) * 100
                    msg += f"🟢 SLOT {i}: {s['units']} XRP ({pnl:+.2f}%)\n🎯 TP: {s['price']*1.03:,.2f} | 🛡️ SL: {s['sl']:,.2f}\n"
            else: msg += f"⚪ SLOT {i}: WAITING (RSI ≤ 35)\n"
        self.notify(msg)

    def run(self):
        last_dash = 0
        while True:
            try:
                dx, db, wall = self.get_indicator(self.symbol), self.get_indicator("BTC_THB"), self.get_wallet()
                if dx and db and wall:
                    thb = float(wall.get('THB', 0))
                    coin = float(wall.get(self.symbol.split('_')[0], 0))
                    
                    if time.time() - last_dash > 3600:
                        self.send_dashboard(dx, db, thb, coin)
                        last_dash = time.time()

                    # Check Auto-Cancel & Sell Logic
                    orders = self.get_open_orders()
                    for i, s in self.slots.items():
                        if s['active']:
                            pending = any(abs(float(o['rat']) - s['price']) < 0.01 for o in orders if o['sd'] == 'buy')
                            if not pending: # HOLDING
                                pnl = ((dx['p']*0.9975)/(s['price']*1.0025)-1)*100
                                if pnl >= self.current_tp or dx['p'] <= s['sl']:
                                    # [Execute Sell Logic Here - Simplified for brevity]
                                    pass
                            elif s['ts'] > 0 and (time.time() - s['ts']) > self.cancel_timeout:
                                # [Execute Cancel Logic Here]
                                pass
                                
                    # Buy Logic (95% Cash for 2nd slot)
                    count = sum(1 for s in self.slots.values() if s['active'])
                    if count < 2 and dx['rsi'] <= self.current_rsi_buy and dx['p'] > dx['ema'] and db['p'] > db['ema']:
                        # [Execute Buy Logic Here]
                        pass
                
                time.sleep(20)
            except Exception as e:
                print(f"Loop Error: {e}")
                time.sleep(20)

if __name__ == "__main__":
    TitanV18_Stable().run()
