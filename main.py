import os, requests, time, hmac, hashlib, json, threading, logging, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- ระบบ Logging สำหรับ Railway ---
sys.stdout.reconfigure(line_buffering=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBotV3:
    def __init__(self):
        # โหลดค่าจาก Variables
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        
        # สถานะเริ่มต้น
        self.state_file = "/tmp/bot_state_v3.json"
        self.last_action, self.avg_price, self.current_stage = self._load_state()
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
                    return d.get('last_action', 'sell'), d.get('avg_price', 0.0), d.get('stage', 0)
            except: pass
        return "sell", 0.0, 0

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump({'last_action': self.last_action, 'avg_price': self.avg_price, 'stage': self.current_stage}, f)

    def get_wallet(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res.get('error') == 0:
            coin = self.symbol.split('_')[1] if '_' in self.symbol else "XRP"
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def notify(self, msg):
        if not self.line_token: return
        headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
        payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
        try: requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: logger.error("Line notification failed")

    def send_detailed_report(self, price, ema_val, pnl):
        thb_bal, coin_bal = self.get_wallet()
        total_equity = thb_bal + (coin_bal * price)
        all_time_growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        
        ema_status = f"{ema_val:,.2f}" if ema_val > 0 else "Calculating (Fast-Sync)..."
        report = (
            "📊 [PORTFOLIO INSIGHT]\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Market: {self.symbol}: {price:,.2f}\n"
            f"📈 EMA(50): {ema_status}\n"
            f"🕒 Time: {datetime.now().strftime('%H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📦 Position: Stage {self.current_stage}/2\n"
            f"📉 Avg Cost: {self.avg_price:,.2f}\n"
            f"✨ Current P/L: {pnl:+.2f}%\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏦 Equity: {total_equity:,.2f} THB\n"
            f"💹 Growth: {all_time_growth:+.2f}%\n"
            f"💵 Cash: {thb_bal:,.2f} | 💎 Coin: {coin_bal:,.4f}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)
        logger.info(f"Report Sent - Equity: {total_equity:,.2f}")

    def run(self):
        logger.info(f"🚀 Bot V3 Started - Symbol: {self.symbol}")
        self.notify(f"🤖 Bot Online | Full Report Active\nSymbol: {self.symbol}\nMode: Fast-Sync EMA")
        
        while True:
            try:
                # 1. ดึงราคาปัจจุบัน
                ticker_res = self._request("GET", "/api/v3/market/ticker")
                price = 0
                if isinstance(ticker_res, list):
                    for item in ticker_res:
                        if item.get('symbol').upper() in [self.symbol, "XRP_THB", "THB_XRP"]:
                            price = float(item.get('last', 0))
                            break
                elif isinstance(ticker_res, dict):
                    price = float(ticker_res.get(self.symbol, {}).get('last', 0))

                if price == 0:
                    logger.warning(f"⚠️ Price not found for {self.symbol}"); time.sleep(30); continue

                # 2. ดึงข้อมูล History (ขยายเป็น 3 วัน = 259200 วินาที เพื่อให้ข้อมูลแน่นและติดง่ายขึ้น)
                now_ts = int(time.time())
                hist = self._request("GET", f"/tradingview/history?symbol={self.symbol}&resolution=15&from={now_ts-259200}&to={now_ts}")
                
                closes = hist.get('c', [])
                ema_val = 0
                
                if isinstance(closes, list) and len(closes) >= 50:
                    ema_val = sum(closes[-50:]) / 50
                    logger.info(f"✅ EMA Sync Complete: {ema_val:,.2f}")
                else:
                    data_len = len(closes) if isinstance(closes, list) else 0
                    logger.warning(f"⚠️ Syncing EMA Data... ({data_len}/50)")

                # 3. คำนวณ P/L
                pnl = ((price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0

                # 4. ส่งรายงาน (ส่งทันที และทุก 1 ชม.)
                if time.time() - self.last_report_time >= 3600:
                    self.send_detailed_report(price, ema_val, pnl)
                    self.last_report_time = time.time()

                # 5. ตรรกะซื้อขาย (ทำงานทันทีที่ ema_val > 0)
                if ema_val > 0:
                    if price > ema_val:
                        thb, _ = self.get_wallet()
                        if self.current_stage == 0 and thb > 50:
                            res = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": thb*0.48, "typ": "market"}, private=True)
                            if res.get('error') == 0:
                                self.avg_price, self.current_stage, self.last_action = price, 1, "buy"
                                self._save_state(); self.notify(f"🟢 [BUY 1/2] Price: {price:,.2f}")
                        elif self.current_stage == 1 and pnl >= 0.5 and thb > 50:
                            res = self._request("POST", "/api/v3/market/place-bid", {"sym": self.symbol, "amt": thb*0.95, "typ": "market"}, private=True)
                            if res.get('error') == 0:
                                self.avg_price = (self.avg_price + price) / 2
                                self.current_stage = 2
                                self._save_state(); self.notify(f"🟢 [BUY 2/2] Price: {price:,.2f}")

                    if self.last_action == "buy":
                        if pnl <= -self.stop_loss or price < (ema_val * 0.997):
                            _, coin = self.get_wallet()
                            if coin > 0.01:
                                res = self._request("POST", "/api/v3/market/place-ask", {"sym": self.symbol, "amt": coin, "typ": "market"}, private=True)
                                if res.get('error') == 0:
                                    self.notify(f"🔴 [SELL ALL] P/L: {pnl:+.2f}% | Price: {price:,.2f}")
                                    self.last_action, self.avg_price, self.current_stage = "sell", 0.0, 0
                                    self._save_state()

            except Exception as e: 
                logger.error(f"❌ Main Loop Error: {e}")
            
            time.sleep(30)

def start_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    BitkubBotV3().run()
