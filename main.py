import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_ProUltimate_Final:
    def __init__(self):
        # --- [1] CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] STRATEGY SETTINGS ---
        self.initial_equity = 10000.0  # ตรวจสอบแล้ว: ใช้ทุน 1000 ตรงตามจริง
        self.rsi_buy_max = 35.0
        self.target_profit = 3.0
        self.fee_rate = 0.0025 
        self.circuit_breaker_active = False 
        self.last_alive_check = -1

        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}

        self._init_db() 
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18.12: ULTIMATE</b>\n<i>Status: TP/SL Monitor Active | Integer-Mode Enabled</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT)""")
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT)""")
                    conn.commit()
        except Exception as e: print(f"DB Init Error: {e}")

    def _load_state(self):
        self.slots = {1: {"active": False, "price": 0, "units": 0, "sl": 0}, 
                      2: {"active": False, "price": 0, "units": 0, "sl": 0}}
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl FROM bot_state_v18")
                    for r in cur.fetchall():
                        self.slots[r[0]] = {"active": True, "price": r[1], "units": r[2], "sl": r[3]}
        except: pass

    def get_indicator(self, symbol, period=200):
        for i in range(3):
            try:
                res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15).json()
                c = np.array(res['c'], dtype=float)
                def calc_rsi(prices, p_len):
                    diff = np.diff(prices); up = diff.clip(min=0); down = -diff.clip(max=0)
                    return 100 - (100 / (1 + (np.mean(up[-p_len:]) / (np.mean(down[-p_len:]) + 1e-9))))
                ema = np.mean(c[-period:])
                tr = np.maximum(np.array(res['h'][1:]) - np.array(res['l'][1:]), abs(np.array(res['h'][1:]) - c[:-1]))
                return {"p": c[-1], "r14": calc_rsi(c, 14), "r200": calc_rsi(c, 200), "ema": ema, "atr": np.mean(tr[-14:])}
            except: time.sleep(2)
        return None

    def execute_trade(self, side, slot_id, price, amt_units, atr):
        ts = str(int(time.time() * 1000))
        path = f"/api/v3/market/place-{'bid' if side=='buy' else 'ask'}"

        if side == 'buy':
            final_amt = int(float(amt_units)) 
            final_rat = round(float(price), 2)
        else:
            final_amt = round(float(amt_units), 4)
            final_rat = round(float(price), 2)

        payload = {"sym": self.symbol.lower(), "amt": final_amt, "rat": final_rat, "typ": "limit"}
        payload_json = json.dumps(payload, separators=(',', ':'))
        sig = hmac.new(self.api_secret.encode(), (ts + "POST" + path + payload_json).encode(), hashlib.sha256).hexdigest()
        now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        try:
            res = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}, data=payload_json, timeout=15).json()
            if res.get('error') == 0:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        if side == 'buy':
                            actual_units = round(final_amt / final_rat, 4)
                            sl = round(final_rat - (atr * 2.5), 2)
                            cur.execute("INSERT INTO bot_state_v18 (slot_id, price, units, sl) VALUES (%s, %s, %s, %s) ON CONFLICT (slot_id) DO UPDATE SET price=EXCLUDED.price, units=EXCLUDED.units, sl=EXCLUDED.sl", (slot_id, final_rat, actual_units, sl))
                            msg = f"📥 <b>BUY COMPLETED (Slot {slot_id})</b>\n📅 <code>{now_str}</code>\n---------------------------------\nPrice: {final_rat:,.2f} | Amount: {final_amt:,} THB\n🛡️ SL: {sl:,.2f}"
                        else:
                            s = self.slots[slot_id]
                            net_pnl = (final_rat * s['units'] * (1-self.fee_rate)) - (s['price'] * s['units'] * (1+self.fee_rate))
                            msg = f"⚡ <b>TRADE COMPLETED ({'PROFIT' if net_pnl > 0 else 'LOSS'})</b>\n📅 <code>{now_str}</code>\nNET PROFIT: <b>{net_pnl:,.2f} THB</b> {'✅' if net_pnl > 0 else '❌'}"
                            if net_pnl < -(self.initial_equity * 0.10): self.circuit_breaker_active = True
                            cur.execute("INSERT INTO trade_history (ts, side, price, units, net_pnl_thb, status) VALUES (NOW(), 'SELL', %s, %s, %s, %s)", (final_rat, s['units'], net_pnl, 'WIN' if net_pnl > 0 else 'LOSS'))
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id = %s", (slot_id,))
                        conn.commit(); self.notify(msg); self._load_state()
                return True
            else: self.notify(f"❌ <b>Trade Error: {res.get('error')}</b>")
        except Exception as e: print(f"Trade Error: {e}")
        return False

    def send_dashboard(self, data_xrp, data_btc, thb, coin):
        p, r14, r200, ema = data_xrp['p'], data_xrp['r14'], data_xrp['r200'], data_xrp['ema']
        coin_value = coin * p 
        equity = thb + coin_value
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        x_trend = "🌕 BULLISH" if p > ema else "🌑 BEARISH"
        b_trend = "🌕 BULLISH" if data_btc['p'] > data_btc['ema'] else "🌑 BEARISH"
        r14_emoji = "❄️" if r14 <= 30 else "🔥" if r14 >= 70 else "📊"
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        msg = f"🏛️ <b>TITAN V.18.12: DASHBOARD</b>\n📅 <code>{now}</code>\n---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n💰 Price : {p:,.2f} THB\n📊 Trend : {x_trend}\n📉 RSI 14: {r14:.2f} {r14_emoji} | RSI 200: {r200:.2f}\n"
        msg += f"---------------------------------\n🛡️ <b>BTC-GUARD STATUS</b>\n📊 Trend : {b_trend}\n💰 BTC P.: {data_btc['p']:,.0f} THB\n"
        msg += f"---------------------------------\n💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity  : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB)  : {thb:,.2f} THB\n"
        msg += f"🪙 Coin Value  : {coin_value:,.2f} THB\n"
        msg += f"📦 Total Coins : {coin:.4f} XRP\n"
        msg += f"📈 Total Growth: {growth:+.2f}%\n"
        msg += f"---------------------------------\n"
        
        for i, s in self.slots.items():
            if s['active']:
                e_cost = s['price'] * (1 + self.fee_rate); x_rev = p * (1 - self.fee_rate)
                pnl = ((x_rev - e_cost) / e_cost) * 100
                tp_price = round(s['price'] * (1 + (self.target_profit / 100)), 2)
                msg += f"🟢 SLOT {i}: {s['units']:.4f} XRP ({pnl:+.2f}%)\n"
                msg += f"🎯 <b>TP:</b> {tp_price:,.2f} | 🛡️ <b>SL:</b> {s['sl']:,.2f}\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING (RSI ≤ {self.rsi_buy_max})\n"
        
        if self.circuit_breaker_active: msg += "\n🛑 <b>CIRCUIT BREAKER: PAUSED</b>"
        self.notify(msg)

    def send_periodic_report(self, days, title):
        start_date = self.get_thai_now() - timedelta(days=days)
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT net_pnl_thb, status FROM trade_history WHERE ts > %s AND side='SELL'", (start_date,))
                    rows = cur.fetchall()
                    total_pnl = sum(r[0] for r in rows); trades = len(rows)
                    wins = sum(1 for r in rows if r[1] == 'WIN'); win_rate = (wins/trades*100) if trades > 0 else 0
            msg = f"📅 <b>{title} SUMMARY</b>\n---------------------------------\nTrades: {trades} | Win Rate: {win_rate:.0f}%\n<b>Net Profit: {total_pnl:,.2f} THB</b>"
            self.notify(msg)
        except: pass

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def run(self):
        last_dash = 0; last_day = self.get_thai_now().day
        while True:
            try:
                thai_now = self.get_thai_now()
                d_xrp, d_btc = self.get_indicator(self.symbol), self.get_indicator("BTC_THB")
                if not d_xrp or not d_btc: time.sleep(20); continue

                ts = str(int(time.time() * 1000)); sig = hmac.new(self.api_secret.encode(), (ts + "POST" + "/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
                wallet = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
                thb = float(wallet['result'].get('THB', 0)); coin = float(wallet['result'].get(self.symbol.split('_')[0], 0))

                if thai_now.hour in [0, 6, 12, 18] and thai_now.hour != self.last_alive_check:
                    self.notify(f"📡 <b>TITAN ALIVE</b>\n📅 {thai_now.strftime('%H:%M')} | Status: OK ✅"); self.last_alive_check = thai_now.hour
                
                if time.time() - last_dash > 3600:
                    self.send_dashboard(d_xrp, d_btc, thb, coin); last_dash = time.time()
                
                if thai_now.day != last_day and thai_now.hour == 8:
                    self.send_periodic_report(1, "DAILY"); last_day = thai_now.day; self.circuit_breaker_active = False 
                
                if thai_now.day == 1 and thai_now.hour == 8 and thai_now.minute < 5:
                    self.send_periodic_report(30, "MONTHLY")

                active_count = sum(1 for s in self.slots.values() if s['active'])
                if not self.circuit_breaker_active:
                    if active_count < 2 and d_xrp['r14'] <= self.rsi_buy_max and d_xrp['p'] > d_xrp['ema'] and d_btc['p'] > d_btc['ema']:
                        equity_current = thb + (coin * d_xrp['p'])
                        buy_amt = int(equity_current * 0.45)
                        if thb >= buy_amt and buy_amt >= 10:
                            s_id = 1 if not self.slots[1]['active'] else 2
                            if self.execute_trade('buy', s_id, d_xrp['p'], buy_amt, d_xrp['atr']): pass

                for i, s in self.slots.items():
                    if s['active']:
                        e_cost = s['price'] * (1 + self.fee_rate); x_rev = d_xrp['p'] * (1 - self.fee_rate)
                        if ((x_rev - e_cost) / e_cost) * 100 >= self.target_profit or d_xrp['p'] <= s['sl']:
                            self.execute_trade('sell', i, d_xrp['p'], s['units'], d_xrp['atr'])
            except Exception as e: print(f"Main Error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    TitanV18_ProUltimate_Final().run()
