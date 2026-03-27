import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta
from decimal import Decimal

class TitanOmniV14_Pro:
    def __init__(self):
        # --- Config (ดึงจาก Railway Variables เดิม) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- Strategy Settings ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "2000")).replace(',', ''))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.0"))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "3.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "30.0"))
        self.fee_pct = 0.0025 

        # --- Safety System ---
        self.daily_drawdown_limit = 5.0
        self.daily_pnl = 0.0
        self.last_pnl_reset = datetime.now(timezone(timedelta(hours=7))).date()
        self.is_bot_active = True 

        # --- Tracking State (สอดคล้องกับ DB คอลัมน์ใหม่ของคุณ) ---
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.rsi_prev = None; self.rsi_memory = None 
        self.entry_rsi = None; self.entry_time = None; self.max_pnl_during_trade = 0.0

        self._init_db()
        self._sync_with_wallet() 
        self.notify("<b>🛡️ TITAN OMNI V.14.0 PRO | DEPLOYED</b>\n<i>Status: Smart Limit + Full Logging Enabled</i>")

    def _init_db(self):
        """ ตรวจสอบและสร้างคอลัมน์ให้ครบตามที่คุณมีในรูปภาพ """
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_state (
                id SERIAL PRIMARY KEY, last_action TEXT, avg_price FLOAT, 
                total_units FLOAT, highest_price FLOAT, dynamic_sl FLOAT, 
                entry_rsi FLOAT, entry_time TIMESTAMP, max_pnl FLOAT, entry_trend TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                id SERIAL PRIMARY KEY, side TEXT, price FLOAT, units FLOAT, 
                pnl_pct FLOAT, pnl_thb FLOAT, reason TEXT, entry_rsi FLOAT, 
                exit_rsi FLOAT, max_pnl_during_trade FLOAT, hold_time_min FLOAT, 
                market_trend TEXT, traded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: self.notify(f"⚠️ <b>DB Init Error:</b> {e}")

    def get_indicators(self, res_min):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution={res_min}&from={int(time.time())-172800}&to={int(time.time())}", timeout=10).json()
            c = np.array(res['c'], dtype=float)
            ema20 = self._ema(c, 20); ema200 = self._ema(c, 200)
            diff = np.diff(c); up = diff.clip(min=0); down = -diff.clip(max=0)
            rsi = 100 - (100 / (1 + (np.mean(up[-14:]) / (np.mean(down[-14:]) + 1e-9))))
            # ATR สำหรับ Dynamic SL
            h = np.array(res['h'], dtype=float); l = np.array(res['l'], dtype=float)
            tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            atr = np.mean(tr[-14:])
            return {"price": c[-1], "ema20": ema20, "ema200": ema200, "rsi": rsi, "atr": atr}
        except: return None

    def _ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def get_order_book(self):
        """ หัวใจของเงินล้าน: เช็คสภาพคล่องก่อนเทรด """
        try:
            res = requests.get(f"https://api.bitkub.com/api/market/books?sym={self.symbol.lower()}&lmt=5").json()
            if res['error'] == 0:
                return res['result']['bids'][0], res['result']['asks'][0] # [price, volume]
            return None, None
        except: return None, None

    def place_smart_order(self, side, amt, target_price):
        """ ป้องกัน Slippage โดยการใช้ Limit Order ที่ราคา Best Bid/Ask """
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        best_bid, best_ask = self.get_order_book()
        if not best_bid or not best_ask: return False

        # ถ้าเป็นเงินล้าน เราจะใช้ราคาที่ทำให้เรา Match ทันทีแต่ไม่เสีย Slippage เกินเหตุ
        exec_price = best_ask[0] if side == "buy" else best_bid[0]
        
        ts = str(int(time.time() * 1000))
        payload = {"sym": self.symbol.lower(), "amt": amt, "rat": exec_price, "typ": "limit"}
        sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
        
        try:
            r = requests.post(f"https://api.bitkub.com{path}", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, data=json.dumps(payload), timeout=10)
            res = r.json()
            if res.get('error') == 0: return True, exec_price
            self.notify(f"❌ <b>Order Failed:</b> {res.get('description')}")
            return False, 0
        except: return False, 0

    def run(self):
        last_rep = 0; last_heartbeat = time.time()
        while True:
            try:
                self._update_daily_pnl()
                d15, d60 = self.get_indicators("15"), self.get_indicators("60")
                if not d15 or not d60: time.sleep(10); continue

                p, rsi, ema20, ema200_1h = d15['price'], d15['rsi'], d15['ema20'], d60['ema200']
                trend_str = "BullTrend" if p > ema200_1h else "DownTrend"
                
                # RSI Reversal Logic
                if self.rsi_prev is None: self.rsi_prev = rsi; self.rsi_memory = rsi
                elif abs(rsi - self.rsi_prev) > 0.1: self.rsi_memory = self.rsi_prev; self.rsi_prev = rsi

                thb, coin = self.get_balance()
                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0
                equity = thb + (coin * p)
                growth = ((equity - self.initial_equity) / self.initial_equity) * 100

                # --- BUY LOGIC ---
                if self.is_bot_active and self.last_action == "sell" and rsi <= self.rsi_buy_target and rsi > (self.rsi_memory or 0):
                    if p > ema200_1h: # คุมเทรนด์ 1H
                        buy_amt_thb = min(thb * 0.98, (equity * (self.risk_per_trade/100)) / (self.stop_loss_pct/100))
                        if buy_amt_thb >= 10:
                            success, exec_p = self.place_smart_order("buy", buy_amt_thb, p)
                            if success:
                                self.last_action = "buy"; self.avg_price = exec_p
                                self.entry_rsi = rsi; self.entry_time = datetime.now()
                                self.highest_price = exec_p; self.max_pnl_during_trade = 0.0
                                self.dynamic_sl = exec_p * (1 - (self.stop_loss_pct/100))
                                self._save_state(trend_str)
                                self.notify(f"🚀 <b>ENTRY BUY: {exec_p:,.2f}</b>\nRSI: {rsi:.2f}")

                # --- SELL LOGIC ---
                elif self.last_action == "buy" and coin > 0.1:
                    self.highest_price = max(self.highest_price, p)
                    self.max_pnl_during_trade = max(self.max_pnl_during_trade, pnl)
                    
                    # Trailing Stop ด้วย ATR
                    if p - (d15['atr'] * 2.5) > self.dynamic_sl: 
                        self.dynamic_sl = p - (d15['atr'] * 2.5)
                    # ล็อคกำไรเมื่อถึงเป้า หรือ หลุด SL
                    reason = ""
                    if pnl >= 10.0: reason = "Take Profit 🎯"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop 🛡️"

                    if reason:
                        success, exec_p = self.place_smart_order("sell", coin, p)
                        if success:
                            pnl_thb = (coin * (exec_p * 0.9975)) - (coin * (self.avg_price * 1.0025))
                            hold_time = (datetime.now() - self.entry_time).total_seconds() / 60 if self.entry_time else 0
                            self._save_trade_history("sell", exec_p, coin, pnl, pnl_thb, reason, rsi, hold_time, trend_str)
                            self.notify(f"💰 <b>EXIT SELL: {exec_p:,.2f}</b>\nReason: {reason}\nProfit: {pnl:+.2f}%")
                            self.last_action = "sell"; self.avg_price = 0; self._save_state(trend_str)

                # --- REPORTING (เหมือนเดิมที่คุณชอบ) ---
                if time.time() - last_rep >= int(os.getenv("REPORT_INTERVAL", "600")):
                    self._send_full_report(p, trend_str, rsi, equity, growth, thb, coin, pnl)
                    last_rep = time.time()

            except Exception as e: print(f"Loop Error: {e}")
            time.sleep(int(os.getenv("CHECK_INTERVAL", "3")))

    def _save_state(self, trend):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""INSERT INTO bot_state (last_action, avg_price, total_units, highest_price, dynamic_sl, entry_rsi, entry_time, max_pnl, entry_trend) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                        (self.last_action, self.avg_price, self.total_units, self.highest_price, self.dynamic_sl, self.entry_rsi, self.entry_time, self.max_pnl_during_trade, trend))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _save_trade_history(self, side, price, units, pnl_pct, pnl_thb, reason, exit_rsi, hold_time, trend):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("""INSERT INTO trade_history (side, price, units, pnl_pct, pnl_thb, reason, entry_rsi, exit_rsi, max_pnl_during_trade, hold_time_min, market_trend) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                        (side, price, units, pnl_pct, pnl_thb, reason, self.entry_rsi, exit_rsi, self.max_pnl_during_trade, hold_time, trend))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def _send_full_report(self, p, trend_icon, rsi, equity, growth, thb, coin, pnl):
        now_ict = datetime.now(timezone(timedelta(hours=7)))
        msg = (f"🛡️ <b>TITAN V.14.0 PRO | {self.symbol}</b>\n"
               f"Status : {'HOLDING' if self.last_action == 'buy' else 'MONITORING'}\n"
               f"Date : {now_ict.strftime('%d/%m/%Y')}\n"
               f"Time : {now_ict.strftime('%H:%M:%S')}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>MARKET INTELLIGENCE</b>\n"
               f"• Price : {p:,.2f} THB\n"
               f"• Trend 1H : {trend_icon}\n"
               f"• RSI : {rsi:.2f} (Prev:{self.rsi_memory:.2f})\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>PORTFOLIO ANALYSIS</b>\n"
               f"• EQUITY : {equity:,.2f} THB\n"
               f"• GROWTH : {growth:+.2f}%\n"
               f"• Cash : {thb:,.2f} | Assets: {coin:.4f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>STRATEGY</b>\n"
               f"• SL : {self.dynamic_sl:,.2f} ({pnl:+.2f}%)\n"
               f"• Max PnL : {self.max_pnl_during_trade:+.2f}%")
        self.notify(msg)

    # --- Utility methods (get_balance, _update_daily_pnl, notify, etc.) เหมือนเดิม 100% ---
