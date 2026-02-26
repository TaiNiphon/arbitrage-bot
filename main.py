import os
import requests
import time
import hmac
import hashlib
import json
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone


# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # API & Notification Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        # แนะนำปรับ INITIAL_EQUITY ใน Railway Variables ให้เป็นยอดรวมปัจจุบัน (Cash + Coin Value)
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5027.00)) 
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        # State Persistence
        self.state_file = "bot_state_v5.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        
        # ตั้งเป็น 0 เพื่อให้ส่งรายงานฉบับเต็มทันทีที่เริ่มรันครั้งแรก
        self.last_report_time = 0 

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def _get_signature(self, ts, method, path, body_str):
        payload = ts + method + path + body_str
        return hmac.new(self.api_secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, payload=None, private=False):
        url = f"{self.host}{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            try:
                ts = requests.get(f"{self.host}/api/v3/servertime", timeout=10).text.strip()
                headers.update({
                    'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                    'X-BTK-SIGN': self._get_signature(ts, method, path, body_str)
                })
            except Exception as e:
                logger.error(f"ServerTime Error: {e}")
                return {"error": 999}
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except Exception as e:
            logger.error(f"Request Error: {e}")
            return {"error": 999}

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price
                }, f)
        except: pass

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    return d['last_action'], d['avg_price'], d['stage'], d.get('total_units', 0.0), d.get('highest_price', 0.0)
            except: pass
        return "sell", 0.0, 0, 0.0, 0.0

    def get_balance(self):
        """ดึงยอดเงินและเหรียญแบบรองรับทุกรูปแบบชื่อ Symbol"""
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res and res.get('error') == 0:
            # ตัด THB_ หรือ _THB ออกเพื่อให้เหลือแค่ชื่อเหรียญ เช่น XRP
            coin_name = self.symbol.replace("THB_", "").replace("_THB", "").upper()
            thb_bal = float(res['result'].get('THB', 0))
            coin_bal = float(res['result'].get(coin_name, 0))
            return thb_bal, coin_bal
        return 0.0, 0.0

    def check_and_cancel_sell_orders(self, current_price, ema_val):
        res = self._request("GET", f"/api/v3/market/my-open-orders?sym={self.symbol}", private=True)
        if res and res.get('error') == 0 and res.get('result'):
            for order in res['result']:
                if order['side'].lower() == "sell":
                    if current_price > (ema_val * 1.002):
                        self._request("POST", "/api/v3/market/cancel-order", {"sym": self.symbol, "id": order['id']}, private=True)
                        self.notify(f"⚠️ [REVIVE] ราคากลับตัวยืนเหนือ EMA! ยกเลิกการขายเพื่อถือต่อ")
                        return True
        return False

    def place_order_v3(self, side, amount, price):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        # ใช้ Limit สำหรับซื้อ และ Market สำหรับขายเพื่อความไว
        typ = "limit" if side == "buy" else "market"
        payload = {
            "sym": self.symbol, 
            "amt": int(amount) if side == "buy" else round(amount, 6),
            "rat": round(price, 4) if typ == "limit" else 0, 
            "typ": typ
        }
        return self._request("POST", path, payload, private=True)

    def calculate_ema(self, prices, period=50):
        if not prices or len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        ema_list = [ema]
        for p in prices[period:]:
            ema = (p * k) + (ema * (1 - k))
            ema_list.append(ema)
        return ema_list

    def notify(self, msg):
        if not self.line_token: logger.info(msg); return
        try:
            headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
            payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: pass

    def send_detailed_report(self, price, pnl, ema_val=None):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        all_time_pnl = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        now_th = self.get_local_time()
        
        # คำนวณจุด Trailing Stop
        t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.last_action == "buy" and pnl >= self.target_profit else "Wait for Target"
        
        ema_str = f"{ema_val:,.2f}" if ema_val else "Calculating..."
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val else ""

        report = (
            "💎 [ULTIMATE REPORT V5.5.3]\n"
            "━━━━━━━━━━━━━━━\n"
            f"📊 MARKET: {self.symbol}\n"
            f"💵 Price: {price:,.2f} | P/L: {pnl:+.2f}%\n"
            f"📈 EMA(50): {ema_str} {diff_ema}\n"
            f"🕒 Time: {now_th.strftime('%d/%m %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            "🏦 ASSET SUMMARY\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Coin: {coin_bal:,.4f} {self.symbol.replace('THB_', '').replace('_THB', '')}\n"
            f"📈 Equity: {total_equity:,.2f} THB\n"
            f"🚀 Growth: {all_time_pnl:+.2f}%\n"
            "━━━━━━━━━━━━━━━\n"
            f"🛡️ Trailing @: {t_stop_price}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"🚀 Bot V5.5.3 Ready\nSymbol: {self.symbol}")
        # กำหนดคู่เทียบชื่อสัญลักษณ์
        search_sym = f"{self.symbol.split('_')[1]}_{self.symbol.split('_')[0]}" if "_" in self.symbol else self.symbol

        while True:
            try:
                # 1. ข้อมูลราคา
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                current_price = None
                if isinstance(ticker_res, list):
                    for item in ticker_res:
                        if item.get('symbol') in [search_sym, self.symbol]:
                            current_price = float(item['last']); break
                elif isinstance(ticker_res, dict):
                    res_data = ticker_res.get('result', ticker_res)
                    price_info = res_data.get(self.symbol, res_data.get(search_sym))
                    if price_info: current_price = float(price_info['last'])

                if current_price is None:
                    logger.warning("Waiting for Ticker data..."); time.sleep(30); continue

                # 2. ข้อมูล EMA (กราฟ 15 นาที)
                history = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                if isinstance(history, dict) and 'c' in history:
                    ema_series = self.calculate_ema(history['c'], 50)
                    if ema_series:
                        ema_val, ema_prev = ema_series[-1], ema_series[-2]
                        is_uptrend = current_price > (ema_val * 1.002) and ema_val > ema_prev
                    else: continue
                else: continue

                # 3. Auto-Sync Wallet (ดึงเหรียญ XRP ที่มีอยู่เข้าระบบบอท)
                thb, coin_bal = self.get_balance()
                if coin_bal * current_price > 50: # ถ้ามีเหรียญมูลค่าเกิน 50 บาท
                    if self.last_action == "sell" or self.current_stage == 0:
                        self.last_action, self.current_stage, self.total_units = "buy", 2, coin_bal
                        self.avg_price = current_price if self.avg_price == 0 else self.avg_price
                        self._save_state()
                        logger.info(f"Synced {coin_bal} units into Bot State.")

                # 4. Anti-Stuck & P/L calculation
                self.check_and_cancel_sell_orders(current_price, ema_val)
                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # --- BUY LOGIC ---
                if is_uptrend and self.current_stage < 2:
                    if self.current_stage == 0 and thb >= 10:
                        res = self.place_order_v3("buy", thb * 0.49, current_price)
                        if res and res.get('error') == 0:
                            self.total_units, self.avg_price, self.current_stage, self.last_action = float(res['result']['rec']), float(res['result']['rat']), 1, "buy"
                            self.highest_price = self.avg_price
                            self._save_state(); self.notify(f"🟢 [BUY 1/2] Price: {self.avg_price:,.2f}")

                    elif self.current_stage == 1 and pnl >= 0.5 and thb >= 10:
                        res = self.place_order_v3("buy", thb * 0.95, current_price)
                        if res and res.get('error') == 0:
                            nq, nr = float(res['result']['rec']), float(res['result']['rat'])
                            self.avg_price = ((self.avg_price * self.total_units) + (nq * nr)) / (self.total_units + nq)
                            self.total_units += nq
                            self.current_stage = 2
                            self._save_state(); self.notify(f"🟢 [BUY 2/2] New Avg: {self.avg_price:,.2f}")

                # --- SELL LOGIC ---
                if self.last_action == "buy" and self.total_units > 0:
                    if current_price > self.highest_price: self.highest_price = current_price; self._save_state()
                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif pnl >= self.target_profit and current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif current_price < (ema_val * 0.998): reason = "Trend Reversed"

                    if reason:
                        res = self.place_order_v3("sell", self.total_units, current_price)
                        if res and res.get('error') == 0:
                            self.notify(f"🔴 [SELL ALL]\nReason: {reason}\nP/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                            self._save_state()

                # Report Cycle (ส่งทันทีในรอบแรก และทุก 3 ชม.)
                if time.time() - self.last_report_time >= 10800:
                    self.send_detailed_report(current_price, pnl, ema_val)
                    self.last_report_time = time.time()

            except Exception as e: 
                logger.error(f"🔥 Critical Loop Error: {e}")
            time.sleep(30)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is Running")
        def log_message(self, *args): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
