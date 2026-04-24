import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMasterV17_2:
    def __init__(self):
        # --- 1. CORE CONFIGURATION ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. STRATEGY SETTINGS ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", "1800.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.5"))
        self.max_slots = int(os.getenv("MAX_SLOTS", "3"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "600.0"))
        self.min_volume_thb = float(os.getenv("MIN_VOLUME_THB", "3000000.0")) 
        self.fee_rate = 0.0025 # ค่าธรรมเนียม 0.25%
        
        # --- 3. SYSTEM STATE ---
        self.positions = {}                
        self.latest_scan_results = []
        self.market_stats = {"total_qualified": 0, "bullish_pct": 0, "btc_status": "N/A"}
        self.sample_asset = {"sym": "XRP", "price": 0.0, "rsi": 0.0}

        self._init_db()                    
        self._sync_positions() # แก้ไขจุดที่ทำให้ Error แล้วครับ
        self.notify(f"<b>💠 TITAN V.17.2 | ULTIMATE ALPHA ONLINE</b>\n<i>Status: BTC Sentinel & Net Fee Active</i>")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=10)
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_positions_v17 (
                symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, 
                dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS trade_log_v17 (
                id SERIAL PRIMARY KEY, symbol TEXT, side TEXT, price FLOAT, 
                pnl_pct FLOAT, pnl_thb FLOAT, timestamp TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"⚠️ DB Error: {e}")

    def _sync_positions(self):
        """ ดึงข้อมูลจากฐานข้อมูลเมื่อเริ่มระบบใหม่ """
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT symbol, avg_price, total_units, dynamic_sl, max_pnl FROM bot_positions_v17")
            for row in cur.fetchall():
                self.positions[row[0]] = {"price": row[1], "units": row[2], "sl": row[3], "max_pnl": row[4]}
            cur.close(); conn.close()
            print(f"✅ Synced {len(self.positions)} positions from DB")
        except: pass

    def get_indicators_v15_style(self, symbol):
        try:
            end = int(time.time())
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={end-86400}&to={end}"
            res = requests.get(url, timeout=10).json()
            if not res or 'c' not in res or len(res['c']) < 30: return None
            c, h, l = np.array(res['c'], dtype=float), np.array(res['h'], dtype=float), np.array(res['l'], dtype=float)
            diff = np.diff(c)
            gain, loss = np.where(diff > 0, diff, 0), np.where(diff < 0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(gain[-14:]) / (np.mean(loss[-14:]) + 1e-9))))
            tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            atr = np.mean(tr[-14:])
            trend = 1 if c[-1] > np.mean(c[-20:]) else 0
            return {'price': c[-1], 'rsi': rsi, 'atr': atr, 'trend': trend}
        except: return None

    def get_btc_sentinel(self):
        btc = self.get_indicators_v15_style("BTC_THB")
        if not btc: return False
        self.market_stats['btc_status'] = "🟢 OK" if btc['trend'] == 1 else "⚠️ WEAK"
        return btc['trend'] == 1

    def get_wallet(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            return float(res['result'].get('THB', 0)) if res.get('error') == 0 else 0.0
        except: return 0.0

    def place_order(self, side, symbol, amt, price):
        try:
            path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
            ts = str(int(time.time() * 1000))
            payload = {"sym": symbol.lower(), "amt": amt, "rat": price, "typ": "limit"}
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+path+json.dumps(payload)).encode(), hashlib.sha256).hexdigest()
            headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
            r = requests.post(f"https://api.bitkub.com{path}", headers=headers, data=json.dumps(payload), timeout=10)
            return r.json().get('error') == 0
        except: return False

    def _log_trade(self, symbol, side, price, pnl_pct=0.0, pnl_thb=0.0):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("INSERT INTO trade_log_v17 (symbol, side, price, pnl_pct, pnl_thb, timestamp) VALUES (%s, %s, %s, %s, %s, %s)",
                        (symbol, side, price, pnl_pct, pnl_thb, datetime.now()))
            conn.commit(); cur.close(); conn.close()
        except: pass

    def run(self):
        last_rep = 0
        while True:
            try:
                btc_safe = self.get_btc_sentinel()
                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]

                xrp = self.get_indicators_v15_style("XRP_THB")
                if xrp: self.sample_asset = {"sym": "XRP", "price": xrp['price'], "rsi": xrp['rsi']}

                thb = self.get_wallet()
                current_scan_data = []
                bullish_count = 0

                # 2. MONITOR & SELL (ระบบขายคำนวณหัก FEE 0.25%)
                for sym in list(self.positions.keys()):
                    ind = self.get_indicators_v15_style(sym)
                    if not ind: continue
                    p, pos = ind['price'], self.positions[sym]
                    
                    # คำนวณ ROI แบบหักค่าธรรมเนียมทั้งขาซื้อและขาขาย
                    buy_val = (pos['price'] * pos['units']) / (1 - self.fee_rate)
                    sell_val = (p * pos['units']) * (1 - self.fee_rate)
                    pnl_pct = ((sell_val - buy_val) / buy_val) * 100
                    
                    if pnl_pct > pos['max_pnl']: pos['max_pnl'] = pnl_pct 
                    new_sl = p - (ind['atr'] * self.risk_per_trade)
                    if new_sl > pos['sl']: 
                        pos['sl'] = new_sl
                        conn = psycopg2.connect(self.db_url); cur = conn.cursor()
                        cur.execute("UPDATE bot_positions_v17 SET dynamic_sl=%s, max_pnl=%s WHERE symbol=%s", (new_sl, pos['max_pnl'], sym))
                        conn.commit(); cur.close(); conn.close()

                    if p <= pos['sl']: 
                        if self.place_order("sell", sym, pos['units'], p):
                            pnl_thb = sell_val - buy_val
                            self._log_trade(sym, "SELL", p, pnl_pct, pnl_thb)
                            self.notify(f"📤 <b>SELL {sym.split('_')[1]}</b>\nROI: {pnl_pct:+.2f}% ({pnl_thb:+.2f} THB)")
                            del self.positions[sym]
                            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
                            cur.execute("DELETE FROM bot_positions_v17 WHERE symbol=%s", (sym,))
                            conn.commit(); cur.close(); conn.close()

                # 3. SCAN & BUY (หัก FEE 0.25% เพื่อหาหน่วยเหรียญที่ได้จริง)
                for sym in qualified:
                    if sym in self.positions: continue
                    ind = self.get_indicators_v15_style(sym)
                    if ind:
                        if ind['trend'] == 1: bullish_count += 1
                        current_scan_data.append({"sym": sym, "rsi": ind['rsi'], "price": ind['price']})

                        if btc_safe and len(self.positions) < self.max_slots and ind['rsi'] <= self.rsi_buy_target and thb >= self.budget_per_slot:
                            if self.place_order("buy", sym, self.budget_per_slot, ind['price']):
                                # จำนวนหน่วยเหรียญที่ได้รับจริงหลังหักค่าธรรมเนียม
                                units = (self.budget_per_slot * (1 - self.fee_rate)) / ind['price']
                                sl = ind['price'] - (ind['atr'] * self.risk_per_trade)
                                self.positions[sym] = {"price": ind['price'], "units": units, "sl": sl, "max_pnl": 0.0}
                                
                                conn = psycopg2.connect(self.db_url); cur = conn.cursor()
                                cur.execute("INSERT INTO bot_positions_v17 VALUES (%s,%s,%s,%s,%s,%s)", 
                                            (sym, ind['price'], units, sl, 0.0, datetime.now()))
                                conn.commit(); cur.close(); conn.close()
                                
                                self._log_trade(sym, "BUY", ind['price'])
                                self.notify(f"🚀 <b>BUY {sym.split('_')[1]}</b>\nRSI: {ind['rsi']:.2f}")
                                thb -= self.budget_per_slot
                    time.sleep(0.4)

                self.latest_scan_results = sorted(current_scan_data, key=lambda x: x['rsi'])[:5]
                self.market_stats.update({"total_qualified": len(qualified), "bullish_pct": (bullish_count/len(qualified)*100) if qualified else 0})

                if time.time() - last_rep >= 600:
                    self._report_full(thb)
                    last_rep = time.time()

            except Exception as e: print(f"Error: {e}"); time.sleep(10)
            time.sleep(10)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_asset_val, slot_details = 0, ""
        alpha = self.latest_scan_results[0] if self.latest_scan_results else self.sample_asset
        
        for i, (sym, pos) in enumerate(self.positions.items(), 1):
            ind = self.get_indicators_v15_style(sym)
            p = ind['price'] if ind else pos['price']
            # มูลค่าเหรียญปัจจุบันหักค่าธรรมเนียมขาย
            current_val = (pos['units'] * p) * (1 - self.fee_rate)
            total_asset_val += current_val
            
            buy_val = (pos['price'] * pos['units']) / (1 - self.fee_rate)
            pnl = ((current_val - buy_val) / buy_val) * 100
            slot_details += f"🟢 <b>SLOT {i} | {sym.split('_')[1]}</b>: {pnl:+.2f}% (Trailing...)\n"

        for i in range(len(self.positions) + 1, self.max_slots + 1):
            dist = max(0, alpha['rsi'] - self.rsi_buy_target)
            bar = "▪️" * max(0, 5 - int(dist/3)) + "▫️" * min(5, int(dist/3))
            slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [{bar[:5]}] RSI {alpha['rsi']:.1f} ({alpha['sym'].replace('THB_','')})\n"

        equity = thb + total_asset_val
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        
        msg = (
            f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>MARKET CONTEXT</b>\n"
            f"• Sentiment: {'🟥 BEARISH' if self.market_stats['bullish_pct'] < 50 else '🟦 BULLISH'} ({self.market_stats['bullish_pct']:.0f}%)\n"
            f"• BTC Health: <b>{self.market_stats['btc_status']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>INTELLIGENCE (Ref: {alpha['sym'].replace('THB_','')})</b>\n"
            f"• Last Price: {alpha['price']:,.2f} THB\n"
            f"• Momentum: ⚡ RSI {alpha['rsi']:.1f} (Target: {self.rsi_buy_target})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
            f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
            f"• ACTIVE ROI: <code>{growth:+.2f}%</code>\n"
            f"• LIQUIDITY: {thb:,.2f} THB\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>OMNI-SLOT EXECUTION</b>\n"
            f"{slot_details.strip()}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <i>{now.strftime('%d/%m/%Y | %H:%M:%S')}</i>"
        )
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanMasterV17_2().run()
