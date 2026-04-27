    # --- 4. PROFESSIONAL REPORTING (RE-DESIGNED V.18) ---
    def send_dashboard(self, data, thb, coin):
        p, rsi, ema = data['p'], data['rsi'], data['ema']
        equity = thb + (coin * p)
        growth = ((equity - self.initial_equity) / self.initial_equity) * 100
        trend = "🌕 BULLISH" if p > ema else "🌑 BEARISH"
        
        # เพิ่มระบบแจ้งสถานะ RSI ให้ดูง่ายเหมือน V.15
        rsi_emoji = "🔥" if rsi >= 70 else "❄️" if rsi <= 30 else "📊"
        now = datetime.now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')

        msg = f"🛡️ <b>TITAN PRO-MAX: PORTFOLIO STATUS</b>\n"
        msg += f"📅 <code>{now}</code>\n"
        msg += f"---------------------------------\n"
        msg += f"📈 <b>MARKET:</b> {self.symbol}\n"
        msg += f"🌡️ <b>TREND :</b> {trend}\n"
        msg += f"💰 <b>PRICE :</b> {p:,.2f} THB\n"
        msg += f"{rsi_emoji} <b>RSI   :</b> {rsi:.2f}\n"
        msg += "---------------------------------\n"
        msg += f"💰 <b>ASSET SUMMARY</b>\n"
        msg += f"Net Equity : <b>{equity:,.2f} THB</b>\n"
        msg += f"Total Growth: {growth:+.2f}% (From {self.initial_equity:,.0f})\n"
        msg += f"Available  : {thb:,.2f} THB\n"
        msg += "---------------------------------\n"
        for i, s in self.slots.items():
            if s['active']:
                e_cost = s['price'] * (1 + self.fee_rate)
                x_rev = p * (1 - self.fee_rate)
                pnl = ((x_rev - e_cost) / e_cost) * 100
                msg += f"🟢 SLOT {i}: IN TRADE (PnL: {pnl:+.2f}%)\n"
            else:
                msg += f"⚪ SLOT {i}: WAITING (RSI ≤ {self.rsi_buy_max})\n"
        self.notify(msg)

    # --- 5. TRANSACTION REPORTS (RE-DESIGNED V.18) ---
    def execute_trade(self, side, slot_id, price, amt_units, atr):
        # ... (ส่วนการส่ง API เหมือนเดิม) ...
        # แก้ไขเฉพาะส่วน self.notify ด้านล่างนี้ครับ:
        
        now = datetime.now().strftime('%d/%m/%Y | ⏰ %H:%M:%S')
        if side == 'buy':
            # รายงานการซื้อที่ดู Pro ขึ้น
            sl = price - (atr * 2.5)
            msg = f"📥 <b>BUY ORDER COMPLETED</b>\n"
            msg += f"📅 <code>{now}</code>\n"
            msg += f"---------------------------------\n"
            msg += f"📍 <b>Slot:</b> {slot_id} | <b>Asset:</b> {self.symbol}\n"
            msg += f"💵 <b>Price:</b> {price:,.2f} THB\n"
            msg += f"📦 <b>Units:</b> {amt_units/price:,.4f}\n"
            msg += f"🛡️ <b>Stop Loss:</b> {sl:,.2f}\n"
            msg += f"---------------------------------\n"
            msg += f"<i>Status: บันทึกลงระบบ Database v18 เรียบร้อย</i>"
            self.notify(msg)
        else:
            # รายงานการขายที่แจกแจงละเอียด (ป้องกันขายหมู/ขาดทุนค่าธรรมเนียม)
            s = self.slots[slot_id]
            gross_pnl = ((price - s['price']) / s['price']) * 100
            fee_thb = (price * amt_units * self.fee_rate) + (s['price'] * amt_units * self.fee_rate)
            net_pnl_thb = (price * amt_units * (1-self.fee_rate)) - (s['price'] * amt_units * (1+self.fee_rate))
            
            status_icon = "✅ PROFIT" if net_pnl_thb > 0 else "❌ LOSS"
            msg = f"⚡ <b>TRADE COMPLETED ({status_icon})</b>\n"
            msg += f"📅 <code>{now}</code>\n"
            msg += f"---------------------------------\n"
            msg += f"📤 <b>Action:</b> SELL {self.symbol} | Slot: {slot_id}\n"
            msg += f"💵 <b>Price :</b> {price:,.2f} THB\n"
            msg += "---------------------------------\n"
            msg += f"📈 Gross PnL: {gross_pnl:+.2f}%\n"
            msg += f"💸 Total Fee: -{fee_thb:,.2f} THB\n"
            msg += f"💰 <b>NET PROFIT : {net_pnl_thb:,.2f} THB</b>\n"
            msg += "---------------------------------\n"
            msg += f"<i>Status: คืนเงินเข้า Wallet พร้อมเทรดรอบใหม่</i>"
            self.notify(msg)
