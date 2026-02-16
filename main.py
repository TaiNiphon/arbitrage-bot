import requests
import time
import os

# --- [1. ตั้งค่าการแจ้งเตือน LINE ] ---
CHANNEL_ACCESS_TOKEN = 'XdQ6URjJAXTkVc1Hden2J2TtiO9hvRqSrv2yerkSwfaZNCzRVhkSe41oHrk2498tUAZm3uMJthaSRrj1U8ofkpqIjUmrvLAW9EQrNd8Bmsz2tMdaiPTK6uLkUXZwaJbOPx3RFE9UJt0vnnnuDiQTPQdB04t89/1O/w1cDnyilFU='
USER_ID ='U202dfbbd9d73297f3918492a766716e2'
PROFIT_THRESHOLD = 0.5 

COINS = {
    'XRP': {'bitkub': 'XRP_THB', 'binance': 'XRPUSDT', 'transfer_fee': 0.25}, 
    'XLM': {'bitkub': 'XLM_THB', 'binance': 'XLMUSDT', 'transfer_fee': 0.02},
    'LTC': {'bitkub': 'LTC_THB', 'binance': 'LTCUSDT', 'transfer_fee': 0.001},
    'DOGE': {'bitkub': 'DOGE_THB', 'binance': 'DOGEUSDT', 'transfer_fee': 5.0}
}

last_alert_time = {coin: 0 for coin in COINS}
ALERT_COOLDOWN = 300 

def send_line_message(text):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}'
    }
    data = {'to': USER_ID, 'messages': [{'type': 'text', 'text': text}]}
    try:
        requests.post(url, headers=headers, json=data, timeout=5)
    except: pass

def get_bitkub_price(symbol):
    try:
        now = int(time.time())
        url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=1&from={now-120}&to={now}"
        res = requests.get(url, timeout=5).json()
        if res.get('s') == 'ok': return float(res['c'][-1])
    except: return None

def get_binance_price(symbol):
    try:
        url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
        res = requests.get(url, timeout=5).json()
        return float(res['askPrice'])
    except: return None

def monitor():
    capital_thb = 10000.0  
    print("🚀 Bot Started! Monitoring prices...")
    send_line_message("🤖 บอทเฝ้าราคาเริ่มทำงานแล้ว!\nแสดงผล: % นำหน้า บาท")

    while True:
        try:
            usdt_thb = get_bitkub_price('USDT_THB') or 31.05
            
            print("\n" * 2) 
            print(f"=== ARBITRAGE DASHBOARD (CAPITAL: {capital_thb:,.0f} THB) ===")
            print(f"{'COIN':<5} | {'BINANCE':<9} | {'BITKUB':<8} | {'NET PROFIT (% / BAHT)'}")
            print("-" * 75)

            for coin, sym in COINS.items():
                bk_price = get_bitkub_price(sym['bitkub'])
                bn_price_usdt = get_binance_price(sym['binance'])
                
                if bk_price and bn_price_usdt:
                    bn_price_thb = bn_price_usdt * usdt_thb
                    
                    buying_power_usdt = (capital_thb * 0.9975) / usdt_thb
                    coin_amount = ((buying_power_usdt - 1.0) * 0.999) / bn_price_usdt
                    final_thb = ((coin_amount - sym['transfer_fee']) * bk_price) * 0.9975
                    
                    net_profit_baht = final_thb - capital_thb - 20 
                    profit_pct = (net_profit_baht / capital_thb) * 100
                    
                    status_icon = "🔥" if profit_pct > 0 else "  "
                    
                    # --- จุดที่แก้ไข: ปิดปีกกาให้ครบและสลับตำแหน่งตามสั่ง ---
                    profit_display = f"({profit_pct:>+6.2f}%) {net_profit_baht:>+8.2f} THB"
                    
                    print(f"{coin:<5} | {bn_price_thb:>9.2f} | {bk_price:>8.2f} | {status_icon} {profit_display}")

                    current_time = time.time()
                    if profit_pct >= PROFIT_THRESHOLD:
                        if current_time - last_alert_time[coin] > ALERT_COOLDOWN:
                            alert_msg = (
                                f"💰 เจอช่องกำไร! [{coin}]\n"
                                f"กำไรสุทธิ: {profit_pct:.2f}% ({net_profit_baht:.2f} บาท)\n"
                                f"Bitkub: {bk_price} | Binance: {bn_price_thb:.2f}"
                            )
                            send_line_message(alert_msg)
                            last_alert_time[coin] = current_time 

            print("-" * 75)
            print(f"USDT/THB: {usdt_thb:.2f} | Update: {time.strftime('%H:%M:%S')}")
            time.sleep(10) 

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    monitor()
