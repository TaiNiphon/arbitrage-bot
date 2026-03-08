import os, requests, time, hmac, hashlib, json, logging, math
import numpy as np
from datetime import datetime, timedelta, timezone

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubProfessionalV8_1:
    def __init__(self):
        # API & Portfolio Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.coin = self.symbol.split('_')[0]
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000)) 

        # Indicators & Strategy Parameters
        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.rsi_period = 14
        self.atr_period = 14
        self.atr_multiplier = float(os.getenv("ATR_MULTIPLIER", 2.0))
        
        # Trading Logic Constants
        self.slope_threshold = float(os.getenv("SIDEWAYS_SLOPE_THRESHOLD", 0.02))
        self.sw_buy_dip = float(os.getenv("SIDEWAYS_BUY_DIP", 0.5))
        self.sw_tp = float(os.getenv("SIDEWAYS_TP_PCT", 1.2))
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))
        self.breakeven_trigger = float(os.getenv("BREAKEVEN_PCT", 1.5))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", 5.0))

        # Internal State (Auto-Sync Logic)
        self.last_action = "sell"
        self.avg_price = 0.0 # จะถูกอัปเดตอัตโนมัติจากยอดคงเหลือจริง
        self.current_stage = 0 
        self.total_units = 0.0
        self.highest_price = 0.0
        self.dynamic_sl = 0.0
        self.report_interval = 1800 
        self.last_report_time = 0
        self.market_phase = "WAITING"

    def _request(self, method, path, params=None, payload=None, private=False):
        url = f"https://api.bitkub.com{path}"
        query_str = "?" + "&".join([f"{k}={v}" for k, v in params.items()]) if params else ""
        if params: url += query_str
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        body_str = json.dumps(payload, separators=(',', ':')) if payload else ""
        if private:
            ts = requests.get("https://api.bitkub.com/api/v3/servertime").text.strip()
            headers.update({
                'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts,
                'X-BTK-SIGN': hmac.new(self.api_secret.encode('utf-8'), (ts + method + path + query_str + body_str).encode('utf-8'), hashlib.sha256).hexdigest()
            })
        try:
            res = requests.request(method, url, headers=headers, data=body_str, timeout=15)
            return res.json()
        except Exception as e:
            logger.error(f"Network Error: {e}")
            return {"error": 999}

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res and res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get(self.coin, 0))
        return 0.0, 0.0

    def place_order(self, side, amt):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        # Bitkub Precision
        clean_amt = math.floor(amt * 100) / 100 if side == "buy" else math.floor(amt * 10**8) / 10**8
        if clean_amt <= 0: return {"error": "Invalid Amount"}
        
        payload = {"sym": self.symbol.lower(), "amt": clean_amt, "rat": 0, "typ": "market"}
        return self._request("POST", path, payload=payload, private=True)

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    def update_indicators(self):
        hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-200000, "to": int(time.time())})
        if not hist or not hist.get('c'): return None, None, None, None, None
        c, h, l = np.array(hist['c']), np.array(hist['h']), np.array(hist['l'])
        ema = np.mean(c[-self.ema_period:])
        ema_prev = np.mean(c[-(self.ema_period+1):-1])
        diff = np.diff(c)
        up, down = diff.copy(), diff.copy()
        up[up < 0] = 0; down[down > 0] = 0
        ma_up, ma_down = np.mean(up[-self.rsi_period:]), np.abs(np.mean(down[-self.rsi_period:]))
        rsi = 100 - (100 / (1 + (ma_up / ma_down))) if ma_down != 0 else 100
        tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
        atr = np.mean(tr[-self.atr_period:])
        return ema, ema_prev, rsi, atr, c[-1]

    def send_report(self, price, pnl, ema_val, rsi, atr):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        now = datetime.now(timezone.utc) + timedelta(hours=7)

        report = (
            f"💠 <b>STATUS: {'HOLDING' if coin_bal * price > 50 else 'IDLE'} | Hybrid V8.1</b>\n"
            f"📅 {now.strftime('%d/%m/%Y %H:%M')}\n"
            f"🧩 <b>PHASE: {self.market_phase}</b>\n"
            f"📊 <b>Sentiment: RSI {rsi:.1f} | ATR {atr:.4f}</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"📊 <b>MARKET: {self.symbol}</b>\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_val:,.2f} ({((price-ema_val)/ema_val*100):+.2f}%)\n"
            f"🕒 P/L: {pnl:+.2f}% (Avg: {self.avg_price:,.2f})\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>🏦 PORTFOLIO</b>\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Coin: {coin_bal:,.4f} ({(coin_bal*price):,.2f} THB)\n"
            f"💎 <b>Equity: {total_equity:,.2f} THB</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>📈 PERFORMANCE</b>\n"
            f"💵 Net Profit: {total_equity-self.initial_equity:,.2f} THB\n"
            f"🚀 Growth: {growth:+.2f}%\n"
            f"🛡️ Trail Stop: {f'{self.dynamic_sl:,.2f}' if self.dynamic_sl > 0 else 'Waiting...'}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)
        self.last_report_time = time.time()

    def run(self):
        # --- INITIAL SYNC ---
        thb, coin_bal = self.get_balance()
        ema, _, rsi, atr, price = self.update_indicators()
        if coin_bal * price > 50:
            self.last_action = "buy"
            self.total_units = coin_bal
            # ใส่ราคาเฉลี่ยที่คุณติดอยู่ (ถ้าหาไม่ได้ให้บอทใช้ราคาตลาดตอนเริ่มเป็นฐาน)
            self.avg_price = float(os.getenv("MY_AVG_PRICE", price)) 
            self.highest_price = price
            self.dynamic_sl = price - (atr * self.atr_multiplier)
            self.notify(f"🔄 <b>RE-SYNCED:</b> เข้าคุมไม้ค้างที่ราคา {self.avg_price}")

        self.notify(f"<b>🚀 V8.1 Pro Activated</b>\nรายงานครบเหมือน V7.3 ระบบเป๊ะเหมือน V8")
        
        while True:
            try:
                ema, ema_prev, rsi, atr, price = self.update_indicators()
                if ema is None: continue
                ema_slope = abs((ema - ema_prev) / ema_prev * 100)
                is_sideways = ema_slope < self.slope_threshold
                self.market_phase = "SIDEWAYS" if is_sideways else ("UPTREND" if ema > ema_prev else "DOWNTREND")
                thb, coin_bal = self.get_balance()
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                if self.last_action == "sell":
                    buy_amt = 0
                    if price > (ema * 1.005) and ema > ema_prev and rsi < 65:
                        buy_amt = thb * 0.98
                        tag = "TREND BUY"
                    elif is_sideways and rsi < 35:
                        buy_amt = thb * 0.40
                        tag = "SW BUY"
                    
                    if buy_amt > 10:
                        res = self.place_order("buy", buy_amt)
                        if res.get('error') == 0:
                            self.avg_price, self.last_action, self.current_stage = price, "buy", 1
                            self.total_units = float(res['result']['rec'])
                            self.highest_price = price
                            self.dynamic_sl = price - (atr * self.atr_multiplier)
                            self.notify(f"🟢 <b>[{tag}]</b>\nPrice: {price}\nSL: {self.dynamic_sl:.2f}")

                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)
                    self.dynamic_sl = max(self.dynamic_sl, self.highest_price - (atr * self.atr_multiplier))
                    if pnl >= self.breakeven_trigger:
                        self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.005)

                    exit_reason = None
                    if price <= self.dynamic_sl: exit_reason = "Dynamic SL/Trailing"
                    elif pnl <= -self.stop_loss_pct: exit_reason = "Hard Stop Loss"
                    elif is_sideways and pnl >= self.sw_tp: exit_reason = "Sideways TP"

                    if exit_reason:
                        res = self.place_order("sell", coin_bal)
                        if res.get('error') == 0:
                            self.notify(f"🔴 <b>[SELL ALL]</b> {exit_reason}\nPNL: {pnl:+.2f}%")
                            self.last_action, self.avg_price, self.current_stage, self.dynamic_sl = "sell", 0, 0, 0

                if time.time() - self.last_report_time >= self.report_interval:
                    self.send_report(price, pnl, ema, rsi, atr)
            except Exception as e: logger.error(e)
            time.sleep(30)

if __name__ == "__main__":
    BitkubProfessionalV8_1().run()
