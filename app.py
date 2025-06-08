import os
import requests
from datetime import datetime
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build
import json
import datetime as dt
from fugle_marketdata import RestClient

app = Flask(__name__)

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
LINE_USER_ID = os.environ.get('LINE_USER_ID')  # 你的 LINE User ID

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, LINE_USER_ID]):
    raise ValueError("LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, LINE_USER_ID 必須設定")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 台灣時區
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

# 定時推送設定
SCHEDULED_MESSAGES = [
    {"time": "07:10", "message": "morning_briefing", "days": "daily"},
    {"time": "08:00", "message": "commute_to_work", "days": "weekdays"},
    {"time": "09:30", "message": "market_open", "days": "weekdays"},
    {"time": "12:00", "message": "market_mid", "days": "weekdays"},
    {"time": "13:45", "message": "market_close", "days": "weekdays"},
    {"time": "17:30", "message": "evening_zhongzheng", "days": "135"},  # 135 代表週一三五
    {"time": "17:30", "message": "evening_xindian", "days": "24"}       # 24 代表週二四
]

# 固定地址
ADDRESSES = {
    "home": "新店區建國路99巷",
    "office": "台北市南京東路三段131號",
    "post_office": "台北市愛國東路216號"
}

# 台股中文名稱 ↔ 股票代碼對照表
stock_name_map = {
    "台積電": "2330",
    "聯電": "2303",
    "陽明": "2609",
    "華航": "2610",
    "長榮航": "2618",
    "00918": "00918",
    "00878": "00878",
    "鴻準": "2354",
    "大盤": "TAIEX"  # 富果 API 大盤指數代碼為 "TAIEX"
}

# 美股中文名稱 ↔ 股票代碼對照表
us_stock_name_map = {
    "輝達": "NVDA",
    "美超微": "SMCI",
    "google": "GOOGL",
    "蘋果": "AAPL"
}

# ==================== 核心功能函數 ====================

def get_weather(location):
    api_key = os.environ.get('WEATHER_API_KEY', '')
    if not api_key:
        return f"❌ {location}天氣\n\n天氣API金鑰未設定\n\n請設定環境變數 WEATHER_API_KEY"
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={api_key}&locationName={location}"
    try:
        res = requests.get(url)
        data = res.json()
        weather = data.get('records', {}).get('location', [])
        if not weather:
            return f"❌ {location}天氣\n\n無法取得資料"
        wx = weather[0].get('weatherElement', [])
        if not wx:
            return f"❌ {location}天氣\n\n資料格式錯誤"
        pop = wx[0]['time'][0]['parameter']['parameterName']
        temp = wx[4]['time'][0]['parameter']['parameterName']
        desc = wx[3]['time'][0]['parameter']['parameterName']
        return f"☀️ {location}天氣\n\n🌡️ 溫度: {temp}°C\n💧 降雨機率: {pop}%\n☁️ 天氣: {desc}\n\n資料來源: 中央氣象署"
    except Exception as e:
        print(f"天氣API錯誤: {str(e)}")
        return f"❌ {location}天氣\n\n取得資料失敗 ({str(e)})"

def get_taiwan_stock_info(code):
    api_key = os.environ.get('FUGLE_API_KEY', '')
    if not api_key:
        return "❌ 富果API金鑰未設定，請設定環境變數 FUGLE_API_KEY"
    try:
        client = RestClient(api_key=api_key)
        quote = client.stock.intraday.quote(symbol_id=code)
        if not quote or 'data' not in quote or not quote['data']:
            return f"{code}: 查無即時行情資料"
        info = quote['data']
        name = info.get('name', code)
        price = info.get('last', 'N/A')
        open_price = info.get('open', 'N/A')
        high = info.get('high', 'N/A')
        low = info.get('low', 'N/A')
        volume = info.get('volume', 'N/A')
        time = info.get('at', 'N/A')
        return (
            f"📈 {name}（{code}）即時行情\n"
            f"時間：{time}\n"
            f"成交價：{price}\n"
            f"開盤：{open_price}\n"
            f"最高：{high}\n"
            f"最低：{low}\n"
            f"成交量：{volume}"
        )
    except Exception as e:
        print(f"台股API錯誤: {str(e)}")
        return f"{code}: 取得行情失敗 ({str(e)})"

def get_us_stock_info(symbol):
    api_key = os.environ.get('ALPHA_VANTAGE_API_KEY', '')
    if not api_key:
        return "Alpha Vantage API金鑰未設定"
    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={api_key}"
        res = requests.get(url)
        data = res.json()
        if 'Global Quote' not in data or not data['Global Quote']:
            return f"{symbol}: 無法取得即時行情"
        latest = data['Global Quote']
        price = latest.get('05. price', 'N/A')
        change = latest.get('10. change percent', 'N/A')
        return f"📈 美股\n\n{symbol}: ${price}\n漲跌幅: {change}"
    except Exception as e:
        print(f"美股API錯誤: {str(e)}")
        return f"{symbol}: 取得資料失敗 ({str(e)})"

def get_news():
    return "📰 國內外新聞\n\n1. 台股創新高\n2. 美國科技股表現強勁\n\n(新聞API串接開發中...)"

def get_traffic(from_place="home", to_place="office"):
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
        creds_json = os.environ.get('GOOGLE_CREDS_JSON')
        if not creds_json:
            return "Google Calendar API金鑰未設定，請設定環境變數 GOOGLE_CREDS_JSON"
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        now = dt.datetime.utcnow().isoformat() + 'Z'
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
    weather = get_weather("新店")
    us_stocks = get_us_stock_info("NVDA")
    calendar = get_calendar()
    return f"🌞 早安！\n\n{weather}\n\n{us_stocks}\n\n{calendar}"

def get_commute_to_work():
    weather = get_weather("中山區")
    traffic = get_traffic("home", "office")
    return f"🚗 上班通勤資訊\n\n{weather}\n\n{traffic}"

def get_market_open():
    stocks = get_taiwan_stock_info("TAIEX")
    news = get_news()
    return f"📈 台股開盤\n\n{stocks}\n\n{news}"

def get_market_mid():
    return get_taiwan_stock_info("TAIEX")

def get_market_close():
    return get_taiwan_stock_info("TAIEX")

def get_evening_zhongzheng():
    weather = get_weather("中正區")
    traffic = get_traffic("office", "post_office")
    return f"🌆 下班資訊（中正區）\n\n{weather}\n\n{traffic}"

def get_evening_xindian():
    weather = get_weather("新店")
    traffic = get_traffic("office", "home")
    return f"🌆 下班資訊（新店）\n\n{weather}\n\n{traffic}"

@app.route("/send_scheduled", methods=['POST'])
def send_scheduled():
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        current_weekday = taiwan_time.weekday()
        print(f"[定時推播] 當前時間: {current_time}, 星期: {current_weekday}")  # 增加 log

        for schedule in SCHEDULED_MESSAGES:
            if schedule['time'] == current_time:
                should_send = False
                if schedule['days'] == 'daily':
                    should_send = True
                elif schedule['days'] == 'weekdays' and current_weekday < 5:
                    should_send = True
                elif schedule['days'] == '135' and current_weekday in [0, 2, 4]:
                    should_send = True
                elif schedule['days'] == '24' and current_weekday in [1, 3]:
                    should_send = True

                if should_send:
                    message_type = schedule['message']
                    print(f"[定時推播] 觸發: {message_type}")  # 增加 log
                    if message_type == "morning_briefing":
                        message = get_morning_briefing()
                    elif message_type == "commute_to_work":
                        message = get_commute_to_work()
                    elif message_type == "market_open":
                        message = get_market_open()
                    elif message_type == "market_mid":
                        message = get_market_mid()
                    elif message_type == "market_close":
                        message = get_market_close()
                    elif message_type == "evening_zhongzheng":
                        message = get_evening_zhongzheng()
                    elif message_type == "evening_xindian":
                        message = get_evening_xindian()
                    else:
                        continue

                    if not message or message.strip() == "":
                        message = "⚠️ 查無資料，請確認關鍵字或稍後再試。"
                    try:
                        print(f"[定時推播] 準備發送: {message_type}")  # 增加 log
                        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
                        print(f"[定時推播] 發送成功: {message_type}")  # 增加 log
                    except Exception as e:
                        print(f"[定時推播] 發送失敗: {str(e)}")  # 增加 log

        return 'OK'
    except Exception as e:
        print(f"[定時推播] 錯誤: {str(e)}")  # 增加 log
        return f"❌ 錯誤: {str(e)}"

@app.route("/")
def index():
    return "OK"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        print("[Webhook] 收到訊息")  # 增加 log
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text.strip()
    reply = ""
    try:
        print(f"[Webhook] 收到用戶訊息: {user_message}")  # 增加 log
        if user_message == "morning_briefing":
            reply = get_morning_briefing()
        elif user_message == "commute_to_work":
            reply = get_commute_to_work()
        elif user_message == "market_open":
            reply = get_market_open()
        elif user_message == "market_mid":
            reply = get_market_mid()
        elif user_message == "market_close":
            reply = get_market_close()
        elif user_message == "evening_zhongzheng":
            reply = get_evening_zhongzheng()
        elif user_message == "evening_xindian":
            reply = get_evening_xindian()
        elif user_message.startswith("台股 "):
            name = user_message.split(" ")[1].strip()
            code = stock_name_map.get(name, name)
            reply = get_taiwan_stock_info(code)
        elif user_message.startswith("美股 "):
            name = user_message.split(" ")[1].strip().lower()
            symbol = us_stock_name_map.get(name, name.upper())
            reply = get_us_stock_info(symbol)
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
    except Exception as e:
        reply = "❌ 錯誤: " + str(e)
        print(f"[Webhook] 處理錯誤: {str(e)}")  # 增加 log

    if not reply or reply.strip() == "":
        reply = "⚠️ 查無資料，請確認關鍵字或稍後再試。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
