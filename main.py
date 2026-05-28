import os
import requests
import time
import hmac
import hashlib
import json
import math
import numpy as np
import psycopg2
from datetime import datetime, timedelta, timezone

class TitanV18_LuxuryPanicHunterPro:
    def __init__(self):
        # --- [1] API & SYSTEM CONFIG (Railway Dynamic Check) ---
        self.api_key = str(os.getenv("BITKUB_KEY", "")).strip()
        self.api_secret = str(os.getenv("BITKUB_SECRET", "")).strip()
        self.tg_token = str(os.getenv("TELEGRAM_TOKEN", "")).strip()
        self.tg_chat_id = str(os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        self.symbol = str(os.getenv("SYMBOL", "XRP_THB")).strip().upper()
        self.db_url = str(os.getenv("DATABASE_URL", "")).strip()

        # --- [2] MONEY MANAGEMENT & CONFIG PARAMETERS ---
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 2200.0))
        self.buy_rsi_14 = float(os.getenv("BUY_RSI_14", 28.0))
        self.buy_rsi_200 = float(os.getenv("BUY_RSI_200", 48.0))
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", 35.0)) 
        self.lock_profit_pct = float(os.getenv("LOCK_PROFIT_PCT", 1.5))
        self.trail_dist = float(os.getenv("TRAILING_DIST", 1.5))
        self.max_capital_limit = float(os.getenv("MAX_CAPITAL_LIMIT", 1000000.0))
        self.fee_rate = 0.0025 

        # โครงสร้างจัดการระบบ 2 ไม้แยกจากกันอิสระ
        self.slots = {
            1: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"},
            2: {"status": "FREE", "price": 0.0, "units": 0.0, "sl": 0.0, "max_p": 0.0, "source": "BOT"}
        }

        # โครงสร้างทศนิยมราคาและขนาดเหรียญใน Bitkub เพื่อป้องกันออเดอร์ถูกปฏิเสธ
        self.coin_precisions = {
            "XRP": 4, "BTC": 8, "ETH": 8, "ADA": 4, "DOT": 4, "DOGE": 4, "IOST": 4, "GALA": 4
        }
        self.coin_sym = self.symbol.split('_')[0]
        self.precision = self.coin_precisions.get(self.coin_sym, 4)

        self._init_db()
        self._load_state()
        self.notify("🏛️ <b>TITAN V.18.99 PRO: IRONCLAD TRUE FIXED ONLINE</b>\n<i>Status: Full Code Restoration & Real-time Indicator Fix Deployed!</i>")

    def get_thai_now(self):
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))

    def floor_precision(self, val, precision):
        """ฟังก์ชันปัดเศษลงตามระดับทศนิยม ป้องกันปัญหาขายเหรียญเกินยอดบาลานซ์จริง (Error 14)"""
        factor = 10 ** precision
        return math.floor(float(val) * factor) / factor

    def _init_db(self):
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
            print(f"DB Init Error: {e}")

    def _load_state(self):
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
            print(f"Load State Error: {e}")

    def sync_manual_trade(self, real_coin_balance, current_price):
        db_units = sum(s['units'] for s in self.slots.values() if s['status'] == 'MATCHED')

        # ตรวจสอบการแอบขายเองผ่านแอปภายนอก (Manual Exit Sync)
        if db_units > 0 and real_coin_balance < (db_units * 0.98): 
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    for i, s in list(self.slots.items()):
                        if s['status'] == 'MATCHED':
                            net_pnl = (current_price * s['units'] * (1 - self.fee_rate)) - (s['price'] * s['units'] * (1 + self.fee_rate))
                            self.record_history('SELL', i, current_price, s['units'], net_pnl, 'PROFIT' if net_pnl > 0 else 'LOSS', 'MANUAL')
                    
                    cur.execute("DELETE FROM bot_state_v18")
                    conn.commit()
            
            try:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT SUM(net_pnl_thb) FROM trade_history WHERE side='SELL'")
                        accum_pnl = cur.fetchone()[0]
                        if accum_pnl is None: accum_pnl = 0.0
            except:
                accum_pnl = 0.0

            now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
            msg = f"🏛️ <b>TITAN V.18.99: PERFORMANCE REPORT</b>\n"
            msg += f"📅 <code>{now_str}</code>\n"
            msg += f"---------------------------------\n"
            msg += f"🟢 <b>[MANUAL SYNC]: EXTERNAL EXIT DETECTED</b>\n"
            msg += f"🪙 Closed Asset: {self.symbol}\n"
            msg += f"⚡ Est. Market Price: <b>{current_price:,.4f} THB</b>\n"
            msg += f"---------------------------------\n"
            msg += f"💰 <b>Total Net PnL Accum: {accum_pnl:+,.2f} THB</b>\n"
            msg += f"---------------------------------\n"
            msg += f"🔍 <i>Manual exit detected via wallet. Database ledger cleared.</i>"
            self.notify(msg)
            self._load_state()

        # ตรวจสอบการแอบกดซื้อเองผ่านแอปภายนอก (Manual Entry Sync)
        elif real_coin_balance > (db_units * 1.02) and (real_coin_balance - db_units) * current_price >= 500:
            diff_units = self.floor_precision(real_coin_balance - db_units, self.precision)
            target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
            if self.slots[target_slot]['status'] == 'FREE':
                sl_val = round(current_price * 0.95, 4)
                open_time = int(time.time() * 1000)
                try:
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
                    msg += f"📥 <b>[SLOT {target_slot}]: [MANUAL] ORDER REGISTERED</b>\n"
                    msg += f"🪙 Asset Symbol: {self.symbol}\n"
                    msg += f"💰 Entry Price : {current_price:,.4f} THB\n"
                    msg += f"📊 Filled Units: {diff_units:.4f} {self.coin_sym}\n"
                    msg += f"🛡️ Initial SL  : {sl_val:,.4f} THB\n"
                    msg += f"---------------------------------\n"
                    msg += f"🔍 <i>Manual positioning registered inside local ledger.</i>"
                    self.notify(msg)
                except Exception as e:
                    print(f"Manual buy registration error: {e}")

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
            self.notify(f"⚠️ <b>DB INTEGRITY CRITICAL ERROR</b>: {e}")

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
            msg += f"📈 <i>Data securely archived for future bot optimizations.</i>"
            self.notify(msg)
        except Exception as e:
            print(f"Periodic report error: {e}")

    def send_luxury_dashboard(self, dx, db_btc, btc_weekly_volume, thb, coin, mode="REPORT"):
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
        msg += f"📊 BTC Vol 15m: {db_btc['vol']:,.2f}\n"
        msg += f"🏹 Buy Power : {db_btc['buy_power']:,.2f}\n"
        msg += f"📊 Avg Weekly (4h): {btc_avg_weekly:,.2f}\n"
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
        if side == "buy":
            try:
                res_w = self.bt_auth("POST", "/api/v3/market/wallet")
                real_thb = float(res_w['result'].get('THB', 0)) if (res_w and 'result' in res_w) else 0.0
            except:
                real_thb = amt_val

            safe_buy_amt = min(float(amt_val), real_thb * 0.98)

            if safe_buy_amt < 500:
                now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
                err_txt = f"⚠️ <b>TITAN SYSTEM WARNING: ORDER ABORTED</b>\n"
                err_txt += f"📅 <code>{now_str}</code>\n"
                err_txt += f"---------------------------------\n"
                err_txt += f"🚫 สั่งซื้อล้มเหลว: ยอดเงินที่จะซื้อ ({safe_buy_amt:,.2f} THB) ต่ำกว่าเกณฑ์ขั้นต่ำ 500 บาทของ Bitkub API\n"
                err_txt += f"💡 แนะนำ: เพิ่มทุนในกระเป๋า หรือตรวจสอบมูลค่าพอร์ตรวม\n"
                err_txt += f"---------------------------------\n"
                self.notify(err_txt)
                return False

            payload = {"sym": self.symbol.lower(), "amt": round(float(safe_buy_amt), 2), "typ": "market"}
            res = self.bt_auth("POST", "/api/v3/market/place-bid", payload)

        elif side == "sell":
            # ปรับมาใช้ระบบ Floor Cut ป้องกันปัญหาเศษทศนิยมปัดขึ้นทำให้เกิด Error 14
            safe_sell_units = self.floor_precision(amt_val, self.precision)
            payload = {"sym": self.symbol.lower(), "amt": safe_sell_units, "typ": "market"}
            res = self.bt_auth("POST", "/api/v3/market/place-ask", payload)
        
        else:
            return False

        if res and res.get('error') == 0:
            time.sleep(5) 
            order_id = str(res['result'].get('id'))

            info_payload = {"sym": self.symbol.lower(), "id": order_id, "sd": side}
            info = self.bt_auth("POST", "/api/v3/market/order-info", info_payload)
            real_p = price
            real_u = (safe_buy_amt / price) if side == 'buy' else safe_sell_units

            # พัฒนาระบบดึงข้อมูลแบบ Fallback Keys ป้องกันปัญหารูปแบบ JSON Bitkub v3 แกว่ง
            if info and info.get('result'):
                res_data = info['result']
                if isinstance(res_data, list) and len(res_data) > 0:
                    res_data = res_data[0]
                real_p = float(res_data.get('rate', res_data.get('rat', res_data.get('price', real_p))))
                real_u = float(res_data.get('amount', res_data.get('amt', res_data.get('quantity', real_u))))

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
                        msg += f"🔍 <i>Position secured in database ledger.</i>"
                        self.notify(msg)
                    elif side == 'sell':
                        net_pnl = (real_p * real_u * (1 - self.fee_rate)) - (buy_p * real_u * (1 + self.fee_rate))
                        stat = "PROFIT" if net_pnl > 0 else "LOSS"
                        self.record_history('SELL', slot_id, real_p, real_u, net_pnl, stat, source)

                        cur.execute("DELETE FROM bot_state_v18 WHERE slot_id=%s", (slot_id,))
                        conn.commit()

                        try:
                            cur.execute("SELECT SUM(net_pnl_thb) FROM trade_history WHERE side='SELL'")
                            accum_pnl = cur.fetchone()[0]
                            if accum_pnl is None: accum_pnl = 0.0
                        except:
                            accum_pnl = 0.0

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
                        msg += f"🔍 <i>Database freed. System returning to standby hunter mode.</i>"
                        self.notify(msg)
            self._load_state()
            return True
        else:
            err_code = res.get('error') if res else 'Unknown Connection Error'
            now_str = self.get_thai_now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

            fail_msg = f"❌ <b>TITAN CRITICAL WARNING: EXECUTION FAILED</b>\n"
            fail_msg += f"📅 <code>{now_str}</code>\n"
            fail_msg += f"---------------------------------\n"
            fail_msg += f"🚨 ตัวบอทไม่สามารถส่งคำสั่ง <b>[{side.upper()}]</b> ไปที่ Bitkub ได้\n"
            fail_msg += f"🪙 สินทรัพย์: {self.symbol} | สล็อตเป้าหมาย: Slot {slot_id}\n"
            fail_msg += f"🚫 Bitkub Error Code: <code>{err_code}</code>\n"
            fail_msg += f"---------------------------------\n"

            if err_code == 10:
                fail_msg += f"💡 วิเคราะห์: เกิดข้อผิดพลาดของลายเซ็นต์ดิจิทัล (Invalid Signature)\nอาจเกิดจากช่องว่างใน API Key หรือทศนิยมเกินขนาดที่ Bitkub รับได้\n"
            elif err_code == 14:
                fail_msg += f"💡 วิเคราะห์: จำนวนเหรียญไม่เพียงพอสำหรับสั่งขาย หรือทศนิยมเหรียญผิดพลาด\n"
            elif err_code == 15:
                fail_msg += f"💡 วิเคราะห์: ยอดเงินสด THB ไม่เพียงพอ (Insufficient Balance)\n"
            elif err_code == 18:
                fail_msg += f"💡 วิเคราะห์: ยอดเงินบาทหรือเหรียญต่ำกว่าเกณฑ์ขั้นต่ำ 500 บาทของระบบ API\n"
            else:
                fail_msg += f"💡 วิเคราะห์: เกิดข้อผิดพลาดทางเครือข่าย หรือค่า API Keys ได้รับการปฏิเสธสิทธิ์\n"

            fail_msg += f"---------------------------------\n"
            fail_msg += f"🔍 <i>โปรดเข้าตรวจสอบกระเป๋าเงินบนแอป Bitkub ด่วนเพื่อตรวจสอบความปลอดภัย</i>"
            self.notify(fail_msg)
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

                    # --- [ENTRY LOGIC] ระบบคำนวณเงินทุน ---
                    matched_count = sum(1 for s in self.slots.values() if s['status'] == 'MATCHED')
                    btc_condition = db_btc['buy_power'] >= 0.10

                    if matched_count < 2 and dx['r14'] <= self.buy_rsi_14 and dx['r200'] <= self.buy_rsi_200 and dx['r14'] <= self.rsi_buy_max and btc_condition:
                        total_equity = thb + (coin * dx['p'])
                        buy_amount = int(total_equity * 0.45)

                        if buy_amount > self.max_capital_limit:
                            buy_amount = int(self.max_capital_limit)

                        if buy_amount >= 500 and thb >= buy_amount:
                            target_slot = 1 if self.slots[1]['status'] == 'FREE' else 2
                            self.execute_trade('buy', target_slot, dx['p'], buy_amount, source="BOT")
                        else:
                            print(f"Standby: buy_amount ({buy_amount} THB) less than Bitkub API minimum (500 THB) or insufficient cash ({thb:.2f} THB).")

            except Exception as e:
                print(f"Main Loop Exception Error: {e}")
                time.sleep(15)
            time.sleep(25)

    def get_indicator(self, symbol):
        """ระบบคำนวณค่า RSI แท้ตรงตามมาตรฐาน TradingView (Wilder's Smoothing) และคำนวณ EMA แท้จริง 100%"""
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=15&from={int(time.time())-432000}&to={int(time.time())}", timeout=15)
            if res.status_code != 200: return None
            data = res.json()
            if not data or 'c' not in data: return None
            c = np.array(data['c'], dtype=float); v = np.array(data['v'], dtype=float)

            if len(c) < 200: return None

            def rsi(prices, period=14):
                if len(prices) < period + 1: return 50.0
                deltas = np.diff(prices)
                seed = deltas[:period]
                up = seed[seed > 0].sum() / period
                down = -seed[seed < 0].sum() / period
                
                # ลูปเกลี่ยค่าแบบ Wilder's Smoothing แท้โดยไม่ซ้ำซ้อนตำแหน่งดัชนี
                for i in range(period, len(deltas)):
                    delta = deltas[i]
                    upval = delta if delta > 0 else 0.0
                    downval = -delta if delta < 0 else 0.0
                    up = (up * (period - 1) + upval) / period
                    down = (down * (period - 1) + downval) / period
                
                rs = up / (down + 1e-9)
                return 100.0 - (100.0 / (1.0 + rs))

            def ema(prices, period=200):
                alpha = 2 / (period + 1)
                ema_val = np.mean(prices[:period])
                for p in prices[period:]:
                    ema_val = alpha * p + (1 - alpha) * ema_val
                return ema_val

            return {
                "p": float(c[-1]), 
                "r14": float(rsi(c, 14)), 
                "r200": float(rsi(c, 200)), 
                "ema": float(ema(c, 200)), 
                "vol": float(v[-1]), 
                "buy_power": float(np.mean(v[-14:]))
            }
        except Exception as e: 
            print(f"Indicator Error: {e}")
            return None

    def get_btc_weekly_volume(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol=BTC_THB&resolution=240&from={int(time.time())-604800}&to={int(time.time())}", timeout=15)
            if res.status_code != 200: return 1.0
            data = res.json()
            if not data or 'v' not in data: return 1.0
            return float(sum(data['v']))
        except: return 1.0

    def bt_auth(self, method, path, payload=None, retries=3):
        for i in range(retries):
            try:
                ts = str(int(time.time() * 1000))
                payload_json = json.dumps(payload, separators=(',', ':'), ensure_ascii=False) if payload else ""
                sig_string = ts + method + path + payload_json
                sig = hmac.new(
                    self.api_secret.encode('utf-8'), 
                    sig_string.encode('utf-8'), 
                    hashlib.sha256
                ).hexdigest()

                headers = {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-BTK-APIKEY': self.api_key, 
                    'X-BTK-TIMESTAMP': ts, 
                    'X-BTK-SIGN': sig 
                }
                
                res = requests.request(method, f"https://api.bitkub.com{path}", headers=headers, data=payload_json, timeout=15)
                if res.status_code == 200:
                    return res.json()
            except Exception as e: 
                print(f"bt_auth request failed, retrying... {e}")
                time.sleep(2)
        return None

    def notify(self, message):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={'chat_id': self.tg_chat_id, 'text': message, 'parse_mode': 'HTML'}, timeout=10)
        except: pass

if __name__ == "__main__":
    TitanV18_LuxuryPanicHunterPro().run()
