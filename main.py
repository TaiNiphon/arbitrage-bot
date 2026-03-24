import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timedelta, timezone

class TitanProMaxV12:
    def __init__(self):
        print("🛡️ Booting TITAN PRO MAX V.12.1.2 (Definitive Edition)...")
        # --- Config & Variables ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Professional Settings ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.0")) 
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "3.0"))
        self.rsi_buy_base = float(os.getenv("RSI_BUY_MAX", "32.0"))
        self.ema_dist_limit = float(os.getenv("EMA_DIST_LIMIT", "1.8"))
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "5"))
        self.report_interval = int(os.getenv("REPORT_INTERVAL", "600"))
        self.fee_pct = float(os.getenv("FEE_PCT", "0.25")) / 100

        # --- Tracking System (Clean State) ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        self.rsi_prev = None; self.rsi_memory = None 
        self.entry_rsi_val = 0.0; self.entry_time = None
        self.max_pnl_val = 0.0; self.entry_trend = "Unknown"

        self._init_db()
        self._load_state_db()

        # ดึงค่า Indicator ทันทีที่รัน เพื่อป้องกันค่า RSI หลอกในรายงานแรก
        d = self.calculate_indicators()
        if d:
            self.rsi_prev = d['rsi']
            self.rsi_memory = d['rsi']

        self.notify(f"<b>💎 TITAN PRO MAX V.12.1.2 Active</b>\nMode: Full-Professional | Risk: {self.risk_per_trade}%")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state (id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, entry_rsi FLOAT, entry_time TIMESTAMP, max_pnl FLOAT, entry_trend TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (id SERIAL PRIMARY KEY, time TIMESTAMP, side TEXT, price FLOAT, pnl_pct FLOAT, pnl_thb FLOAT, reason TEXT, entry_rsi FLOAT, exit_rsi FLOAT, max_pnl_during_trade FLOAT, hold_time_min FLOAT, market_trend TEXT)""")
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _save_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("DELETE FROM bot_state")
            cur.execute("INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_rsi_val, self.entry_time, self.max_pnl_val, self.entry_trend))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _load_state_db(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend FROM bot_state LIMIT 1")
            row = cur.fetchone()
            if row: self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_rsi_val, self.entry_time, self.max_pnl_val, self.entry_trend = row
            cur.close(); conn.close()
        except: pass

    def calculate_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            ema20 = self._ema(c, 20); ema50 = self._ema(c, 50)
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema20": ema20, "ema50": ema50, "rsi": rsi, "atr": atr}
        except: return None

    def _ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def _report(self, price, ema20, ema50, rsi, pnl, thb, coin, status="MONITORING"):
        coin_val = coin * price; total_equity = thb + coin_val
        growth_pct = ((total_equity - self.initial_equity) / self.initial_equity) * 100 if self.initial_equity > 0 else 0
        dist_ema = ((price - ema20) / ema20) * 100
        market_trend = "BULLISH 📈" if price > ema50 else "BEARISH 📉"
        trend_icon = "💎" if price > ema50 else "⚠️"
        
        hook_icon = "🟢" if rsi > (self.rsi_memory or 0) else "🔴"
        prev_rsi_val = self.rsi_memory if self.rsi_memory is not None else rsi

        now_dt = datetime.now(timezone(timedelta(hours=7)))
        date_str = now_dt.strftime('%d/%m/%Y')
        time_str = now_dt.strftime('%H:%M:%S')
        div = "━" * 18

        msg = (
            f"<b>{trend_icon} TITAN PRO MAX V.12 | {self.symbol}</b>\n"
            f"<code>Status : {status}</code>\n"
            f"<code>Date   : {date_str}</code>\n"
            f"<code>Time   : {time_str}</code>\n{div}\n"
            f"<b>📊 MARKET INTELLIGENCE</b>\n"
            f"• Price    : <b>{price:,.2f}</b> THB\n"
            f"• Trend    : <b>{market_trend}</b>\n"
            f"• RSI (14) : <code>{rsi:.2f}</code> {hook_icon} (Prev:{prev_rsi_val:.2f})\n"
            f"• EMA Dist : <code>{dist_ema:+.2f}%</code>\n{div}\n"
            f"<b>🏦 PORTFOLIO ANALYSIS</b>\n"
            f"• <b>EQUITY</b>  : <b>{total_equity:,.2f} THB</b>\n"
            f"• <b>GROWTH</b>  : <b>{growth_pct:+.2f}%</b>\n"
            f"• Cash    : {thb:,.2f} | Assets: {coin:.4f}\n{div}\n"
        )
        if self.last_action == "buy" and coin > 0:
            sl_dist = ((price - self.dynamic_sl) / self.dynamic_sl * 100) if self.dynamic_sl > 0 else 0
            msg += f"<b>🛡️ RISK MGMT</b>\n• P/L Net : <b>{pnl:+.2f}%</b>\n• StopLoss: {self.dynamic_sl:,.2f} (<code>{sl_dist:+.2f}%</code>)"
        else:
            risk_thb = total_equity * (self.risk_per_trade/100)
            msg += f"<b>🛡️ STRATEGY</b>\n• Risk/Trade: {self.risk_per_trade}% (<code>~{risk_thb:,.2f}</code>)\n• Status: <i>Searching Entry...</i>"
        self.notify(msg)

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.calculate_indicators()
                if not d: time.sleep(10); continue
                p, ema20, ema50, rsi, atr = d['price'], d['ema20'], d['ema50'], d['rsi'], d['atr']

                buy_fee = 1 + self.fee_pct; sell_fee = 1 - self.fee_pct
                pnl = (((p * sell_fee) - (self.avg_price * buy_fee)) / (self.avg_price * buy_fee) * 100) if self.avg_price > 0 else 0
                thb, coin = self.get_balance()

                # --- Smart RSI Memory Update ---
                if self.rsi_prev is None:
                    self.rsi_prev = rsi; self.rsi_memory = rsi
                elif abs(rsi - self.rsi_prev) > 0.001:
                    self.rsi_memory = self.rsi_prev
                    self.rsi_prev = rsi

                # --- BUY LOGIC ---
                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 300:
                    active_rsi_limit = self.rsi_buy_base if p > ema50 else (self.rsi_buy_base - 5.0)
                    dist_ema = ((p - ema20) / ema20) * 100

                    if rsi < active_rsi_limit and rsi > (self.rsi_memory or 0) and abs(dist_ema) < self.ema_dist_limit:
                        total_equity = thb + (coin * p)
                        risk_amt = (total_equity * (self.risk_per_trade / 100)) / (self.stop_loss_pct / 100)
                        buy_amt = min(thb * 0.98, risk_amt)

                        if buy_amt >= 10 and self.place_order("buy", buy_amt):
                            self.avg_price = p; self.total_units = buy_amt / p
                            self.last_action = "buy"; self.highest_price = p
                            self.dynamic_sl = p * (1 - (self.stop_loss_pct/100))
                            self.entry_rsi_val = rsi; self.entry_time = datetime.now(timezone(timedelta(hours=7)))
                            self.entry_trend = "Bullish" if p > ema50 else "Bearish"
                            self.max_pnl_val = 0.0; self._save_state_db()
                            self.notify(f"<b>🚀 ENTRY ({self.entry_trend}): {p:,.2f}</b>\nSize: {buy_amt:,.2f} THB | RSI: {rsi:.2f}")

                # --- SELL LOGIC ---
                elif self.last_action == "buy" and coin > 0:
                    self.max_pnl_val = max(self.max_pnl_val, pnl)
                    self.highest_price = max(self.highest_price, p)
                    trail_price = self.highest_price - (atr * 2.5)
                    
                    if pnl >= 1.2: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.0065)
                    self.dynamic_sl = max(self.dynamic_sl, trail_price)

                    reason = None
                    if pnl >= 10.0: reason = "Take Profit 💰"
                    elif pnl <= -self.stop_loss_pct: reason = "Stop Loss 🔴"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop 🛡️"

                    if reason:
                        if self.place_order("sell", coin):
                            profit_thb = (coin * p * sell_fee) - (self.total_units * self.avg_price * buy_fee)
                            self._log_trade(p, pnl, profit_thb, reason, rsi)
                            self.notify(f"<b>💰 EXIT: {p:,.2f}</b>\nReason: {reason}\nProfit: <b>{profit_thb:+.2f} THB</b>")
                            self.last_action = "sell"; self.last_sell_time = time.time(); self._save_state_db()

                if time.time() - last_rep >= self.report_interval:
                    self._report(p, ema20, ema50, rsi, pnl, thb, coin)
                    last_rep = time.time()

            except Exception as e: print(f"❌ Run Error: {e}")
            time.sleep(self.check_interval)

    def _log_trade(self, p, pnl, thb, reason, rsi):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            hold_min = (datetime.now(timezone(timedelta(hours=7))) - self.entry_time).total_seconds()/60 if self.entry_time else 0
            cur.execute("INSERT INTO trade_history (time, side, price, pnl_pct, pnl_thb, reason, entry_rsi, exit_rsi, max_pnl_during_trade, hold_time_min, market_trend) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (datetime.now(timezone(timedelta(hours=7))), "SELL", p, pnl, thb, reason, self.entry_rsi_val, rsi, self.max_pnl_val, hold_min, self.entry_trend))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def get_balance(self):
        try:
            res = self._request("POST", "/api/v3/market/wallet", private=True)
            if res.get('error') == 0: return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
        except: pass
        return 0.0, 0.0

    def place_order(self, side, amt):
        try:
            path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
            res = self._request("POST", path, payload={"sym": self.symbol.lower(), "amt": amt, "rat": 0, "typ": "market"}, private=True)
            return res.get('error') == 0
        except: return False

    def _request(self, method, path, payload=None, private=False):
        url = f"https://api.bitkub.com{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if private:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+method+path+(json.dumps(payload) if payload else "")).encode(), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=json.dumps(payload) if payload else "").json()

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanProMaxV12().run()
