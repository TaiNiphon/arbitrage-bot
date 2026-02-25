import os, requests, time, hmac, hashlib, json, threading, logging, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- แก้ไขปัญหา Log บน Railway ---
sys.stdout.reconfigure(line_buffering=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.symbol = os.getenv("SYMBOL", "THB_XRP")
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        
        self.state_file = "/tmp/bot_state_final.json"
        self.last_action, self.avg_price, self.current_stage = self._load_state()
        self.last_report_time = 0

    def _request(self, method, path, payload=None, private=False):
        url = f"https://api.bitkub.com{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = requests.get("https://api.bitkub.com/api/v3/servertime").text.strip()
            sig = hmac.new(self.api_secret.encode('utf-8'), (ts + method + path + body_str).encode('utf-8'), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        try:
            return requests.request(method, url, headers=headers, data=body_str, timeout=10).json()
        except: return {"error": 999}

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    return d.get('last_action', 'sell'), d.get('avg_price', 0.0), d.get('stage', 0)
            except: pass
        return "sell", 0.0, 0

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump({'last_action': self.last_action, 'avg_price': self.avg_price, 'stage': self.current_stage}, f)

    def get_wallet(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res.get('error') == 0:
            coin = self.symbol.split('_')[1]
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def notify(self, msg):
        if not self.line_token: return
        headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
        payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=5)

    def send_full_report(self, price, ema_val, pnl):
        thb_bal, coin_bal = self.get_wallet()
        total_equity = thb_bal + (coin_bal * price)
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        report = (
            "📊 [Full Portfolio Report]\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 ราคา: {price:,.2f} | EMA: {ema_val:,.2f}\n"
            f"🧭 เทรนด์: {'🟢 ขึ้น' if price > ema_val else '🔴 ลง'}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📦 สถานะ: ถือ {self.current_stage}/2 ไม้\n"
            f"📉 ต้นทุน: {self.avg_price:,.2f} | P/L: {pnl:+.2f}%\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏦 พอร์ต: {total_equity:,.2f} THB\n"
            f"📈 กำไรสะสม: {growth:+.2f}%\n"
            "━━━━━━━━━━━━━━━\n"
            f"💵 Cash: {thb_bal:,.2f} | 💎 Coin: {coin_bal:,.4f}"
        )
        self.notify(report)

    def run(self):
        self.notify("🤖 Bot Online | Full Report Active")
        while True:
            try:
                # แก้ไขการดึงราคาให้แม่นยำขึ้น
                ticker = self._request("GET", "/api/v3/market/ticker")
                price = float(ticker.get(self.symbol, {}).get('last', 0))
                if price == 0: # ป้องกันกรณี Symbol สลับด้าน
                    alt_sym = "_".join(self.symbol.split("_")[::-1])
                    price = float(ticker.get(alt_sym, {}).get('last', 0))

                if price == 0:
                    logger.warning(f"Could not find price for {self.symbol}"); time.sleep(20); continue

                # ดึง EMA
                hist = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}")
                ema_val = sum(hist.get('c', [])[-50:]) / 50 if len(hist.get('c', [])) >= 50 else 0

                if ema_val == 0: time.sleep(20); continue

                pnl = ((price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # --- กลยุทธ์การซื้อ (2 ไม้) ---
                if price > ema_val:
                    thb, _ = self.get_wallet()
                    if self.current_stage == 0 and thb > 50:
                        res = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": thb*0.48, "typ": "market"}, private=True)
                        if res.get('error') == 0:
                            self.avg_price, self.current_stage, self.last_action = price, 1, "buy"
                            self._save_state(); self.notify(f"🟢 BUY ไม้ 1: {price}")
                    elif self.current_stage == 1 and pnl >= 0.5 and thb > 50:
                        res = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": thb*0.95, "typ": "market"}, private=True)
                        if res.get('error') == 0:
                            self.avg_price = (self.avg_price + price) / 2
                            self.current_stage = 2
                            self._save_state(); self.notify(f"🟢 BUY ไม้ 2: {price}")

                # --- กลยุทธ์การขาย ---
                if self.last_action == "buy":
                    if pnl <= -self.stop_loss or price < (ema_val * 0.997):
                        _, coin = self.get_wallet()
                        if coin > 0.01:
                            res = self._request("POST", "/api/v3/market/place-ask", {"sym": self.symbol, "amt": coin, "typ": "market"}, private=True)
                            if res.get('error') == 0:
                                self.notify(f"🔴 SELL ALL: {pnl:+.2f}%")
                                self.last_action, self.avg_price, self.current_stage = "sell", 0.0, 0
                                self._save_state()

                # รายงานพอร์ตทุก 1 ชั่วโมง
                if time.time() - self.last_report_time >= 3600:
                    self.send_full_report(price, ema_val, pnl)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Error: {e}")
            time.sleep(30)

def start_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Active")
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    BitkubBot().run()
