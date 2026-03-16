import os, requests, time, hmac, hashlib, json, threading, math
import numpy as np
from datetime import datetime, timedelta, timezone

class TitanMasterV10:
    def __init__(self):
        # 1. Identity & Config
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = "XRP_THB"
        
        # 2. Hardcoded Professional Logic (วินัยคงที่ ไม่ปรับไปมา)
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "5000")).replace(',', ''))
        self.stop_loss_pct = 1.0     # คัทลอสทันทีที่ 1% เพื่อรักษาทุน 99%
        self.tp_target = 1.5         # เป้ากำไรที่หวังผลได้จริง
        self.rsi_buy_max = 50        # ห้ามซื้อถ้า RSI เกิน 50 (ป้องกันดอย)
        self.ema_dist_limit = 0.3    # ต้องใกล้เส้น EMA20 เท่านั้นถึงซื้อ
        
        # 3. State Management
        self.state_file = "titan_master_v10.json"
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        self._load_state()

    def update_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}").json()
            c = np.array(res['c'], dtype=float)
            ema = self.calculate_ema(c, 20)
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema": ema, "rsi": rsi, "atr": atr}
        except: return None

    def _report(self, price, pnl, thb, coin, rsi, status="MASTER_ACTIVE"):
        coin_val = coin * price; total = thb + coin_val
        growth = ((total - self.initial_equity) / self.initial_equity) * 100
        div = "━━━━━━━━━━━━━━━"
        msg = (
            f"<b>🏆 TITAN MASTER V.10 (XRP)</b>\n"
            f"🕒 Status: {status}\n{div}\n"
            f"💰 Price: <b>{price:,.2f}</b> | P/L: <b>{pnl:+.2f}%</b>\n"
            f"📊 RSI: {rsi:.1f} | EMA Guard: 🟢 Safe\n{div}\n"
            f"🏦 <b>LIVE PORTFOLIO</b>\n"
            f"💵 Cash: {thb:,.2f} THB\n"
            f"💠 XRP: {coin:.4f} ({coin_val:,.2f} THB)\n"
            f"💎 Equity: <b>{total:,.2f} THB</b>\n"
            f"🚀 Growth: {growth:+.2f}%\n{div}\n"
            f"🛡️ SL: {self.dynamic_sl:,.2f} | TP: {self.avg_price*1.015:,.2f}"
        )
        self.notify(msg)

    def run(self):
        self.notify("<b>🚀 TITAN MASTER V.10 ACTIVE</b>\nระบบเริ่มทำงานด้วยวินัยระดับสถาบัน")
        last_rep = 0
        while True:
            try:
                d = self.update_indicators()
                if not d: time.sleep(20); continue
                p, ema, rsi, atr = d['price'], d['ema'], d['rsi'], d['atr']
                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0
                thb, coin = self.get_balance()

                # --- 🟢 Professional Entry ---
                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 900:
                    dist_ema = ((p - ema) / ema) * 100
                    if rsi < self.rsi_buy_max and dist_ema < self.ema_dist_limit:
                        if self.place_order("buy", thb * 0.98):
                            self.avg_price, self.total_units = p, (thb * 0.975) / p
                            self.last_action, self.highest_price = "buy", p
                            self.dynamic_sl = p * (1 - (self.stop_loss_pct/100)); self._save_state()
                            self.notify(f"<b>🚀 ENTRY: {p:,.2f}</b>\nจังหวะพักตัว RSI: {rsi:.1f}")

                # --- 🔴 Professional Exit ---
                elif self.last_action == "buy" and coin > 0:
                    self.highest_price = max(self.highest_price, p)
                    # Trailing & Breakeven Protection
                    if pnl >= 0.5: self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.0025) # บังทุน
                    self.dynamic_sl = max(self.dynamic_sl, self.highest_price - (atr * 1.0))
                    
                    reason = None
                    if pnl >= self.tp_target: reason = "Take Profit 💰"
                    elif pnl <= -self.stop_loss_pct: reason = "Stop Loss 🔴"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop 🛡️"

                    if reason:
                        if self.place_order("sell", coin):
                            self.notify(f"<b>💰 EXIT: {p:,.2f}</b>\nP/L: {pnl:+.2f}%\nReason: {reason}")
                            self.last_action, self.avg_price = "sell", 0; self.last_sell_time = time.time(); self._save_state()

                if time.time() - last_rep >= 600:
                    self._report(p, pnl, thb, coin, rsi)
                    last_rep = time.time()
            except: pass
            time.sleep(30)

    # --- API Helper (Bitkub) ---
    def _request(self, method, path, payload=None, private=False):
        url = f"https://api.bitkub.com{path}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if private:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(self.api_secret.encode('utf-8'), (ts+method+path+(json.dumps(payload) if payload else "")).encode('utf-8'), hashlib.sha256).hexdigest()
            headers.update({'X-BTK-APIKEY': self.api_key, 'X-BTK-TIMESTAMP': ts, 'X-BTK-SIGN': sig})
        return requests.request(method, url, headers=headers, data=json.dumps(payload) if payload else "").json()

    def get_balance(self):
        res = self._request("POST", "/api/v3/market/wallet", private=True)
        if res.get('error') == 0: return float(res['result'].get('THB', 0)), float(res['result'].get('XRP', 0))
        return 0.0, 0.0

    def place_order(self, side, amt):
        path = "/api/v3/market/place-bid" if side == "buy" else "/api/v3/market/place-ask"
        res = self._request("POST", path, payload={"sym": self.symbol.lower(), "amt": amt, "rat": 0, "typ": "market"}, private=True)
        return res.get('error') == 0

    def calculate_ema(self, p, n):
        a = 2/(n+1); e = p[0]
        for x in p[1:]: e = (x * a) + (e * (1 - a))
        return e

    def notify(self, m):
        try: requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", json={"chat_id": self.tg_chat_id, "text": m, "parse_mode": "HTML"})
        except: pass

    def _save_state(self):
        with open(self.state_file, "w") as f: json.dump({"last_action": self.last_action, "avg_price": self.avg_price}, f)

    def _load_state(self):
        if os.path.exists(self.state_file):
            d = json.load(open(self.state_file)); self.last_action = d['last_action']; self.avg_price = d['avg_price']

if __name__ == "__main__":
    TitanMasterV10().run()
