import os
import requests
from datetime import datetime
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import yfinance as yf
import twstock

app = Flask(__name__)

# LINE Bot 設定
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 台灣時區
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

# 定時推送設定
SCHEDULED_MESSAGES = [
    {"time": "07:10", "message": "morning_briefing", "days": "daily"},      # 新店天氣+美股+行程+節假日
    {"time": "08:00", "message": "commute_to_work", "days": "weekdays"},    # 中山區天氣+家→公司車流
    {"time": "09:30", "message": "market_open", "days": "weekdays"},        # 台股開盤+國內外新聞
    {"time": "12:00", "message": "market_mid", "days": "weekdays"},         # 台股盤中
    {"time": "13:45", "message": "market_close", "days": "weekdays"},       # 台股收盤
    {"time": "17:30", "message": "evening_zhongzheng", "days": "135"},      # 中正區天氣+公司→郵局車流(一三五)
    {"time": "17:30", "message": "evening_xindian", "days": "24"}           # 新店天氣+公司→家車流(二四)
]

# 固定地址
ADDRESSES = {
    "home": "新店區建國路99巷",
    "office": "台北市南京東路三段131號", 
    "post_office": "台北市愛國東路216號"
}

# ==================== 核心功能函數 ====================

def get_weather(location):
    """取得指定地區天氣"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%m/%d %H:%M')
        weather_data = f"☀️ {location}天氣 ({current_time}):\n\n🌡️ 溫度: 25°C\n💨 微風\n☁️ 多雲\n🌧️ 降雨機率: 20%\n\n⚠️ 氣象局API整合開發中..."
        return weather_data
    except Exception as e:
        return f"❌ {location}天氣查詢失敗: {str(e)}"

def get_us_stocks():
    """取得美股資訊"""
    stocks = ["NVDA", "SMCI", "GOOGL", "AAPL", "MSFT"]
    result = "📈 美股資訊\n"
    for stock in stocks:
        try:
            ticker = yf.Ticker(stock)
            hist = ticker.history(period="1d")
            if hist.empty:
                result += f"{stock}: 無資料\n"
                continue
            close_price = hist['Close'].iloc[-1]
            after_hours_price = close_price  # 模擬盤後價
            result += f"{stock}: 收盤價 ${close_price:.2f} (盤後 ${after_hours_price:.2f})\n"
        except Exception as e:
            result += f"{stock}: 取得資料失敗\n"
    return result

def get_taiwan_stocks():
    """取得台股資訊"""
    try:
        index = twstock.Index()
        index_data = index.get('tse')
        if not index_data:
            return "📈 台股資訊\n\n無法取得資料"
        latest = index_data[-1]
        return f"📈 台股資訊\n\n加權指數: {latest.price}\n漲跌幅: {latest.change}%\n時間: {latest.time}"
    except Exception as e:
        return f"📈 台股資訊\n\n取得資料失敗: {str(e)}"

def get_news():
    """取得新聞資訊"""
    return "📰 國內外新聞\n\n1. 台股創新高\n2. 美國科技股表現強勁\n\n(實際API串接開發中...)"

def get_traffic(from_place="home", to_place="office"):
    """取得車流資訊"""
    from_addr = ADDRESSES.get(from_place, from_place)
    to_addr = ADDRESSES.get(to_place, to_place)
    return f"🚗 車流資訊 ({from_place} → {to_place})\n\n{from_addr} → {to_addr}\n\n預計時間: 30分鐘\n\n(實際API串接開發中...)"

def get_calendar():
    """取得行事曆與節日"""
    return "📅 今日行程\n\n• 09:00 會議\n• 14:00 客戶拜訪\n\n🎉 今日節日: 無\n\n(實際API串接開發中...)"

def get_morning_briefing():
    """早安綜合資訊"""
    weather = get_weather("新店")
    us_stocks = get_us_stocks()
    calendar = get_calendar()
    return f"🌞 早安！\n\n{weather}\n\n{us_stocks}\n\n{calendar}"

def get_commute_to_work():
    """上班通勤資訊"""
    weather = get_weather("中山區")
    traffic = get_traffic("home", "office")
    return f"🚗 上班通勤資訊\n\n{weather}\n\n{traffic}"

def get_market_open():
    """台股開盤資訊"""
    stocks = get_taiwan_stocks()
    news = get_news()
    return f"📈 台股開盤\n\n{stocks}\n\n{news}"

def get_evening_zhongzheng():
    """下班資訊（中正區）"""
    weather = get_weather("中正區")
    traffic = get_traffic("office", "post_office")
    return f"🌆 下班資訊（中正區）\n\n{weather}\n\n{traffic}"

def get_evening_xindian():
    """下班資訊（新店）"""
    weather = get_weather("新店")
    traffic = get_traffic("office", "home")
    return f"🌆 下班資訊（新店）\n\n{weather}\n\n{traffic}"

# ==================== 定時推送系統 ====================

@app.route("/send_scheduled", methods=['POST'])
def send_scheduled():
    """處理定時推送請求"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        current_weekday = taiwan_time.weekday()  # 0=Monday, 6=Sunday
        
        for schedule in SCHEDULED_MESSAGES:
            if schedule['time'] == current_time:
                should_send = False
                if schedule['days'] == 'daily':
                    should_send = True
                elif schedule['days'] == 'weekdays' and current_weekday < 5:
                    should_send = True
                elif schedule['days'] == '135' and current_weekday in [0, 2, 4]:  # 一三五
                    should_send = True
                elif schedule['days'] == '24' and current_weekday in [1, 3]:      # 二四
                    should_send = True
                
                if should_send:
                    message_type = schedule['message']
                    if message_type == "morning_briefing":
                        message = get_morning_briefing()
                    elif message_type == "commute_to_work":
                        message = get_commute_to_work()
                    elif message_type == "market_open":
                        message = get_market_open()
                    elif message_type == "market_mid":
                        message = get_taiwan_stocks()
                    elif message_type == "market_close":
                        message = get_taiwan_stocks()
                    elif message_type == "evening_zhongzheng":
                        message = get_evening_zhongzheng()
                    elif message_type == "evening_xindian":
                        message = get_evening_xindian()
                    else:
                        continue
                    
                    try:
                        line_bot_api.push_message(os.environ.get('LINE_USER_ID'), TextSendMessage(text=message))
                    except Exception as e:
                        print(f"發送定時訊息錯誤: {str(e)}")

        return 'OK'
    except Exception as e:
        return f"❌ 錯誤: {str(e)}"

# ==================== LINE Bot 處理 ====================

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text.strip()
    reply = ""
    try:
        if user_message == "morning_briefing":
            reply = get_morning_briefing()
        elif user_message == "commute_to_work":
            reply = get_commute_to_work()
        elif user_message == "market_open":
            reply = get_market_open()
        elif user_message == "market_mid":
            reply = get_taiwan_stocks()
        elif user_message == "market_close":
            reply = get_taiwan_stocks()
        elif user_message == "evening_zhongzheng":
            reply = get_evening_zhongzheng()
        elif user_message == "evening_xindian":
            reply = get_evening_xindian()
        elif user_message == "美股":
            reply = get_us_stocks()
        elif user_message == "台股":
            reply = get_taiwan_stocks()
        elif user_message == "新聞":
            reply = get_news()
        elif user_message == "車流":
            reply = get_traffic()
        elif user_message in ["新店", "中山區", "中正區"]:
            reply = get_weather(user_message)
        elif user_message == "測試":
            reply = "🤖 系統測試 v42\n\n✅ 連線正常\n✅ 推送系統運作中\n✅ 重寫版本\n\n📋 功能列表:\n• 美股、台股 (真實API)\n• 天氣 (新店/中山區/中正區)\n• 車流 (機車路線)\n• 新聞\n\n⏰ 定時推送:\n• 07:10 早安綜合\n• 08:00 上班通勤\n• 09:30 開盤+新聞\n• 12:00 台股盤中\n• 13:45 台股收盤\n• 17:30 下班資訊"
        elif user_message == "幫助":
            reply = "📚 LINE Bot 功能列表:"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 錯誤: " + str(e)))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
