import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMaster_V17_2_Final_Complete:
    def __init__(self):
        # --- CONFIG & CREDENTIALS ---
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

        # --- MEMORY SYSTEM (หัวใจสำคัญที่ทำให้ค่าไม่ซ้ำ) ---
        self.positions = {}                
        self.scan_results = [] # เก็บผลสแกนล่าสุดของรอบนั้นๆ
        self.market_stats = {"total_qualified": 0, "bullish_pct": 0, "btc_status": "N/A", "match_count": 0}
        
        self._init_db()                    
        self._sync_positions()
        self.notify("<b>💠 TITAN V.17.2 | COMPLETED</b>\n<i>ปิดงานแก้โค้ด: รายงานเต็มรูปแบบ + Slot เรียงลำดับจริง</i>")

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
                    cur.execute("SELECT symbol, avg_price, total_units, dynamic_sl, max_pnl FROM bot_positions_v17")
                    for r in cur.fetchall(): self.positions[r[0]] = {"price": r[1], "units": r[2], "sl": r[3], "max_pnl": r[4]}
        except: pass

    def get_indicators(self, symbol):
        try:
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-(15*60*100)}&to={int(time.time())}"
            res = requests.get(url, timeout=10).json()
            if not res or 'c' not in res or len(res['c']) < 30: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c)
            g, lo = np.where(diff>0, diff, 0), np.where(diff<0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(lo[-14:]) + 1e-9))))
            return {'price': c[-1], 'rsi': rsi, 'trend': 1 if c[-1] > np.mean(c[-20:]) else 0}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                symbols = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                
                btc = self.get_indicators("BTC_THB")
                self.market_stats['btc_status'] = "🟢 OK" if btc and btc['trend']==1 else "⚠️ WEAK"

                temp_results = []
                bullish_count = 0
                match_count = 0

                for sym in symbols:
                    ind = self.get_indicators(sym)
                    if ind:
                        if ind['trend'] == 1: bullish_count += 1
                        if ind['rsi'] <= self.rsi_buy_target: match_count += 1
                        
                        # เก็บทุกเหรียญที่สแกนลง List ชั่วคราวของรอบนี้
                        temp_results.append({
                            "sym": sym, 
                            "rsi": round(ind['rsi'], 2), 
                            "price": ind['price']
                        })
                    time.sleep(0.4)

                # อัปเดต Memory หลักหลังสแกนเสร็จทั้งรอบ
                self.scan_results = temp_results
                self.market_stats.update({
                    "total_qualified": len(symbols),
                    "bullish_pct": (bullish_count/len(symbols)*100) if symbols else 0,
                    "match_count": match_count
                })
                
                if time.time() - last_rep >= 600:
                    self._report_full(self.get_wallet())
                    last_rep = time.time()
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(10)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        
        # --- เรียงลำดับ RSI จากน้อยไปมาก เพื่อใช้ในรายงาน ---
        # กรองเอาเฉพาะเหรียญที่ไม่ได้ถืออยู่
        wait_candidates = sorted(
            [d for d in self.scan_results if d['sym'] not in self.positions],
            key=lambda x: x['rsi']
        )
        
        total_val, slot_details = 0, ""
        current_pos_list = list(self.positions.keys())

        # จัดการ Slot 1, 2, 3
        for i in range(1, 4):
            if i <= len(current_pos_list):
                # เหรียญที่ถืออยู่
                sym = current_pos_list[i-1]
                pos = self.positions[sym]
                # หาค่า RSI ล่าสุดของเหรียญนี้จากผลสแกน
                current_data = next((x for x in self.scan_results if x['sym'] == sym), {"rsi": 0, "price": pos['price']})
                pnl = ((current_data['price'] - pos['price']) / pos['price']) * 100
                total_val += (pos['units'] * current_data['price'])
                slot_details += f"🟢 <b>SLOT {i} | {sym.split('_')[1]}</b>: {pnl:+.2f}% (RSI: {current_data['rsi']:.1f})\n"
            else:
                # เหรียญที่รอซื้อ (ดึงตามลำดับ RSI น้อย -> มาก)
                w_idx = i - len(current_pos_list) - 1
                if w_idx < len(wait_candidates):
                    target = wait_candidates[w_idx]
                    rsi_v, name = target['rsi'], target['sym'].split('_')[1]
                    prog = max(0, min(4, int((rsi_v-35)/10))) if rsi_v > 35 else 0
                    bar = "▫️"*prog + "🔹" + "▫️"*(4-prog)
                    slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [{bar}] RSI {rsi_v:.1f} ({name})\n"
                else:
                    slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI 50.0 (--)\n"

        equity = thb + (total_val * (1 - self.fee_rate))
        alpha = wait_candidates[0] if wait_candidates else None
        
        msg = (
            f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>MARKET CONTEXT</b>\n"
            f"• Sentiment: {'🟦 BULLISH' if self.market_stats['bullish_pct'] > 50 else '🟥 BEARISH'} ({self.market_stats['bullish_pct']:.0f}%)\n"
            f"• BTC Health: <b>{self.market_stats['btc_status']}</b>\n"
            f"• Assets Found: <b>{self.market_stats['total_qualified']} Coins</b>\n"
            f"• Qualified Assets: <b>{self.market_stats['match_count']} Coins</b>\n" # บรรทัดที่พี่สั่งเพิ่ม
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>INTELLIGENCE</b>\n"
            f"• Ref: {alpha['sym'].split('_')[1] if alpha else '---'}\n"
            f"• Last Price: {alpha['price']:,.2f} THB\n" if alpha else ""
            f"• Momentum: ⚡ RSI {alpha['rsi']:.1f if alpha else 0.0} (TGT: 35.0)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
            f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
            f"• ACTIVE ROI: <code>{((equity-self.initial_equity)/self.initial_equity)*100:+.2f}%</code>\n"
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
    TitanMaster_V17_2_Final_Complete().run()
