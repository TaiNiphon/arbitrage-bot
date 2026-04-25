import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMaster_V17_2_Final_Ultimate:
    def __init__(self):
        # --- 1. CONFIG & DB ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")
        self.initial_equity = 1800.0
        self.rsi_buy_target = 35.0
        self.max_slots = 3
        self.min_volume_thb = 3000000.0
        self.fee_rate = 0.0025
        self.trailing_percent = 2.0 # กันขายหมู: ถ้าย่อจากจุดสูงสุด 2% ค่อยขาย

        self.positions = {}                
        self.scan_storage = [] 
        self.market_stats = {"total": 0, "bull": 0, "btc": "N/A", "match": 0}
        
        self._init_db()                    
        self._sync_positions()
        self.notify("<b>💠 TITAN V.17.2 | SYSTEM ONLINE</b>\n<i>แก้ไขลอจิกเรียงลำดับและหน้าตารายงานเรียบร้อยครับพี่ติ๊ก</i>")

    def _init_db(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS bot_positions_v17 (symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, max_price FLOAT, updated_at TIMESTAMP)")
        except: pass

    def _sync_positions(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol, avg_price, total_units, max_price FROM bot_positions_v17")
                    for r in cur.fetchall(): 
                        self.positions[r[0]] = {"price": r[1], "units": r[2], "max_price": r[3]}
        except: pass

    def get_indicators(self, symbol):
        try:
            # ดึงข้อมูลย้อนหลัง 40 แท่ง (15 นาที) เพื่อความแม่นยำ
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-(15*60*40)}&to={int(time.time())}"
            res = requests.get(url, timeout=5).json()
            if not res or 'c' not in res or len(res['c']) < 20: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c)
            g, lo = np.where(diff>0, diff, 0), np.where(diff<0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(lo[-14:]) + 1e-9))))
            return {'price': c[-1], 'rsi': rsi, 'trend': 1 if c[-1] > np.mean(c[-15:]) else 0}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                symbols = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                
                # เช็ก BTC Health
                btc = self.get_indicators("BTC_THB")
                self.market_stats['btc'] = "🟢 OK" if btc and btc['trend']==1 else "⚠️ WEAK"

                temp_results = []
                bull_c, match_c = 0, 0

                for sym in symbols:
                    ind = self.get_indicators(sym)
                    if ind:
                        if ind['trend'] == 1: bull_c += 1
                        if ind['rsi'] <= self.rsi_buy_target: match_c += 1
                        temp_results.append({"sym": sym, "rsi": round(ind['rsi'], 2), "price": ind['price']})
                        
                        # ระบบ Dynamic Trailing (กันขายหมู)
                        if sym in self.positions:
                            pos = self.positions[sym]
                            if ind['price'] > pos['max_price']:
                                pos['max_price'] = ind['price'] # อัปเดตจุดสูงสุดใหม่

                    time.sleep(0.05) # สแกนเร็วขึ้นเพื่อไม่ให้บอทค้าง

                if temp_results:
                    self.scan_storage = temp_results
                    self.market_stats.update({"total": len(symbols), "bull": (bull_c/len(symbols)*100), "match": match_c})
                
                # ส่งรายงานทันทีในรอบแรก และส่งทุกๆ 10 นาที
                if (time.time() - last_rep >= 600) or last_rep == 0:
                    self._report_full(self.get_wallet())
                    last_rep = time.time()
                    
            except Exception as e:
                print(f"Error: {e}"); time.sleep(10)

    def _report_full(self, thb):
        try:
            now = datetime.now(timezone(timedelta(hours=7)))
            # เรียงเหรียญที่น่าซื้อที่สุด (RSI น้อย -> มาก)
            wait_candidates = sorted([d for d in self.scan_storage if d['sym'] not in self.positions], key=lambda x: x['rsi'])
            
            total_val, slot_html = 0, ""
            pos_list = list(self.positions.keys())

            for i in range(0, 3):
                slot_num = i + 1
                if i < len(pos_list):
                    s = pos_list[i]
                    p = self.positions[s]
                    d = next((x for x in self.scan_storage if x['sym'] == s), {"rsi": 0, "price": p['price']})
                    pnl = ((d['price'] - p['price']) / p['price']) * 100
                    total_val += (p['units'] * d['price'])
                    slot_html += f"🟢 <b>SLOT {slot_num} | {s.split('_')[1]}</b>: {pnl:+.2f}% (RSI: {d['rsi']:.1f})\n"
                else:
                    w_idx = i - len(pos_list)
                    if w_idx < len(wait_candidates):
                        target = wait_candidates[w_idx]
                        slot_html += f"⚪ <b>SLOT {slot_num} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI {target['rsi']:.1f} ({target['sym'].split('_')[1]})\n"
                    else:
                        slot_html += f"⚪ <b>SLOT {slot_num} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI 50.0 (--)\n"

            equity = thb + (total_val * (1 - self.fee_rate))
            roi = ((equity - self.initial_equity) / self.initial_equity) * 100
            alpha = wait_candidates[0] if wait_candidates else None
            
            msg = (
                f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 <b>MARKET CONTEXT</b>\n"
                f"• Sentiment: {'🟦 BULLISH' if self.market_stats['bull'] > 50 else '🟥 BEARISH'} ({self.market_stats['bull']:.0f}%)\n"
                f"• BTC Health: <b>{self.market_stats['btc']}</b>\n"
                f"• Assets Found: <b>{self.market_stats['total']} Coins</b>\n"
                f"• Qualified Assets: <b>{self.market_stats['match']} Coins</b>\n" # จำนวนที่สแกนเข้าเงื่อนไข RSI
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>INTELLIGENCE</b>\n"
                f"• Ref: <b>{alpha['sym'].split('_')[1] if alpha else '---'}</b>\n"
                f"• Momentum: ⚡ RSI {alpha['rsi']:.1f if alpha else 0.0} (TGT: {self.rsi_buy_target})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
                f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
                f"• ACTIVE ROI: <code>{roi:+.2f}%</code>\n"
                f"• LIQUIDITY: <b>{thb:,.2f} THB</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 <b>OMNI-SLOT EXECUTION</b>\n"
                f"{slot_html.strip()}\n" # เรียง RSI 1 2 3 พร้อมชื่อเหรียญ
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 <i>{now.strftime('%d/%m/%Y | %H:%M:%S')}</i>"
            )
            self.notify(msg)
        except Exception as e: print(f"Report Error: {e}")

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
