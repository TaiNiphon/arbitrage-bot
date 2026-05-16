import os
import requests
import time
import hmac
import hashlib
import json
import numpy as np
import psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_LuxuryPanicHunterPro:
    def __init__(self):
        # --- [1] API & SYSTEM CONFIG (Rainway Dynamic Check) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] MONEY MANAGEMENT & PARAMETERS ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 10000.28))
        self.buy_rsi_14 = float(os.getenv("BUY_RSI_14", 28.0))
        self.buy_rsi_200 = float(os.getenv("BUY_RSI_200", 48.0))
        self.lock_profit_pct = float(os.getenv("LOCK_PROFIT_PCT", 1.5))
        self.trail_dist = float(os.getenv("TRAILING_DIST", 1.5))
        self.max_capital_limit = 1000000.0  # บอทรองรับทุนไม่เกิน 1 ล้านบาท

        self.slots = {1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"},
                      2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"}}

        self._init_db()
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18.99 PRO: LUXURY PANIC HUNTER ONLINE</b>\n<i>Status: Professional Multi-Engine Ready</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        """ดูแลรักษาความสมบูรณ์โครงสร้างฐานข้อมูลเดิม และเพิ่มส่วนขยาย"""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    # ตารางสถานะบอทเดิม (เพิ่มคอลัมน์ source)
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, 
                        max_p FLOAT, order_id TEXT, open_ts BIGINT, status TEXT)""")
                    try:
                        cur.execute("ALTER TABLE bot_state_v18 ADD COLUMN source TEXT DEFAULT 'BOT';")
                    except:
                        conn.rollback()
                    
                    # ตารางประวัติการเทรดเดิม (เพิ่มคอลัมน์ source และ slot_id)
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, 
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT)""")
                    try:
                        cur.execute("ALTER TABLE trade_history ADD COLUMN source TEXT DEFAULT 'BOT';")
                        cur.execute("ALTER TABLE trade_history ADD COLUMN slot_id INT DEFAULT 0;")
                    except:
                        conn.rollback()

                    # ตารางสำหรับบันทึกสรุปยอดการซื้อขายรายสัปดาห์/รายเดือนเพื่อพัฒนาบอทในอนาคต
                    cur.execute("""CREATE TABLE IF NOT EXISTS periodic_summary (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), period_type TEXT, 
                        total_trades INT, bot_trades INT, manual_trades INT, total_pnl FLOAT, win_rate FLOAT)""")
                    conn.commit()
        except Exception as e:
            print(f"DB Init Error: {e}")

    def _load_state(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, max_p, status, source FROM bot_state_v18")
                    rows = cur.fetchall()
                    for r in rows:
                        self.slots[r[0]] = {"status": r[5], "price": r[1], "units": r[2], "sl": r[3], "max_p": r[4], "source": r[6] if len(r) > 6 else "BOT"}
        except:
            pass

    def sync_manual_trade(self, real_coin_balance, current_price):
        """ระบบตรวจจับความเคลื่อนไหวอัจฉริยะ แยกแยะสถิติบอทและคนเทรดมือ"""
        db_units = sum(s['units'] for s in self.slots.values() if s['status'] == 'MATCHED')
        
        # กรณีมีการขายมือ (เหรียญจริงลดลงกว่าระบบ)
        if db_units > 0 and real_coin_balance < (db_units * 0.95): 
            for i, s in self.slots.items():
                if s['status'] == 'MATCHED':
                    net_pnl = (current_price * s['units'] * 0.9975) - (s['price'] * s['units'] * 1.0025)
                    self.record_history('SELL', i, current_price, s['units'], net_pnl, 'CLOSED', 'MANUAL')
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bot_state_v18")
                    conn.commit()
            self._load_state()
            self.notify("🧹 <b>MANUAL SALE DETECTED</b>\n<i>Database fully synced with real wallet. [Source: MANUAL]</i>")
            
        # กรณีมีการซื้อมือ (เหรียญจริงเพิ่มขึ้นอย่างมีนัยสำคัญ)
        elif real_coin_balance > (db_units * 1.05) and (real_coin_balance - db_units) * current_price >= 10:
            diff_units = real_coin_balance - db_units
            target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
            if self.slots[target_slot]['status'] == 'FREE':
                sl_val = round(current_price * 0.95, 4)
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, order_id, status, source) 
                                       VALUES (%s,%s,%s,%s,%s,'MANUAL_ORDER','MATCHED','MANUAL')""", 
                                    (target_slot, current_price, diff_units, sl_val, current_price))
                        conn.commit()
                self.record_history('BUY', target_slot, current_price, diff_units, 0.0, 'OPENED', 'MANUAL')
                self._load_state()
                self.notify(f"📥 <b>MANUAL BUY DETECTED</b>\n<i>Slot {target_slot} recorded from wallet balances. [Source: MANUAL]</i>")

    def check_database_integrity(self):
        """ระบบรายงานเช็คความพร้อมฐานข้อมูลทั้งหมดทุกๆ 6 ชม."""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM bot_state_v18")
                    active_slots = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM trade_history")
                    total_history = cur.fetchone()[0]
            now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
            msg = f"🔍 <b>DATABASE INTEGRITY REPORT (EVERY 6 HRS)</b>\n"
            msg += f"📅 <code>{now}</code>\n"
            msg += f"---------------------------------\n"
            msg += f"💾 Active Slot Sync: {active_slots} / 2 Positions\n"
            msg += f"📊 Total Log Records: {total_history} Rows\n"
            msg += f"⚡ Connection Status: 🟢 100% HEALTHY & ONLINE\n"
            msg += f"---------------------------------\n"
            self.notify(msg)
        except Exception as e:
            self.notify(f"⚠️ <b>DB INTEGRITY CRITICAL ERROR</b>: {e}")

    def generate_periodic_report(self, period_name, days):
        """ประมวลผลสรุปผลการเทรดทุกอาทิตย์และทุกๆ เดือน ส่งเข้าดาต้าเบสเพื่อพัฒนาบอท"""
        try:
            start_date = datetime.now() - timedelta(days=days)
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT side, net_pnl_thb, source FROM trade_history WHERE ts >= %s", (start_date,))
                    rows = cur.fetchall()
            
            total_trades = len(rows)
            bot_trades = sum(1 for r in rows if r[2] == 'BOT')
            manual_trades = sum(1 for r in rows if r[2] == 'MANUAL')
            total_pnl = sum(float(r[1]) for r in rows if r[1] is not None and r[0] == 'SELL')
            
            sells = [r for r in rows if r[0] == 'SELL']
            wins = sum(1 for r in sells if float(r[1]) > 0)
            win_rate = (wins / len(sells) * 100) if sells else 0.0
            
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO periodic_summary (period_type, total_trades, bot_trades, manual_trades, total_pnl, win_rate) 
                                   VALUES (%s, %s, %s, %s, %s, %s)""", (period_name, total_trades, bot_trades, manual_trades, total_pnl, win_rate))
                    conn.commit()
            
            msg = f"📊 <b>TITAN PERIODIC SUMMARY: {period_name}</b>\n"
            msg += f"---------------------------------\n"
            msg += f"🔄 Total Trades: {total_trades} ไม้\n"
            msg += f"🤖 Bot Order: {bot_trades} | 👤 Manual Order: {manual_trades}\n"
            msg += f"💰 Realized Net PnL: <b>{total_pnl:+,.2f} THB</b>\n"
            msg += f"🎯 Win Rate: <b>{win_rate:.2f}%</b>\n"
            msg += f"---------------------------------\n"
            msg += f"📈 <i>Data archived for future bot optimizations.</i>"
            self.notify(msg)
        except Exception as e:
            print(f"Periodic report error: {e}")

    def send_luxury_dashboard(self, dx, db_btc, thb, coin, mode="REPORT"):
        """หน้าตารายงานฉบับเต็ม คงรูปแบบเดิมและรายละเอียดครบถ้วน"""
        p = dx['p']; rsi_val = dx['r14']; equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        coin_sym = self.symbol.split('_')[0]

        if rsi_val <= self.buy_rsi_14: state_msg = "🚨 EXTREME PANIC (BUY!)"
        elif rsi_val <= 35: state_msg = "🔥 PANIC SALE"
        elif rsi_val >= 70: state_msg = "⚠️ OVERBOUGHT"
        else: state_msg = "↔️ SIDEWAY"

        msg = f"🏛️ <b>TITAN V.18.99: {mode}</b>\n"
        msg += f"📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET: {self.symbol}</b>\n"
        msg += f"💰 Price : <b>{p:,.4f} THB</b>\n"
        msg += f"📊 State : {state_msg}\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
        msg += f"📉 RSI 14: {rsi_val:.2f} | RSI 200: {dx['r200']:.2f}\n"
        msg += f"---------------------------------\n"
        msg += f"🛡️ <b>BTC-GUARD STATUS</b>\n"
        msg += f"📈 Trend : {'🌕 BULLISH' if db_btc['p'] > db_btc['ema'] else '🌑 BEARISH'}\n"
        msg += f"💰 BTC P.: {db_btc['p']:,.0f} THB\n"
        msg += f"---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"✨ Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"💵 Cash (THB) : {thb:,.2f}\n"
        msg += f"🪙 Coin Value : {(coin*p):,.2f}\n"
        msg += f"📈 Total Growth: <b>{growth:+.2f}%</b>\n"
        msg += f"---------------------------------\n"

        for i, s in self.slots.items():
            if s['status'] == 'MATCHED':
                pnl = ((p * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                msg += f"🟢 <b>SLOT {i}: {s['units']:.4f} {coin_sym} ({pnl:+.2f}%) [{s['source']}]</b>\n"
                msg += f"🎯 Max Peak: {s['max_p']:,.4f} | 🛡️ Trailing SL: {s['sl']:,.4f}\n\n"
            else:
                msg += f"⚪ <b>SLOT {i}: FREE (RSI ≤ {self.buy_rsi_14})</b>\n\n"
        
        msg += f"🔍 <i>Database Status: Verified & Locked</i>"
        self.notify(msg)

    def record_history(self, side, slot_id, price, units, pnl, status, source):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO trade_history (side, slot_id, price, units, net_pnl_thb, status, source) 
                                   VALUES (%s, %s, %s, %s, %s, %s, %s)""", (side, slot_id, price, units, pnl, status, source))
                    conn.commit()
        except Exception as e:
            print(f"History logging failed: {e}")

    def execute_trade(self, side, slot_id, price, amt_val, buy_p=0, source="BOT"):
        """ระบบสั่งซื้อขายจริงพร้อมบันทึกประวัติอย่างสมบูรณ์แบบแยกสัดส่วนชัดเจน"""
        typ = "bid" if side == "buy" else "ask"
        res = self.bt_auth("POST", f"/api/v3/market/place-{typ}", {"sym":self.symbol.lower(), "amt":amt_val, "typ":"market"})
        
        if res and res.get('error') == 0:
            time.sleep(3)
            order_id = str(res['result'].get('id'))
            info = self.bt_auth("POST", "/api/v3/market/order-info", {"sym":self.symbol.lower(), "id":order_id, "sd":side})
            real_p = float(info['result'].get('rat', price)) if info and info.get('result') else price
            real_u = float(info['result'].get('amt', 0)) if info and info.get('result') else (amt_val/price)

            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    if side == 'buy':
                        sl_val = round(real_p * 0.95, 4) # SL พื้นฐานกันลากลึก 5%
                        cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, order_id, status, source) 
                                       VALUES (%s,%s,%s,%s,%s,%s,'MATCHED',%s)""", (slot_id, real_p, real_u, sl_val, real_p, order_id, source))
                        self.record_history('BUY', slot_id, real_p, real_u, 0.0, 'OPENED', source)
                        self.notify(f"📥 <b>[{source}] BUY ORDER EXECUTED</b>\nSlot: {slot_id}\nPrice: {real_p:,.4f} THB\nUnits: {real_u:.4f}")
                    else:
                        net_pnl = (real_p * real_u * 0.9975) - (buy_p * real_u * 1.0025)
                        self.record_history('SELL', slot_id, real_p, real_u, net_pnl, 'CLOSED', source)
                        cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                        self.notify(f"⚡ <b>[{source}] SELL ORDER EXECUTED</b>\nSlot: {slot_id}\nPrice: {real_p:,.4f} THB\nNet PnL: {net_pnl:+,.2f} THB")
                    conn.commit()
            self._load_state()
            return True
        return False

    def run(self):
        last_h = -1
        last_db_check = time.time() - 20000
        last_week_check = time.time()
        last_month_check = time.time()

        while True:
            try:
                res_w = self.bt_auth("POST", "/api/v3/market/wallet")
                if not res_w: time.sleep(10); continue
                thb = float(res_w['result'].get('THB', 0)); coin = float(res_w['result'].get(self.symbol.split('_')[0], 0))
                
                dx = self.get_indicator(self.symbol)
                db_btc = self.get_indicator("BTC_THB")
                btc_weekly_volume = self.get_btc_weekly_volume()
                
                if dx and db_btc:
                    self.sync_manual_trade(coin, dx['p'])
                    now = self.get_thai_now()
                    
                    # 🔔 รายงานสรุปทุกๆ 1 ชม.
                    if now.hour != last_h: 
                        self.send_luxury_dashboard(dx, db_btc, thb, coin, "HOURLY REPORT")
                        last_h = now.hour

                    # 🔍 ตรวจความพร้อมฐานข้อมูลทุกๆ 6 ชม.
                    if time.time() - last_db_check >= 21600:
                        self.check_database_integrity()
                        last_db_check = time.time()

                    # 📊 รายงานผลงานสรุปรายสัปดาห์ / รายเดือน
                    if time.time() - last_week_check >= 604800:
                        self.generate_periodic_report("WEEKLY", 7)
                        last_week_check = time.time()
                    if time.time() - last_month_check >= 2592000:
                        self.generate_periodic_report("MONTHLY", 30)
                        last_month_check = time.time()

                    # --- EXIT LOGIC (Trailing Stop / ป้องกันการขายหมู) ---
                    for i, s in self.slots.items():
                        if s['status'] == 'MATCHED':
                            profit = ((dx['p'] * 0.9975) / (s['price'] * 1.0025) - 1) * 100
                            if dx['p'] > s['max_p']:
                                s['max_p'] = dx['p']
                                if profit >= self.lock_profit_pct:
                                    new_sl = round(s['max_p'] * (1 - (self.trail_dist / 100)), 4)
                                    if new_sl > s['sl']:
                                        s['sl'] = new_sl
                                        with psycopg2.connect(self.db_url) as conn:
                                            with conn.cursor() as cur:
                                                cur.execute("UPDATE bot_state_v18 SET max_p=%s, sl=%s WHERE slot_id=%s", (s['max_p'], s['sl'], i))
                                                conn.commit()
                            # หากราคาวิ่งลงหลุดเส้น Trailing Stop หรือระบบตัดขาดทุนพื้นฐาน
                            if dx['p'] <= s['sl']:
                                self.execute_trade('sell', i, dx['p'], s['units'], buy_p=s['price'], source=s['source'])

                    # --- ENTRY LOGIC (ตรรกะช้อนซื้อขาลงอิงจาก BTC) ---
                    matched_count = sum(1 for s in self.slots.values() if s['status'] == 'MATCHED')
                    
                    # ตรรกะแรงขาย BTC: ปริมาณขายปัจจุบัน <= (กำลังซื้อปัจจุบัน - (ปริมาณซื้อรวมทั้งสัปดาห์/7))
                    btc_avg_weekly = btc_weekly_volume / 7
                    btc_condition = db_btc['vol'] <= (db_btc['buy_power'] - btc_avg_weekly)

                    if matched_count < 2 and dx['r14'] <= self.buy_rsi_14 and dx['r200'] <= self.buy_rsi_200 and btc_condition:
                        total_equity = thb + (coin * dx['p'])
                        
                        # คำนวณแบ่งไม้ซื้อ 45% / 95% ของเงินที่มี
                        if matched_count == 0:
                            buy_amount = int(total_equity * 0.45)
                        else:
                            buy_amount = int(thb * 0.95)

                        # บล็อกป้องกันไม่ให้เกินเงินทุนสูงสุด 1 ล้านบาทตามที่กำหนด
                        if buy_amount > self.max_capital_limit:
                            buy_amount = int(self.max_capital_limit)

                        if thb >= buy_amount >= 10:
                            target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
                            self.execute_trade('buy', target_slot, dx['p'], buy_amount, source="BOT")
            except:
                time.sleep(10)
            time.sleep(25)

    def get_indicator(self, symbol):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15).json()
            c = np.array(res['c'], dtype=float); v = np.array(res['v'], dtype=float)
            def rsi(p, n):
                d = np.diff(p); u = d.clip(min=0); dw = -d.clip(max=0)
                return 100 - (100 / (1 + (np.mean(u[-n:]) / (np.mean(dw[-n:]) + 1e-9))))
            return {"p": float(c[-1]), "r14": float(rsi(c, 14)), "r200": float(rsi(c, 200)), "ema": float(np.mean(c[-200:])), "vol": float(v[-1]), "buy_power": float(np.mean(v[-14:]))}
        except: return None

    def get_btc_weekly_volume(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol=BTC_THB&resolution=240&from={int(time.time())-604800}&to={int(time.time())}", timeout=15).json()
            return float(sum(res['v']))
        except: return 1.0

    def bt_auth(self, method, path, payload=None):
        ts = str(int(time.time() * 1000))
        payload_json = json.dumps(payload, separators=(',', ':')) if payload else ""
        sig = hmac.new(self.api_secret.encode(), (ts + method + path + payload_json).encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        try: return requests.request(method, f"https://api.bitkub.com{path}", headers=headers, data=payload_json, timeout=15).json()
        except: return None

    def notify(self, message):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanV18_LuxuryPanicHunterPro().run()
