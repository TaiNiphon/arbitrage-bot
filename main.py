import os, requests, time, hmac, hashlib, json, threading, logging, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- บังคับพ่น Log ทันทีสำหรับ Railway ---
sys.stdout.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"

        # ตั้งค่าคู่เหรียญ (แนะนำใช้ THB_XRP)
        self.symbol = os.getenv("SYMBOL", "THB_XRP")
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.state_file = "/tmp/bot_state_v3.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
        self.last_report_time = 0

    def _request(self, method, path, payload=None, private=False):
        url = f"{self.host}{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""

        if private:
            try:
                ts = requests.get(f"{self.host}/api/v3/servertime", timeout=5).text.strip()
                payload_auth = ts + method + path + body_str
                sig = hmac.new(self.api_secret.encode('utf-8'), payload_auth.encode('utf-8'), hashlib.sha256).hexdigest()
                headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
            except: return {"error": 999}

        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
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
        try:
            with open(self.state_file, "w") as f:
                json.dump({"last_action": self.last_action, "avg_price": self.avg_price, "stage": self.current_stage, "total_units": self.total_units, "highest_price": self.highest_price}, f)
        except: pass

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res.get('error') == 0:
            coin = self.symbol.split('_')[1] if '_' in self.symbol else "XRP"
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def notify(self, msg):
        logger.info(f"Notify: {msg}")
        if not self.line_token: return
        try:
            headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
            payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: pass

    def send_detailed_report(self, price, ema_val, pnl):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        all_time_pnl = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        
        report = (
            "📊 [PORTFOLIO INSIGHT]\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Market: {self.symbol}: {price:,.2f}\n"
            f"📈 EMA(50): {ema_val:,.2f}\n"
            f"🕒 Time: {datetime.now().strftime('%H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📦 Position: Stage {self.current_stage}/2\n"
            f"📉 Avg Cost: {self.avg_price:,.2f}\n"
            f"✨ Current P/L: {pnl:+.2f}%\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏦 Equity: {total_equity:,.2f} THB\n"
            f"💹 Growth: {all_time_pnl:+.2f}%\n"
            f"💵 Cash: {thb_bal:,.2f} | 💎 Coin: {coin_bal:,.4f}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"🚀 Bot Ultimate Edition Started\nSymbol: {self.symbol}")
        while True:
            try:
                # --- Get Price (ยืดหยุ่น รองรับทั้ง THB_XRP และ XRP_THB) ---
                ticker = self._request("GET", "/api/v3/market/ticker")
                current_price = 0
                if isinstance(ticker, dict):
                    # ลองหาด้วย Key ที่ตั้งไว้ หรือ Key สลับฝั่ง
                    current_price = float(ticker.get(self.symbol, {}).get('last', 0))
                    if current_price == 0: # ลองสลับ XRP_THB <-> THB_XRP
                        alt_sym = "_".join(self.symbol.split("_")[::-1])
                        current_price = float(ticker.get(alt_sym, {}).get('last', 0))

                if current_price == 0:
                    logger.warning(f"Price for {self.symbol} not found. Retrying...")
                    time.sleep(15); continue

                # --- Get EMA ---
                hist = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                closes = hist.get('c', [])
                if len(closes) < 50:
                    time.sleep(30); continue
                ema_val = sum(closes[-50:]) / 50 # SMA สำหรับความเสถียร

                pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # --- BUY/SELL LOGIC (แบบที่คุณใช้อยู่) ---
                if current_price > ema_val and self.current_stage < 2:
                    thb, _ = self.get_balance()
                    if thb > 50:
                        path = "/api/v3/market/place-bid"
                        side_msg = "BUY 1/2" if self.current_stage == 0 else "BUY 2/2"
                        amt = thb * 0.48 if self.current_stage == 0 else thb * 0.95
                        res = self._request("POST", path, {"sym": self.symbol, "amt": amt, "typ": "market"}, private=True)
                        if res.get('error') == 0:
                            self.avg_price = current_price if self.current_stage == 0 else (self.avg_price + current_price)/2
                            self.current_stage += 1
                            self.last_action = "buy"
                            self._save_state()
                            self.notify(f"🟢 [{side_msg}] Price: {current_price:,.2f}")

                if self.last_action == "buy" and (pnl <= -self.stop_loss or current_price < ema_val * 0.997):
                    _, coin = self.get_balance()
                    if coin > 0.01:
                        res = self._request("POST", "/api/v3/market/place-ask", {"sym": self.symbol, "amt": coin, "typ": "market"}, private=True)
                        if res.get('error') == 0:
                            self.notify(f"🔴 [SELL ALL] P/L: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage = "sell", 0.0, 0
                            self._save_state()

                # --- ส่งรายงานทุก 1 ชั่วโมง (ปรับให้เร็วขึ้นเพื่อเช็คผล) ---
                if time.time() - self.last_report_time >= 3600:
                    self.send_detailed_report(current_price, ema_val, pnl)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

def start_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    BitkubBot().run()
