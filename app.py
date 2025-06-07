import os
import requests
from datetime import datetime
import pytz
import yfinance as yf
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from google.oauth2 import service_account
from googleapiclient.discovery import build
import datetime as dt

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
    """取得指定地區天氣（中央氣象局API）"""
    api_key = os.environ.get('WEATHER_API_KEY', '')
    if not api_key:
        return f"❌ {location}天氣\n\n天氣API金鑰未設定\n\n請設定環境變數 WEATHER_API_KEY"
    url = f"https://opendata.cwb.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={api_key}&locationName={location}"
    try:
        res = requests.get(url)
        data = res.json()
        weather = data.get('records', {}).get('location', [])
        if not weather:
            return f"❌ {location}天氣\n\n無法取得資料"
        wx = weather[0].get('weatherElement', [])
        if not wx:
            return f"❌ {location}天氣\n\n資料格式錯誤"
        # 降雨機率、溫度、天氣描述
        pop = wx[0]['time'][0]['parameter']['parameterName']
        temp = wx[4]['time'][0]['parameter']['parameterName']
        desc = wx[3]['time'][0]['parameter']['parameterName']
        return f"☀️ {location}天氣\n\n🌡️ 溫度: {temp}°C\n💧 降雨機率: {pop}%\n☁️ 天氣: {desc}\n\n資料來源: 中央氣象局"
    except Exception as e:
        print(f"天氣API錯誤: {str(e)}")
        return f"❌ {location}天氣\n\n取得資料失敗 ({str(e)})"

def get_us_stocks():
    """取得美股資訊（yfinance）"""
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
            result += f"{stock}: 收盤價 ${close_price:.2f}\n"
        except Exception as e:
            result += f"{stock}: 取得資料失敗 ({str(e)})\n"
    return result

def get_taiwan_market():
    """取得台股大盤與重要個股資訊（yfinance）"""
    # 取得大盤指數
    try:
        twii = yf.Ticker("^TWII")
        hist = twii.history(period="1d")
        if hist.empty:
            twii_price = "無法取得"
        else:
            twii_price = int(hist['Close'].iloc[-1])
    except Exception as e:
        twii_price = f"錯誤: {str(e)}"

    # 取得重要個股
    stocks = [
        ("台積電", "2330.TW"),
        ("鴻海", "2317.TW"),
        ("聯發科", "2454.TW")
    ]
    result = f"📈 台股大盤\n加權指數: {twii_price}\n\n"
    for name, code in stocks:
        try:
            ticker = yf.Ticker(code)
            hist = ticker.history(period="1d")
            if hist.empty:
                result += f"{name}: 無資料\n"
                continue
            close_price = hist['Close'].iloc[-1]
            result += f"{name}: {close_price:.2f}\n"
        except Exception as e:
            result += f"{name}: 取得資料失敗 ({str(e)})\n"
    return result

def get_taiwan_stocks():
    """台股資訊（相容舊函數）"""
    return get_taiwan_market()

def get_news():
    """取得新聞資訊（範例，可自行串接新聞API）"""
    return "📰 國內外新聞\n\n1. 台股創新高\n2. 美國科技股表現強勁\n\n(新聞API串接開發中...)"

def get_traffic(from_place="home", to_place="office"):
    """取得車流資訊（Google Maps API，需金鑰）"""
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        return "🚗 車流資訊\n\n(Google Maps API金鑰未設定)"
    from_addr = ADDRESSES.get(from_place, from_place)
    to_addr = ADDRESSES.get(to_place, to_place)
    try:
        url = f"https://maps.googleapis.com/maps/api/directions/json?origin={from_addr}&destination={to_addr}&key={api_key}"
        res = requests.get(url)
        data = res.json()
        if data.get('status') != 'OK':
            return f"🚗 車流資訊\n\n({from_place} → {to_place})\n\n無法取得路線"
        route = data['routes'][0]['legs'][0]
        duration = route['duration']['text']
        distance = route['distance']['text']
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n預計時間: {duration}\n距離: {distance}\n\n資料來源: Google Maps"
    except Exception as e:
        print(f"車流API錯誤: {str(e)}")
        return f"🚗 車流資訊\n\n取得資料失敗 ({str(e)})"

def get_google_calendar_events():
    SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
    try:
        creds = service_account.Credentials.from_service_account_file(
            'credentials.json', scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        now = dt.datetime.utcnow().isoformat() + 'Z'  # 'Z' 代表 UTC 時間
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return '今日無行程'
        result = '📅 今日行程\n\n'
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            result += f"• {start} {event['summary']}\n"
        return result
    except Exception as e:
        print(f"Google Calendar API錯誤: {str(e)}")
        return f"行事曆資料取得失敗 ({str(e)})"

def get_calendar():
    return get_google_calendar_events()

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
        print(f"定時推送錯誤: {str(e)}")
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
