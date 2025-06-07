import os
import requests
from datetime import datetime
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# LINE Bot 設定
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 台灣時區
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

# 定時推送設定
SCHEDULED_MESSAGES = [
    {"time": "07:10", "message": "morning_briefing", "days": "daily"},
    {"time": "08:00", "message": "commute_to_work", "days": "weekdays"},
    {"time": "09:30", "message": "market_open", "days": "weekdays"},
    {"time": "12:00", "message": "market_mid", "days": "weekdays"},
    {"time": "13:45", "message": "market_close", "days": "weekdays"},
    {"time": "17:30", "message": "evening_zhongzheng", "days": "135"},
    {"time": "17:30", "message": "evening_xindian", "days": "24"}
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
        api_key = os.environ.get('OPENWEATHERMAP_API_KEY')
        url = f'http://api.openweathermap.org/data/2.5/weather?q={location}&appid={api_key}&units=metric&lang=zh_tw'
        response = requests.get(url)
        data = response.json()

        if response.status_code == 200:
            current_temp = data['main']['temp']
            weather_description = data['weather'][0]['description']
            humidity = data['main']['humidity']
            wind_speed = data['wind']['speed']
            current_time = datetime.now(TAIWAN_TZ).strftime('%m/%d %H:%M')

            weather_data = f"☀️ {location}天氣 ({current_time}):\n\n🌡️ 溫度: {current_temp}°C\n🌫️ {weather_description}\n💨 風速: {wind_speed} m/s\n💧 湿度: {humidity}%"
            return weather_data
        else:
            return "❌ 查詢天氣時出錯，請稍後再試"
    except Exception as e:
        return f"❌ 無法取得天氣資訊: {str(e)}"

def get_stock_price(symbol):
    """取得股票價格"""
    try:
        api_key = os.environ.get('ALPHA_VANTAGE_API_KEY')
        url = f'https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol={symbol}&interval=1min&apikey={api_key}'
        response = requests.get(url)
        data = response.json()

        if 'Time Series (1min)' in data:
            latest_time = list(data['Time Series (1min)'].keys())[0]
            stock_data = data['Time Series (1min)'][latest_time]
            price = stock_data['1. open']
            return f"📈 {symbol} 股票當前價格: ${price}"
        else:
            return "❌ 無法取得股市資料，請稍後再試"
    except Exception as e:
        return f"❌ 股票資訊查詢錯誤: {str(e)}"

def get_traffic(start, end):
    """取得交通資訊 (假設使用 Google Maps API)"""
    try:
        api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
        url = f'https://maps.googleapis.com/maps/api/directions/json?origin={start}&destination={end}&key={api_key}'
        response = requests.get(url)
        data = response.json()

        if data['status'] == 'OK':
            duration = data['routes'][0]['legs'][0]['duration']['text']
            traffic_info = f"🚗 由 {start} 到 {end} 的交通時間: {duration}"
            return traffic_info
        else:
            return "❌ 查詢交通狀況失敗，請稍後再試"
    except Exception as e:
        return f"❌ 交通資訊查詢錯誤: {str(e)}"

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
                elif schedule['days'] == '24' and current_weekday in [1, 3]:  # 二四
                    should_send = True
                
                if should_send:
                    message_type = schedule['message']
                    
                    # 根據訊息類型產生內容
                    if message_type == "morning_briefing":
                        message = get_weather("新店")
                    elif message_type == "commute_to_work":
                        message = get_traffic(ADDRESSES["home"], ADDRESSES["office"])
                    elif message_type == "market_open":
                        message = get_stock_price("2330.TW")  # 假設你關注的是台積電
                    elif message_type == "market_mid":
                        message = get_stock_price("2330.TW")
                    elif message_type == "market_close":
                        message = get_stock_price("2330.TW")
                    elif message_type == "evening_zhongzheng":
                        message = get_weather("中正區")
                    elif message_type == "evening_xindian":
                        message = get_weather("新店")
                    else:
                        continue
                    
                    # 發送定時訊息
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
        # 例：查詢天氣
        if user_message == "新店天氣":
            reply = get_weather("新店")
        elif user_message == "台積電股價":
            reply = get_stock_price("2330.TW")
        elif user_message == "車流":
            reply = get_traffic(ADDRESSES["home"], ADDRESSES["office"])
        
        # 其它功能
        elif user_message == "測試":
            reply = "🤖 系統測試成功
