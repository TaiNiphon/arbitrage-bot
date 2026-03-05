import os, requests, time, hmac, hashlib, json, threading, logging, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BitkubProBotV6_Fixed:
    def __init__(self):
        # API Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.host = "https://api.bitkub.com"

        # Strategy Config (ถอดแบบจาก V.6.0 Pro เป๊ะ)
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper() 
        self.coin = self.symbol.split('_')[0]
        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 2030.71)) 
        self.tp_stage_1 = float(os.getenv("TP_STAGE_1", 2.5))    
        self.stop_loss = float(os.getenv("STOP_LOSS_PCT", 2.0))
        self.trailing_pct = float(os.getenv("TRAILING_PCT", 1.0))

        self.ema_period = int(os.getenv("EMA_PERIOD", 50))
        self.fee_pct = 0.0025 
        self.min_trade = 10.0 

        self.state_file = f"bot_state_v6_pro.json"
        self.last_report_time = 0

        # --- เสริมจุดบอด: Force Sync กับ Wallet จริงตอนเริ่ม ---
        self._sync_setup()

    def _sync_setup(self):
        logger.info("🛠️ Syncing with Wallet...")
        thb, coin_bal = self.get_balance()
        ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
        price = 0
        if isinstance(ticker, list):
            for item in ticker:
                if item['symbol'].upper() == self.symbol: price = float(item['last'])

        if coin_bal * price > self.min_trade:
            self.last_action, self.total_units, self.current_stage = "buy", coin_bal, 2
            self.avg_price = price
            self.highest_price = price
            # พยายามโหลดทุนจริงถ้ามีไฟล์เดิม
            if os.path.exists(self.state_file):
                try:
                    with open(self.state_file, "r") as f:
                        d = json.load(f)
                        self.avg_price = d.get('avg_price', price)
                        self.current_stage = d.get('stage', 2)
                except: pass
        else:
            self.last_action, self.avg_price, self.current_stage, self.total_units, self.highest_price = "sell", 0.0, 0, 0.0, 0.0
        self.last_pnl = 0.0

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_action": self.last_action, "avg_price": self.avg_price,
                    "stage": self.current_stage, "total_units": self.total_units,
                    "highest_price": self.highest_price
                }, f)
        except: pass

    def get_local_time(self):
        return datetime.now(timezone.utc) + timedelta(hours=7)

    def get_server_time(self):
        try: return requests.get(f"{self.host}/api/v3/servertime").text.strip()
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
        # ปรับทศนิยมให้ Bitkub ยอมรับ
        clean_amt = math.floor(amt * 100) / 100 if side == "buy" else math.floor(amt * 10000) / 10000
        payload = {"sym": self.symbol.lower(), "amt": clean_amt, "rat": 0, "typ": typ}
        return self._request("POST", path, payload=payload, private=True)

    def notify(self, msg):
        if not self.tg_token: return
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", 
                          json={"chat_id": self.tg_chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        except: pass

    # --- รายงานหน้าตาแบบ V.6.0 Pro เป๊ะ ---
    def send_detailed_report(self, price, pnl, ema_val):
        thb_bal, coin_bal = self.get_balance()
        coin_value = coin_bal * price
        total_equity = thb_bal + coin_value
        net_profit = total_equity - self.initial_equity
        growth_pct = (net_profit / self.initial_equity) * 100
        now_th = self.get_local_time()

        is_holding = coin_value > self.min_trade
        status = "HOLDING COIN" if is_holding else "HOLDING CASH"

        # แก้ไขจุดนี้: ใช้ :+.2f เพื่อให้เครื่องหมาย + หรือ - แสดงผลตามค่าจริงอัตโนมัติ
        diff_ema = f"({((price - ema_val)/ema_val*100):+.2f}%)" if ema_val else ""
        
        pnl_label = "Net P/L" if is_holding else "Last Trade P/L"
        pnl_display = pnl if is_holding else self.last_pnl

        report = (
            f"🚀 <b>{status}</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━\n"
            f"📊 <b>MARKET: {self.symbol}</b>\n"
            f"💵 Price: {price:,.2f} THB\n"
            f"📈 EMA({self.ema_period}): {ema_val:,.2f} {diff_ema}\n"
            f"🕒 {pnl_label}: {pnl_display:+.2f}% (Fee Incl.)\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>🏦 PORTFOLIO</b>\n"
            f"💰 Cash: {thb_bal:,.2f} THB\n"
            f"🪙 Coin: {coin_bal:,.4f} ({coin_value:,.2f} THB)\n"
            f"💎 <b>Equity: {total_equity:,.2f} THB</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>📈 PERFORMANCE</b>\n"
            f"💵 Net Profit: {net_profit:,.2f} THB\n"
            f"🚀 Growth: {growth_pct:+.2f}%\n"
            f"🛡️ Trailing @: {f'{self.highest_price * 0.99:,.2f}' if self.current_stage == 3 else 'Waiting...'}\n"
            "━━━━━━━━━━━━━━━"
        )
        self.notify(report)

    def run(self):
        self.notify(f"<b>🚀 Bot V6.0 Pro Started</b>\nMonitoring {self.symbol} (EMA {self.ema_period})")

        while True:
            try:
                ticker = self._request("GET", "/api/v3/market/ticker", params={"sym": self.symbol.lower()})
                price = 0
                if isinstance(ticker, list):
                    for item in ticker:
                        if item['symbol'].upper() == self.symbol: price = float(item['last'])

                hist = self._request("GET", "/tradingview/history", params={"symbol": self.symbol, "resolution": "15", "from": int(time.time())-172800, "to": int(time.time())})
                prices = hist.get('c', [])
                ema = sum(prices[-self.ema_period:]) / self.ema_period if len(prices) >= self.ema_period else None
                ema_prev = sum(prices[-(self.ema_period+1):-1]) / self.ema_period if len(prices) > self.ema_period else None

                thb, coin_bal = self.get_balance()
                pnl = (((price*0.9975) - (self.avg_price*1.0025)) / (self.avg_price*1.0025) * 100) if self.avg_price > 0 else 0

                # --- กลยุทธ์ V.6.0 Pro: ไม้ 1 (Confirm Trend) ---
                if self.last_action == "sell" and ema and price > ema * 1.01 and ema > ema_prev:
                    res = self.place_order("buy", thb * 0.45)
                    if res.get('error') == 0:
                        self.avg_price, self.last_action, self.current_stage = price, "buy", 1
                        self.total_units = float(res['result'].get('rec', (thb*0.45/price)*0.9975))
                        self.highest_price = price
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 1/2] Confirmed</b>\nPrice: {price:,.2f}\nUnits: {self.total_units:,.4f}")

                # --- กลยุทธ์ V.6.0 Pro: ไม้ 2 (Pyramiding เมื่อกำไร) ---
                elif self.current_stage == 1 and pnl > 0.5 and ema and price > ema * 1.01:
                    res = self.place_order("buy", thb * 0.95)
                    if res.get('error') == 0:
                        new_units = float(res['result'].get('rec', (thb*0.95/price)*0.9975))
                        self.avg_price = ((self.avg_price * self.total_units) + (price * new_units)) / (self.total_units + new_units)
                        self.total_units += new_units
                        self.current_stage = 2
                        self._save_state()
                        self.notify(f"🟢 <b>[BUY 2/2] Pyramiding</b>\nAdded: {new_units:,.4f}\nNew Avg: {self.avg_price:,.2f}")

                #
