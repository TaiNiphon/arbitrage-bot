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
        self.symbol = os.getenv("SYMBOL", "thb_xrp").lower() # แนะนำใช้ thb_xrp ตาม format bitkub
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))
        self.state_file = "/tmp/bot_state_v7_final.json"
        
        # Load State
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
            coin = self.symbol.split('_')[1].upper() # e.g. XRP
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

    def place_order(self, side, amount_thb=None, amount_coin=None):
        """ side: 'buy' or 'sell' """
        path = "/api/v3/market/place-bid" if side == 'buy' else "/api/v3/market/place-ask"
        payload = {
            "sym": self.symbol.upper(),
            "typ": "market", # ใช้ Market order เพื่อความชัวร์ในการเข้าไม้
            "amt": amount_thb if side == 'buy' else amount_coin
        }
        res = self._request("POST", path, payload, private=True)
        if res.get('error') == 0:
            return res['result']
        else:
            self.notify(f"❌ Order Error: {res.get('error')}")
            return None

    def send_detailed_report(self, price, ema_val, pnl):
        thb_bal, coin_bal = self.get_balance()
        total_equity = thb_bal + (coin_bal * price)
        all_time_pnl = ((total_equity - self.initial_equity) / self.initial_equity) * 100
        ema_diff = ((price - ema_val) / ema_val * 100) if ema_val else 0
        now_th = self.get_local_time()
        
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
        self.notify(f"🚀 Bot v7.6 Final Fixed (Live)\nReady for {self.symbol.upper()}")
        while True:
            try:
                # 1. ดึงราคาปัจจุบัน (Fix API v3 format)
                ticker_res = self._request("GET", f"/api/v3/market/ticker?sym={self.symbol.upper()}")
                current_price = 0
                sym_key = self.symbol.upper()
                if sym_key in ticker_res:
                    current_price = float(ticker_res[sym_key].get('last', 0))

                if current_price == 0:
                    time.sleep(10); continue

                # 2. คำนวณ EMA
                history = self._request("GET", f"/tradingview/history?symbol={self.symbol.upper()}&resolution=15&from={int(time.time())-172800}&to={int(time.time())}")
                ema_val = self.calculate_ema(history.get('c', []), 50)

                if ema_val:
                    pnl = ((current_price - self.avg_price) / self.avg_price * 100) if self.avg_price > 0 else 0.0
                    
                    # อัปเดตราคาพุ่งสูงสุดเพื่อทำ Trailing Stop
                    if self.last_action == "buy" and current_price > self.highest_price:
                        self.highest_price = current_price
                        self._save_state()

                    # --- Logic การส่ง Report ---
                    if time.time() - self.last_report_time >= 10800: # ทุก 3 ชม.
                        self.send_detailed_report(current_price, ema_val, pnl)
                        self.last_report_time = time.time()

                    # --- Logic การตัดสินใจซื้อ (ไม้ 1 & 2) ---
                    thb_bal, coin_bal = self.get_balance()
                    
                    # ไม้ที่ 1: ซื้อเมื่อราคาตัด EMA(50) ขึ้นไป 1%
                    if self.current_stage == 0 and current_price > (ema_val * 1.01):
                        buy_amt = self.initial_equity / 2 # ใช้ครึ่งหนึ่งของทุนตั้งต้น
                        if thb_bal >= buy_amt:
                            order = self.place_order('buy', amount_thb=buy_amt)
                            if order:
                                self.last_action = "buy"
                                self.current_stage = 1
                                self.avg_price = current_price
                                self.highest_price = current_price
                                self.notify(f"✅ ซื้อไม้ที่ 1 สำเร็จที่ราคา {current_price}")
                                self._save_state()

                    # ไม้ที่ 2: ซื้อเพิ่มเมื่อราคาย่อตัวลงมาใกล้ EMA (Pullback) หลังจากมีไม้ 1 แล้ว
                    elif self.current_stage == 1 and current_price <= (ema_val * 1.005):
                        buy_amt = thb_bal * 0.95 # ใช้เงินที่เหลือเกือบหมด
                        if thb_bal > 10:
                            order = self.place_order('buy', amount_thb=buy_amt)
                            if order:
                                # คำนวณราคาเฉลี่ยใหม่ (แบบคร่าวๆ)
                                self.avg_price = (self.avg_price + current_price) / 2
                                self.current_stage = 2
                                self.notify(f"✅ ซื้อไม้ที่ 2 (ไม้แก้) สำเร็จที่ราคา {current_price}\nราคาเฉลี่ยใหม่: {self.avg_price:,.2f}")
                                self._save_state()

                    # --- Logic การขาย (Take Profit / Trailing Stop) ---
                    if self.last_action == "buy":
                        # Trailing Stop: ถ้ากำไรถึงเป้าแล้วราคาย่อลงจากจุดสูงสุด 1%
                        if pnl >= self.target_profit:
                            stop_signal = self.highest_price * (1 - (self.trailing_pct/100))
                            if current_price <= stop_signal:
                                if self.place_order('sell', amount_coin=coin_bal):
                                    self.notify(f"💰 ปิดกำไร (Trailing Stop) ที่ {current_price} (PNL: {pnl:+.2f}%)")
                                    self.last_action = "sell"; self.current_stage = 0; self.avg_price = 0; self.highest_price = 0
                                    self._save_state()

                        # Stop Loss
                        elif pnl <= -self.stop_loss:
                            if self.place_order('sell', amount_coin=coin_bal):
                                self.notify(f"⚠️ Stop Loss ที่ {current_price} (PNL: {pnl:+.2f}%)")
                                self.last_action = "sell"; self.current_stage = 0; self.avg_price = 0; self.highest_price = 0
                                self._save_state()

            except Exception as e: logger.error(f"Loop Error: {e}")
            time.sleep(30) # เช็กทุก 30 วินาที

def run_health_check():
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Active")
        def log_message(self, *a): return
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_check, daemon=True).start()
    BitkubBot().run()
