import os, requests, time, hmac, hashlib, json, csv, math
import numpy as np
from datetime import datetime, timedelta, timezone

class TitanMasterV10:
    def __init__(self):
        print("🛠️ Initializing TITAN MASTER V.10 (Optimized)...")
        self.api_key = os.getenv("BITKUB_KEY")
        self.api_secret = os.getenv("BITKUB_SECRET")
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.symbol = os.getenv("SYMBOL", "XRP_THB").upper()

        # --- อ่านค่าจาก Variables หน้า Railway ---
        self.initial_equity = float(str(os.getenv("INITIAL_EQUITY", "4726")).replace(',', ''))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "2.0")) # ตามที่พี่แจ้งล่าสุด
        self.rsi_buy_max = float(os.getenv("RSI_BUY_MAX", "35.0"))   # ตามที่พี่แจ้งล่าสุด

        self.tp_target = 10.0         
        self.ema_dist_limit = 0.5    # ปรับให้ยืดหยุ่นขึ้นเล็กน้อย

        self.state_file = "titan_v10_state.json"
        self.log_file = "trade_history.csv"
        self.last_action = "sell"; self.avg_price = 0.0; self.total_units = 0.0
        self.highest_price = 0.0; self.dynamic_sl = 0.0; self.last_sell_time = 0
        self._load_state()
        print(f"✅ Setup Complete. Symbol: {self.symbol}")

    def update_indicators(self):
        try:
            res = requests.get(f"https://api.bitkub.com/tradingview/history?symbol={self.symbol}&resolution=15&from={int(time.time())-86400}&to={int(time.time())}").json()
            c = np.array(res['c'], dtype=float)
            ema = self.calculate_ema(c, 20)
            diff = np.diff(c)
            rsi = 100 - (100 / (1 + (np.mean(diff.clip(min=0)[-14:]) / (np.mean(-diff.clip(max=0)[-14:]) + 1e-9))))
            atr = np.mean(np.maximum(np.array(res['h'], dtype=float)[1:] - np.array(res['l'], dtype=float)[1:], abs(np.array(res['h'], dtype=float)[1:] - c[:-1])))
            return {"price": c[-1], "ema": ema, "rsi": rsi, "atr": atr}
        except Exception as e:
            print(f"⚠️ Indicator Error: {e}")
            return None

    def _report(self, price, pnl, thb, coin, rsi, status="MASTER_ACTIVE"):
        coin_val = coin * price; total = thb + coin_val
        growth = ((total - self.initial_equity) / self.initial_equity) * 100 if self.initial_equity > 0 else 0
        diff_thb = total - self.initial_equity

        # คำนวณจุดคุ้มทุน (BE) และระยะ SL
        be_price = self.avg_price * 1.0051 if self.avg_price > 0 else 0
        sl_dist = ((price - self.dynamic_sl) / self.dynamic_sl * 100) if self.dynamic_sl > 0 else 0

        div = "━━━━━━━━━━━━━━━"
        guard_status = "🟢 Safe" if rsi < self.rsi_buy_max else "🔴 Wait"
        growth_text = f"{growth:+.2f}% (<b>{diff_thb:,.2f} THB</b>)"

        msg = (
            f"<b>🏆 TITAN MASTER V.10 ({self.symbol})</b>\n"
            f"🕒 Status: {status}\n{div}\n"
            f"💰 Price: <b>{price:,.2f}</b> | P/L: <b>{pnl:+.2f}%</b>\n"
            f"📊 RSI: {rsi:.1f} | EMA Guard: {guard_status}\n"
            f"🛡️ Config: RSI &lt; {self.rsi_buy_max} | SL: {self.stop_loss_pct}%\n{div}\n"
            f"🏦 <b>LIVE PORTFOLIO</b>\n"
            f"💵 Cash: {thb:,.2f} THB\n"
            f"💠 {self.symbol.split('_')[0]}: {coin:.4f} ({coin_val:,.2f} THB)\n"
            f"💎 Equity: <b>{total:,.2f} THB</b>\n"
            f"🚀 Growth: {growth_text}\n{div}\n"
        )
        if self.last_action == "buy" and coin > 0:
            msg += f"🎯 BE Price: {be_price:,.2f}\n🛡️ SL: {self.dynamic_sl:,.2f} (<b>{sl_dist:+.2f}%</b>)\n💰 TP: {self.avg_price*1.015:,.2f}"
        else:
            msg += f"💤 Status: <b>Waiting for Entry...</b>"
        self.notify(msg)

    def run(self):
        last_rep = 0
        while True:
            try:
                d = self.update_indicators()
                if not d: time.sleep(20); continue
                p, ema, rsi, atr = d['price'], d['ema'], d['rsi'], d['atr']
                pnl = (((p * 0.9975) - (self.avg_price * 1.0025)) / (self.avg_price * 1.0025) * 100) if self.avg_price > 0 else 0
                thb, coin = self.get_balance()

                if self.last_action == "sell" and (time.time() - self.last_sell_time) > 900:
                    dist_ema = ((p - ema) / ema) * 100
                    if rsi < self.rsi_buy_max and dist_ema < self.ema_dist_limit:
                        if self.place_order("buy", thb * 0.98):
                            self.avg_price = p; self.total_units = (thb * 0.975) / p
                            self.last_action = "buy"; self.highest_price = p
                            self.dynamic_sl = p * (1 - (self.stop_loss_pct/100)); self._save_state()
                            self.notify(f"<b>🚀 ENTRY: {p:,.2f}</b>\nRSI: {rsi:.1f}")

                elif self.last_action == "buy" and coin > 0:
                    self.highest_price = max(self.highest_price, p)
                    if pnl >= 1.0: # Lock Profit (ตามที่พี่แจ้ง)
                        self.dynamic_sl = max(self.dynamic_sl, self.avg_price * 1.0025) 
                    
                    # ปรับ ATR เป็น 2.5 เพื่อให้ทนแกว่งได้มากขึ้น
                    self.dynamic_sl = max(self.dynamic_sl, self.highest_price - (atr * 2.5))

                    reason = None
                    if pnl >= self.tp_target: reason = "Take Profit 💰"
                    elif pnl <= -self.stop_loss_pct: reason = "Stop Loss 🔴"
                    elif p <= self.dynamic_sl: reason = "Trailing Stop 🛡️"

                    if reason:
                        profit_thb = (coin * p * 0.9975) - (self.total_units * self.avg_price * 1.0025)
                        if self.place_order("sell", coin):
                            self._log_trade("SELL", p, coin*p, pnl, profit_thb, reason)
                            self.notify(f"<b>💰 EXIT: {p:,.2f}</b>\nP/L: {pnl:+.2f}% (<b>{profit_thb:+.2f} THB</b>)\nReason: {reason}")
                            self.last_action = "sell"; self.avg_price = 0; self.last_sell_time = time.time(); self._save_state()

                if time.time() - last_rep >= 600:
                    self._report(p, pnl, thb, coin, rsi)
                    last_rep = time.time()
            except Exception as e: print(f"❌ Error: {e}")
            time.sleep(30)

    def _log_trade(self, side, price, val, pnl_pct, pnl_thb, reason):
        f_exists = os.path.isfile(self.log_file)
        with open(self.log_file, 'a', newline='') as f:
            w = csv.writer(f); 
            if not f_exists: w.writerow(['Time', 'Side', 'Price', 'Value', 'PnL%', 'PnL_THB', 'Reason'])
            now = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M')
            w.writerow([now, side, price, val, f"{pnl_pct:.2f}%", f"{pnl_thb:.2f}", reason])

    def _save_state(self):
        with open(self.state_file, "w") as f: json.dump({"last_action": self.last_action, "avg_price": self.avg_price, "units": self.total_units}, f)

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    d = json.load(f); self.last_action = d['last_action']; self.avg_price = d['avg_price']; self.total_units = d.get('units', 0.0)
            except: pass

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

if __name__ == "__main__":
    TitanMasterV10().run()
