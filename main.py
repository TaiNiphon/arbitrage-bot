import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # 1. API & Env Setup
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # 2. Strategy Parameters
        self.symbol = os.getenv("SYMBOL", "THB_XRP").upper()
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 2000.00)) 
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        self.ema_period = 50
        self.fee_pct = 0.0025 # 0.25%
        self.min_trade = 50.0

        # 3. State Management
        self.state_file = "bot_state_v65.json"
        self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price, self.last_pnl = self._load_state()
        
        # 4. API Optimization
        self.time_offset = 0
        self.last_report_time = 0
        self._sync_time()

    def _sync_time(self):
        try:
            res = requests.get(f"{self.host}/api/v3/servertime", timeout=10)
            self.time_offset = int(res.text.strip()) - int(time.time() * 1000)
            logger.info(f"Time Synced. Offset: {self.time_offset}ms")
        except: pass

    def notify(self, msg):
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            requests.post(url, json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def _get_sig(self, ts, method, path, body):
        payload = ts + method + path + body
        return hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def api_req(self, method, path, payload=None, private=False):
        url = f"{self.host}{path}"
        body = json.dumps(payload, separators=(',', ':')) if payload else ""
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        
        if private:
            ts = str(int(time.time() * 1000) + self.time_offset)
            headers.update({
                'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': self._get_sig(ts, method, path, body)
            })
        try:
            r = requests.request(method, url, headers=headers, data=body, timeout=15)
            if r.status_code == 429: # Rate Limit
                time.sleep(10)
                return self.api_req(method, path, payload, private)
            return r.json()
        except: return {}

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    return d.get('la','sell'), d.get('ap',0.0), d.get('st',0), d.get('tu',0.0), d.get('hp',0.0), d.get('lp',0.0)
            except: pass
        return "sell", 0.0, 0, 0.0, 0.0, 0.0

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump({'la':self.last_action, 'ap':self.avg_price, 'st':self.current_stage, 'tu':self.total_units, 'hp':self.highest_price, 'lp':self.last_pnl}, f)

    def get_market_data(self):
        # ป้องกัน Error 'list' object has no attribute 'get'
        t = requests.get(f"{self.host}/api/market/ticker?sym={self.symbol}").json()
        price = float(t.get(self.symbol, {}).get('last', 0))
        
        h = requests.get(f"{self.host}/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}").json()
        cls = h.get('c', [])
        ema = None
        if len(cls) >= self.ema_period:
            k = 2 / (self.ema_period + 1)
            ema = sum(cls[:self.ema_period]) / self.ema_period
            for p in cls[self.ema_period:]: ema = (p * k) + (ema * (1 - k))
        return price, ema, cls[-2] if len(cls) > 1 else price

    def send_report(self, price, ema):
        w = self.api_req("POST", "/api/v3/market/wallet", {}, private=True)
        res = w.get('result', {})
        thb = float(res.get('THB', 0))
        coin_key = self.symbol.split('_')[1] # เช่น XRP
        coin = float(res.get(coin_key, 0))
        
        equity = thb + (coin * price)
        pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025)) * 100 if self.avg_price > 0 else 0
        
        status = "🚀 RUNNING PROFIT" if self.current_stage == 3 else ("🚀 HOLDING COIN" if coin*price > 50 else "💰 HOLDING CASH")
        
        msg = (
            f"<b>{status}</b>\n📅 {datetime.now(timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M')}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 MARKET: {self.symbol}\n💵 Price: {price:,.2f} THB\n"
            f"📈 EMA(50): {ema:,.2f} ({((price-ema)/ema*100):+.2f}%)\n"
            f"🕒 Net P/L: {pnl if self.avg_price > 0 else self.last_pnl:+.2f}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🏦 PORTFOLIO\n💰 Cash: {thb:,.2f} THB\n🪙 {coin_key}: {coin:,.4f}\n"
            f"💎 Equity: {equity:,.2f} THB\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📈 PERFORMANCE\n💵 Profit: {equity - self.initial_equity:,.2f} THB\n"
            f"🚀 Growth: {((equity - self.initial_equity)/self.initial_equity*100):+.2f}%\n"
            f"🛡️ Trailing @: {self.highest_price * (1 - self.trailing_pct/100):,.2f}"
        )
        self.notify(msg)

    def run(self):
        self.notify(f"<b>🚀 Bot V6.5 Final Integration Started</b>")
        while True:
            try:
                price, ema, prev_price = self.get_market_data()
                if price <= 0 or not ema: 
                    time.sleep(30); continue
                
                if self.last_report_time == 0:
                    self.send_report(price, ema)
                    self.last_report_time = time.time()

                # --- Logic การเทรด (Uptrend Confirmation) ---
                is_up = price > (ema * 1.002) and price > prev_price
                w = self.api_req("POST", "/api/v3/market/wallet", {}, private=True).get('result', {})
                thb, coin = float(w.get('THB', 0)), float(w.get(self.symbol.split('_')[1], 0))
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025)) * 100 if self.avg_price > 0 else 0

                # 🟢 BUY LOGIC (2 Stages)
                if is_up and self.current_stage < 2:
                    if self.current_stage == 0 and thb > 50:
                        r = self.api_req("POST", "/api/v3/market/place-bid", {"sym":self.symbol, "amt":round(thb*0.48, 2), "rat":round(price,4), "typ":"limit"}, private=True)
                        if r.get('error') == 0:
                            self.total_units, self.avg_price, self.current_stage, self.last_action, self.highest_price = float(r['result']['rec']), float(r['result']['rat']), 1, "buy", float(r['result']['rat'])
                            self._save_state(); self.notify(f"🟢 [BUY 1/2] @ {self.avg_price}")
                    elif self.current_stage == 1 and pnl > 0.3 and thb > 50:
                        r = self.api_req("POST", "/api/v3/market/place-bid", {"sym":self.symbol, "amt":round(thb*0.95, 2), "rat":round(price,4), "typ":"limit"}, private=True)
                        if r.get('error') == 0:
                            nq, nr = float(r['result']['rec']), float(r['result']['rat'])
                            self.avg_price = ((self.avg_price * self.total_units) + (nq * nr)) / (self.total_units + nq)
                            self.total_units += nq; self.current_stage = 2
                            self._save_state(); self.notify(f"🟢 [BUY 2/2] New Avg: {self.avg_price}")

                # 🔴 SELL LOGIC (Partial TP & Trailing)
                if self.last_action == "buy" and self.total_units > 0:
                    self.highest_price = max(self.highest_price, price)
                    if pnl <= -self.stop_loss:
                        r = self.api_req("POST", "/api/v3/market/place-ask", {"sym":self.symbol, "amt":math.floor(coin*1e7)/1e7, "rat":0, "typ":"market"}, private=True)
                        if r.get('error') == 0:
                            self.last_pnl = pnl; self.last_action, self.avg_price, self.current_stage, self.total_units = "sell", 0.0, 0, 0.0
                            self._save_state(); self.notify(f"🔴 [STOP LOSS] P/L: {pnl:.2f}%")
                    elif self.current_stage == 2 and pnl >= self.target_profit:
                        r = self.api_req("POST", "/api/v3/market/place-ask", {"sym":self.symbol, "amt":math.floor((self.total_units*0.5)*1e7)/1e7, "rat":0, "typ":"market"}, private=True)
                        if r.get('error') == 0:
                            self.total_units *= 0.5; self.current_stage = 3
                            self._save_state(); self.notify(f"💰 [PARTIAL SELL] Locked: {pnl:.2f}%")
                    elif self.current_stage == 3:
                        if price <= (self.highest_price * (1 - self.trailing_pct/100)) or price < ema:
                            r = self.api_req("POST", "/api/v3/market/place-ask", {"sym":self.symbol, "amt":math.floor(coin*1e7)/1e7, "rat":0, "typ":"market"}, private=True)
                            if r.get('error') == 0:
                                self.last_pnl = pnl; self.last_action, self.avg_price, self.current_stage, self.total_units = "sell", 0.0, 0, 0.0
                                self._save_state(); self.notify(f"🔴 [FINAL SELL] P/L: {pnl:.2f}%")

                if time.time() - self.last_report_time >= 3600:
                    self.send_report(price, ema)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Err: {e}")
            time.sleep(30)

def run_hc():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_hc, daemon=True).start()
    BitkubBot().run()
