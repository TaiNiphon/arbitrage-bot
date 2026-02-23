import json
import hmac
import hashlib
import time
import requests
import logging

# --- แก้ไขฟังก์ชันการสร้าง Signature ให้รองรับมาตรฐาน V3 ---
def generate_signature(api_secret, timestamp, method, path, body_str=''):
    """
    สร้างลายเซ็นดิจิทัลตามมาตรฐาน Bitkub V3
    message = timestamp + method + path + body
    """
    message = f"{timestamp}{method}{path}{body_str}"
    return hmac.new(
        api_secret.encode('utf-8'), 
        message.encode('utf-8'), 
        hashlib.sha256
    ).hexdigest()

def get_wallet():
    """ดึงยอดเงินคงเหลือ (รองรับ API V3)"""
    path = "/api/v3/market/wallet"
    method = "POST"
    # Bitkub V3 แนะนำให้ใช้ Timestamp ในหน่วยมิลลิวินาที
    timestamp = int(time.time() * 1000)
    
    # สำคัญมาก: สำหรับ Wallet ต้องส่ง Body เป็น dict เปล่า 
    # และแปลงเป็น String ที่ไม่มีช่องว่าง (separators) เพื่อทำ Signature
    body_dict = {}
    body_json = json.dumps(body_dict, separators=(',', ':'))
    
    sig = generate_signature(API_SECRET, timestamp, method, path, body_json)
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-BTK-APIKEY': API_KEY,
        'X-BTK-TIMESTAMP': str(timestamp),
        'X-BTK-SIGN': sig
    }
    
    try:
        # ส่ง request โดยแนบ body_dict เปล่าไปด้วย
        res = requests.post(f"{API_HOST}{path}", headers=headers, json=body_dict, timeout=15)
        data = res.json()
        
        if data.get('error') == 0:
            # API V3 คืนค่าเป็น list ของเหรียญ
            result = data.get('result', [])
            # สร้าง dict เพื่อให้ดึงข้อมูลง่ายขึ้น เช่น wallet['THB']
            return {item['symbol']: item['available'] for item in result}
        else:
            logging.error(f"Bitkub Wallet API Error: {data}")
            return {}
    except Exception as e:
        logging.error(f"Wallet Connection Failed: {e}")
        return {}
