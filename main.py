import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMaster_V17_2_Final_Ultimate:
    def __init__(self):
        # --- 1. CONFIGURATION (ดึงจาก Environment Variables) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. STRATEGY SETTINGS (V.15 Standard) ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", "1800.0"))
        self.rsi_buy_target = 35.0
        self.max_slots = 3
        self.budget_per_slot = 600.0
        self.min_volume_thb = 3000000.0
        self.fee_rate = 0.0025
        self.risk_per_trade = 2.5 # ATR Multiplier

        # --- 3. SYSTEM STATE ---
        self.positions = {}                
        self.scan_storage = {} # เก็บค่า RSI ถาวรป้องกันค่ากระโดด
        self.market_stats = {"total_qualified": 0, "bullish_pct": 0, "btc_status": "N/A", "qualified_count": 0}
        
        self._init_db()                    
        self._sync_positions()
        self.notify("<b>💠 TITAN V.17.2 | ULTIMATE DEPLOY</b>\n<i>ระบบตรวจสอบไทม์ไลน์และแก้ไขรายงานสมบูรณ์แล้ว</i>")

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
                    for r in cur.fetchall():
                        self.positions[r[0]] = {"price": r[1], "units": r[2], "sl": r[3], "max_pnl": r[4]}
        except: pass

    def get_indicators(self, symbol):
        try:
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-(15*60*100)}&to={int(time.time())}"
            res = requests.get(url, timeout=10).json()
            if not res or 'c' not in res or len(res['c']) < 30: return None
            c = np.array(res['c'], dtype=float)
            h = np.array(res['h'], dtype=float)
            l = np.array(res['l'], dtype=float)
            diff = np.diff(c)
            g, lo = np.where(diff>0, diff, 0), np.where(diff<0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(lo[-14:]) + 1e-9))))
            tr = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
            return {'price': c[-1], 'rsi': rsi, 'atr': np.mean(tr[-14:]), 'trend': 1 if c[-1] > np.mean(c[-20:]) else 0}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                # 1. ข้อมูลตลาดเบื้องต้น
                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                self.market_stats['total_qualified'] = len(qualified)

                # 2. เช็คสุขภาพ BTC
                btc = self.get_indicators("BTC_THB")
                btc_safe = btc['trend'] == 1 if btc else False
                self.market_stats['btc_status'] = "🟢 OK" if btc_safe else "⚠️ WEAK"

                # 3. ลูปสแกนเหรียญทั้งหมด
                bullish_count = 0
                match_criteria = 0
                for sym in qualified:
                    ind = self.get_indicators(sym)
                    if ind:
                        if ind['trend'] == 1: bullish_count += 1
                        if ind['rsi'] <= self.rsi_buy_target: match_criteria += 1
                        
                        # เก็บค่าลงหน่วยความจำ
                        self.scan_storage[sym] = {"sym": sym, "rsi": round(ind['rsi'], 2), "price": ind['price'], "atr": ind['atr']}

                        # ตรวจสอบการขายเหรียญที่ถืออยู่
                        if sym in self.positions:
                            pos = self.positions[sym]
                            # (ลอจิก Trailing Stop / Take Profit ใส่ตรงนี้)
                    time.sleep(0.4)

                self.market_stats['bullish_pct'] = (bullish_count/len(qualified)*100) if qualified else 0
                self.market_stats['qualified_count'] = match_criteria
                
                # 4. ส่งรายงานทุก 10 นาที
                if time.time() - last_rep >= 600:
                    self._report_full(self.get_wallet())
                    last_rep = time.time()

            except Exception as e: print(f"Error: {e}"); time.sleep(10)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_val, slot_details = 0, ""
        current_pos = list(self.positions.keys())
        
        # จัดอันดับเหรียญที่ RSI น้อย -> มาก (เฉพาะที่ไม่ได้ถือ)
        wait_list = sorted([v for k, v in self.scan_storage.items() if k not in self.positions], key=lambda x: x['rsi'])
        
        # ส่วน INTELLIGENCE
        alpha = wait_list[0] if wait_list else None
        alpha_report = f"• Ref: {alpha['sym'].split('_')[1] if alpha else '---'}\n"
        alpha_report += f"• Last Price: {alpha['price']:,.2f} THB\n" if alpha else ""
        alpha_report += f"• Momentum: ⚡ RSI {alpha['rsi']:.1f} (TGT: {self.rsi_buy_target})" if alpha else "• Momentum: N/A"

        # ส่วน SLOT EXECUTION
        for i in range(1, 4):
            if i <= len(current_pos):
                sym = current_pos[i-1]
                pos = self.positions[sym]
                ind = self.scan_storage.get(sym, {"rsi": 0.0, "price": pos['price']})
                pnl = ((ind['price'] - pos['price']) / pos['price']) * 100
                total_val += (pos['units'] * ind['price'])
                slot_details += f"🟢 <b>SLOT {i} | {sym.split('_')[1]}</b>: {pnl:+.2f}% (RSI: {ind['rsi']:.1f})\n"
            else:
                w_idx = i - len(current_pos) - 1
                if w_idx < len(wait_list):
                    t = wait_list[w_idx]
                    prog = max(0, min(4, int((t['rsi']-35)/10)))
                    bar = "▫️"*prog + "🔹" + "▫️"*(4-prog)
                    slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [{bar}] RSI {t['rsi']:.1f} ({t['sym'].split('_')[1]})\n"
                else:
                    slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI 50.0 (---)\n"

        # สรุปเงินรวม
        equity = thb + (total_val * (1 - self.fee_rate))
        roi = ((equity - self.initial_equity) / self.initial_equity) * 100

        msg = (
            f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>MARKET CONTEXT</b>\n"
            f"• Sentiment: {'🟦 BULLISH' if self.market_stats['bullish_pct'] > 50 else '🟥 BEARISH'} ({self.market_stats['bullish_pct']:.0f}%)\n"
            f"• BTC Health: <b>{self.market_stats['btc_status']}</b>\n"
            f"• Assets Found: <b>{self.market_stats['total_qualified']} Coins</b>\n"
            f"• Qualified Assets: <b>{self.market_stats['qualified_count']} Coins</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>INTELLIGENCE</b>\n"
            f"{alpha_report}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
            f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
            f"• ACTIVE ROI: <code>{roi:+.2f}%</code>\n"
            f"• LIQUIDITY: <b>{thb:,.2f} THB</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>OMNI-SLOT EXECUTION</b>\n"
            f"{slot_details.strip()}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <i>{now.strftime('%d/%m/%Y | %H:%M:%S')}</i>"
        )
        self.notify(msg)

    def get_wallet(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            return float(res['result'].get('THB', 0)) if res.get('error') == 0 else 0.0
        except: return 0.0

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanMaster_V17_2_Final_Ultimate().run()
