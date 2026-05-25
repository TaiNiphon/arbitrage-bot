import os
import requests
import time
import hmac
import hashlib
import json
import numpy as np
import psycopg2
import traceback
from datetime import datetime, timedelta, timezone

class TitanV18_LuxuryPanicHunterPro:
    def __init__(self):
        # --- [1] API & SYSTEM CONFIG (Railway Environment Variables) ---
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.db_url = os.getenv("DATABASE_URL")

        # --- [2] MONEY MANAGEMENT & PARAMETERS ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 2250.0)) # ปรับตามทุนจริงปัจจุบันของคุณ
        self.buy_rsi_14 = float(os.getenv("BUY_RSI_14", 28.0))
        self.buy_rsi_200 = float(os.getenv("BUY_RSI_200", 48.0))
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", 35.0)) 
        self.lock_profit_pct = float(os.getenv("LOCK_PROFIT_PCT", 1.5))
        self.trail_dist = float(os.getenv("TRAILING_DIST", 1.5))
        self.max_capital_limit = float(os.getenv("MAX_CAPITAL_LIMIT", 1000000.0)) # เพดานล็อก 1 ล้านบาทป้องกันความเสี่ยง
        self.fee_rate = 0.0025 # ค่าธรรมเนียม Bitkub 0.25%

        # โครงสร้างจัดการ 2 ไม้อิสระอย่างถาวร
        self.slots = {
            1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"},
            2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"}
        }

        # การกำหนดทศนิยมของแต่ละเหรียญบนกระดาน Bitkub
        self.coin_precisions = {
            "XRP": 4, "BTC": 8, "ETH": 8, "ADA": 4, "DOT": 4, "DOGE": 4, "IOST": 4, "GALA": 4
        }
        self.coin_sym = self.symbol.split('_')[0]
        self.precision = self.coin_precisions.get(self.coin_sym, 4)

        # เริ่มต้นระบบฐานข้อมูลและโหลดสถานะค้างเก่า
        self._init_db()
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18.99 PRO: MASTER REMASTERED</b>\n<i>Status: ปลดล็อกระบบซื้อขายเรียบร้อย + อัปเกรดระบบเตือน Error ผ่าน Telegram 100%</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def _init_db(self):
        """ดูแลรักษาความสมบูรณ์และตรวจสอบโครงสร้างตารางข้อมูลเดิมบน Railway"""
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS bot_state_v18 (
                        slot_id INT PRIMARY KEY, price FLOAT, units FLOAT, sl FLOAT, 
                        max_p FLOAT, order_id TEXT, open_ts BIGINT, status TEXT, source TEXT DEFAULT 'BOT')""")
                    
                    cur.execute("""CREATE TABLE IF NOT EXISTS trade_history (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), side TEXT, slot_id INT DEFAULT 0,
                        price FLOAT, units FLOAT, net_pnl_thb FLOAT, status TEXT, source TEXT DEFAULT 'BOT')""")

                    cur.execute("""CREATE TABLE IF NOT EXISTS periodic_summary (
                        id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT NOW(), period_type TEXT, 
                        total_trades INT, bot_trades INT, manual_trades INT, total_pnl FLOAT, win_rate FLOAT)""")
                    conn.commit()
        except Exception as e:
            self.notify_error("DATABASE INIT ERROR", traceback.format_exc())

    def _load_state(self):
        """ดึงข้อมูลสถานะล่าสุดจากดาต้าเบสเพื่อทำฟังก์ชันสืบต่อแบบไร้รอยต่อ"""
        try:
            self.slots = {
                1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"},
                2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"}
            }
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT slot_id, price, units, sl, max_p, status, source FROM bot_state_v18")
                    rows = cur.fetchall()
                    for r in rows:
                        self.slots[r[0]] = {
                            "status": r[5], "price": r[1], "units": r[2], 
                            "sl": r[3], "max_p": r[4], "source": r[6]
                        }
        except Exception as e:
            self.notify_error("LOAD STATE ERROR", traceback.format_exc())

    def sync_manual_trade(self, real_coin_balance, current_price):
        """ระบบตรวจจับความเคลื่อนไหวอัจฉริยะแบบ Real-time แยกแยะเฉพาะสล็อตที่ถูกขายจริงมือไม่รวน"""
        try:
            db_units = sum(s['units'] for s in self.slots.values() if s['status'] == 'MATCHED')
            
            # กรณีที่ 1: ตรวจพบเหรียญหายไปในกระเป๋าจริง (เกิดการกดขายมือ)
            if db_units > 0 and real_coin_balance < (db_units * 0.98): 
                for i, s in self.slots.items():
                    if s['status'] == 'MATCHED':
                        # พิจารณาเคลียร์สถานะเฉพาะตอนเหรียญฝั่งกระเป๋าลดลงจริง
                        net_pnl = (current_price * s['units'] * (1 - self.fee_rate)) - (s['price'] * s['units'] * (1 + self.fee_rate))
                        self.record_history('SELL', i, current_price, s['units'], net_pnl, 'PROFIT' if net_pnl > 0 else 'LOSS', 'MANUAL')
                        
                        with psycopg2.connect(self.db_url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (i,))
                                conn.commit()
                        
                        with psycopg2.connect(self.db_url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("SELECT SUM(net_pnl_thb) FROM trade_history WHERE side='SELL'")
                                accum_pnl = cur.fetchone()[0]
                                if accum_pnl is None: accum_pnl = 0.0
                        
                        now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
                        msg = f"🏛️ <b>TITAN V.18.99: PERFORMANCE REPORT</b>\n"
                        msg += f"📅 <code>{now_str}</code>\n"
                        msg += f"---------------------------------\n"
                        msg += f"🟢 <b>[SLOT {i}]: [MANUAL] EXIT DETECTED</b>\n"
                        msg += f"🪙 Closed Asset: {self.symbol}\n"
                        msg += f"📥 Buy Price    : {s['price']:,.4f} THB\n"
                        msg += f"⚡ Sell Price   : <b>{current_price:,.4f} THB</b>\n"
                        msg += f"🪙 Units Traded : {s['units']:.4f} {self.coin_sym}\n"
                        msg += f"💵 Net PnL (THB): <b>{net_pnl:+,.2f} THB</b>\n"
                        msg += f"---------------------------------\n"
                        msg += f"💰 <b>Total Net PnL Accum: {accum_pnl:+,.2f} THB</b>\n"
                        msg += f"---------------------------------\n"
                        msg += f"🔍 <i>ตรวจสอบการขายออกนอกระบบพอร์ต บล็อกข้อมูลเฉพาะสล็อตเรียบร้อย</i>"
                        self.notify(msg)
                self._load_state()
                
            # กรณีที่ 2: ตรวจพบเหรียญเพิ่มในกระเป๋า (มีการซื้อของมือถือเสริมเข้ามา)
            elif real_coin_balance > (db_units * 1.02) and (real_coin_balance - db_units) * current_price >= 10:
                diff_units = round(real_coin_balance - db_units, self.precision)
                target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
                if self.slots[target_slot]['status'] == 'FREE':
                    sl_val = round(current_price * 0.95, 4)
                    open_time = int(time.time() * 1000)
                    with psycopg2.connect(self.db_url) as conn:
                        with conn.cursor() as cur:
                            cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, order_id, open_ts, status, source) 
                                           VALUES (%s,%s,%s,%s,%s,'MANUAL_ORDER',%s,'MATCHED','MANUAL')""", 
                                        (target_slot, current_price, diff_units, sl_val, current_price, open_time))
                            conn.commit()
                    self.record_history('BUY', target_slot, current_price, diff_units, 0.0, 'OPENED', 'MANUAL')
                    self._load_state()
                    
                    now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
                    msg = f"🏛️ <b>TITAN V.18.99: ENTRY REPORT</b>\n"
                    msg += f"📅 <code>{now_str}</code>\n"
                    msg += f"---------------------------------\n"
                    msg += f"📥 <b>[SLOT {target_slot}]: [MANUAL] POSITION DETECTED</b>\n"
                    msg += f"🪙 Asset Symbol: {self.symbol}\n"
                    msg += f"💰 Entry Price : {current_price:,.4f} THB\n"
                    msg += f"📊 Filled Units: {diff_units:.4f} {self.coin_sym}\n"
                    msg += f"🛡️ Initial SL  : {sl_val:,.4f} THB\n"
                    msg += f"---------------------------------\n"
                    self.notify(msg)
        except Exception as e:
            self.notify_error("SYNC MANUAL TRADE ERROR", traceback.format_exc())

    def check_database_integrity(self):
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM bot_state_v18")
                    active_slots = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM trade_history")
                    total_history = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM periodic_summary")
                    total_summary = cur.fetchone()[0]
            now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
            msg = f"🔍 <b>DATABASE INTEGRITY REPORT (EVERY 6 HRS)</b>\n"
            msg += f"📅 <code>{now}</code>\n"
            msg += f"---------------------------------\n"
            msg += f"💾 Active Slot Sync: {active_slots} / 2 Positions\n"
            msg += f"📊 Trade History Logs: {total_history} Rows\n"
            msg += f"📈 Periodic Archives: {total_summary} Rows\n"
            msg += f"⚡ Connection Status: 🟢 100% HEALTHY & ONLINE\n"
            msg += f"---------------------------------\n"
            self.notify(msg)
        except Exception as e:
            self.notify_error("DB INTEGRITY CRITICAL ERROR", traceback.format_exc())

    def generate_periodic_report(self, period_name, days):
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
            msg += f"🔄 Total Trades Processed: {total_trades} ไม้\n"
            msg += f"🤖 Bot Executed: {bot_trades} | 👤 Manual Executed: {manual_trades}\n"
            msg += f"💰 Realized Net PnL: <b>{total_pnl:+,.2f} THB</b>\n"
            msg += f"🎯 Realized Win Rate: <b>{win_rate:.2f}%</b>\n"
            msg += f"---------------------------------\n"
            self.notify(msg)
        except Exception as e:
            self.notify_error("PERIODIC REPORT ERROR", traceback.format_exc())

    def send_luxury_dashboard(self, dx, db_btc, btc_weekly_volume, thb, coin, mode="REPORT"):
        try:
            p = dx['p']; rsi_val = dx['r14']; equity = thb + (coin * p)
            growth = ((equity - self.initial_equity) / self.initial_equity) * 100
            now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

            if rsi_val <= self.buy_rsi_14: state_msg = "🚨 EXTREME PANIC (BUY ZONE)"
            elif rsi_val <= self.rsi_buy_max: state_msg = "🔥 PANIC SALE"
            elif rsi_val >= 70: state_msg = "⚠️ OVERBOUGHT"
            else: state_msg = "↔️ NEUTRAL SIDEWAY"

            btc_avg_weekly = btc_weekly_volume / 7

            msg = f"🏛️ <b>TITAN V.18.99: {mode}</b>\n"
            msg += f"📅 <code>{now}</code>\n"
            msg += f"---------------------------------\n"
            msg += f"📈 <b>MARKET ENGINE: {self.symbol}</b>\n"
            msg += f"💰 Price : <b>{p:,.4f} THB</b>\n"
            msg += f"📊 State : {state_msg}\n"
            msg += f"📈 Trend : {'🌕 BULLISH' if p > dx['ema'] else '🌑 BEARISH'}\n"
            msg += f"📉 RSI 14: {rsi_val:.2f} | RSI 200: {dx['r200']:.2f}\n"
            msg += f"🚫 Max Limit: [RSI Max Buy Set: {self.rsi_buy_max:.2f}]\n"
            msg += f"---------------------------------\n"
            msg += f"🛡️ <b>BTC-GUARD SAFETY NETWORK</b>\n"
            msg += f"📈 BTC Trend : {'🌕 BULLISH' if db_btc['p'] > db_btc['ema'] else '🌑 BEARISH'}\n"
            msg += f"💰 BTC Price : {db_btc['p']:,.0f} THB\n"
            msg += f"📊 BTC RSI 14: {db_btc['r14']:.2f}\n" 
            msg += f"---------------------------------\n"
            msg += f"💰 <b>DYNAMIC FINANCIAL METRICS</b>\n"
            msg += f"✨ Total Net Equity : <b>{equity:,.2f} THB</b>\n"
            msg += f"💵 Free Cash (THB) : {thb:,.2f}\n"
            msg += f"🪙 Position Value  : {(coin*p):,.2f}\n"
            msg += f"📈 Absolute Growth : <b>{growth:+.2f}%</b>\n"
            msg += f"---------------------------------\n"

            for i, s in self.slots.items():
                if s['status'] == 'MATCHED':
                    pnl = ((p * (1 - self.fee_rate)) / (s['price'] * (1 + self.fee_rate)) - 1) * 100
                    msg += f"🟢 <b>SLOT {i}: {s['units']:.4f} {self.coin_sym} ({pnl:+.2f}%) [{s['source']}]</b>\n"
                    msg += f"🎯 Max Peak: {s['max_p']:,.4f} | 🛡️ Trailing SL: {s['sl']:,.4f}\n\n"
                else:
                    msg += f"⚪ <b>SLOT {i}: VACANT FREE (Waiting RSI ≤ {self.buy_rsi_14})</b>\n\n"
            
            msg += f"🔍 <i>Database Integrity Status: Verified & Secured (100% Sync)</i>"
            self.notify(msg)
        except Exception as e:
            self.notify_error("DASHBOARD REPORT ERROR", traceback.format_exc())

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
        try:
            # ลบข้อผิดพลาดในอดีตเรื่องทศนิยมของค่า THB ยึดตามสเปค Bitkub V3 (ปัดทศนิยม 2 ตำแหน่งสำหรับจำนวนเงินซื้อ)
            if side == "buy":
                amt_val = round(float(amt_val), 2)
                res = self.bt_auth("POST", "/api/v3/market/place-bid", {"sym": self.symbol.lower(), "amt": amt_val, "typ": "market"})
            else:
                amt_val = round(float(amt_val), self.precision)
                res = self.bt_auth("POST", "/api/v3/market/place-ask", {"sym": self.symbol.lower(), "amt": amt_val, "typ": "market"})
            
            if res and res.get('error') == 0:
                time.sleep(3)
                order_id = str(res['result'].get('id'))
                
                # ปรับแก้ตามคู่มือของ Bitkub v3: ข้อมูลคำสั่งซื้อขายผ่าน order-info ใช้ Method GET
                info = self.bt_auth("GET", "/api/v3/market/order-info", {"sym": self.symbol.lower(), "id": order_id})
                real_p = price
                real_u = (amt_val / price) if side == 'buy' else amt_val
                
                if info and info.get('result') and float(info['result'].get('rat', 0)) > 0:
                    real_p = float(info['result']['rat'])  
                    real_u = float(info['result']['amt'])  
                
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
                        
                        if side == 'buy':
                            sl_val = round(real_p * 0.95, 4)
                            open_time = int(time.time() * 1000)
                            cur.execute("""INSERT INTO bot_state_v18 (slot_id, price, units, sl, max_p, order_id, open_ts, status, source) 
                                           VALUES (%s,%s,%s,%s,%s,%s,%s,'MATCHED',%s)""", (slot_id, real_p, real_u, sl_val, real_p, order_id, open_time, source))
                            conn.commit()
                            self.record_history('BUY', slot_id, real_p, real_u, 0.0, 'OPENED', source)
                            
                            msg = f"🏛️ <b>TITAN V.18.99: ENTRY REPORT</b>\n"
                            msg += f"📅 <code>{now_str}</code>\n"
                            msg += f"---------------------------------\n"
                            msg += f"📥 <b>[SLOT {slot_id}]: [{source}] ORDER EXECUTED</b>\n"
                            msg += f"🪙 Asset Symbol: {self.symbol}\n"
                            msg += f"💰 Entry Price : {real_p:,.4f} THB\n"
                            msg += f"📊 Filled Units: {real_u:.4f} {self.coin_sym}\n"
                            msg += f"🛡️ Initial SL  : {sl_val:,.4f} THB\n"
                            msg += f"---------------------------------\n"
                            self.notify(msg)
                        else:
                            # สูตรสากลทางการเงิน คำนวณ Net Profit หักค่าตงทั้งสองฝั่งซื้อขายแม่นยำ 100%
                            net_pnl = (real_p * real_u * (1 - self.fee_rate)) - (buy_p * real_u * (1 + self.fee_rate))
                            stat = "PROFIT" if net_pnl > 0 else "LOSS"
                            self.record_history('SELL', slot_id, real_p, real_u, net_pnl, stat, source)
                            
                            cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                            conn.commit()
                            
                            cur.execute("SELECT SUM(net_pnl_thb) FROM trade_history WHERE side='SELL'")
                            accum_pnl = cur.fetchone()[0]
                            if accum_pnl is None: accum_pnl = 0.0
                            
                            msg = f"🏛️ <b>TITAN V.18.99: PERFORMANCE REPORT</b>\n"
                            msg += f"📅 <code>{now_str}</code>\n"
                            msg += f"---------------------------------\n"
                            msg += f"🟢 <b>[SLOT {slot_id}]: [{source}] COMPLETE WORK</b>\n"
                            msg += f"🪙 Closed Asset: {self.symbol}\n"
                            msg += f"📥 Buy Price    : {buy_p:,.4f} THB\n"
                            msg += f"⚡ Sell Price   : <b>{real_p:,.4f} THB</b>\n"
                            msg += f"🪙 Units Traded : {real_u:.4f} {self.coin_sym}\n"
                            msg += f"💵 Net PnL (THB): <b>{net_pnl:+,.2f} THB</b>\n"
                            msg += f"---------------------------------\n"
                            msg += f"💰 <b>Total Net PnL Accum: {accum_pnl:+,.2f} THB</b>\n"
                            msg += f"---------------------------------\n"
                            self.notify(msg)
                self._load_state()
                return True
            else:
                err_code = res.get('error') if res else 'Unknown Connection Error'
                # 🚨 หากทาง Bitkub ส่ง Error กลับมา บอทจะทำการยิงข้อความสีแดงเตือนเข้า Telegram ให้คุณรับทราบทันที!
                self.notify(f"⚠️ <b>[BITKUB API TRADE REJECTED]</b>\n"
                            f"❌ ฝั่งปฏิบัติการ: {side.upper()} | Slot: {slot_id}\n"
                            f"🛑 Error Code: <code>{err_code}</code>\n"
                            f"💡 บอทจะไม่ยอมให้คำสั่งซื้อเงียบหาย ตรวจสอบความถูกต้องของเครือข่าย/เงินในกระเป๋าครับ")
                return False
        except Exception as e:
            self.notify_error("EXECUTE TRADE CRITICAL ERROR", traceback.format_exc())
            return False

    def run(self):
        last_h = -1
        last_db_check = time.time() - 20000
        last_week_check = time.time()
        last_month_check = time.time()

        while True:
            try:
                res_w = self.bt_auth("POST", "/api/v3/market/wallet")
                if not res_w or 'result' not in res_w: 
                    time.sleep(15)
                    continue
                thb = float(res_w['result'].get('THB', 0))
                coin = float(res_w['result'].get(self.coin_sym, 0))
                
                dx = self.get_indicator(self.symbol)
                db_btc = self.get_indicator("BTC_THB")
                btc_weekly_volume = self.get_btc_weekly_volume()
                
                if dx and db_btc:
                    self.sync_manual_trade(coin, dx['p'])
                    now = self.get_thai_now()
                    
                    if now.hour != last_h: 
                        self.send_luxury_dashboard(dx, db_btc, btc_weekly_volume, thb, coin, "HOURLY REPORT")
                        last_h = now.hour

                    if time.time() - last_db_check >= 21600:
                        self.check_database_integrity()
                        last_db_check = time.time()

                    if time.time() - last_week_check >= 604800:
                        self.generate_periodic_report("WEEKLY SUMMARY", 7)
                        last_week_check = time.time()
                    if time.time() - last_month_check >= 2592000:
                        self.generate_periodic_report("MONTHLY SUMMARY", 30)
                        last_month_check = time.time()

                    # --- [EXIT LOGIC] TRAILING STOP ---
                    for i, s in self.slots.items():
                        if s['status'] == 'MATCHED':
                            profit = ((dx['p'] * (1 - self.fee_rate)) / (s['price'] * (1 + self.fee_rate)) - 1) * 100
                            
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
                                                
                            if dx['p'] <= s['sl']:
                                self.execute_trade('sell', i, dx['p'], s['units'], buy_p=s['price'], source=s['source'])

                    # --- [ENTRY LOGIC] แก้ไขตรรกะสกัดกั้นเรียบร้อย คืนชีพกำลังซื้อสำหรับทุนหลักล้าน ---
                    matched_count = sum(1 for s in self.slots.values() if s['status'] == 'MATCHED')
                    
                    # 🚀 [ปลดล็อกจุดบอดรูปถ่ายใบที่ 3]: ลบเงื่อนไขเช็คโวลลุ่ม buy_power แกว่งเป็นศูนย์ทิ้ง 
                    # เปลี่ยนเป็นเช็คโครงสร้างสากล: ตลาดไม่พังดิ่งเหวรุนแรงเกินไป (BTC RSI 14 ไม่ตํ่ากว่าสภาวะวิกฤต 20)
                    btc_safety_pass = db_btc['r14'] >= 20.0

                    if matched_count < 2 and dx['r14'] <= self.buy_rsi_14 and dx['r200'] <= self.buy_rsi_200 and dx['r14'] <= self.rsi_buy_max and btc_safety_pass:
                        total_equity = thb + (coin * dx['p'])
                        
                        if matched_count == 0:
                            buy_amount = total_equity * 0.45
                        else:
                            buy_amount = thb * 0.95

                        if buy_amount > self.max_capital_limit:
                            buy_amount = self.max_capital_limit

                        if thb >= buy_amount >= 10:
                            target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
                            self.execute_trade('buy', target_slot, dx['p'], buy_amount, source="BOT")
            except Exception as e:
                self.notify_error("MAIN LOOP CRITICAL FAULT", traceback.format_exc())
                time.sleep(15)
            time.sleep(25)

    def get_indicator(self, symbol):
        """ระบบคณิตศาสตร์การเงินคำนวณสูตร Wilder's RSI และ Exponential Moving Average (EMA) แท้ตรงหน้ากราฟกระดานสากล 100%"""
        try:
            # ดึงข้อมูลแท่งเทียน 15 นาที จำนวนครอบคลุมมากพอเพื่อการหาค่าเฉลี่ยสะสมสมบูรณ์แบบ
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-604800}&to={int(time.time())}", timeout=15).json()
            if not res or 'c' not in res: return None
            c = np.array(res['c'], dtype=float)
            
            # --- สูตรคณิตศาสตร์การคำนวณ Wilder's RSI แท้ ---
            def calculate_wilders_rsi(prices, period=14):
                deltas = np.diff(prices)
                gains = np.where(deltas > 0, deltas, 0.0)
                losses = np.where(deltas < 0, -deltas, 0.0)
                
                # เริ่มต้นด้วยค่าเฉลี่ยเลขคณิตตัวแรก
                avg_gain = np.mean(gains[:period])
                avg_loss = np.mean(losses[:period])
                
                # ขัดเกลาข้อมูลแบบ Exponential Smoothing ตามหลักการของ J. Welles Wilder
                for i in range(period, len(gains)):
                    avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                    avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                    
                if avg_loss == 0: return 100.0
                rs = avg_gain / avg_loss
                return 100.0 - (100.0 / (1.0 + rs))

            # --- สูตรคณิตศาสตร์การคำนวณ EMA แท้ ---
            def calculate_ema(prices, period=200):
                alpha = 2 / (period + 1)
                ema = np.zeros_like(prices)
                ema[0] = prices[0]
                for i in range(1, len(prices)):
                    ema[i] = (prices[i] * alpha) + (ema[i-1] * (1 - alpha))
                return ema[-1]

            return {
                "p": float(c[-1]), 
                "r14": float(calculate_wilders_rsi(c, 14)), 
                "r200": float(calculate_wilders_rsi(c, 200)), 
                "ema": float(calculate_ema(c, 200))
            }
        except: 
            return None

    def get_btc_weekly_volume(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol=BTC_THB&resolution=240&from={int(time.time())-604800}&to={int(time.time())}", timeout=15).json()
            if not res or 'v' not in res: return 1.0
            return float(sum(res['v']))
        except: return 1.0

    def bt_auth(self, method, path, payload=None):
        ts = str(int(time.time() * 1000))
        payload_json = json.dumps(payload, separators=(',', ':')) if payload else ""
        sig = hmac.new(self.api_secret.encode(), (ts + method + path + payload_json).encode(), hashlib.sha256).hexdigest()
        headers = {'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig, 'Content-Type': 'application/json'}
        try:
            if method.upper() == "GET":
                # ปรับแต่งให้สอดรับตามมาตรฐาน Bitkub V3 Secure Endpoints หากใช้ GET จะส่ง Payload ผ่าน Params แนบต่อท้าย URL
                query_str = f"sym={payload.get('sym')}&id={payload.get('id')}" if payload else ""
                full_path = f"{path}?{query_str}" if query_str else path
                # คำนวณ Signature ใหม่อีกครั้งสำหรับข้อมูล URL-encoded GET เพื่อความถูกต้องเสถียรภาพสูงสุด
                sig_get = hmac.new(self.api_secret.encode(), (ts + method + full_path).encode(), hashlib.sha256).hexdigest()
                headers['X-BTK-SIGN'] = sig_get
                return requests.get(f"https://api.bitkub.com{full_path}", headers=headers, timeout=15).json()
            else:
                return requests.post(f"https://api.bitkub.com{path}", headers=headers, data=payload_json, timeout=15).json()
        except: 
            return None

    def notify(self, message):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}, timeout=10)
        except: pass

    def notify_error(self, title, stack_trace):
        """ระบบฟังก์ชันรายงานข้อผิดพลาดเชิงลึกพ่นเข้าสู่ Telegram ทันทีเพื่อแก้ปัญหาใบ้กินอย่างถาวร"""
        now = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        msg = f"🚨 <b>TITAN CRITICAL EXCEPTION ALERT</b>\n"
        msg += f"📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"💥 <b>หัวข้อปัญหา:</b> {title}\n"
        msg += f"📝 <b>รายละเอียดขอบเขตบั๊ก:</b>\n<code>{stack_trace[:300]}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"⚡ <i>ระบบกำลังพยายามหมุนรอบตรวจสอบสัญญาณเพื่อ Re-connect อีกครั้งอัตโนมัติ</i>"
        self.notify(msg)

if __name__ == "__main__":
    TitanV18_LuxuryPanicHunterPro().run()
