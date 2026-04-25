import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMasterV17_2_Ultimate:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")
        self.initial_equity = 1800.0
        self.rsi_buy_target = 35.0
        self.max_slots = 3
        self.min_volume_thb = 3000000.0 

        self.positions = {}                
        self.latest_scan_results = []
        self.market_stats = {"total_qualified": 0, "bullish_pct": 0, "btc_status": "N/A"}
        
        self._init_db()                    
        self._sync_positions()
        # แจ้งเตือนสถานะเริ่มต้น
        self.notify("<b>💠 TITAN V.17.2 | ULTIMATE ALPHA</b>\n<i>ระบบกำลังเริ่มสแกน... กรุณารอสักครู่เพื่อให้ข้อมูลครบถ้วนครับ</i>")

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS bot_positions_v17 (symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)")
        except: pass

    def _sync_positions(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol, avg_price, total_units FROM bot_positions_v17")
                    for r in cur.fetchall(): self.positions[r[0]] = {"price": r[1], "units": r[2]}
        except: pass

    def get_indicators(self, symbol):
        try:
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}"
            res = requests.get(url, timeout=10).json()
            if not res or 'c' not in res or len(res['c']) < 30: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c)
            g, lo = np.where(diff > 0, diff, 0), np.where(diff < 0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(lo[-14:]) + 1e-9))))
            trend = 1 if c[-1] > np.mean(c[-20:]) else 0
            return {'price': c[-1], 'rsi': rsi, 'trend': trend}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                # เช็ก BTC Health ก่อน
                btc = self.get_indicators("BTC_THB")
                self.market_stats['btc_status'] = "🟢 OK" if btc and btc['trend'] == 1 else "⚠️ WEAK"

                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]

                current_scan_data = []
                bullish_count = 0
                match_count = 0

                for sym in qualified:
                    ind = self.get_indicators(sym)
                    if ind:
                        if ind['trend'] == 1: bullish_count += 1
                        if ind['rsi'] <= self.rsi_buy_target: match_count += 1
                        current_scan_data.append({"sym": sym, "rsi": ind['rsi'], "price": ind['price']})
                    time.sleep(0.3)

                if current_scan_data:
                    self.latest_scan_results = sorted(current_scan_data, key=lambda x: x['rsi'])
                    self.market_stats.update({
                        "total_qualified": match_count,
                        "bullish_pct": (bullish_count/len(qualified)*100) if qualified else 0
                    })

                # ส่งรายงานเมื่อมีข้อมูล (ป้องกันรายงานสั้นจุ๊ดจู๋)
                if (time.time() - last_rep >= 600) or (last_rep == 0 and len(self.latest_scan_results) > 0):
                    self._report_full(self.get_wallet())
                    last_rep = time.time()

            except Exception as e: print(f"Error: {e}"); time.sleep(10)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_asset_val, slot_html = 0, ""
        wait_candidates = [d for d in self.latest_scan_results if d['sym'] not in self.positions]
        alpha = self.latest_scan_results[0] if self.latest_scan_results else None

        for i in range(1, self.max_slots + 1):
            pos_sym = list(self.positions.keys())[i-1] if i <= len(self.positions) else None
            if pos_sym:
                p = self.positions[pos_sym]
                ind = next((x for x in self.latest_scan_results if x['sym'] == pos_sym), None)
                curr_p = ind['price'] if ind else p['price']
                total_asset_val += (p['units'] * curr_p)
                pnl = ((curr_p - p['price']) / p['price']) * 100
                slot_html += f"🟢 <b>SLOT {i} | {pos_sym.split('_')[1]}</b>: {pnl:+.2f}% (RSI: {ind['rsi'] if ind else 0:.1f})\n"
            else:
                w_idx = i - len(self.positions) - 1
                if 0 <= w_idx < len(wait_candidates):
                    target = wait_candidates[w_idx]
                    slot_html += f"⚪ <b>SLOT {i} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI {target['rsi']:.1f} ({target['sym'].split('_')[1]})\n"
                else:
                    slot_html += f"⚪ <b>SLOT {i} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI -- (--)\n"

        equity = thb + (total_asset_val * 0.9975)
        msg = (
            f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>MARKET CONTEXT</b>\n"
            f"• Sentiment: {'🟥 BEARISH' if self.market_stats['bullish_pct'] < 50 else '🟦 BULLISH'} ({self.market_stats['bullish_pct']:.0f}%)\n"
            f"• BTC Health: <b>{self.market_stats['btc_status']}</b>\n"
            f"• Qualified Assets: <b>{self.market_stats['total_qualified']} Coins</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>INTELLIGENCE (Ref: {alpha['sym'].split('_')[1] if alpha else '---'})</b>\n"
            f"• Last Price: {alpha['price']:,.2f} THB\n" if alpha else ""
            f"• Momentum: ⚡ RSI {alpha['rsi']:.1f if alpha else 0.0}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
            f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
            f"• ACTIVE ROI: <code>{((equity-self.initial_equity)/self.initial_equity)*100:+.2f}%</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>OMNI-SLOT EXECUTION</b>\n"
            f"{slot_html.strip()}\n"
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
    TitanMasterV17_2_Ultimate().run()
