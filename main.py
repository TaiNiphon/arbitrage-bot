import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubProBotV6_3:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Trading Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.coin = self.symbol.split('_')[0]
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000.00))
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.fee_pct = 0.0025 
        self.min_trade = 10.0 

        # --- Force Sync Logic ---
        self.sync_and_setup()
        self.last_report_time = 0

    def sync_and_setup(self):
        """ระบบตรวจสอบยอดจริงเพื่อป้องกันการซื้อซ้ำซ้อน"""
        logger.info("Force Syncing with Bitkub Wallet...")
        thb_bal, coin_bal = self.get_balance()
        
        # ดึงราคาล่าสุด
        ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
        curr_price = 0
        if isinstance(ticker, list):
            for item in ticker:
                if item['symbol'].upper() == self.symbol:
                    curr_price = float(item['last']); break

        # ถ้ามีเหรียญในกระเป๋า (มูลค่า > 10 บาท) ให้ถือว่าซื้อครบแล้ว (Stage 2)
        if coin_bal * curr_price > self.min_trade:
            self.last_action = "buy"
            self.total_units = coin_bal
            self.avg_price = curr_price # ใช้ราคาปัจจุบันอ้างอิง
            self.current_stage = 2 
            self.highest_price = curr_price
            logger.info(f"Detected {coin_bal} {self.coin}. Setting status to HOLDING (Stage 2).")
        else:
            self.last_action = "sell"
            self.total_units = 0.0
            self.avg_price = 0.0
            self.current_stage = 0
            self.highest_price = 0.0
            logger.info("No assets detected. Ready for new signals.")

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def get_server_time(self):
        try:
            return requests.get(f"{self.host}/api/v3/servertime", timeout=10).text.strip()
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
        try:
            requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def send_detailed_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100
        now_th = self.get_local_time()
        
        status = "🚀 HOLDING" if coin_value > self.min_trade else "💰 WAITING"
        ema_str = f"{ema_val:,.2f}" if ema_val else "N/A"
        t_stop = f"{self.highest_price * (1 - (self.trailing_pct/100)):,.2f}" if self.current_stage == 3 else "Waiting..."

        report = (
            f"<b>{status} | {self.symbol} (V6.3)</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_str}\n"
            f"🕒 Current P/L: {pnl:+.2f}%\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Asset: {coin_bal:,.4f} ({coin_value:,.2f})\n"
            f"💎 Equity: {total_equity:,.2f} THB\n"
            "━━━━━━━━━━━━━━━\n"
            f"🚀 Total Growth: {growth_pct:+.2f}%\n"
            f"🛡️ Trailing @: {t_stop}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"<b>🤖 Bitkub V6.3 Hybrid Started</b>\nSync Complete: {'Holding' if self.current_stage > 0 else 'Waiting Signal'}")
        
        while True:
            try:
                # 1. Fetch Data
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                price = 0
                if isinstance(ticker, list):
                    for item in ticker:
                        if item['symbol'].upper() == self.symbol: price = float(item['last']); break
                
                hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
                prices = hist.get('c', [])
                ema = sum(prices[-self.ema_period:]) / self.ema_period if len(prices) >= self.ema_period else None

                thb, coin_bal = self.get_balance()
                pnl = ((price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0

                # 2. Strategy Logic: Buy (Only if NO assets)
                if self.last_action == "sell" and ema and price > ema * 1.01:
                    res = self.place_order("buy", thb * 0.95)
                    if res.get('error') == 0:
                        self.last_action, self.current_stage, self.avg_price = "buy", 2, price
                        self.highest_price = price
                        self.notify(f"🟢 <b>[BUY] Signal Confirmed</b>\nPrice: {price:,.2f}")

                # 3. Strategy Logic: Exit (If HOLDING)
                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)
                    
                    # Partial TP
                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        res = self.place_order("sell", coin_bal * 0.5)
                        if res.get('error') == 0:
                            self.current_stage = 3
                            self.notify(f"🟠 <b>[TP 50%] Locked</b>\nPNL: {pnl:+.2f}%")
                    
                    # Full Exit Conditions
                    reason = None
                    if pnl <= -self.stop_loss: reason = "Stop Loss"
                    elif self.current_stage == 3 and price < self.highest_price * (1 - self.trailing_pct/100): reason = "Trailing Stop"
                    elif ema and price < ema * 0.985: reason = "Trend Reversed (EMA)"

                    if reason:
                        res = self.place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            self.last_action, self.current_stage, self.avg_price = "sell", 0, 0
                            self.notify(f"🔴 <b>[SELL ALL]</b>\nReason: {reason}\nPNL: {pnl:+.2f}%")

                # 4. Detailed Report (Every 30 Mins)
                if time.time() - self.last_report_time >= 1800:
                    self.send_detailed_report(price, pnl, ema)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Error: {e}")
            time.sleep(30)

# --- Health Check ---
def run_health():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    BitkubProBotV6_3().run()
