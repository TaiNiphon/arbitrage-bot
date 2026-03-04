import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Config & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubUltimateBotV66_Final:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()
        self.coin = self.symbol.split('_')[0]
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 10090.61)) 
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 5.0))
        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.fee_pct = 0.0025
        self.min_trade = 10.0

        self.state_file = "bot_state_xrp_10k.json"
        self._load_state()
        self.last_report_time = 0

    # --- [เพิ่มฟังก์ชัน EMA แท้] ---
    def calculate_ema_proper(self, prices, period):
        if len(prices) < period: return None
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # เริ่มต้นด้วย SMA
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    self.last_action = d.get('last_action', 'sell')
                    self.avg_price = d.get('avg_price', 0.0)
                    self.current_stage = d.get('stage', 0)
                    self.total_units = d.get('total_units', 0.0)
                    self.highest_price = d.get('highest_price', 0.0)
                    self.last_pnl = d.get('last_pnl', 0.0)
                    return
            except: pass
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price, self.last_pnl = "sell", 0.0, 0, 0.0, 0.0, 0.0

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price, "last_pnl": self.last_pnl
                }, f)
        except Exception as e: logger.error(f"Save State Error: {e}")

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def get_server_time(self):
        try: return requests.get(f"{self.host}/api/v3/servertime", timeout=10).text.strip()
        except: return str(int(time.time() * 1000))

    def _get_signature(self, ts, method, path, query="", body=""):
        payload = ts + method + path + query + body
        return hmac.new(self.api_secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, params=None, payload=None, private=False):
        url = f"{self.host}{path}"
        query_str = "?" + "&".join([f"{k}={v}" for k, v in params.items()]) if params else ""
        if params: url += query_str
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = self.get_server_time()
            headers.update({
                'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': self._get_signature(ts, method, path, query_str, body_str)
            })
        try:
            res = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return res.json()
        except: return {"error": 999}

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res and res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get(self.coin, 0))
        return 0.0, 0.0

    def place_order(self, side, amt, typ="market"):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        payload = {
            "sym": self.symbol.lower(),
            "amt": math.floor(amt * 100) / 100 if side == "buy" else math.floor(amt * 10000) / 10000,
            "rat": 0, "typ": typ
        }
        return self._request("POST", path, payload=payload, private=True)

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def send_detailed_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100
        now_th = self.get_local_time()

        is_holding = coin_value > self.min_trade
        status = "HOLDING COIN" if is_holding else "HOLDING CASH"
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val and ema_val > 0 else "(N/A)"
        pnl_display = pnl if is_holding else self.last_pnl
        pnl_label = "Net P/L" if is_holding else "Last Trade P/L"
        t_stop = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.current_stage == 3 else "Waiting..."

        report = (
            f"💰 <b>{status}</b>\n📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📊 <b>MARKET: {self.symbol}</b>\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_val if ema_val else 0:,.2f} {diff_ema}\n"
            f"🕒 {pnl_label}: {pnl_display:+.2f}% (Fee Incl.)\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏦 <b>PORTFOLIO</b>\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Coin: {coin_bal:,.4f} ({coin_value:,.2f} THB)\n"
            f"💎 <b>Equity: {total_equity:,.2f} THB</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"📈 <b>PERFORMANCE</b>\n"
            f"💵 Net Profit: {net_profit:,.2f} THB\n"
            f"🚀 Growth: {growth_pct:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"🚀 <b>Bitkub V6.6 Proper EMA Started</b>\nMonitoring {self.symbol} (EMA {self.ema_period} / TF 1h)")

        while True:
            try:
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                price = 0
                if isinstance(ticker, list):
                    for item in ticker:
                        if item['symbol'].upper() == self.symbol: price = float(item['last']); break

                # ดึงข้อมูลย้อนหลัง 120 ชม. เพื่อความแม่นยำของ EMA
                hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "60", "from": int(time.time())-432000, "to": int(time.time())})
                prices_list = hist.get('c', [])
                ema = self.calculate_ema_proper(prices_list, self.ema_period) # ใช้สูตร EMA แท้

                thb, coin_bal = self.get_balance()
                equity = thb + (coin_bal * price)

                pnl = 0
                if self.avg_price > 0:
                    buy_cost = self.avg_price * (1 + self.fee_pct)
                    sell_value = price * (1 - self.fee_pct)
                    pnl = ((sell_value - buy_cost) / buy_cost) * 100

                if self.last_report_time == 0:
                    self.send_detailed_report(price, pnl, ema)
                    self.last_report_time = time.time()

                # Logic การซื้อไม้ที่ 1
                if coin_bal * price < self.min_trade and ema and price > ema * 1.005:
                    buy_amt = thb * 0.48
                    res = self.place_order("buy", buy_amt)
                    if res.get('error') == 0:
                        units = float(res['result'].get('rec', 0))
                        if units == 0: units = (buy_amt * (1 - self.fee_pct)) / price
                        self.last_action, self.current_stage, self.avg_price = "buy", 1, price
                        self.total_units = units
                        self.highest_price = price
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 1/2] Confirmed</b>\nPrice: {price:,.2f}\nEMA: {ema:,.2f}")

                # Logic การซื้อไม้ที่ 2
                elif coin_bal * price > self.min_trade and thb > (equity * 0.40) and price < self.avg_price * 0.99:
                    buy_amt = thb * 0.95
                    res = self.place_order("buy", buy_amt)
                    if res.get('error') == 0:
                        units = float(res['result'].get('rec', 0))
                        if units == 0: units = (buy_amt * (1 - self.fee_pct)) / price
                        self.avg_price = ((self.avg_price * self.total_units) + (price * units)) / (self.total_units + units)
                        self.total_units += units
                        self.current_stage = 2
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 2/2] Confirmed</b>\nPrice: {price:,.2f}\nAvg Price: {self.avg_price:,.2f}")

                # Logic การขาย
                elif coin_bal * price > self.min_trade:
                    self.highest_price = max(self.highest_price, price)
                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        sell_units = coin_bal * 0.5
                        res = self.place_order("sell", sell_units)
                        if res.get('error') == 0:
                            self.current_stage = 3
                            self._save_state()
                            self.notify(f"🟠 <b>[TP 50%] Locked</b>\nPrice: {price:,.2f}\nPNL: {pnl:+.2f}%")

                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif self.current_stage == 3 and price < self.highest_price * (1 - self.trailing_pct/100):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif ema and price < ema: # ขายทันทีเมื่อหลุดเส้น EMA (เพิ่มความปลอดภัย)
                        reason = "Price below EMA (Trend Changed)"

                    if reason:
                        res = self.place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            self.last_pnl = pnl
                            self.last_action, self.current_stage, self.avg_price, self.total_units = "sell", 0, 0, 0
                            self._save_state()
                            self.notify(f"🔴 <b>[SELL ALL]</b>\nReason: {reason}\nPrice: {price:,.2f}\nPNL: {pnl:+.2f}%")

                if time.time() - self.last_report_time >= 1800:
                    self.send_detailed_report(price, pnl, ema)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Error: {e}")
            time.sleep(30)

def run_health():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    BitkubUltimateBotV66_Final().run()
