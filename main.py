import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 2000.00)) 
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.fee_pct = float(os.getenv("FEE_PCT", 0.25)) / 100 
        self.min_trade = float(os.getenv("MIN_TRADE", 50.0))

        self.state_file = "bot_state_v6_final.json"
        self.time_offset = 0
        self._sync_server_time()
        
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price, self.last_pnl = self._load_state()
        self.last_report_time = 0

    def _sync_server_time(self):
        try:
            res = requests.get(f"{self.host}/api/v3/servertime", timeout=10)
            server_ts = int(res.text.strip())
            self.time_offset = server_ts - int(time.time() * 1000)
            logger.info(f"Time synced. Offset: {self.time_offset}ms")
        except: logger.error("Failed to sync time")

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def notify(self, msg):
        if not (self.tg_token and self.tg_chat_id): return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            requests.post(url, json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except Exception as e: logger.error(f"Notify Error: {e}")

    def _get_signature(self, ts, method, path, body_str):
        payload = ts + method + path + body_str
        return hmac.new(self.api_secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, payload=None, private=False):
        url = f"{self.host}{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = str(int(time.time() * 1000) + self.time_offset)
            headers.update({
                'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': self._get_signature(ts, method, path, body_str)
            })
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            data = response.json()
            # จัดการกรณี API ส่ง Error กลับมา
            if isinstance(data, dict) and data.get('error') != 0:
                logger.warning(f"API Error {path}: {data}")
            return data
        except Exception as e:
            logger.error(f"Request Exception {path}: {e}")
            return {"error": 999}

    def _save_state(self):
        try:
            temp_file = self.state_file + ".tmp"
            with open(temp_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price, "last_pnl": self.last_pnl
                }, f)
            os.replace(temp_file, self.state_file)
        except: pass

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    return (d.get('last_action', 'sell'), d.get('avg_price', 0.0), 
                            d.get('stage', 0), d.get('total_units', 0.0), 
                            d.get('highest_price', 0.0), d.get('last_pnl', 0.0))
            except: pass
        return "sell", 0.0, 0, 0.0, 0.0, 0.0

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if isinstance(res, dict) and res.get('error') == 0:
            coin_key = self.symbol.split('_')[1]
            result = res.get('result', {})
            return float(result.get('THB', 0)), float(result.get(coin_key, 0))
        return 0.0, 0.0

    def calculate_net_pnl(self, current_price):
        if self.avg_price <= 0: return 0.0
        buy_cost = self.avg_price * (1 + self.fee_pct)
        sell_value = current_price * (1 - self.fee_pct)
        return ((sell_value - buy_cost) / buy_cost) * 100

    def place_order_v3(self, side, amount, price):
        if side == "buy" and amount < self.min_trade: return None
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        typ = "limit" if side == "buy" else "market"
        clean_amount = math.floor(amount * 10000000) / 10000000 if side == "sell" else round(amount, 2)
        payload = {
            "sym": self.symbol, "amt": clean_amount,
            "rat": round(price, 4) if typ == "limit" else 0, "typ": typ
        }
        return self._request("POST", path, payload, private=True)

    def calculate_ema(self, prices, period):
        if not prices or len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]:
            ema = (p * k) + (ema * (1 - k))
        return ema

    def send_detailed_report(self, price, pnl, ema_val=None):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100

        now_th = self.get_local_time()
        is_holding = coin_bal * price > self.min_trade
        
        if self.current_stage == 3: status = "🚀 RUNNING PROFIT"
        else: status = "🚀 HOLDING COIN" if is_holding else "💰 HOLDING CASH"

        ema_str = f"{ema_val:,.2f}" if ema_val else "N/A"
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val and ema_val > 0 else ""
        t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if is_holding and (pnl >= self.target_profit or self.current_stage == 3) else "Waiting..."

        display_pnl = pnl if is_holding else self.last_pnl
        pnl_label = "Net P/L" if is_holding else "Last Trade P/L"

        report = (
            f"<b>{status}</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"<b>📊 MARKET: {self.symbol}</b>\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_str} {diff_ema}\n"
            f"🕒 {pnl_label}: {display_pnl:+.2f}% (Fee Incl.)\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>🏦 PORTFOLIO</b>\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Coin: {coin_bal:,.4f} ({coin_value:,.2f} THB)\n"
            f"💎 <b>Equity: {total_equity:,.2f} THB</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>📈 PERFORMANCE</b>\n"
            f"💵 Net Profit: {net_profit:,.2f} THB\n"
            f"🚀 Growth: {growth_pct:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop_price}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"<b>🚀 Bot V6.2 Robust Started</b>")
        
        while True:
            try:
                # แก้ไขการดึงราคาให้รองรับทั้งแบบ Dict และ List
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                current_price = 0.0
                
                if isinstance(ticker_res, dict):
                    # รูปแบบ Bitkub V3 ปกติ
                    current_price = float(ticker_res.get('result', {}).get(self.symbol, {}).get('last', 0))
                elif isinstance(ticker_res, list):
                    # รูปแบบสำรองถ้า Bitkub ส่งมาเป็น List
                    for item in ticker_res:
                        if item.get('symbol') == self.symbol:
                            current_price = float(item.get('last', 0))
                
                if current_price <= 0:
                    time.sleep(30); continue

                # ดึง History
                end_ts = int(time.time())
                start_ts = end_ts - 172800
                path_hist = f"/tradingview/history?symbol={self.symbol}&resolution=15&from={start_ts}&to={end_ts}"
                history = self._request("GET", path_hist)
                
                prices = []
                if isinstance(history, dict) and 'c' in history:
                    prices = history.get('c', [])
                
                ema_val = self.calculate_ema(prices, self.ema_period)
                ema_prev = self.calculate_ema(prices[:-1], self.ema_period) if len(prices) > self.ema_period else ema_val

                # ส่งรายงานทันทีในรอบแรก
                if self.last_report_time == 0:
                    self.send_detailed_report(current_price, self.calculate_net_pnl(current_price), ema_val)
                    self.last_report_time = time.time()

                if not ema_val:
                    time.sleep(30); continue

                is_uptrend = current_price > (ema_val * 1.002) and ema_val > ema_prev
                thb, coin_bal = self.get_balance()
                pnl = self.calculate_net_pnl(current_price)

                # Logic การซื้อ/ขาย (เหมือนเดิมแต่ปลอดภัยขึ้น)
                if is_uptrend and self.current_stage < 2:
                    if self.current_stage == 0 and thb >= self.min_trade:
                        res = self.place_order_v3("buy", thb * 0.48, current_price)
                        if isinstance(res, dict) and res.get('error') == 0:
                            self.total_units = float(res['result']['rec'])
                            self.avg_price = float(res['result']['rat'])
                            self.current_stage, self.last_action = 1, "buy"
                            self.highest_price = self.avg_price
                            self._save_state()
                            self.notify(f"<b>🟢 [BUY 1/2]</b> @ {self.avg_price:,.2f}")
                    
                    elif self.current_stage == 1 and pnl >= 0.3 and thb >= self.min_trade:
                        res = self.place_order_v3("buy", thb * 0.95, current_price)
                        if isinstance(res, dict) and res.get('error') == 0:
                            nq, nr = float(res['result']['rec']), float(res['result']['rat'])
                            self.avg_price = ((self.avg_price * self.total_units) + (nq * nr)) / (self.total_units + nq)
                            self.total_units += nq
                            self.current_stage = 2
                            self._save_state()
                            self.notify(f"<b>🟢 [BUY 2/2]</b> New Avg: {self.avg_price:,.2f}")

                if self.last_action == "buy" and self.total_units > 0:
                    self.highest_price = max(self.highest_price, current_price)
                    reason, sell_all = None, False

                    if pnl <= -self.stop_loss:
                        reason, sell_all = f"Stop Loss ({pnl:.2f}%)", True
                    elif self.current_stage == 2 and pnl >= self.target_profit:
                        sell_amount = self.total_units * 0.5
                        res = self.place_order_v3("sell", sell_amount, current_price)
                        if isinstance(res, dict) and res.get('error') == 0:
                            self.total_units -= sell_amount
                            self.current_stage = 3
                            self._save_state()
                            self.notify(f"<b>💰 [PARTIAL SELL 50%]</b> Locked Profit: {pnl:+.2f}%")
                        continue 
                    elif self.current_stage == 3:
                        if current_price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                            reason, sell_all = f"Trailing Stop (Final @ {pnl:.2f}%)", True
                        elif current_price < (ema_val * 0.995):
                            reason, sell_all = "Trend Reversed (Final)", True

                    if sell_all and reason:
                        res = self.place_order_all_v3("sell", current_price)
                        if isinstance(res, dict) and res.get('error') == 0:
                            self.last_pnl = pnl
                            self.notify(f"<b>🔴 [FINAL SELL]</b>\nReason: {reason}\nNet P/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                            self._save_state()

                if time.time() - self.last_report_time >= 3600:
                    self.send_detailed_report(current_price, pnl, ema_val)
                    self.last_report_time = time.time()
                    self._sync_server_time()

            except Exception as e: 
                logger.error(f"🔥 Loop Error: {e}")
            time.sleep(30)

    def place_order_all_v3(self, side, price):
        # ฟังก์ชันพิเศษสำหรับขายหมดจด 100%
        _, coin_bal = self.get_balance()
        if coin_bal <= 0: return {"error": 1}
        path = "/api/v3/market/place-ask"
        # ใช้ 10000000 เพื่อปัดทศนิยม 7 ตำแหน่งให้ Bitkub ยอมรับ
        clean_amount = math.floor(coin_bal * 10000000) / 10000000
        payload = {"sym": self.symbol, "amt": clean_amount, "rat": 0, "typ": "market"}
        return self._request("POST", path, payload, private=True)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
