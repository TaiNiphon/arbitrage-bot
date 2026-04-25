import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class Titan_V17_2_Final_Hybrid:
    def __init__(self):
        # 1. โหลดค่า Config และ Database (ใช้จาก Railway Secrets)
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.db_url = os.getenv("DATABASE_URL")
        
        self.initial_equity = 1800.0   
        self.rsi_buy_target = 35.0    
        self.min_volume_thb = 3000000.0 

        self.positions = {}                
        self.scan_storage = [] 
        self.market_stats = {"total": 0, "match": 0}
        
        self._init_db()                    
        self._sync_positions()
        # แจ้งเตือนทันทีว่าบอทฟื้นแล้ว
        self.notify("<b>💠 TITAN V.17.2 | RELOADED</b>\n<i>ระบบกำลังกู้คืนข้อมูลและเริ่มสแกนชุดใหญ่ครับ...</i>")

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
            # ดึงข้อมูลย้อนหลัง 100 แท่งเพื่อให้คำนวณ RSI ได้นิ่งที่สุด
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-(15*60*100)}&to={int(time.time())}"
            res = requests.get(url, timeout=5).json()
            if not res or 'c' not in res or len(res['c']) < 20: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c)
            g, lo = np.where(diff>0, diff, 0), np.where(diff<0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(g[-14:]) / (np.mean(lo[-14:]) + 1e-9))))
            return {'price': c[-1], 'rsi': round(rsi, 2)}
        except: return None # ถ้ามีปัญหาให้คืนค่า None เพื่อให้บอทไปต่อได้

    def run(self):
        last_rep = 0
        while True:
            try:
                # ดึงรายชื่อเหรียญทั้งหมดที่มีโวลุ่มตามเงื่อนไข
                ticker_res = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                symbols = [s for s, v in ticker_res.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]
                
                temp_results = []
                match_c = 0

                for sym in symbols:
                    ind = self.get_indicators(sym)
                    if ind: # ป้องกัน TypeError (เช็กว่ามีข้อมูลจริงค่อยใส่ลิสต์)
                        if ind['rsi'] <= self.rsi_buy_target: match_c += 1
                        temp_results.append({"sym": sym, "rsi": ind['rsi'], "price": ind['price']})
                    time.sleep(0.05) 

                if temp_results:
                    # เรียงลำดับ RSI น้อย -> มาก (XRP, ADA จะได้ขึ้นมาโชว์)
                    self.scan_storage = sorted(temp_results, key=lambda x: x['rsi'])
                    self.market_stats.update({"total": len(symbols), "match": match_c})
                
                # บังคับส่งรายงานรอบแรกทันทีเมื่อสแกนครบชุด และส่งทุก 10 นาที
                if (time.time() - last_rep >= 600) or (last_rep == 0 and len(self.scan_storage) > 0):
                    self._report_hybrid(self.get_wallet())
                    last_rep = time.time()

            except Exception as e:
                print(f"Loop Error: {e}"); time.sleep(10)

    def _report_hybrid(self, thb):
        try:
            now = datetime.now(timezone(timedelta(hours=7)))
            wait_candidates = [d for d in self.scan_storage if d['sym'] not in self.positions]
            
            total_val, slot_html = 0, ""
            pos_list = list(self.positions.keys())

            # ลอจิก Omni-Slot แบบ Hybrid แยกเหรียญ 1-2-3
            for i in range(0, 3):
                slot_num = i + 1
                if i < len(pos_list):
                    s = pos_list[i]
                    p = self.positions[s]
                    d = next((x for x in self.scan_storage if x['sym'] == s), None)
                    curr_p = d['price'] if d else p['price']
                    pnl = ((curr_p - p['price']) / p['price']) * 100
                    total_val += (p['units'] * curr_p)
                    slot_html += f"🟢 <b>SLOT {slot_num} | {s.split('_')[1]}</b>: {pnl:+.2f}% (RSI: {d['rsi'] if d else 0:.1f})\n"
                else:
                    w_idx = i - len(pos_list)
                    if w_idx < len(wait_candidates):
                        target = wait_candidates[w_idx]
                        slot_html += f"⚪ <b>SLOT {slot_num} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI {target['rsi']:.1f} ({target['sym'].split('_')[1]})\n"
                    else:
                        slot_html += f"⚪ <b>SLOT {slot_num} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI -- (--)\n"

            equity = thb + (total_val * (1 - 0.0025))
            roi = ((equity - self.initial_equity) / self.initial_equity) * 100
            alpha = wait_candidates[0] if wait_candidates else None
            
            # หน้าตารายงาน Hybrid ที่พี่ติ๊กต้องการ (5991 + 5992)
            msg = (
                f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 <b>MARKET CONTEXT</b>\n"
                f"• Assets Found: <b>{self.market_stats['total']} Coins</b>\n"
                f"• Qualified Assets: <b>{self.market_stats['match']} Coins</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>INTELLIGENCE (Ref: {alpha['sym'].split('_')[1] if alpha else '---'})</b>\n"
                f"• Last Price: {alpha['price']:,.2f} THB\n" if alpha else ""
                f"• Momentum: ⚡ RSI {alpha['rsi']:.1f if alpha else 0.0}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
                f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
                f"• ACTIVE ROI: <code>{roi:+.2f}%</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 <b>OMNI-SLOT EXECUTION</b>\n"
                f"{slot_html.strip()}\n"
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
    Titan_V17_2_Final_Hybrid().run()
