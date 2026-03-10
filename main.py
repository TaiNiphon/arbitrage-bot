import os, requests, time, hmac, hashlib, json, threading, logging, math
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubUltimateV8_5_PRO:
    def __init__(self):
        # 1. API & Telegram Setup
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # 2. Strategy Config
        self.symbol = os.getenv("SYMBOL", "XRP_THB").strip().upper() 
        self.coin = self.symbol.split('_')[0]

        # ดึงค่า Config
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "5000")).replace(',', ''))
        self.ema_period = int(os.getenv("EMA_PERIOD", 20))
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", 3.0))
        self.atr_multiplier = float(os.getenv("ATR_MULTIPLIER", 2.5))
        self.breakeven_pct = float(os.getenv("BREAKEVEN_PCT", 0.7))
        self.slope_threshold = float(os.getenv("SIDEWAYS_SLOPE_THRESHOLD", 0.01))
        self.rsi_max = float(os.getenv("RSI_MAX", 75)) 
        self.rsi_min = float(os.getenv("RSI_MIN", 35))
        self.buy_alloc_pct = float(os.getenv("SIDEWAYS_BUY_ALLOC", 50)) / 100

        # 3. Internal State
        self.state_file = "bot_state_v8_5_pro.json"
        self.last_action = "sell"
        self.avg_price = 0.0
        self.current_stage = 0 
        self.total_units = 0.0
        self.highest_price = 0.0
        self.last_report_time = 0
        self.report_interval = 1800 
        self.dynamic_sl = 0.0
        self.market_phase = "INITIALIZING"

        self._sync_setup()

    def _sync_setup(self):
        thb, coin_bal = self.get_balance()
        try:
            ticker_res = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
            price = float(ticker_res[0].get('last', 0)) if isinstance(ticker_res, list) else 0.0
        except: price = 0.0

        manual_avg = os.getenv("MY_AVG_PRICE")
        manual_avg_val = float(str(manual_avg).replace(',', '')) if manual_avg else 0.0

        if float(coin_bal) * price > 50:
            self.last_action, self.total_units = "buy", float(coin_bal)
            self.avg_price = manual_avg_val if manual_avg_val > 0 else price
            self.highest_price = max(self.avg_price, price)
            self.current_stage = 2 
        else:
            self.last_action, self.avg_price, self.current_stage = "sell", 0.0, 0
        self._save_state()

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

    def calculate_ema(self, prices, period):
        if len(prices) < period: return np.mean(prices)
        alpha = 2 / (period + 1)
        ema = prices[0]
        for p in prices[1:]: ema = (p * alpha) + (ema * (1 - alpha))
        return ema

    def update_indicators(self):
        try:
            hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
            if not hist or 'c' not in hist: return None
            c, h, l = np.array(hist['c'], dtype=float), np.array(hist['h'], dtype=float), np.array(hist['l'], dtype=float)
            ema = self.calculate_ema(c, self.ema_period)
            ema_prev = self.calculate_ema(c[:-1], self.ema_period)
            diff = np.diff(c)
            up, down = diff.clip(min=0), -1 * diff.clip(max=0)
            ma_up, ma_down = np.mean(up[-14:]), np.mean(down[-14:])
            rsi = 100 - (100 / (1 + (ma_up / ma_down))) if ma_down != 0 else 100
            tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            atr = np.mean(tr[-14:])
            return {"ema": ema, "ema_prev": ema_prev, "rsi": rsi, "atr": atr, "price": c[-1], "high": h[-1]}
        except: return None

    def run(self):
        self.notify(f"<b>🚀 V8.5 PRO HYBRID: ONLINE</b>\n{self.symbol} | Smart TP Logic")
        while True:
            try:
                data = self.update_indicators()
                if not data: time.sleep(20); continue

                price, ema, ema_prev, rsi, atr, high = data['price'], data['ema'], data['ema_prev'], data['rsi'], data['atr'], data['high']
                ema_slope = abs((ema - ema_prev) / ema_prev * 100)
                is_sideways = ema_slope < self.slope_threshold
                self.market_phase = "SIDEWAYS" if is_sideways else ("UPTREND" if ema > ema_prev else "DOWNTREND")

                thb, coin_bal = self.get_balance()
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                # --- 🟢 BUY LOGIC ---
                if self.last_action == "sell" or self.current_stage == 1:
                    if self.current_stage == 0 and price > (ema * 1.001) and self.rsi_min < rsi < self.rsi_max:
                        buy_amt = thb * self.buy_alloc_pct
                        res = self.place_order("buy", buy_amt)
                        if res.get('error') == 0:
                            self.avg_price, self.last_action, self.current_stage = price, "buy", 1
                            self.highest_price = high
                            self.notify(f"🟢 <b>[BUY STAGE 1]</b>\nPrice: {price}\nAlloc: {self.buy_alloc_pct*100}%")

                    elif self.current_stage == 1 and price > self.avg_price and ema > ema_prev and not is_sideways:
                        res = self.place_order("buy", thb * 0.96)
                        if res.get('error') == 0:
                            self.avg_price = (self.avg_price + price) / 2
                            self.current_stage = 2
                            self.notify(f"🟢 <b>[BUY STAGE 2 - FULL]</b>\nPrice: {price}\nTrend Confirmed")

                # --- 🔴 SELL LOGIC ---
                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, high)
                    current_multiplier = self.atr_multiplier if pnl < self.tp_stage_1 else (self.atr_multiplier * 0.7)
                    self.dynamic_sl = self.highest_price - (atr * current_multiplier)

                    if self.current_stage == 2 and pnl >= self.tp_stage_1:
                        res = self.place_order("sell", coin_bal * 0.5)
                        if res.get('error') == 0:
                            self.current_stage = 3
                            self.notify(f"💰 <b>[PARTIAL TP 50%]</b>\nProfit Target Hit! Now Trailing the rest.")

                    reason = None
                    if pnl <= -self.stop_loss_pct: 
                        reason = "Stop Loss"
                    elif pnl >= self.breakeven_pct and price <= (self.avg_price * 1.0025): 
                        reason = "Breakeven Protected"
                    elif price <= self.dynamic_sl: 
                        reason = "Trailing Stop Hit"
                    elif price < (ema * 0.997) and ema < ema_prev and pnl < 0: 
                        reason = "Trend Down (Cut Loss)"

                    if reason:
    res = self.place_order("sell", coin_bal)
    if res.get('error') == 0:
        self.notify(f"🔴 [EXIT] {reason}\nPNL (Net): {pnl:+.2f}%")
        self.last_action, self.avg_price, self.current_stage = "sell", 0, 0
        self.dynamic_sl = 0

                    self._save_state()
                if time.time() - self.last_report_time >= self.report_interval:
                    self.send_pro_report(price, pnl, ema, rsi, atr)
                    self.last_report_time = time.time()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30)

    def send_pro_report(self, price, pnl, ema, rsi, atr):
        try:
            thb_bal, coin_bal = self.get_balance()
            total_equity = thb_bal + (coin_bal * price)
            net_profit = total_equity - self.initial_equity
            growth = (net_profit / self.initial_equity) * 100
            now = datetime.now(timezone.utc) + timedelta(hours=7)
            divider = "━━━━━━━━━━━━━━━"

            stage_map = {0: "IDLE", 1: "STAGE 1 (50%)", 2: "STAGE 2 (FULL)", 3: "TRAILING PROFIT"}
            current_status = stage_map.get(self.current_stage, "UNKNOWN")

            # แก้ไขส่วน Trailing @ ให้ตัดทศนิยมเหลือ 2 ตำแหน่ง
            trailing_display = f"{self.dynamic_sl:,.2f}" if self.dynamic_sl > 0 else "Waiting..."

            report = (
                f"⚪ <b>{current_status} | Hybrid V8.5 PRO</b>\n"
                f"📅 {now.strftime('%d/%m/%Y %H:%M')}\n"
                f"{divider}\n"
                f"📊 <b>MARKET: {self.symbol}</b>\n"
                f"💵 Price: {price:,.2f} THB\n"
                f"📈 EMA({self.ema_period}): {ema:,.2f} ({((price-ema)/ema*100):+.2f}%)\n"
                f"🕒 Net P/L: {pnl:+.2f}% (Fee Incl.)\n"
                f"🧩 Phase: {self.market_phase}\n"
                f"{divider}\n"
                f"🏛️ <b>PORTFOLIO</b>\n"
                f"💰 Cash: {thb_bal:,.2f} THB\n"
                f"🪙 Coin: {coin_bal:.4f} ({(coin_bal*price):,.2f} THB)\n"
                f"💎 <b>Equity: {total_equity:,.2f} THB</b>\n"
                f"{divider}\n"
                f"📈 <b>PERFORMANCE</b>\n"
                f"💵 Net Profit: {net_profit:,.2f} THB\n"
                f"🚀 Growth: {growth:+.2f}%\n"
                f"🛡️ Trailing @: {trailing_display}\n"
                f"📉 BreakEven @: {(self.avg_price * 1.0025):,.2f}\n"
                f"{divider}"
            )
            self.notify(report)
        except: pass

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({"last_action": self.last_action, "avg_price": self.avg_price, "stage": self.current_stage}, f)
        except: pass

def run_hc():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"V8.5 PRO ACTIVE")
        def log_message(self, *a): return
    try: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()
    except: pass

if __name__ == "__main__":
    threading.Thread(target=run_hc, daemon=True).start()
    BitkubUltimateV8_5_PRO().run()
