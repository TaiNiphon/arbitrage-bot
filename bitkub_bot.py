import os
import requests
import time
import hmac
import hashlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- 1. Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- 2. Dummy Server for Railway ---
def run_dummy_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Trading Bot is Active")
        def log_message(self, format, *args): return
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 3. Configuration ---
API_KEY = os.getenv("BITKUB_KEY")
API_SECRET = os.getenv("BITKUB_SECRET")
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
# ยึด XRP_THB จาก Railway เป็นหลัก
SYMBOL = os.getenv("SYMBOL", "XRP_THB").upper()

PROFIT_TARGET = float(os.getenv("PROFIT_TARGET", 0.0255))  
STOP_LOSS = float(os.getenv("STOP_LOSS", 0.020))      
EMA_PERIOD = 50        
TIMEFRAME = "15"       

API_HOST = "https://api.bitkub.com"

# --- 4. Helper Functions ---
def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": str(text)}]}
    try: requests.post(url, headers=headers, json=payload, timeout=10)
    except: pass

def bitkub_v3_auth(method, path, body={}):
    try:
        ts_res = requests.get(f"{API_HOST}/api/v3/servertime")
        ts = ts_res.text.strip()
        json_body = json.dumps(body, separators=(',', ':'))
        payload = ts + method + path + json_body
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            'Accept': 'application/json', 
            'Content-Type': '
