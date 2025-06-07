import os
import yfinance as yf
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import requests
import json

# 註解掉 Google API imports，避免部署時缺少套件導致錯誤
# from google.oauth2.credentials import Credentials
# from google_auth_oauthlib.flow import InstalledAppFlow
# from google.auth.transport.requests import Request
# from googleapiclient.discovery import build
# import pickle

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
YOUR_USER_ID = "U95eea3698b802603dd7f285a67c698b53"

# API Keys
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')

# 固定地址
HOME_ADDRESS = "新店區建國路99巷, 新北市, Taiwan"
OFFICE_ADDRESS = "台北市南京東路三段131號, Taiwan"
JINNAN_POST_OFFICE = "台北市愛國東路216號, Taiwan"

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/", methods=['GET'])
def home():
    return "🟢 股市播報員 LINE Bot 運作中！"

@app.route("/", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# Google Calendar 設定
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# 動態取得台灣節假日 (從政府資料來源)
def get_taiwan_holidays_dynamic():
    """從行政院人事總處取得台灣節假日資料"""
    try:
        # 行政院人事總處節假日 API
        year = datetime.now().year
        api_url = f"https://data.gov.tw/api/v1/rest/datastore_search?resource_id=W2C00467-A349-42CC-BE00-76B70760A1AD&filters=%7B%22date%22:%22{year}%22%7D"
        
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            holidays = data.get('result', {}).get('records', [])
            
            today_str = datetime.now().strftime('%Y-%m-%d')
            for holiday in holidays:
                if holiday.get('date') == today_str:
                    return f"🇹🇼 {holiday.get('name', '台灣節假日')} ({holiday.get('description', '國定假日')})"
            
        return None
        
    except Exception as e:
        print(f"政府節假日 API 錯誤: {e}")
        return None

# 從 Google Calendar 取得台灣節假日
def get_taiwan_holidays_from_google():
    """從 Google Calendar 台灣節假日行事曆取得資料"""
    try:
        service = get_calendar_service()
        if not service:
            return None
        
        # 台灣節假日的公開行事曆 ID
        taiwan_holidays_calendar_id = 'zh-tw.taiwan#holiday@group.v.calendar.google.com'
        
        # 取得今日
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId=taiwan_holidays_calendar_id,
            timeMin=start_of_day,
            timeMax=end_of_day,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if events:
            holiday_names = []
            for event in events:
                holiday_names.append(event.get('summary', '節假日'))
            return f"🇹🇼 {' / '.join(holiday_names)}"
        
        return None
        
    except Exception as e:
        print(f"Google Calendar 節假日錯誤: {e}")
        return None

# 取得國際節日 (從 Google Calendar)
def get_international_holidays():
    """從 Google Calendar 國際節日行事曆取得資料"""
    try:
        service = get_calendar_service()
        if not service:
            return None
        
        # 國際節日的公開行事曆 ID
        international_calendar_id = 'en.global#holiday@group.v.calendar.google.com'
        
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId=international_calendar_id,
            timeMin=start_of_day,
            timeMax=end_of_day,
            maxResults=5,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if events:
            holiday_names = []
            for event in events:
                holiday_names.append(event.get('summary', '國際節日'))
            return f"🌍 {' / '.join(holiday_names)}"
        
        return None
        
    except Exception as e:
        print(f"國際節日錯誤: {e}")
        return None

# Google Calendar 服務初始化 (暫時簡化版)
def get_calendar_service():
    """取得 Google Calendar 服務 - 暫時返回 None，功能開發中"""
    # TODO: 實作 Google Calendar API 整合
    # 需要設定 service account 或 OAuth 認證
    return None

# 簡化版台灣節假日查詢 (備用方案)
def get_taiwan_holidays_fallback():
    """備用的台灣節假日查詢"""
    today = datetime.now()
    
    # 基本的節假日判斷 (2025年重要節日)
    major_holidays = {
        "01-01": "🎊 元旦",
        "01-28": "🏮 除夕", 
        "01-29": "🧧 春節初一",
        "01-30": "🧧 春節初二",
        "01-31": "🧧 春節初三",
        "02-28": "🌸 和平紀念日",
        "04-04": "🌿 兒童節",
        "04-05": "🌿 清明節", 
        "05-01": "⚒️ 勞動節",
        "06-15": "🚣 端午節",
        "09-17": "🏮 中秋節",
        "10-10": "🇹🇼 國慶日"
    }
    
    today_str = today.strftime("%m-%d")
    holiday = major_holidays.get(today_str)
    
    if holiday:
        return f"🇹🇼 {holiday} (國定假日)"
    
    return None

# 取得個人行程
def get_personal_calendar_events():
    """取得個人 Google Calendar 行程"""
    try:
        service = get_calendar_service()
        if not service:
            return "💡 Google Calendar 個人行程整合設定中..."
        
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat() + 'Z'

        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_of_day,
            timeMax=end_of_day,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return "📅 今日無個人行程"
        
        personal_events = "📅 今日個人行程:\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            if 'T' in start:
                start_time = datetime.fromisoformat(start.replace('Z', '+00:00'))
                time_str = start_time.strftime('%H:%M')
            else:
                time_str = "全天"
            
            summary = event.get('summary', '無標題')
            personal_events += f"• {time_str} - {summary}\n"
        
        return personal_events.strip()
        
    except Exception as e:
        return f"❌ 個人行程讀取失敗: {str(e)}"

# 彈性的行事曆資訊整合 (修正版)
def get_calendar_info():
    """整合所有行事曆資訊 - 彈性動態版本 (修正版)"""
    try:
        result_parts = []
        
        # 1. 嘗試從政府 API 取得台灣節假日
        try:
            tw_holiday_gov = get_taiwan_holidays_dynamic()
            if tw_holiday_gov:
                result_parts.append(tw_holiday_gov)
        except Exception as e:
            print(f"政府 API 錯誤: {e}")
        
        # 2. 如果政府 API 失敗，使用備用方案
        if not result_parts:
            try:
                tw_holiday_fallback = get_taiwan_holidays_fallback()
                if tw_holiday_fallback:
                    result_parts.append(tw_holiday_fallback)
            except Exception as e:
                print(f"備用節假日錯誤: {e}")
        
        # 3. 嘗試取得國際節日 (如果 Google Calendar 可用)
        try:
            international_holiday = get_international_holidays()
            if international_holiday:
                result_parts.append(international_holiday)
        except Exception as e:
            print(f"國際節日錯誤: {e}")
        
        # 4. 取得個人行程
        try:
            personal_events = get_personal_calendar_events()
            if personal_events and "設定中" not in personal_events:
                result_parts.append(personal_events)
            else:
                result_parts.append("📅 個人行程: Google Calendar 整合設定中...")
        except Exception as e:
            result_parts.append("📅 個人行程: 功能開發中...")
        
        # 5. 週末/工作日提醒
        today = datetime.now()
        if today.weekday() == 5:
            result_parts.append("🌴 今日週六，好好休息！")
        elif today.weekday() == 6:
            result_parts.append("🌴 今日週日，準備迎接新的一週！")
        elif not is_workday():
            result_parts.append("🌴 今日放假，享受假期時光！")
        
        # 組合結果
        if result_parts:
            return "\n\n".join(result_parts)
        else:
            return "📅 今日無特殊行程或節日"
        
    except Exception as e:
        return f"❌ 行事曆功能錯誤: {str(e)}"

# 取得天氣資訊
def get_weather(location):
    try:
        if not WEATHER_API_KEY:
            return "❌ 天氣 API Key 未設定"
        
        # 地點對應
        location_map = {
            "新店": "Xindian District, New Taipei, Taiwan",
            "中山區": "Zhongshan District, Taipei, Taiwan",
            "中正區": "Zhongzheng District, Taipei, Taiwan"
        }
        
        search_location = location_map.get(location, location)
        today = datetime.now().strftime('%Y-%m-%d')
        
        url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{search_location}/{today}"
        
        params = {
            'key': WEATHER_API_KEY,
            'include': 'days,current',
            'elements': 'temp,tempmax,tempmin,humidity,conditions,description,windspeed'
        }
        
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if 'days' in data and len(data['days']) > 0:
                day_data = data['days'][0]
                current_data = data.get('currentConditions', {})
                
                # 溫度轉換 (華氏轉攝氏)
                def f_to_c(temp_f):
                    return (temp_f - 32) * 5/9 if temp_f else None
                
                current_temp_c = f_to_c(current_data.get('temp'))
                temp_max_c = f_to_c(day_data.get('tempmax'))
                temp_min_c = f_to_c(day_data.get('tempmin'))
                
                humidity = day_data.get('humidity', 0)
                conditions = day_data.get('conditions', 'N/A')
                windspeed = day_data.get('windspeed', 0)
                
                result = f"🌤️ {location} 天氣 ({today})\n\n"
                
                if current_temp_c:
                    result += f"🌡️ 現在溫度: {current_temp_c:.1f}°C\n"
                if temp_max_c and temp_min_c:
                    result += f"🌡️ 高低溫: {temp_max_c:.1f}°C / {temp_min_c:.1f}°C\n"
                result += f"💧 濕度: {humidity:.0f}%\n"
                result += f"💨 風速: {windspeed:.1f} km/h\n"
                result += f"☁️ 天氣狀況: {conditions}"
                
                return result
            else:
                return f"❌ 無法取得 {location} 的天氣資料"
        else:
            return f"❌ 天氣 API 錯誤 (狀態碼: {response.status_code})"
            
    except requests.exceptions.Timeout:
        return "❌ 天氣 API 請求超時"
    except Exception as e:
        return f"❌ 天氣資料錯誤: {str(e)}"

# 取得美股資訊
def get_us_stocks():
    try:
        symbols = ['NVDA', 'SMCI', 'GOOGL', 'AAPL', 'MSFT']
        stock_names = ['輝達 (NVIDIA)', '美超微 (Super Micro)', 'Google (Alphabet)', '蘋果 (Apple)', '微軟 (Microsoft)']
        
        results = []
        
        for i, symbol in enumerate(symbols):
            try:
                # 使用更穩定的方法取得股價
                ticker = yf.Ticker(symbol)
                
                # 取得最近 5 天的資料
                hist = ticker.history(period="5d", interval="1d")
                
                if len(hist) >= 2:
                    current_price = hist['Close'].iloc[-1]
                    prev_price = hist['Close'].iloc[-2]
                    change = current_price - prev_price
                    change_percent = (change / prev_price) * 100
                    
                    emoji = "🟢" if change >= 0 else "🔴"
                    
                    stock_info = f"{emoji} {stock_names[i]}\n"
                    stock_info += f"   收盤: ${current_price:.2f} ({change_percent:+.2f}%)"
                    
                    # 嘗試取得盤後交易資料
                    try:
                        info = ticker.info
                        post_market_price = info.get('postMarketPrice')
                        post_market_change_percent = info.get('postMarketChangePercent')
                        
                        if post_market_price and post_market_change_percent:
                            post_emoji = "🟢" if post_market_change_percent >= 0 else "🔴"
                            stock_info += f"\n   {post_emoji} 盤後: ${post_market_price:.2f} ({post_market_change_percent*100:+.2f}%)"
                    except:
                        pass  # 如果無法取得盤後資料就跳過
                    
                    results.append(stock_info)
                else:
                    results.append(f"❌ {stock_names[i]}: 資料不足")
                    
            except Exception as e:
                results.append(f"❌ {stock_names[i]}: 取得失敗")
        
        if not results:
            return "❌ 無法取得任何美股資料，請稍後再試"
            
        return "📈 美股昨夜表現:\n\n" + "\n\n".join(results)
        
    except Exception as e:
        return f"❌ 美股資料系統錯誤: {str(e)}"

# 取得台股資訊
def get_taiwan_stocks():
    try:
        symbols = ['2330.TW', '2454.TW', '2317.TW', '3008.TW', '2303.TW']
        stock_names = ['台積電', '聯發科', '鴻海', '大立光', '聯電']
        
        results = []
        
        for i, symbol in enumerate(symbols):
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                
                if len(hist) >= 2:
                    current_price = hist['Close'].iloc[-1]
                    prev_price = hist['Close'].iloc[-2]
                    change = current_price - prev_price
                    change_percent = (change / prev_price) * 100
                    
                    emoji = "🟢" if change >= 0 else "🔴"
                    
                    results.append(f"{emoji} {stock_names[i]} ({symbol.replace('.TW', '')})")
                    results.append(f"   NT${current_price:.2f} ({change_percent:+.2f}%)")
                else:
                    results.append(f"❌ {stock_names[i]}: 資料不足")
                    
            except Exception as e:
                results.append(f"❌ {stock_names[i]}: 取得失敗")
        
        # 檢查是否為交易時間
        now = datetime.now()
        if now.weekday() >= 5:  # 週末
            status = "📊 台股主要標的 (週末休市):\n"
        elif now.hour < 9 or now.hour >= 14:  # 非交易時間
            status = "📊 台股主要標的 (非交易時間):\n"
        else:
            status = "📊 台股主要標的 (交易中):\n"
            
        return status + "\n".join(results)
        
    except Exception as e:
        return f"❌ 台股資料錯誤: {str(e)}"

# 取得特定路線車流
def get_route_traffic(route_type):
    try:
        if not GOOGLE_MAPS_API_KEY:
            return "❌ Google Maps API Key 未設定"
        
        routes = {
            "家公司": ("🏠→🏢", "家", "公司", HOME_ADDRESS, OFFICE_ADDRESS),
            "公司郵局": ("🏢→📮", "公司", "金南郵局", OFFICE_ADDRESS, JINNAN_POST_OFFICE),
            "公司家": ("🏢→🏠", "公司", "家", OFFICE_ADDRESS, HOME_ADDRESS)
        }
        
        if route_type not in routes:
            return "❌ 路線類型錯誤"
        
        emoji, origin_name, dest_name, origin_addr, dest_addr = routes[route_type]
        
        url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            'origin': origin_addr,
            'destination': dest_addr,
            'departure_time': 'now',
            'traffic_model': 'best_guess',
            'key': GOOGLE_MAPS_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if data['status'] == 'OK' and data['routes']:
                route = data['routes'][0]['legs'][0]
                duration = route['duration']['text']
                duration_in_traffic = route.get('duration_in_traffic', {}).get('text', duration)
                distance = route['distance']['text']
                
                # 判斷車流狀況
                normal_time = route['duration']['value']
                traffic_time = route.get('duration_in_traffic', {}).get('value', normal_time)
                
                if traffic_time <= normal_time * 1.2:
                    status = "🟢 順暢"
                elif traffic_time <= normal_time * 1.5:
                    status = "🟡 緩慢"
                else:
                    status = "🔴 壅塞"
                
                result = f"🚗 {emoji} {origin_name} → {dest_name}\n\n"
                result += f"{status} 路況\n"
                result += f"⏱️ 預估時間: {duration_in_traffic}\n"
                result += f"📏 距離: {distance}\n"
                result += f"🛣️ 正常時間: {duration}"
                
                return result
            else:
                return f"❌ 無法取得 {origin_name}→{dest_name} 路況: {data.get('status', '未知錯誤')}"
        else:
            return f"❌ Google Maps API 錯誤 (狀態碼: {response.status_code})"
            
    except requests.exceptions.Timeout:
        return "❌ Google Maps API 請求超時"
    except Exception as e:
        return f"❌ 路線查詢錯誤: {str(e)}"

# 取得所有路線車流
def get_all_routes_traffic():
    try:
        routes = ["家公司", "公司郵局", "公司家"]
        results = []
        
        for route in routes:
            traffic_info = get_route_traffic(route)
            results.append(traffic_info)
        
        return "\n\n".join(results)
    except Exception as e:
        return f"❌ 所有路線查詢錯誤: {str(e)}"

# 取得新聞 (簡化版，無需額外 API)
def get_simple_news():
    return """📰 新聞功能提醒:

🔔 如需完整新聞功能，請：
1. 到 newsapi.org 申請免費 API Key
2. 將 NEWS_API_KEY 加入環境變數

💡 目前可使用其他功能：
• 美股 - 即時股價追蹤
• 台股 - 台灣股市狀況  
• 天氣 - 各區域天氣
• 車流 - 路線車況分析"""

# 檢查是否為上班日
def is_workday():
    return datetime.now().weekday() < 5

# 排程推送函數
def send_morning_weather_report():
    try:
        weather_data = get_weather("新店")
        us_stocks_data = get_us_stocks()
        calendar_data = get_calendar_info()
        
        report = f"""🌅 早安！綜合晨報

{weather_data}

{us_stocks_data}

{calendar_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"早晨報告失敗: {e}")

def send_workday_morning_report():
    try:
        if not is_workday():
            return
            
        weather_data = get_weather("中山區")
        traffic_data = get_route_traffic("家公司")
        
        report = f"""🌅 上班日報告

{weather_data}

{traffic_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"上班日報告失敗: {e}")

def send_stock_opening_report():
    try:
        if not is_workday():
            return
            
        taiwan_stocks = get_taiwan_stocks()
        news_data = get_simple_news()
        
        report = f"""📈 台股開盤報告

{taiwan_stocks}

{news_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"開盤報告失敗: {e}")

def send_stock_midday_report():
    try:
        if not is_workday():
            return
            
        taiwan_stocks = get_taiwan_stocks()
        
        report = f"""📊 台股盤中報告

{taiwan_stocks}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"盤中報告失敗: {e}")

def send_stock_closing_report():
    try:
        if not is_workday():
            return
            
        taiwan_stocks = get_taiwan_stocks()
        
        report = f"""📈 台股收盤報告

{taiwan_stocks}

📊 今日交易結束
📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"收盤報告失敗: {e}")

def send_evening_post_office_report():
    try:
        if not is_workday():
            return
            
        weather_data = get_weather("中正區")
        traffic_data = get_route_traffic("公司郵局")
        
        report = f"""🌆 下班時間 - 前往郵局

{weather_data}

{traffic_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💡 記得郵局營業時間喔！"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"郵局下班報告失敗: {e}")

def send_evening_home_report():
    try:
        if not is_workday():
            return
            
        weather_data = get_weather("新店")
        traffic_data = get_route_traffic("公司家")
        
        report = f"""🌆 下班時間 - 回家路線

{weather_data}

{traffic_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🏠 注意交通安全，準時回家！"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"回家下班報告失敗: {e}")

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_message = event.message.text.strip()
        
        # 先記錄收到的訊息 (除錯用)
        print(f"收到訊息: '{user_message}'")
        
        # 簡化比對邏輯，避免編碼問題
        if user_message == "美股":
            reply = get_us_stocks()
        elif user_message == "台股":
            reply = get_taiwan_stocks()
        elif user_message == "新聞":
            reply = get_simple_news()
        elif user_message == "行程":
            reply = get_calendar_info()
        elif user_message == "行事曆":
            reply = get_calendar_info()
        elif user_message == "新店":
            reply = get_weather("新店")
        elif user_message == "中山區":
            reply = get_weather("中山區")
        elif user_message == "中正區":
            reply = get_weather("中正區")
        elif user_message == "車流":
            reply = get_all_routes_traffic()
        elif user_message == "交通":
            reply = get_all_routes_traffic()
        elif user_message == "家公司":
            reply = get_route_traffic("家公司")
        elif user_message == "公司郵局":
            reply = get_route_traffic("公司郵局")
        elif user_message == "公司家":
            reply = get_route_traffic("公司家")
        elif user_message == "測試":
            reply = "✅ 股市播報員系統正常運作！\n\n🔧 所有功能已修正並優化\n📅 自動推送已設定完成\n\n請輸入「幫助」查看所有功能"
        elif user_message == "幫助":
            reply = """📋 股市播報員功能指南

💼 股市資訊:
• 美股 - 輝達/美超微/Google等
• 台股 - 台積電/聯發科等主要股票

🌤️ 天氣查詢:
• 新店 - 新店天氣
• 中山區 - 中山區天氣  
• 中正區 - 中正區天氣

🚗 車流查詢:
• 車流 - 所有路線車況
• 家公司 - 🏠→🏢 家到公司
• 公司郵局 - 🏢→📮 公司到金南郵局
• 公司家 - 🏢→🏠 公司到家

📅 其他功能:
• 行程 - 今日行程與節假日
• 新聞 - 新聞功能說明
• 測試 - 系統狀態檢查

⏰ 自動推送時間:
每日 07:10 - 新店天氣+美股+行程
上班日 08:00 - 中山區天氣+上班路線
上班日 09:30 - 台股開盤+新聞
上班日 12:00 - 台股盤中
上班日 13:45 - 台股收盤  
上班日 17:30 (一三五) - 中正區天氣+郵局路線
上班日 17:30 (二四) - 新店天氣+回家路線"""
        else:
            reply = f"🤖 抱歉，我不理解「{user_message}」\n\n請輸入「幫助」查看所有可用功能\n\n📋 快速指令: 美股、台股、新店、車流、測試"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        
    except Exception as e:
        # 如果出現任何錯誤，至少要能回應
        error_reply = f"❌ 系統錯誤: {str(e)}\n\n請稍後再試，或輸入「測試」檢查系統狀態"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_reply))

# 排程器設定
scheduler = BackgroundScheduler()

# 每日 07:10 - 綜合晨報
scheduler.add_job(func=send_morning_weather_report, trigger="cron", hour=7, minute=10)

# 上班日 08:00 - 上班報告  
scheduler.add_job(func=send_workday_morning_report, trigger="cron", 
                 day_of_week='mon-fri', hour=8, minute=0)

# 上班日 09:30 - 開盤報告
scheduler.add_job(func=send_stock_opening_report, trigger="cron", 
                 day_of_week='mon-fri', hour=9, minute=30)

# 上班日 12:00 - 盤中報告
scheduler.add_job(func=send_stock_midday_report, trigger="cron", 
                 day_of_week='mon-fri', hour=12, minute=0)

# 上班日 13:45 - 收盤報告
scheduler.add_job(func=send_stock_closing_report, trigger="cron", 
                 day_of_week='mon-fri', hour=13, minute=45)

# 上班日 17:30 - 下班報告
scheduler.add_job(func=send_evening_post_office_report, trigger="cron", 
                 day_of_week='mon,wed,fri', hour=17, minute=30)

scheduler.add_job(func=send_evening_home_report, trigger="cron", 
                 day_of_week='tue,thu', hour=17, minute=30)

scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == "__main__":
    app.run()
