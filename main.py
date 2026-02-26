import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"
        self.symbol = os.getenv("SYMBOL", "xrp_thb").lower() 
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        self.state_file = "/tmp/bot_state_v7_final.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = self._load_state()
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
                ts = requests.get(f"{self.host}/api/v3/servertime", timeout=5).text.strip()
                headers.update({
                    'X-BTK-APIKEY': self.api_key,
                    'X-BTK-TIMESTAMP': ts,
                    'X-BTK-SIGN': self._get_signature(ts, method, path, body_str)
                })
            except: return {"error": 999}
        try:
            response = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return response.json()
        except Exception as e:
            logger.error(f"API Error: {e}")
            return {"error": 999}

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action,
                    "avg_price": self.avg_price,
                    "stage": self.current_stage,
                    "total_units": self.total_units,
                    "highest_price": self.highest_price
                }, f)
        except Exception as e: logger.error(f"Save State Error: {e}")

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    return d['last_action'], d['avg_price'], d['stage'], d.get('total_units', 0.0), d.get('highest_price', 0.0)
            except: pass
        return "sell", 0.0, 0, 0.0, 0.0

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res.get('error') == 0:
            coin = self.symbol.split('_')[0].upper()
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def calculate_ema(self, prices, period=50):
        if len(prices) < period: return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price * k) + (ema * (1 - k))
        return ema

    def notify(self, msg):
        if not self.line_token: logger.info(msg); return
        try:
            headers = {"Authorization": f"Bearer {self.line_token}", "Content-Type": "application/json"}
            payload = {"to": self.line_id, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        except: logger.error("Line Notify Error")

    def send_detailed_report(self, price, ema_val, pnl):
        """ รายงานรูปแบบเต็มเหมือน 14:47 """
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        all_time_pnl = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        ema_diff = ((price - ema_val) / ema_val * 100) if ema_val else 0
        now_th = self.get_local_time()
        
        # คำนวณ Trailing Stop
        t_stop_price = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.last_action == "buy" and pnl >= self.target_profit else "Wait for Target"

        report = (
            "📊 [PORTFOLIO INSIGHT]\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Market: {self.symbol.upper()}: {price:,.2f}\n"
            f"📈 EMA(50): {ema_val:,.2f} ({ema_diff:+.2f}%)\n"
            f"🕒 Time: {now_th.strftime('%H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📦 Position: Stage {self.current_stage}/2\n"
            f"📉 Avg Cost: {self.avg_price:,.2f}\n"
            f"✨ Current P/L: {pnl:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop_price}\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏦 Equity: {total_equity:,.2f} THB\n"
            f"💹 Growth: {all_time_pnl:+.2f}%\n"
            f"💵 Cash: {thb_bal:,.2f} | 💎 Coin: {coin_bal:,.4f}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"🚀 Bot v7.6 Final Fixed\nReady for {self.symbol.upper()}")
        while True:
            try:
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol}")
                current_price = 0
                if isinstance(ticker_res, list):
                    sym_data = next((item for item in ticker_res if item['symbol'] == self.symbol.upper()), None)
                    if sym_data: current_price = float(sym_data.get('lowest_ask', 0))
                
                if current_price == 0:
                    time.sleep(10); continue

                history = self._request("GET", f"/tradingview/history?symbol={self.symbol.upper()}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                ema_val = self.calculate_ema(history.get('c', []), 50)
                
                if ema_val:
                    pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0
                    
                    # ตรวจสอบเวลาเพื่อรายงานผล (ทุก 3 ชม. หรือตามเงื่อนไขที่ตั้งไว้)
                    if time.time() - self.last_report_time >= 10800:
                        self.send_detailed_report(current_price, ema_val, pnl)
                        self.last_report_time = time.time()

                    # --- ส่วนของ Logic ซื้อ/ขาย (คงเดิม) ---
                    # ... (ใส่ Logic ซื้อขายของคุณที่นี่) ...

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
