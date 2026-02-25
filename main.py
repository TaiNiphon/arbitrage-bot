import os, requests, time, hmac, hashlib, json, threading, logging, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- Logging ---
sys.stdout.reconfigure(line_buffering=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBotUltimate:
    def __init__(self):
        # โหลดค่า Config (แนวคิดเดิมของคุณ)
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() # มาตรฐาน XRP_THB
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0)) # กำไรเป้าหมายเพื่อเริ่ม Trailing
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0)) # ย่อ 1% จากจุดสูงสุดแล้วขาย

        self.state_file = "/tmp/bot_state_v3.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        self.last_report_time = 0

    def _request(self, method, path, payload=None, private=False):
        url = f"https://api.bitkub.com{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            try:
                ts = requests.get("https://api.bitkub.com/api/v3/servertime", timeout=5).text.strip()
                sig_payload = ts + method + path + body_str
                sig = hmac.new(self.api_secret.encode('utf-8'), sig_payload.encode('utf-8'), hashlib.sha256).hexdigest()
                headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
            except: return {"error": 999}
        try:
            res = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return res.json()
        except: return {"error": 999}

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    return d['last_action'], d['avg_price'], d['stage'], d.get('total_units', 0.0), d.get('highest_price', 0.0)
            except: pass
        return "sell", 0.0, 0, 0.0, 0.0

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump({
                "last_action": self.last_action, "avg_price": self.avg_price,
                "stage": self.current_stage, "total_units": self.total_units,
                "highest_price": self.highest_price
            }, f)

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res.get('error') == 0:
            # แก้ไขจุดตัดชื่อเหรียญให้ยืดหยุ่น (XRP_THB หรือ THB_XRP)
            coin = "XRP" if "XRP" in self.symbol else self.symbol.split('_')[0]
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def notify(self, msg):
        if not self.line_token: return
        headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
        payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)

    def send_detailed_report(self, price, ema_val, pnl):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        
        # แสดงจุด Trailing Stop ตามแนวคิดเดิมของคุณ
        t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.last_action == "buy" and pnl >= self.target_profit else "Wait for Target"

        report = (
            "📊 [PORTFOLIO INSIGHT]\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Market {self.symbol}: {price:,.2f}\n"
            f"📈 EMA(50): {ema_val:,.2f}\n"
            f"🕒 Time: {datetime.now().strftime('%H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📦 Stage: {self.current_stage}/2 | {self.last_action.upper()}\n"
            f"📉 Avg Cost: {self.avg_price:,.2f}\n"
            f"✨ Current P/L: {pnl:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop_price}\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏦 Equity: {total_equity:,.2f} THB\n"
            f"💹 Growth: {growth:+.2f}%\n"
            f"💵 Cash: {thb_bal:,.2f} | 💎 Coin: {coin_bal:,.4f}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        logger.info(f"🚀 Bot V3 Started - {self.symbol}")
        self.notify(f"🚀 Bot Ultimate Started\nCapital: {self.initial_equity} THB")

        while True:
            try:
                # 1. ดึงราคา (รองรับ V3 Ticker List)
                ticker_res = self._request("GET", "/api/v3/market/ticker")
                price = 0
                if isinstance(ticker_res, list):
                    for item in ticker_res:
                        if item.get('symbol').upper() in [self.symbol, "XRP_THB", "THB_XRP"]:
                            price = float(item.get('last', 0)); break

                if price == 0:
                    time.sleep(10); continue

                # 2. คำนวณ EMA (ใช้ Fast-Sync 3 วัน)
                hist = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-259200}&to={int(time.time())}")
                closes = hist.get('c', [])
                if len(closes) < 50:
                    logger.warning("Waiting for EMA data..."); time.sleep(30); continue
                ema_val = sum(closes[-50:]) / 50

                pnl = ((price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # 3. รายงาน (ทันทีและทุก 1 ชม. เพื่อความต่อเนื่อง)
                if time.time() - self.last_report_time >= 3600:
                    self.send_detailed_report(price, ema_val, pnl)
                    self.last_report_time = time.time()

                # 4. BUY LOGIC (2 Stages ตามแนวคิดคุณ)
                if price > ema_val:
                    thb, _ = self.get_balance()
                    if self.current_stage == 0 and thb > 50:
                        res = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": thb*0.49, "typ": "market"}, private=True)
                        if res.get('error') == 0:
                            self.total_units = float(res['result']['rec'])
                            self.avg_price, self.current_stage, self.last_action, self.highest_price = price, 1, "buy", price
                            self._save_state(); self.notify(f"🟢 [BUY 1/2] Price: {price:,.2f}")
                    elif self.current_stage == 1 and pnl >= 0.5 and thb > 50:
                        res = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": thb*0.95, "typ": "market"}, private=True)
                        if res.get('error') == 0:
                            new_units = float(res['result']['rec'])
                            self.avg_price = ((self.avg_price * self.total_units) + (price * new_units)) / (self.total_units + new_units)
                            self.total_units += new_units
                            self.current_stage = 2
                            self._save_state(); self.notify(f"🟢 [BUY 2/2] New Avg: {self.avg_price:,.2f}")

                # 5. SELL LOGIC (Trailing Stop + SL ตามแนวคิดคุณ)
                if self.last_action == "buy":
                    if price > self.highest_price:
                        self.highest_price = price
                        self._save_state()

                    reason = None
                    if pnl <= -self.stop_loss: reason = f"Stop Loss ({pnl:.2f}%)"
                    elif pnl >= self.target_profit and price <= (self.highest_price * (1 - (self.trailing_pct/100))):
                        reason = f"Trailing Stop (Exit @ {pnl:.2f}%)"
                    elif price < (ema_val * 0.997): reason = "Trend Reversed"

                    if reason:
                        _, coin = self.get_balance()
                        if coin > 0.001:
                            res = self._request("POST", "/api/v3/market/place-ask", {"sym": self.symbol, "amt": coin, "typ": "market"}, private=True)
                            if res.get('error') == 0:
                                self.notify(f"🔴 [SELL ALL]\nReason: {reason}\nP/L: {pnl:+.2f}%")
                                self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
                                self._save_state()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

def start_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    BitkubBotUltimate().run()
