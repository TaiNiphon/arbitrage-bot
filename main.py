import os, requests, time, hmac, hashlib, json, threading, logging, math
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubUltimateV8_5:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB").strip().upper() 
        self.coin = self.symbol.split('_')[0]
        
        try:
            self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "5000")).replace(',', ''))
            self.ema_period = int(os.getenv("EMA_PERIOD", 50))
            self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
            self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", 3.0))
            self.atr_multiplier = float(os.getenv("ATR_MULTIPLIER", 2.5))
            self.slope_threshold = float(os.getenv("SIDEWAYS_SLOPE_THRESHOLD", 0.02))
        except:
            self.initial_equity, self.ema_period, self.tp_stage_1, self.stop_loss_pct, self.atr_multiplier, self.slope_threshold = 5000, 50, 2.5, 3.0, 2.5, 0.02

        self.rsi_period = 14
        self.atr_period = 14

        # Internal State
        self.state_file = "bot_state_v8_5.json"
        self.last_action = "sell"
        self.avg_price = 0.0
        self.current_stage = 0 
        self.total_units = 0.0
        self.highest_price = 0.0
        self.report_interval = 1800 
        self.last_report_time = 0
        self.market_phase = "INITIALIZING"
        self.dynamic_sl = 0.0

        self._sync_setup()

    def _sync_setup(self):
        logger.info("🛠️ Syncing Master System...")
        thb, coin_bal = self.get_balance()
        try:
            ticker_res = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
            price = float(ticker_res[0].get('last', 0)) if isinstance(ticker_res, list) else 0.0
        except: price = 0.0

        if float(coin_bal) * float(price) > 50:
            self.last_action, self.total_units, self.current_stage = "buy", float(coin_bal), 2
            manual_avg = os.getenv("MY_AVG_PRICE")
            self.avg_price = float(str(manual_avg).replace(',', '')) if manual_avg else 0.0
            self.highest_price = max(self.avg_price, price)
        else:
            self.last_action, self.avg_price, self.current_stage, self.total_units = "sell", 0.0, 0, 0.0
        self._save_state()

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({"last_action": self.last_action, "avg_price": self.avg_price, "stage": self.current_stage, "total_units": self.total_units}, f)
        except: pass

    def _request(self, method, path, params=None, payload=None, private=False):
        url = f"{self.host}{path}"
        query_str = "?" + "&".join([f"{k}={v}" for k, v in params.items()]) if params else ""
        if params: url += query_str
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            try:
                ts = requests.get(f"{self.host}/api/v3/servertime", timeout=5).text.strip()
                sig = hmac.new(self.api_secret.encode('utf-8'), (ts + method + path + query_str + body_str).encode('utf-8'), hashlib.sha256).hexdigest()
                headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
            except: return {"error": 888}
        try:
            res = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return res.json()
        except: return {"error": 999}

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if isinstance(res, dict) and res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get(self.coin, 0))
        return 0.0, 0.0

    def place_order(self, side, amt):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        clean_amt = math.floor(float(amt) * 100) / 100 if side == "buy" else math.floor(float(amt) * 10000) / 10000
        payload = {"sym": self.symbol.lower(), "amt": clean_amt, "rat": 0, "typ": "market"}
        return self._request("POST", path, payload=payload, private=True)

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def update_data(self):
        try:
            hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
            if not hist or 'c' not in hist: return None
            c = np.array(hist['c'], dtype=float)
            h = np.array(hist['h'], dtype=float)
            l = np.array(hist['l'], dtype=float)
            ema = np.mean(c[-self.ema_period:])
            ema_prev = np.mean(c[-(self.ema_period+1):-1])
            diff = np.diff(c)
            up, down = diff.clip(min=0), -1 * diff.clip(max=0)
            ma_up, ma_down = np.mean(up[-self.rsi_period:]), np.mean(down[-self.rsi_period:])
            rsi = 100 - (100 / (1 + (ma_up / ma_down))) if ma_down != 0 else 100
            tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            atr = np.mean(tr[-self.atr_period:])
            return {"ema": ema, "ema_prev": ema_prev, "rsi": rsi, "atr": atr, "price": c[-1]}
        except: return None

    def run(self):
        self.notify(f"<b>🔥 Hybrid Master V8.5 Activated</b>\nTarget: {self.symbol}\nMode: Trend Pyramiding + ATR Guard")
        while True:
            try:
                data = self.update_data()
                if not data: 
                    time.sleep(15)
                    continue

                price, ema, ema_prev, rsi, atr = data['price'], data['ema'], data['ema_prev'], data['rsi'], data['atr']
                ema_slope = abs((ema - ema_prev) / ema_prev * 100)
                is_sideways = ema_slope < self.slope_threshold
                self.market_phase = "SIDEWAYS" if is_sideways else ("UPTREND" if ema > ema_prev else "DOWNTREND")

                thb, coin_bal = self.get_balance()
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                if self.last_action == "sell":
                    if price > (ema * 1.005) and ema > (ema_prev * 1.001) and rsi < 65:
                        res = self.place_order("buy", thb * 0.95)
                        if res.get('error') == 0:
                            self.avg_price, self.last_action, self.current_stage, self.total_units = price, "buy", 2, float(res['result']['rec'])
                            self.highest_price = price
                            self.notify(f"🟢 <b>[BUY] Entry</b>\nPrice: {price}")

                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)
                    self.dynamic_sl = self.highest_price - (atr * self.atr_multiplier)

                    reason = None
                    if pnl <= -self.stop_loss_pct: reason = "Stop Loss"
                    elif price <= self.dynamic_sl: reason = "ATR Trail Stop"
                    elif price < (ema * 0.99) and ema < ema_prev and not is_sideways: reason = "Trend Reversed"

                    if reason:
                        res = self.place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            self.notify(f"🔴 <b>[SELL ALL] {reason}</b>\nPNL: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.total_units = "sell", 0, 0, 0
                            self._save_state()

                if time.time() - self.last_report_time >= self.report_interval:
                    self.send_detailed_report(price, pnl, ema, rsi, atr)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

    def send_detailed_report(self, price, pnl, ema, rsi, atr):
        try:
            thb_bal, coin_bal = self.get_balance()
            total_equity = thb_bal + (coin_bal * price)
            net_profit = total_equity - self.initial_equity
            growth = (net_profit / self.initial_equity) * 100
            now = datetime.now(timezone.utc) + timedelta(hours=7)

            report = (
                f"💠 <b>STATUS: {'HOLDING' if coin_bal > 0 else 'IDLE'} | V8.5</b>\n"
                f"📅 {now.strftime('%d/%m/%Y %H:%M')}\n"
                f"🧩 <b>PHASE: {self.market_phase}</b>\n"
                f"📊 <b>Sentiment: RSI {rsi:.1f} | ATR {atr:.2f}</b>\n\n"
                f"📈 <b>MARKET: {self.symbol}</b>\n"
                f"💵 Price: {price:,.2f} THB\n"
                f"📊 EMA({self.ema_period}): {ema:,.2f} ({((price-ema)/ema*100):+.2f}%)\n"
                f"🕒 P/L: {pnl:+.2f}% (Avg: {self.avg_price:,.2f})\n\n"
                f"💰 <b>PORTFOLIO</b>\n"
                f"💵 Cash: {thb_bal:,.2f} THB\n"
                f"🪙 Coin: {coin_bal:.4f} ({ (coin_bal * price):,.2f} THB)\n"
                f"💎 <b>Equity: {total_equity:,.2f} THB</b>\n\n"
                f"🚀 <b>PERFORMANCE</b>\n"
                f"💵 Net Profit: {net_profit:,.2f} THB\n"
                f"📈 Growth: {growth:+.2f}%\n"
                f"🛡️ Trail Stop @: {self.dynamic_sl:,.2f}\n"
                "━━━━━━━━━━━━━━━"
            )
            self.notify(report)
        except: pass

def run_hc():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"V8.5 Active")
        def log_message(self, *a): return
    try: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()
    except: pass

if __name__ == "__main__":
    threading.Thread(target=run_hc, daemon=True).start()
    BitkubUltimateV8_5().run()
