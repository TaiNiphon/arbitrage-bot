import requests
import time
from datetime import datetime, timedelta, timezone

# --- [ ⚙️ ตั้งค่าการใช้งาน ] ---
LINE_TOKEN = "u2vnwJhXwbuCvJr3AmEuc8gSSq3O6nU+WWxCS2UheRhAJKUm4ng70Xs/caUMwNbC6vy9HM5maaWkmjYJul3Xjak/9TbmdHc/hmIoulZTa2YcILhEe7hh/PulVIMololrYBqURtmBrSZCAh3lE5UwtwdB04t89/1O/w1cDnyilFU="
USER_ID = "Ua88ba52b810900b7ba8df4c08b376496"
PROFIT_THRESHOLD = 0.5  # กำไรขั้นต่ำที่แจ้งเตือน (%)
ALERT_COOLDOWN = 300    # พักแจ้งเตือน 5 นาที

# กำหนดคู่เงิน (ใช้ชื่อที่สอดคล้องกับ TradingView API ของ Bitkub)
COINS = {
    'XRP':  {'bk_sym': 'XRP_THB',  'bn_sym': 'XRPUSDT',  'transfer_fee': 0.25}, 
    'XLM':  {'bk_sym': 'XLM_THB',  'bn_sym': 'XLMUSDT',  'transfer_fee': 0.02},
    'LTC':  {'bk_sym': 'LTC_THB',  'bn_sym': 'LTCUSDT',  'transfer_fee': 0.001},
    'DOGE': {'bk_sym': 'DOGE_THB', 'bn_sym': 'DOGEUSDT', 'transfer_fee': 5.0}
}

last_alert_time = {coin: 0 for coin in COINS}

def get_thai_time():
    """แสดงเวลาไทย GMT+7 สำหรับ Server ต่างประเทศ"""
    return datetime.now(timezone.utc) + timedelta(hours=7)

def send_line(text):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_TOKEN}'}
    data = {'to': USER_ID, 'messages': [{'type': 'text', 'text': text}]}
    try:
        requests.post(url, headers=headers, json=data, timeout=5)
    except: pass

def get_bitkub_price_tv(symbol):
    """ดึงราคาที่แม่นยำผ่าน TradingView History API (เหมือนแอป Bitkub)"""
    try:
        now = int(time.time())
        # ดึงข้อมูลย้อนหลัง 2 นาทีเพื่อให้ได้ราคาล่าสุดที่เสถียร
        url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=1&from={now-120}&to={now}"
        res = requests.get(url, timeout=5).json()
        if res.get('s') == 'ok':
            return float(res['c'][-1]) # ราคาปิดล่าสุด
    except: return None

def get_binance_price(symbol):
    """ดึงราคาจาก Binance (ใช้ราคา Ask เพื่อคำนวณต้นทุนซื้อ)"""
    try:
        url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
        res = requests.get(url, timeout=5).json()
        return float(res['askPrice'])
    except: return None

def monitor():
    capital_thb = 10000.0  
    start_time = get_thai_time().strftime('%H:%M:%S')
    print(f"🚀 Bot Started at {start_time} (TH Time)")
    send_line(f"🤖 บอทเฝ้าราคาเริ่มทำงานแล้ว!\nเวลา: {start_time}")

    while True:
        try:
            # ดึงราคา USDT/THB จาก Bitkub เพื่อคำนวณต้นทุน
            usdt_thb = get_bitkub_price_tv('USDT_THB') or 31.08
            
            thai_now = get_thai_time()
            print("\n" + "="*75)
            print(f" ARBITRAGE DASHBOARD | {thai_now.strftime('%d/%m/%Y %H:%M:%S')}")
            print(f" CAPITAL: {capital_thb:,.0f} THB | USDT/THB: {usdt_thb:.2f}")
            print("-" * 75)
            print(f"{'COIN':<5} | {'BINANCE(฿)':<10} | {'BITKUB(฿)':<10} | {'NET PROFIT'}")
            print("-" * 75)

            for coin, info in COINS.items():
                bk_price = get_bitkub_price_tv(info['bk_sym'])
                bn_price_usdt = get_binance_price(info['bn_sym'])
                
                if bk_price and bn_price_usdt:
                    bn_price_thb = bn_price_usdt * usdt_thb
                    
                    # คำนวณกำไรสุทธิ (หัก Fee เทรด 2 ฝั่ง และ Fee โอน)
                    buy_usdt = (capital_thb * 0.9975) / usdt_thb
                    buy_coin = ((buy_usdt - 1.0) * 0.999) / bn_price_usdt
                    sell_thb = ((buy_coin - info['transfer_fee']) * bk_price) * 0.9975
                    
                    profit_baht = sell_thb - capital_thb - 20 # เผื่อ Slippage 20 บาท
                    profit_pct = (profit_baht / capital_thb) * 100
                    
                    status_icon = "❌"
                    if profit_pct > 0: status_icon = "✅"
                    if profit_pct >= PROFIT_THRESHOLD: status_icon = "🔥"
                    
                    print(f"{coin:<5} | {bn_price_thb:>10.2f} | {bk_price:>10.2f} | {status_icon} {profit_pct:>+6.2f}% ({profit_baht:>+7.1f}฿)")

                    # ระบบแจ้งเตือน LINE
                    current_ts = time.time()
                    if profit_pct >= PROFIT_THRESHOLD:
                        if current_ts - last_alert_time[coin] > ALERT_COOLDOWN:
                            msg = (f"💰 เจอช่องกำไร! [{coin}]\n"
                                   f"กำไร: {profit_pct:.2f}% ({profit_baht:.2f} บาท)\n"
                                   f"Bitkub: {bk_price} | Binance: {bn_price_thb:.2f}\n"
                                   f"เวลา: {thai_now.strftime('%H:%M:%S')}")
                            send_line(msg)
                            last_alert_time[coin] = current_ts

            time.sleep(15) # หน่วงเวลา 15 วินาทีต่อรอบ

        except Exception as e:
            print(f"⚠️ Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    monitor()
