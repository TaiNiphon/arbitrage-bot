import os, requests, time, hmac, hashlib, json, numpy as np, psycopg2
from datetime import datetime, timezone, timedelta

class TitanMasterV17_2_Fixed:
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
        self.rsi_sell_zone = 70.0 
        self.max_slots = 3
        self.budget_per_slot = 600.0
        self.min_volume_thb = 3000000.0 
        self.fee_rate = 0.0025

        # --- 3. SYSTEM STATE ---
        self.positions = {}                
        self.latest_scan_results = [] # เก็บเหรียญทั้งหมดที่สแกนเจอ
        self.market_stats = {"total_qualified": 0, "bullish_pct": 0, "btc_status": "N/A"}

        self._init_db()                    
        self._sync_positions()
        self.notify(f"<b>💠 TITAN V.17.2 | HYBRID UPDATE</b>\n<i>Status: เรียงลำดับ RSI 1-2-3 พร้อมชื่อเหรียญ Active</i>")

    def _init_db(self):
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=10)
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS bot_positions_v17 (
                symbol TEXT PRIMARY KEY, avg_price FLOAT, total_units FLOAT, 
                dynamic_sl FLOAT, max_pnl FLOAT, updated_at TIMESTAMP)""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e: print(f"⚠️ DB Error: {e}")

    def _sync_positions(self):
        try:
            conn = psycopg2.connect(self.db_url); cur = conn.cursor()
            cur.execute("SELECT symbol, avg_price, total_units, dynamic_sl, max_pnl FROM bot_positions_v17")
            for row in cur.fetchall():
                self.positions[row[0]] = {"price": row[1], "units": row[2], "sl": row[3], "max_pnl": row[4]}
            cur.close(); conn.close()
        except: pass

    def get_indicators_v15_style(self, symbol):
        try:
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}"
            res = requests.get(url, timeout=10).json()
            if not res or 'c' not in res or len(res['c']) < 20: return None
            c = np.array(res['c'], dtype=float)
            diff = np.diff(c)
            gain, loss = np.where(diff > 0, diff, 0), np.where(diff < 0, -diff, 0)
            rsi = 100 - (100 / (1 + (np.mean(gain[-14:]) / (np.mean(loss[-14:]) + 1e-9))))
            return {'price': c[-1], 'rsi': rsi, 'trend': 1 if c[-1] > np.mean(c[-20:]) else 0}
        except: return None

    def run(self):
        last_rep = 0
        while True:
            try:
                ticker = requests.get("https://api.bitkub.com/api/market/ticker", timeout=10).json()
                qualified = [s for s, v in ticker.items() if s.startswith("THB_") and float(v['quoteVolume']) >= self.min_volume_thb]

                current_scan_data = []
                match_condition_count = 0

                for sym in qualified:
                    ind = self.get_indicators_v15_style(sym)
                    if ind:
                        current_scan_data.append({"sym": sym, "rsi": ind['rsi'], "price": ind['price']})
                        if ind['rsi'] <= self.rsi_buy_target:
                            match_condition_count += 1
                    time.sleep(0.1)

                # เรียงลำดับจาก RSI น้อยไปมาก เพื่อใช้ใน Report
                self.latest_scan_results = sorted(current_scan_data, key=lambda x: x['rsi'])
                self.market_stats.update({"total_qualified": match_condition_count})

                if time.time() - last_rep >= 600 or last_rep == 0:
                    self._report_full(self.get_wallet())
                    last_rep = time.time()

            except Exception as e: print(f"Error: {e}"); time.sleep(10)

    def _report_full(self, thb):
        now = datetime.now(timezone(timedelta(hours=7)))
        total_asset_val, slot_details = 0, ""
        
        # กรองเหรียญที่ยังไม่ได้ถือ เพื่อเอามาโชว์ในช่อง WAIT
        wait_candidates = [d for d in self.latest_scan_results if d['sym'] not in self.positions]
        
        # ส่วนแสดงผล SLOT 1-2-3
        for i in range(1, self.max_slots + 1):
            pos_sym = list(self.positions.keys())[i-1] if i <= len(self.positions) else None
            
            if pos_sym:
                # กรณีมีเหรียญในมือ
                pos = self.positions[pos_sym]
                ind = next((x for x in self.latest_scan_results if x['sym'] == pos_sym), None)
                p = ind['price'] if ind else pos['price']
                current_val = (pos['units'] * p) * (1 - self.fee_rate)
                total_asset_val += current_val
                pnl = ((current_val - (pos['price']*pos['units'])) / (pos['price']*pos['units'])) * 100
                slot_details += f"🟢 <b>SLOT {i} | {pos_sym.split('_')[1]}</b>: {pnl:+.2f}% (RSI: {ind['rsi'] if ind else '--':.1f})\n"
            else:
                # กรณีว่าง (WAIT) - ดึงลำดับจาก wait_candidates
                w_idx = i - len(self.positions) - 1
                if w_idx >= 0 and w_idx < len(wait_candidates):
                    target = wait_candidates[w_idx]
                    slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI {target['rsi']:.1f} ({target['sym'].split('_')[1]})\n"
                else:
                    slot_details += f"⚪ <b>SLOT {i} | WAIT</b>: [▫️▫️🔹▫️▫️] RSI -- (--)\n"

        equity = thb + total_asset_val
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        
        msg = (
            f"💠 <b>TITAN V.17.2 | ULTIMATE ALPHA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>MARKET CONTEXT</b>\n"
            f"• Qualified Assets: <b>{self.market_stats['total_qualified']} Coins</b>\n" # จำนวนที่เข้าเงื่อนไข
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>PORTFOLIO PERFORMANCE</b>\n"
            f"• NET EQUITY: <b>{equity:,.2f} THB</b>\n"
            f"• ACTIVE ROI: <code>{growth:+.2f}%</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>OMNI-SLOT EXECUTION</b>\n"
            f"{slot_details.strip()}\n" # ตรงนี้จะเรียง RSI น้อยไปมาก 1-2-3 พร้อมชื่อครับ
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <i>{now.strftime('%d/%m/%Y | %H:%M:%S')}</i>"
        )
        self.notify(msg)

    def get_wallet(self):
        try:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode(), (ts+"POST"+"/api/v3/market/wallet").encode(), hashlib.sha256).hexdigest()
            res = requests.post("https://api.bitkub.com/api/v3/market/wallet", 
                                headers={'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig}, timeout=10).json()
            return float(res['result'].get('THB', 0)) if res.get('error') == 0 else 0.0
        except: return 0.0

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                           json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanMasterV17_2_Fixed().run()
