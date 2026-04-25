import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMasterV17_2_Official:
    def __init__(self):
        # --- 1. CORE CONFIG ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")

        # --- 2. STRATEGY SETTINGS ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", "1800.0"))
        self.rsi_buy_target = float(os.getenv("RSI_BUY_MAX", "35.0"))
        self.rsi_sell_zone = 70.0 
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "2.5"))
        self.max_slots = int(os.getenv("MAX_SLOTS", "3"))
        self.budget_per_slot = float(os.getenv("BUDGET_PER_SLOT", "600.0"))
        self.min_volume_thb = 3000000.0 
        self.fee_rate = 0.0025

        # --- 3. SYSTEM STATE ---
        self.positions = {}                
        self.latest_scan_results = []
        self.market_stats = {"total_qualified": 0, "bullish_pct": 0, "btc_status": "N/A"}

        self._init_db()                    
        self._sync_positions()
        self.notify("<b>💠 TITAN V.17.2 | SYSTEM ONLINE</b>\n<i>Status: Scanner & RSI-Slots Active</i>")

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_positions_v17 (
                        symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, 
                        dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
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
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}"
            res = requests.get(url, timeout=10).json()
            if not res or 'c' not in res or len(res['c']) < 20: return None
            c, h, l = np.array(res['c'], dtype=float), np.array(res['h'], dtype=float), np.array(res['l'], dtype=float)
            diff = np.diff(c)
            g, lo = np.where(diff > 0, diff, 0), np.where(diff < 0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(lo[-14:]) + 1e-9))))
            tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            atr = np.mean(tr[-14:])
            trend = 1 if c[-1] > np.mean(c[-20:]) else 0
            return {'price': c[-1], 'rsi': rsi, 'atr': atr, 'trend': trend}
        except: return None

    def get_wallet(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            return float(res['result'].get('THB', 0)) if res.get('error') == 0 else 0.0
        except: return 0.0

    def run(self):
        last_rep = 0
        while True:
            try:
                # 1. BTC & MARKET SCAN
                btc = self.get_indicators("BTC_THB")
                self.market_stats['btc_status'] = "🟢 OK" if btc and btc['trend'] == 1 else "⚠️ WEAK"

                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]

                current_scan_data, bullish_count = [], 0
                for sym in qualified:
                    ind = self.get_indicators(sym)
                    if ind:
                        if ind['trend'] == 1: bullish_count += 1
                        current_scan_data.append({"sym": sym, "rsi": ind['rsi'], "price": ind['price']})
                    time.sleep(0.3)

                if current_scan_data:
                    self.latest_scan_results = sorted(current_scan_data, key=lambda x: x['rsi'])
                    self.market_stats.update({"total_qualified": len(qualified), "bullish_pct": (bullish_count/len(qualified)*100) if qualified else 0})

                # 2. REPORT TRIGGER
                if time.time() - last_rep >= 600:
                    self._report_full(self.get_wallet())
                    last_rep = time.time()

            except Exception as e: print(f"Error: {e}"); time.sleep(10)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_val, slot_html = 0, ""
        
        # กองกลางเหรียญที่น่าสนใจ (เรียง RSI ต่ำสุด 3 อันดับแรก)
        wait_list = [d for d in self.latest_scan_results if d['sym'] not in self.positions]
        
        for i in range(1, self.max_slots + 1):
            pos_sym = list(self.positions.keys())[i-1] if i <= len(self.positions) else None
            
            if pos_sym:
                # --- SLOT ถือครอง ---
                p_data = self.positions[pos_sym]
                ind = next((x for x in self.latest_scan_results if x['sym'] == pos_sym), None)
                curr_p = ind['price'] if ind else p_data['price']
                total_val += (p_data['units'] * curr_p)
                pnl = ((curr_p - p_data['price']) / p_data['price']) * 100
                slot_html += f"🟢 <b>SLOT {i} | {pos_sym.split('_')[1]}</b>: {pnl:+.2f}% (RSI: {ind['rsi'] if ind else 0:.1f})\n"
            else:
                # --- SLOT ว่าง (แสดงเหรียญตามลำดับ RSI 1-2-3) ---
                w_idx = i - len(self.positions) - 1
                if 0 <= w_idx < len(wait_list):
                    t = wait_list[w_idx]
                    rsi = t['rsi']
                    # Visual Bar Logic
                    if rsi <= self.rsi_buy_target:
                        fill = max(0, min(5, int((self.rsi_buy_target - rsi) / 2) + 1))
                        bar = "▪️" * fill + "▫️" * (5 - fill) + " 📉"
                    elif rsi >= self.rsi_sell_zone:
                        fill = max(0, min(5, int((rsi - self.rsi_sell_zone) / 2) + 1))
                        bar = "📈 " + "▫️" * (5 - fill) + "▪️" * fill
                    else:
                        prog = max(0, min(4, int((rsi - 35) / (70 - 35) * 5)))
                        bar = "▫️" * prog + "🔹" + "▫️" * (4 - prog)
                    slot_html += f"⚪ <b>SLOT {i} | WAIT</b>: [{bar}] RSI {rsi:.1f} ({t['sym'].split('_')[1]})\n"
                else:
                    slot_html += f"⚪ <b>SLOT {i} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI -- (--)\n"

        equity = thb + (total_val * 0.9975)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        ref = self.latest_scan_results[0] if self.latest_scan_results else {"sym": "THB_BTC", "price": 0, "rsi": 0}

        msg = (
            f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>MARKET CONTEXT</b>\n"
            f"• Sentiment: {'🟥 BEARISH' if self.market_stats['bullish_pct'] < 50 else '🟦 BULLISH'} ({self.market_stats['bullish_pct']:.0f}%)\n"
            f"• BTC Health: <b>{self.market_stats['btc_status']}</b>\n"
            f"• Qualified: <b>{self.market_stats['total_qualified']} Assets</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>INTELLIGENCE (Rank #1: {ref['sym'].split('_')[1]})</b>\n"
            f"• Last Price: {ref['price']:,.2f} THB\n"
            f"• Momentum: ⚡ RSI {ref['rsi']:.1f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
            f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
            f"• ACTIVE ROI: <code>{growth:+.2f}%</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>OMNI-SLOT EXECUTION</b>\n"
            f"{slot_html.strip()}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <i>{now.strftime('%d/%m/%Y | %H:%M:%S')}</i>"
        )
        self.notify(msg)

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanMasterV17_2_Official().run()
