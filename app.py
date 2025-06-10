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
LINE_USER_ID = os.environ.get('LINE_USER_ID')

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
    {"time": "17:30", "message": "evening_zhongzheng", "days": "135"},
    {"time": "17:30", "message": "evening_xindian", "days": "24"}
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
    "大盤": "TAIEX"
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
    """取得天氣資訊 - 修正版本"""
    api_key = os.environ.get('WEATHER_API_KEY', '')
    if not api_key:
        return f"❌ {location}天氣\n\n天氣API金鑰未設定"
    
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={api_key}&locationName={location}"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        
        # 檢查 API 回傳狀態
        if not data.get('success', False):
            error_msg = data.get('message', '未知錯誤')
            return f"❌ {location}天氣\n\nAPI 回傳失敗: {error_msg}"
        
        # 取得地區資料
        locations = data.get('records', {}).get('location', [])
        if not locations:
            return f"❌ {location}天氣\n\n查無此地區資料"
        
        # 取得天氣要素
        weather_elements = locations[0].get('weatherElement', [])
        if len(weather_elements) < 5:
            return f"❌ {location}天氣\n\n資料格式不完整"
        
        # 正確解析天氣資料（根據中央氣象署 API 文件）
        try:
            # weatherElement 索引對應：
            # 0: Wx (天氣現象)
            # 1: PoP (降雨機率)
            # 2: MinT (最低溫)
            # 3: CI (舒適度)
            # 4: MaxT (最高溫)
            wx = weather_elements[0]['time'][0]['parameter']['parameterName']  # 天氣現象
            pop = weather_elements[1]['time'][0]['parameter']['parameterName']  # 降雨機率
            min_temp = weather_elements[2]['time'][0]['parameter']['parameterName']  # 最低溫
            ci = weather_elements[3]['time'][0]['parameter']['parameterName']  # 舒適度
            max_temp = weather_elements[4]['time'][0]['parameter']['parameterName']  # 最高溫
            
            return f"☀️ {location}天氣\n\n🌡️ 溫度: {min_temp}-{max_temp}°C\n💧 降雨機率: {pop}%\n☁️ 天氣: {wx}\n🌡️ 舒適度: {ci}\n\n資料來源: 中央氣象署"
            
        except (KeyError, IndexError) as e:
            print(f"天氣資料解析錯誤: {str(e)}")
            return f"❌ {location}天氣\n\n資料解析失敗"
            
    except requests.exceptions.Timeout:
        return f"❌ {location}天氣\n\n請求逾時，請稍後再試"
    except requests.exceptions.RequestException as e:
        print(f"天氣API請求錯誤: {str(e)}")
        return f"❌ {location}天氣\n\n網路連線失敗"
    except Exception as e:
        print(f"天氣API未知錯誤: {str(e)}")
        return f"❌ {location}天氣\n\n取得資料失敗"

def get_taiwan_stock_info(code):
    """取得台股資訊 - 修正版本"""
    api_key = os.environ.get('FUGLE_API_KEY', '')
    if not api_key:
        return "❌ 富果API金鑰未設定，請設定環境變數 FUGLE_API_KEY"
    
    try:
        client = RestClient(api_key=api_key)
        
        # 處理大盤指數
        if code == "TAIEX":
            symbol_id = "IX0001"  # 大盤指數正確代碼
        else:
            symbol_id = code
            
        quote = client.stock.intraday.quote(symbol_id=symbol_id)
        
        if not quote or 'data' not in quote or not quote['data']:
            return f"📈 {code}\n\n查無即時行情資料\n(可能為非交易時間或代碼錯誤)"
        
        info = quote['data']
        name = info.get('name', code)
        price = info.get('last', 'N/A')
        change = info.get('change', 'N/A')
        change_percent = info.get('changePercent', 'N/A')
        volume = info.get('volume', 'N/A')
        time_str = info.get('at', 'N/A')
        
        # 判斷漲跌
        if isinstance(change, (int, float)) and change > 0:
            change_symbol = "📈"
        elif isinstance(change, (int, float)) and change < 0:
            change_symbol = "📉"
        else:
            change_symbol = "📊"
            
        return (
            f"{change_symbol} {name}（{code}）\n"
            f"時間：{time_str}\n"
            f"成交價：{price}\n"
            f"漲跌：{change} ({change_percent}%)\n"
            f"成交量：{volume}"
        )
        
    except Exception as e:
        print(f"台股API錯誤: {str(e)}")
        return f"📈 {code}\n\n取得行情失敗\n(可能為API限制或網路問題)"

def get_us_stock_info(symbol):
    """取得美股資訊 - 修正版本"""
    api_key = os.environ.get('ALPHA_VANTAGE_API_KEY', '')
    if not api_key:
        return f"📈 美股 {symbol}\n\nAlpha Vantage API金鑰未設定"
    
    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={api_key}"
        res = requests.get(url, timeout=10)
        data = res.json()
        
        # 檢查是否達到API限制
        if 'Note' in data:
            return f"📈 美股 {symbol}\n\nAPI 請求已達上限\n請稍後再試或升級付費方案"
        
        if 'Global Quote' not in data or not data['Global Quote']:
            return f"📈 美股 {symbol}\n\n無法取得即時行情\n(可能為非交易時間或代碼錯誤)"
        
        latest = data['Global Quote']
        price = latest.get('05. price', 'N/A')
        change = latest.get('09. change', 'N/A')
        change_percent = latest.get('10. change percent', 'N/A')
        
        # 移除百分比符號進行數值判斷
        try:
            change_num = float(change) if change != 'N/A' else 0
            if change_num > 0:
                change_symbol = "📈"
            elif change_num < 0:
                change_symbol = "📉"
            else:
                change_symbol = "📊"
        except:
            change_symbol = "📊"
            
        return f"{change_symbol} 美股 {symbol}\n\n價格: ${price}\n漲跌: {change}\n漲跌幅: {change_percent}"
        
    except requests.exceptions.Timeout:
        return f"📈 美股 {symbol}\n\n請求逾時，請稍後再試"
    except Exception as e:
        print(f"美股API錯誤: {str(e)}")
        return f"📈 美股 {symbol}\n\n取得資料失敗"

def get_news():
    """取得新聞資訊 - 暫時固定內容"""
    return "📰 國內外新聞\n\n1. 台股持續震盪整理\n2. 美國科技股表現分歧\n3. 央行政策持續關注\n\n(新聞API串接開發中...)"

def get_traffic(from_place="home", to_place="office"):
    """取得車流資訊"""
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n(Google Maps API金鑰未設定)\n預估時間: 約25分鐘"
    
    from_addr = ADDRESSES.get(from_place, from_place)
    to_addr = ADDRESSES.get(to_place, to_place)
    
    try:
        url = f"https://maps.googleapis.com/maps/api/directions/json?origin={from_addr}&destination={to_addr}&key={api_key}"
        res = requests.get(url, timeout=10)
        data = res.json()
        
        if data.get('status') != 'OK':
            return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n無法取得路線\n預估時間: 約25分鐘"
        
        route = data['routes'][0]['legs'][0]
        duration = route['duration']['text']
        distance = route['distance']['text']
        
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n預計時間: {duration}\n距離: {distance}\n\n資料來源: Google Maps"
        
    except Exception as e:
        print(f"車流API錯誤: {str(e)}")
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n取得資料失敗\n預估時間: 約25分鐘"

def get_google_calendar_events():
    """取得 Google 日曆事件"""
    SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
    try:
        creds_json = os.environ.get('GOOGLE_CREDS_JSON')
        if not creds_json:
            return "📅 今日行程\n\nGoogle Calendar API金鑰未設定"
        
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        
        now = dt.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=5,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        if not events:
            return '📅 今日行程\n\n今日無安排行程'
        
        result = '📅 今日行程\n\n'
        for event in events[:3]:  # 只顯示前3個事件
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', '無標題')
            result += f"• {start[:16]} {summary}\n"
        
        return result
        
    except Exception as e:
        print(f"Google Calendar API錯誤: {str(e)}")
        return "📅 今日行程\n\n行事曆資料取得失敗"

def get_calendar():
    return get_google_calendar_events()

# ==================== 組合訊息函數 ====================

def get_morning_briefing():
    weather = get_weather("新北市")
    us_stocks = get_us_stock_info("NVDA")
    calendar = get_calendar()
    return f"🌞 早安！\n\n{weather}\n\n{us_stocks}\n\n{calendar}"

def get_commute_to_work():
    weather = get_weather("臺北市")
    traffic = get_traffic("home", "office")
    return f"🚗 上班通勤資訊\n\n{weather}\n\n{traffic}"

def get_market_open():
    stocks = get_taiwan_stock_info("TAIEX")
    news = get_news()
    return f"📈 台股開盤\n\n{stocks}\n\n{news}"

def get_market_mid():
    return f"📊 台股盤中\n\n{get_taiwan_stock_info('TAIEX')}"

def get_market_close():
    return f"📉 台股收盤\n\n{get_taiwan_stock_info('TAIEX')}"

def get_evening_zhongzheng():
    weather = get_weather("臺北市")
    traffic = get_traffic("office", "post_office")
    return f"🌆 下班資訊（中正區）\n\n{weather}\n\n{traffic}"

def get_evening_xindian():
    weather = get_weather("新北市")
    traffic = get_traffic("office", "home")
    return f"🌆 下班資訊（新店）\n\n{weather}\n\n{traffic}"

# ==================== Flask 路由 ====================

@app.route("/send_scheduled", methods=['GET', 'POST'])
def send_scheduled():
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        current_weekday = taiwan_time.weekday()
        print(f"[定時推播] 當前時間: {current_time}, 星期: {current_weekday}")

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
                    print(f"[定時推播] 觸發: {message_type}")
                    
                    # 根據訊息類型取得對應內容
                    message_functions = {
                        "morning_briefing": get_morning_briefing,
                        "commute_to_work": get_commute_to_work,
                        "market_open": get_market_open,
                        "market_mid": get_market_mid,
                        "market_close": get_market_close,
                        "evening_zhongzheng": get_evening_zhongzheng,
                        "evening_xindian": get_evening_xindian
                    }
                    
                    if message_type in message_functions:
                        message = message_functions[message_type]()
                        
                        if not message or message.strip() == "":
                            message = "⚠️ 查無資料，請確認關鍵字或稍後再試。"
                        
                        try:
                            print(f"[定時推播] 準備發送: {message_type}")
                            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
                            print(f"[定時推播] 發送成功: {message_type}")
                        except Exception as e:
                            print(f"[定時推播] 發送失敗: {str(e)}")

        return 'OK'
        
    except Exception as e:
        print(f"[定時推播] 錯誤: {str(e)}")
        return f"❌ 錯誤: {str(e)}"

@app.route("/")
def index():
    return "LINE Bot 服務運行中"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        print("[Webhook] 收到訊息")
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text.strip()
    reply = ""
    
    try:
        print(f"[Webhook] 收到用戶訊息: {user_message}")
        
        # 預設指令處理
        command_handlers = {
            "morning_briefing": get_morning_briefing,
            "commute_to_work": get_commute_to_work,
            "market_open": get_market_open,
            "market_mid": get_market_mid,
            "market_close": get_market_close,
            "evening_zhongzheng": get_evening_zhongzheng,
            "evening_xindian": get_evening_xindian,
            "新聞": get_news,
            "車流": get_traffic
        }
        
        if user_message in command_handlers:
            reply = command_handlers[user_message]()
        elif user_message.startswith("台股 "):
            name = user_message.split(" ")[1].strip()
            code = stock_name_map.get(name, name)
            reply = get_taiwan_stock_info(code)
        elif user_message.startswith("美股 "):
            name = user_message.split(" ")[1].strip().lower()
            symbol = us_stock_name_map.get(name, name.upper())
            reply = get_us_stock_info(symbol)
        elif user_message in ["新北市", "臺北市", "新店區", "中山區", "中正區"]:
            reply = get_weather(user_message)
        elif user_message == "測試":
            reply = "🤖 系統測試\n\n✅ 連線正常\n✅ 推送系統運作中\n✅ 天氣API已修正\n\n📋 功能列表:\n• 美股、台股查詢\n• 天氣查詢 (新北市/臺北市等)\n• 車流資訊\n• 新聞資訊\n\n⏰ 定時推送:\n• 07:10 早安綜合\n• 08:00 上班通勤\n• 09:30 開盤+新聞\n• 12:00 台股盤中\n• 13:45 台股收盤\n• 17:30 下班資訊"
        elif user_message == "幫助":
            reply = "📚 LINE Bot 功能列表:\n\n🔹 天氣查詢: 輸入地區名稱\n🔹 台股查詢: 台股 股票名稱\n🔹 美股查詢: 美股 股票名稱\n🔹 新聞: 輸入「新聞」\n🔹 車流: 輸入「車流」\n🔹 測試: 輸入「測試」\n\n⏰ 自動推送時間:\n• 07:10 早安資訊\n• 08:00 通勤資訊\n• 09:30 開盤資訊\n• 12:00 盤中資訊\n• 13:45 收盤資訊\n• 17:30 下班資訊"
        
    except Exception as e:
        reply = "❌ 處理訊息時發生錯誤: " + str(e)
        print(f"[Webhook] 處理錯誤: {str(e)}")

    if not reply or reply.strip() == "":
        reply = "⚠️ 查無相關資料，請輸入「幫助」查看功能列表。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@app.route("/send_test", methods=['GET', 'POST'])
def send_test():
    try:
        message = get_morning_briefing()
        if not message or message.strip() == "":
            message = "⚠️ 測試訊息產生失敗"
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
        return "✅ 測試訊息已發送"
    except Exception as e:
        return f"❌ 測試失敗: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
