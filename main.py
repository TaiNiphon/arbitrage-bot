import os, requests, time, hmac, hashlib, json, logging, math
import numpy as np
from datetime import datetime, timedelta, timezone

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubHybridV7_3Full:
    def __init__(self):
        # API & Portfolio Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.coin = self.symbol.split('_')[0]
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 5000)) 
        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        
        # Advanced Indicators (V7.3 Special)
        self.rsi_period = 14
        self.rsi_oversold = float(os.getenv("RSI_OVERSOLD", 30))
        self.atr_period = 14

        # Trading Parameters
        self.slope_threshold = float(os.getenv("SIDEWAYS_SLOPE_THRESHOLD", 0.02))
        self.sw_buy_dip = float(os.getenv("SIDEWAYS_BUY_DIP", 0.5))
        self.sw_tp = float(os.getenv("SIDEWAYS_TP_PCT", 1.2))
        self.sw_alloc = float(os.getenv("SIDEWAYS_BUY_ALLOC", 0.3))
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 5.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        # Internal State
        self.last_action = "sell"
        self.avg_price = 0.0
        self.current_stage = 0 
        self.total_units = 0.0
        self.highest_price = 0.0
        self.report_interval = 1800 
        self.last_report_time = 0
        self.market_phase = "WAITING"
        self.rsi_val = 50.0
        self.atr_val = 0.0

    # --- API Helper Functions (Standard V7) ---
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
        except: return {"error": 999}

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res and res.get('error') == 0:
            return float(res['result'].get('THB', 0)), float(res['result'].get(self.coin, 0))
        return 0.0, 0.0

    def place_order(self, side, amt):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        # Precision handling for Bitkub
        clean_amt = math.floor(amt * 100) / 100 if side == "buy" else math.floor(amt * 10000) / 10000
        payload = {"sym": self.symbol.lower(), "amt": clean_amt, "rat": 0, "typ": "market"}
        return self._request("POST", path, payload=payload, private=True)

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    # --- Advanced Math (RSI/ATR) ---
    def update_indicators(self):
        hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
        if not hist.get('c'): return None, None, None
        
        c = np.array(hist['c'])
        h = np.array(hist['h'])
        l = np.array(hist['l'])
        
        # EMA
        ema = np.mean(c[-self.ema_period:])
        ema_prev = np.mean(c[-(self.ema_period+1):-1])
        
        # RSI
        diff = np.diff(c)
        up = diff.clip(min=0)
        down = -1 * diff.clip(max=0)
        ma_up = np.mean(up[-self.rsi_period:])
        ma_down = np.mean(down[-self.rsi_period:])
        self.rsi_val = 100 - (100 / (1 + (ma_up / ma_down))) if ma_down != 0 else 100
        
        # ATR
        tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
        self.atr_val = np.mean(tr[-self.atr_period:])
        
        return ema, ema_prev, c[-1]

    # --- Detailed Report (V7.2 Layout) ---
    def send_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        growth = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        now = datetime.now(timezone.utc) + timedelta(hours=7)

        report = (
            f"💠 <b>STATUS: {'HOLDING' if coin_bal * price > 50 else 'IDLE'} | Hybrid V7.3</b>\n"
            f"📅 {now.strftime('%d/%m/%Y %H:%M')}\n"
            f"🧩 <b>PHASE: {self.market_phase}</b>\n"
            f"📊 <b>Sentiment: RSI {self.rsi_val:.1f} | ATR {self.atr_val:.2f}</b>\n"
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
            f"🛡️ Trail Stop: {f'{(self.highest_price*(1-self.trailing_pct/100)):,.2f}' if self.current_stage==3 else 'Waiting...'}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)
        self.last_report_time = time.time()

    def run(self):
        self.notify(f"<b>🔥 V7.3 Full Pro Activated</b>\nAll Features (Pyramiding/Partial TP) Sync'd")
        while True:
            try:
                ema, ema_prev, price = self.update_indicators()
                if ema is None: continue

                ema_slope = abs((ema - ema_prev) / ema_prev * 100)
                is_sideways = ema_slope < self.slope_threshold
                self.market_phase = "SIDEWAYS" if is_sideways else ("UPTREND" if ema > ema_prev else "DOWNTREND")
                
                thb, coin_bal = self.get_balance()
                pnl = (((price * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0

                # --- TRADING LOGIC (The Complete Hybrid) ---
                if self.last_action == "sell":
                    # 1. Trend Entry (Breakout + RSI Filter)
                    if price > (ema * 1.005) and ema > (ema_prev * 1.001) and self.rsi_val < 65:
                        res = self.place_order("buy", thb * 0.48)
                        if res.get('error') == 0:
                            self.avg_price, self.last_action, self.current_stage, self.total_units, self.highest_price = price, "buy", 1, float(res['result']['rec']), price
                            self.notify(f"🟢 <b>[TREND BUY 1/2]</b>\nPrice: {price}")

                    # 2. Sideways Entry (Buy Dip + Oversold Filter)
                    elif is_sideways and price < (ema * (1 - self.sw_buy_dip/100)) and self.rsi_val < self.rsi_oversold:
                        res = self.place_order("buy", thb * self.sw_alloc)
                        if res.get('error') == 0:
                            self.avg_price, self.last_action, self.current_stage, self.total_units = price, "buy", "sw_grid", float(res['result']['rec'])
                            self.notify(f"🔵 <b>[SIDEWAYS BUY] RSI {self.rsi_val:.1f}</b>")

                elif self.last_action == "buy" and coin_bal > 0:
                    self.highest_price = max(self.highest_price, price)

                    # --- CASE: Sideways Mode ---
                    if self.current_stage == "sw_grid":
                        if pnl >= self.sw_tp:
                            self.place_order("sell", coin_bal)
                            self.last_action, self.avg_price, self.current_stage = "sell", 0, 0
                            self.notify(f"💰 <b>[SW PROFIT] {pnl:+.2f}%</b>")
                        elif not is_sideways and price > (ema * 1.01):
                            self.current_stage = 1 # Switch to Trend Mode
                            self.notify("🚀 <b>[MODE UPGRADE] SW -> TREND</b>")

                    # --- CASE: Trend Mode (Including Stage 2 & 3) ---
                    else:
                        # Pyramiding (ซื้อเพิ่มไม้ 2) - Missing in previous version
                        if self.current_stage == 1 and pnl > 0.5 and price > (ema * 1.005):
                            res = self.place_order("buy", thb * 0.95)
                            if res.get('error') == 0:
                                new_units = float(res['result']['rec'])
                                self.avg_price = ((self.avg_price * self.total_units) + (price * new_units)) / (self.total_units + new_units)
                                self.total_units += new_units; self.current_stage = 2
                                self.notify("🟢 <b>[TREND BUY 2/2] Pyramiding</b>")

                        # Partial TP (แบ่งขายทำกำไร) - Missing in previous version
                        elif self.current_stage == 2 and pnl >= self.tp_stage_1:
                            res = self.place_order("sell", coin_bal * 0.5)
                            if res.get('error') == 0:
                                self.total_units -= (coin_bal * 0.5); self.current_stage = 3
                                self.notify(f"💰 <b>[PARTIAL TP 50%]</b> PNL: {pnl:+.2f}%")

                        # Exit Strategy (Trailing / Stop Loss)
                        reason = None
                        if pnl <= -self.stop_loss: reason = "Stop Loss"
                        elif self.current_stage == 3 and price < self.highest_price * (1 - self.trailing_pct/100): reason = "Trailing Stop"
                        elif price < (ema * 0.99) and ema < ema_prev and not is_sideways: reason = "Trend Reversed"

                        if reason:
                            self.place_order("sell", coin_bal)
                            self.last_action, self.avg_price, self.current_stage = "sell", 0, 0
                            self.notify(f"🔴 <b>[SELL ALL] {reason}</b>")

                if time.time() - self.last_report_time >= self.report_interval:
                    self.send_report(price, pnl, ema)

            except Exception as e: logger.error(f"Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    BitkubHybridV7_3Full().run()
