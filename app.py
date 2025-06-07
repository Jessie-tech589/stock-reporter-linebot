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
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle

# LINE Bot 設定 - 從環境變數取得
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
YOUR_USER_ID = "U95eea3698b802603dd7f285a67c698b53"

# API Keys
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')
NEWS_API_KEY = os.getenv('NEWS_API_KEY')  # 需要申請新聞 API

# API URLs
WEATHER_BASE_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
GOOGLE_MAPS_API_URL = "https://maps.googleapis.com/maps/api/directions/json"
NEWS_API_URL = "https://newsapi.org/v2/top-headlines"

# Google Calendar 設定
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/", methods=['GET'])
def home():
    return "🟢 股市清報 LINE Bot 運作中！"

@app.route("/", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# Google Calendar 服務初始化
def get_calendar_service():
    """取得 Google Calendar 服務"""
    creds = None
    # token.pickle 儲存用戶的存取和更新令牌
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # 如果沒有有效的憑證，讓用戶登入
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 需要設定 Google Calendar API 憑證檔案
            if os.path.exists('credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                return None
        
        # 儲存憑證供下次使用
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except:
        return None

# 台灣節假日資料
def get_taiwan_holidays():
    """取得台灣節假日資訊"""
    holidays_2025 = {
        "01-01": "🎊 元旦",
        "01-25": "🏮 小年夜", 
        "01-26": "🏮 除夕",
        "01-27": "🧧 春節初一",
        "01-28": "🧧 春節初二", 
        "01-29": "🧧 春節初三",
        "01-30": "🧧 春節初四",
        "01-31": "🧧 春節初五",
        "02-28": "🌸 和平紀念日",
        "04-03": "🌺 兒童節",
        "04-04": "🌿 清明節",
        "04-05": "🌿 民族掃墓節調整放假",
        "05-01": "⚒️ 勞動節",
        "06-09": "🚣 端午節",
        "09-17": "🏮 中秋節",
        "10-10": "🇹🇼 國慶日",
        "10-11": "🇹🇼 國慶日調整放假"
    }
    
    today = datetime.now().strftime("%m-%d")
    return holidays_2025.get(today, None)

# 西洋節日資料
def get_western_holidays():
    """取得西洋節日資訊"""
    western_holidays = {
        "01-01": "🎊 新年 New Year's Day",
        "02-14": "💝 情人節 Valentine's Day",
        "03-17": "☘️ 聖派翠克節 St. Patrick's Day",
        "04-01": "🤡 愚人節 April Fool's Day",
        "05-12": "👩 母親節 Mother's Day (第二個週日)",
        "06-16": "👨 父親節 Father's Day (第三個週日)",
        "10-31": "🎃 萬聖節 Halloween",
        "11-28": "🦃 感恩節 Thanksgiving (第四個週四)",
        "12-24": "🎄 平安夜 Christmas Eve",
        "12-25": "🎅 聖誕節 Christmas Day",
        "12-31": "🎉 跨年夜 New Year's Eve"
    }
    
    today = datetime.now().strftime("%m-%d")
    return western_holidays.get(today, None)

# 取得今日行事曆 (包含個人行程、節假日)
def get_today_calendar_events():
    """取得今日的完整行事曆資訊"""
    try:
        # 個人行程
        personal_events = ""
        service = get_calendar_service()
        
        if service:
            # 取得今日開始和結束時間
            now = datetime.now()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat() + 'Z'

            # 呼叫 Calendar API
            events_result = service.events().list(
                calendarId='primary',
                timeMin=start_of_day,
                timeMax=end_of_day,
                maxResults=10,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            if events:
                personal_events = "📅 個人行程:\n"
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    if 'T' in start:  # 有時間的事件
                        start_time = datetime.fromisoformat(start.replace('Z', '+00:00'))
                        time_str = start_time.strftime('%H:%M')
                    else:  # 全天事件
                        time_str = "全天"
                    
                    summary = event.get('summary', '無標題')
                    personal_events += f"• {time_str} - {summary}\n"
        
        # 台灣節假日
        tw_holiday = get_taiwan_holidays()
        holiday_info = ""
        if tw_holiday:
            holiday_info += f"\n🇹🇼 台灣節日: {tw_holiday}\n"
        
        # 西洋節日
        western_holiday = get_western_holidays()
        if western_holiday:
            holiday_info += f"🌍 西洋節日: {western_holiday}\n"
        
        # 組合結果
        result = ""
        if personal_events:
            result += personal_events
        else:
            result += "📅 今日無個人行程\n"
        
        if holiday_info:
            result += holiday_info
        
        if not personal_events and not holiday_info:
            result = "📅 今日無特別行程或節日"
        
        return result.strip()
        
    except Exception as e:
        return f"❌ 行事曆讀取失敗: {str(e)}"

# 取得真實天氣資料
def get_weather_by_location(location, date=None):
    """取得指定地點的天氣資訊"""
    try:
        if not WEATHER_API_KEY:
            return "❌ 天氣 API Key 未設定"
        
        location_mapping = {
            "新店": "Xindian District, New Taipei, Taiwan",
            "中山區": "Zhongshan District, Taipei, Taiwan", 
            "中正區": "Zhongzheng District, Taipei, Taiwan"
        }
        
        search_location = location_mapping.get(location, location)
        
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        
        url = f"{WEATHER_BASE_URL}/{search_location}/{date}"
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
                
                # 當前溫度
                current_temp = current_data.get('temp')
                if current_temp:
                    current_temp_c = (current_temp - 32) * 5/9
                else:
                    current_temp_c = None
                
                # 最高最低溫
                temp_max = day_data.get('tempmax')
                temp_min = day_data.get('tempmin')
                temp_max_c = (temp_max - 32) * 5/9 if temp_max else None
                temp_min_c = (temp_min - 32) * 5/9 if temp_min else None
                
                humidity = day_data.get('humidity', 0)
                conditions = day_data.get('conditions', 'N/A')
                windspeed = day_data.get('windspeed', 0)
                
                weather_report = f"🌤️ {location} 天氣 ({date})\n"
                if current_temp_c:
                    weather_report += f"🌡️ 現在: {current_temp_c:.1f}°C\n"
                if temp_max_c and temp_min_c:
                    weather_report += f"🌡️ 高低溫: {temp_max_c:.1f}°C / {temp_min_c:.1f}°C\n"
                weather_report += f"💧 濕度: {humidity:.0f}%\n"
                weather_report += f"💨 風速: {windspeed:.1f}km/h\n"
                weather_report += f"☁️ {conditions}"
                
                return weather_report
            else:
                return f"❌ 無法取得 {location} 的天氣資料"
        else:
            return f"❌ 天氣 API 錯誤 ({response.status_code})"
            
    except Exception as e:
        return f"❌ 天氣資料失敗: {str(e)}"

f"❌ {origin}→{destination}: {str(e)}")
        
        return f"🚗 {location} 即時車流:\n" + "\n\n".join(traffic_info)
        
    except Exception as e:
        return f"❌ 車流資料失敗: {str(e)}"

# 取得美股資料 (包含盤後交易)
def get_us_stocks():
    """取得美股資料 (正常交易 + 盤後交易)"""
    try:
        symbols = ['NVDA', 'SMCI', 'GOOGL', 'AAPL', 'MSFT']  # 輝達、美超微、Google、蘋果、微軟
        stock_names = ['輝達 (NVIDIA)', '美超微 (Super Micro)', 'Google (Alphabet)', '蘋果 (Apple)', '微軟 (Microsoft)']
        results = []
        
        for i, symbol in enumerate(symbols):
            try:
                ticker = yf.Ticker(symbol)
                
                # 取得歷史資料 (正常交易時間)
                hist = ticker.history(period="5d")
                
                if len(hist) >= 2:
                    # 正常交易時間的收盤價
                    current_price = hist['Close'].iloc[-1]
                    prev_price = hist['Close'].iloc[-2]
                    change = current_price - prev_price
                    change_percent = (change / prev_price) * 100
                    
                    # 嘗試取得即時資料 (可能包含盤後價格)
                    try:
                        info = ticker.info
                        current_market_price = info.get('currentPrice', current_price)
                        post_market_price = info.get('postMarketPrice', None)
                        post_market_change = info.get('postMarketChange', None)
                        post_market_change_percent = info.get('postMarketChangePercent', None)
                        
                        # 判斷正常交易時間漲跌
                        emoji = "🟢" if change >= 0 else "🔴"
                        
                        result_text = f"{emoji} {stock_names[i]}\n"
                        result_text += f"   收盤: ${current_price:.2f} ({change_percent:+.2f}%)"
                        
                        # 如果有盤後交易資料
                        if post_market_price and post_market_change and post_market_change_percent:
                            post_emoji = "🟢" if post_market_change >= 0 else "🔴"
                            result_text += f"\n   {post_emoji} 盤後: ${post_market_price:.2f} ({post_market_change_percent*100:+.2f}%)"
                        
                        results.append(result_text)
                        
                    except:
                        # 如果無法取得即時資料，就只顯示收盤價
                        emoji = "🟢" if change >= 0 else "🔴"
                        results.append(f"{emoji} {stock_names[i]}")
                        results.append(f"   收盤: ${current_price:.2f} ({change_percent:+.2f}%)")
                        
                else:
                    results.append(f"❌ {stock_names[i]}: 資料不足")
                    
            except Exception as e:
                results.append(f"❌ {stock_names[i]}: 取得失敗")
        
        return "📈 美股昨夜表現:\n" + "\n\n".join(results)
        
    except Exception as e:
        return f"❌ 美股資料失敗: {str(e)}"

# 取得台股資料
def get_taiwan_stocks():
    """取得台股資料"""
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
        
        return "📊 台股主要標的:\n" + "\n".join(results)
        
    except Exception as e:
        return f"❌ 台股資料失敗: {str(e)}"

# 取得新聞資料
def get_major_news():
    """取得國內外重大新聞"""
    try:
        if not NEWS_API_KEY:
            return "❌ 新聞 API Key 未設定"
        
        # 取得台灣新聞
        tw_params = {
            'country': 'tw',
            'category': 'business',
            'pageSize': 3,
            'apiKey': NEWS_API_KEY
        }
        
        tw_response = requests.get(NEWS_API_URL, params=tw_params, timeout=10)
        
        # 取得國際新聞
        intl_params = {
            'country': 'us',
            'category': 'business',
            'pageSize': 3,
            'apiKey': NEWS_API_KEY
        }
        
        intl_response = requests.get(NEWS_API_URL, params=intl_params, timeout=10)
        
        news_text = "📰 重大新聞:\n\n"
        
        # 處理台灣新聞
        if tw_response.status_code == 200:
            tw_data = tw_response.json()
            if tw_data['articles']:
                news_text += "🇹🇼 台灣:\n"
                for article in tw_data['articles'][:2]:
                    title = article['title']
                    news_text += f"• {title}\n"
                news_text += "\n"
        
        # 處理國際新聞
        if intl_response.status_code == 200:
            intl_data = intl_response.json()
            if intl_data['articles']:
                news_text += "🌍 國際:\n"
                for article in intl_data['articles'][:2]:
                    title = article['title']
                    news_text += f"• {title}\n"
        
        return news_text
        
    except Exception as e:
        return f"❌ 新聞資料失敗: {str(e)}"

# 檢查是否為上班日
def is_workday():
    """檢查今天是否為上班日 (週一到週五)"""
    return datetime.now().weekday() < 5

# 07:10 新店天氣報告 (每日) + 美股
def send_xindian_morning_report():
    try:
        weather_data = get_weather_by_location("新店")
        calendar_data = get_today_calendar_events()
        us_stocks_data = get_us_stocks()  # 加入美股資訊
        
        report = f"""🌅 早安！綜合晨報

{weather_data}

{us_stocks_data}

{calendar_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"新店早晨報告失敗: {e}")

# 08:00 中山區天氣+車流報告 (僅上班日)
def send_zhongshan_workday_report():
    try:
        if not is_workday():
            return
            
        weather_data = get_weather_by_location("中山區")
        traffic_data = get_real_traffic_status("中山區")
        
        report = f"""🌅 上班日報告 - 中山區

{weather_data}

{traffic_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"中山區上班日報告失敗: {e}")

# 09:30 台股開盤+新聞 (僅上班日)
def send_stock_opening_report():
    try:
        if not is_workday():
            return
            
        taiwan_stocks = get_taiwan_stocks()
        news_data = get_major_news()
        
        report = f"""📈 台股開盤報告

{taiwan_stocks}

{news_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"台股開盤報告失敗: {e}")

# 12:00 台股盤中報告 (僅上班日)
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
        print(f"台股盤中報告失敗: {e}")

# 13:45 台股收盤報告 (僅上班日)
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
        print(f"台股收盤報告失敗: {e}")

# 17:30 下班報告
def send_zhongzheng_evening_report():
    try:
        if not is_workday():
            return
            
        weather_data = get_weather_by_location("中正區")
        traffic_data = get_real_traffic_status("中正區")
        
        report = f"""🌆 下班時間 - 中正區

{weather_data}

{traffic_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
注意交通安全！"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"中正區下班報告失敗: {e}")

def send_xindian_evening_report():
    try:
        if not is_workday():
            return
            
        weather_data = get_weather_by_location("新店")
        traffic_data = get_real_traffic_status("新店")
        
        report = f"""🌆 下班時間 - 新店

{weather_data}

{traffic_data}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
注意交通安全！"""

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=report))
    except Exception as e:
        print(f"新店下班報告失敗: {e}")

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text.lower()
    
    if user_message == "美股":
        reply = get_us_stocks()
    elif user_message == "台股":
        reply = get_taiwan_stocks()
    elif user_message == "新聞":
        reply = get_major_news()
    elif user_message == "行程" or user_message == "行事曆":
        reply = get_today_calendar_events()
    elif user_message == "車流" or user_message == "交通":
        reply = get_route_traffic_status()
    elif user_message in ["家公司", "上班路線"]:
        reply = get_specific_route_traffic("家公司")
    elif user_message in ["公司郵局", "郵局路線"]:
        reply = get_specific_route_traffic("公司郵局")
    elif user_message in ["公司家", "回家路線"]:
        reply = get_specific_route_traffic("公司家")
    elif user_message in ["新店天氣", "新店"]:
        reply = get_weather_by_location("新店")
    elif user_message in ["中山區天氣", "中山區"]:
        reply = get_weather_by_location("中山區")
    elif user_message in ["中正區天氣", "中正區"]:
        reply = get_weather_by_location("中正區")
    elif user_message == "幫助" or user_message == "help":
        reply = """📋 可用指令:

💼 股市&新聞:
• 美股 - 美股報價
• 台股 - 台股報價
• 新聞 - 重大新聞

📅 行程:
• 行程/行事曆 - 今日行程+節假日

🌤️ 天氣查詢:
• 新店/新店天氣 • 中山區/中山區天氣 • 中正區/中正區天氣

🚗 車流查詢:
• 車流/交通 - 三條路線車流
• 家公司/上班路線 - 家→公司
• 公司郵局/郵局路線 - 公司→金南郵局  
• 公司家/回家路線 - 公司→家

⏰ 自動推送時間:
每日 07:10 - 新店天氣+美股(輝達/美超微/Google)+行程+節假日
上班日 08:00 - 中山區天氣+家→公司車流
上班日 09:30 - 台股開盤+新聞
上班日 12:00 - 台股盤中
上班日 13:45 - 台股收盤
上班日 17:30 (一三五) - 中正區天氣+公司→郵局車流
上班日 17:30 (二四) - 新店天氣+公司→家車流"""
    else:
        reply = "🤖 請輸入「幫助」查看可用指令"
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# 排程器設定
scheduler = BackgroundScheduler()

# 每日 07:10 - 新店天氣+行程
scheduler.add_job(func=send_xindian_morning_report, trigger="cron", hour=7, minute=10)

# 上班日 08:00 - 中山區天氣+車流
scheduler.add_job(func=send_zhongshan_workday_report, trigger="cron", 
                 day_of_week='mon-fri', hour=8, minute=0)

# 上班日 09:30 - 台股開盤+新聞
scheduler.add_job(func=send_stock_opening_report, trigger="cron", 
                 day_of_week='mon-fri', hour=9, minute=30)

# 上班日 12:00 - 台股盤中
scheduler.add_job(func=send_stock_midday_report, trigger="cron", 
                 day_of_week='mon-fri', hour=12, minute=0)

# 上班日 13:45 - 台股收盤
scheduler.add_job(func=send_stock_closing_report, trigger="cron", 
                 day_of_week='mon-fri', hour=13, minute=45)

# 上班日 17:30 - 下班報告
scheduler.add_job(func=send_zhongzheng_evening_report, trigger="cron", 
                 day_of_week='mon,wed,fri', hour=17, minute=30)

scheduler.add_job(func=send_xindian_evening_report, trigger="cron", 
                 day_of_week='tue,thu', hour=17, minute=30)

scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == "__main__":
    app.run()
