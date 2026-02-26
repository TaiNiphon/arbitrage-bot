import os, requests, time, hmac, hashlib, json, threading, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubBot:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_id = os.getenv("LINE_USER_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config
        self.symbol = os.getenv("SYMBOL", "xrp_thb").lower() 
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 1500.00))
        self.target_profit = float(os.getenv("TARGET_PROFIT_PCT", 3.0))
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        # State Management
        self.state_file = "/tmp/bot_state_master.json"
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
                    return d.get('last_action', 'sell'), d.get('avg_price', 0.0), d.get('stage', 0), d.get('total_units', 0.0), d.get('highest_price', 0.0)
            except: pass
        return "sell", 0.0, 0, 0.0, 0.0

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", {}, private=True)
        if res.get('error') == 0:
            coin = self.symbol.split('_')[0].upper()
            return float(res['result'].get('THB', 0)), float(res['result'].get(coin, 0))
        return 0.0, 0.0

    def cancel_all_orders(self):
        """ ยกเลิกออเดอร์ค้าง (V3 Standard) """
        res = self._request("GET", f"/api/v3/market/my-open-orders?sym={self.symbol}", private=True)
        if res.get('error') == 0 and res.get('result'):
            for order in res['result']:
                payload = {"sym": self.symbol, "id": order['id'], "sd": order['side'].lower()}
                self._request("POST", "/api/v3/market/cancel-order", payload, private=True)
            return True
        return False

    def place_order_v3(self, side, amount, price):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        # ใช้ Precision 2 ตำแหน่งสำหรับราคาตาม Bitkub Standard
        payload = {
            "sym": self.symbol,
            "amt": int(amount) if side == "buy" else round(amount, 8),
            "rat": round(price, 2),
            "typ": "limit"
        }
        return self._request("POST", path, payload, private=True)

    def calculate_ema
