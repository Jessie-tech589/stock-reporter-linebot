import os
import requests
import time
import json
import pytz
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from fugle_marketdata import RestClient

app = Flask(__name__)

# ====== 環境變數 ======
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
LINE_USER_ID = os.environ.get('LINE_USER_ID')
WEATHER_API_KEY = os.environ.get('WEATHER_API_KEY')
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
TAIWAN_TZ = pytz.timezone('Asia/Taipei')
# ====== 固定地址 ======
ADDRESSES = {
    "home": "新北市新店區建國路99巷",
    "office": "台北市中山區南京東路三段131號",
    "post_office": "台北市中正區愛國東路216號"
}

# ====== 自訂機車路線 ======
CUSTOM_ROUTES = {
    "家到公司": {
        "origin": "新北市新店區建國路",
        "destination": "台北市中山區南京東路三段131號",
        "waypoints": ["新北市 民族路", "新北市 北新路", "台北市 羅斯福路", "台北市 基隆路", "台北市 辛亥路", "台北市 復興南路"]
    },
    "公司到家": {
        "origin": "台北市中山區南京東路三段131號",
        "destination": "新北市新店區建國路",
        "waypoints": ["台北市 復興南路", "台北市 辛亥路", "台北市 基隆路", "台北市 羅斯福路", "新北市 北新路", "新北市 民族路"]
    },
    "公司到郵局": {
        "origin": "台北市中山區南京東路三段131號",
        "destination": "台北市中正區愛國東路216號",
        "waypoints": ["林森北路", "林森南路", "信義路二段10巷", "愛國東路21巷"]
    }
}

# ====== 股票名稱對照表 ======
stock_name_map = {
    "台積電": "2330", "聯電": "2303", "陽明": "2609", "華航": "2610",
    "長榮航": "2618", "00918": "00918", "00878": "00878", "鴻準": "2354", "大盤": "TAIEX"
}
us_stock_name_map = {
    "輝達": "NVDA", "美超微": "SMCI", "google": "GOOGL", "蘋果": "AAPL", "特斯拉": "TSLA", "微軟": "MSFT"
}
# ====== 自訂機車路線查詢 ======
def get_custom_traffic(route_name):
    if route_name not in CUSTOM_ROUTES:
        return "❌ 查無自訂路線"
    data = CUSTOM_ROUTES[route_name]
    params = {
        "origin": data["origin"],
        "destination": data["destination"],
        "waypoints": "|".join(data["waypoints"]),
        "mode": "driving",
        "departure_time": "now",
        "language": "zh-TW",
        "key": GOOGLE_MAPS_API_KEY
    }
    try:
        url = "https://maps.googleapis.com/maps/api/directions/json"
        res = requests.get(url, params=params, timeout=10)
        js = res.json()
        if js["status"] != "OK":
            return f"❌ 取得路線失敗: {js.get('error_message', js['status'])}"
        leg = js["routes"][0]["legs"][0]
        duration = leg["duration"]["text"]
        distance = leg["distance"]["text"]
        normal_time = leg.get("duration_in_traffic", {}).get("text", duration)
        traffic_status = "🟢 順暢"
        try:
            min_time = leg["duration"]["value"]
            real_time = leg.get("duration_in_traffic", {}).get("value", min_time)
            delta = real_time - min_time
            if delta > 10 * 60:
                traffic_status = "🔴 擁擠"
            elif delta > 3 * 60:
                traffic_status = "🟡 稍慢"
        except:
            pass
        return (f"🚦 機車路線 ({route_name})\n"
                f"{data['origin']} → {data['destination']}\n"
                f"{traffic_status} 預計: {normal_time}（正常:{duration}）\n"
                f"距離: {distance}\n"
                f"主要經過: {' → '.join(data['waypoints'])}\n"
                f"資料來源: Google Maps")
    except Exception as e:
        return f"❌ 車流查詢失敗：{e}"
# ====== 天氣查詢 ======
def get_weather(location):
    api_key = WEATHER_API_KEY
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089"
    params = {
        "Authorization": api_key,
        "format": "JSON",
        "locationName": location,
        "elementName": "MinT,MaxT,PoP12h,Wx,CI"
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        locations = data.get('records', {}).get('locations', [])[0].get('location', [])
        if not locations:
            return f"❌ {location}天氣\n\n查無此地區資料"
        weather = locations[0]
        name = weather.get('locationName', location)
        weather_elements = {
            e['elementName']: e['time'][0]['elementValue'][0]['value']
            for e in weather['weatherElement']
        }
        min_temp = weather_elements.get('MinT', '')
        max_temp = weather_elements.get('MaxT', '')
        pop = weather_elements.get('PoP12h', '')
        wx = weather_elements.get('Wx', '')
        ci = weather_elements.get('CI', '')
        return (
            f"☀️ {name}天氣\n\n"
            f"🌡️ 溫度: {min_temp}-{max_temp}°C\n"
            f"💧 降雨機率: {pop}%\n"
            f"☁️ 天氣: {wx}\n"
            f"🌡️ 舒適度: {ci}\n\n"
            f"資料來源: 中央氣象署"
        )
    except Exception as e:
        return f"❌ {location}天氣\n\n取得資料失敗"
# ====== NewsAPI 新聞查詢 ======
def get_news(keyword=""):
    api_key = NEWS_API_KEY
    url = "https://newsapi.org/v2/top-headlines"
    params = {
        "apiKey": api_key,
        "q": keyword or "台灣",
        "language": "zh",
        "pageSize": 5
    }
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if data.get("status") != "ok" or not data.get("articles"):
            return "找不到相關新聞。"
        reply = "📰 最新新聞：\n"
        for article in data["articles"]:
            reply += f"• {article['title']}\n"
            if article.get("url"):
                reply += f"{article['url']}\n"
        return reply
    except Exception as e:
        return f"❌ 新聞查詢失敗：{e}"


# ====== Google Calendar 查詢 ======
def get_google_calendar_events():
    SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
    try:
        creds_json = os.environ.get('GOOGLE_CREDS_JSON')
        if not creds_json:
            return "📅 今日行程\n\nGoogle Calendar API金鑰未設定"
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        taiwan_tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(taiwan_tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        events_result = service.events().list(
            calendarId='wjessie@gmail.com',
            timeMin=today_start.isoformat(),
            timeMax=today_end.isoformat(),
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return '📅 今日行程\n\n今日無安排行程'
        result = '📅 今日行程\n\n'
        for event in events[:5]:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', '無標題')
            if 'T' in start:
                time_part = start.split('T')[1][:5]
                result += f"• {time_part} {summary}\n"
            else:
                result += f"• 全天 {summary}\n"
        return result
    except Exception as e:
        return f"📅 今日行程\n\n行事曆資料取得失敗: {str(e)}"
# ====== 台股查詢 ======
def get_taiwan_stock_info(code):
    api_key = os.environ.get('FUGLE_API_KEY', '')
    if not api_key:
        return "❌ 富果API金鑰未設定"
    try:
        client = RestClient(api_key=api_key)
        symbol_id = "IX0001" if code == "TAIEX" else code
        quote = client.stock.intraday.quote(symbol_id=symbol_id)
        if not quote or 'data' not in quote or not quote['data']:
            return f"📈 {code}\n\n查無即時行情資料"
        info = quote['data']
        name = info.get('name', code)
        price = info.get('last', 'N/A')
        change = info.get('change', 'N/A')
        change_percent = info.get('changePercent', 'N/A')
        volume = info.get('volume', 'N/A')
        time_str = info.get('at', 'N/A')
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
        return f"📈 {code}\n\n取得行情失敗"


# ====== 美股查詢 ======
def get_us_stock_info(symbol):
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if hist.empty:
            return f"📈 美股 {symbol}\n\n無法取得即時行情"
        current_price = hist['Close'].iloc[-1]
        prev_close = hist['Open'].iloc[-1]
        change = current_price - prev_close
        change_percent = (change / prev_close) * 100 if prev_close != 0 else 0
        if change > 0:
            change_symbol = "📈"
        elif change < 0:
            change_symbol = "📉"
        else:
            change_symbol = "📊"
        return (f"{change_symbol} 美股 {symbol}\n\n"
                f"價格: ${current_price:.2f}\n"
                f"漲跌: {change:+.2f}\n"
                f"漲跌幅: {change_percent:+.2f}%")
    except ImportError:
        return f"📈 美股 {symbol}\n\nyfinance 套件未安裝"
    except Exception as e:
        return f"📈 美股 {symbol}\n\n取得資料失敗: {str(e)}"
# ====== 匯率查詢（ExchangeRate-API 免費版） ======
def get_exchange_rate():
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        res = requests.get(url, timeout=10)
        data = res.json()
        if data.get("result") != "success":
            return "❌ 匯率查詢失敗"
        rates = data["rates"]
        # 主要關注的幾個
        usd_twd = rates.get("TWD", 0)
        usd_jpy = rates.get("JPY", 0)
        usd_cny = rates.get("CNY", 0)
        usd_hkd = rates.get("HKD", 0)
        usd_gbp = rates.get("GBP", 0)
        reply = (
            f"💱 匯率\n\n"
            f"1美元 ➜ {usd_twd:.2f} 台幣\n"
            f"1美元 ➜ {usd_jpy:.2f} 日圓\n"
            f"1美元 ➜ {usd_cny:.2f} 人民幣\n"
            f"1美元 ➜ {usd_hkd:.2f} 港幣\n"
            f"1美元 ➜ {usd_gbp:.2f} 英磅"
        )
        return reply
    except Exception as e:
        return f"❌ 匯率查詢失敗: {str(e)}"


# ====== 油價查詢（使用中油官網API或抓取公開資料） ======
def get_fuel_price():
    try:
        url = "https://vipmbr.cpc.com.tw/OpenData.aspx?SN=8A2E8C1D1DF9494C"  # 中油公開油價 XML/CSV API
        res = requests.get(url, timeout=10)
        res.encoding = 'utf-8'
        text = res.text
        # 很簡單的抓法（直接字串找關鍵字）
        def extract_price(keyword):
            start = text.find(keyword)
            if start == -1:
                return "N/A"
            start = text.find(">", start) + 1
            end = text.find("<", start)
            return text[start:end].strip()
        gas92 = extract_price("Unleaded Gasoline 92")
        gas95 = extract_price("Unleaded Gasoline 95")
        gas98 = extract_price("Unleaded Gasoline 98")
        diesel = extract_price("Premium Diesel")
        return (f"⛽ 油價資訊（今日）\n\n"
                f"92無鉛汽油: {gas92} 元/公升\n"
                f"95無鉛汽油: {gas95} 元/公升\n"
                f"98無鉛汽油: {gas98} 元/公升\n"
                f"超級柴油: {diesel} 元/公升\n\n"
                f"資料來源: 台灣中油")
    except Exception as e:
        return f"❌ 油價查詢失敗: {str(e)}"
# ====== 定時推播排程 ======
SCHEDULED_MESSAGES = [
    {"time": "07:10", "message": "morning_briefing", "days": "daily"},
    {"time": "08:00", "message": "commute_to_work", "days": "weekdays"},
    {"time": "09:30", "message": "market_open", "days": "weekdays"},
    {"time": "12:00", "message": "market_mid", "days": "weekdays"},
    {"time": "13:45", "message": "market_close", "days": "weekdays"},
    {"time": "17:30", "message": "evening_zhongzheng", "days": "135"},
    {"time": "17:30", "message": "evening_xindian", "days": "24"},
    {"time": "07:30", "message": "us_market_report", "days": "23456"}  # 調整後的美股早報排程
]

# ====== 各類組合訊息函式 ======
def get_morning_briefing():
    weather = get_weather("新北市新店區")
    news = get_news()
    calendar = get_google_calendar_events()
    exchange = get_exchange_rate()
    return f"🌞 早安！\n\n{weather}\n\n{news}\n\n{calendar}\n\n{exchange}"

def get_commute_to_work():
    traffic = get_custom_traffic("家到公司")
    weather = get_weather("台北市中山區")
    return f"🚗 上班通勤\n\n{weather}\n\n{traffic}"

def get_market_open():
    return "📈 台股開盤通知（可自訂內容）"

def get_market_mid():
    return "📊 台股盤中快訊（可自訂內容）"

def get_market_close():
    return "📉 台股收盤資訊（可自訂內容）"

def get_evening_zhongzheng():
    traffic = get_custom_traffic("公司到郵局")
    weather = get_weather("台北市中正區")
    fuel_price = get_fuel_price()
    return f"🌆 下班（郵局）\n\n{weather}\n\n{traffic}\n\n{fuel_price}"

def get_evening_xindian():
    traffic = get_custom_traffic("公司到家")
    weather = get_weather("新北市新店區")
    fuel_price = get_fuel_price()
    return f"🌆 下班（返家）\n\n{weather}\n\n{traffic}\n\n{fuel_price}"

def get_us_market_report():
    symbols = ["NVDA", "TSLA", "AAPL", "GOOGL", "MSFT", "SMCI"]
    messages = []
    for symbol in symbols:
        msg = get_us_stock_info(symbol)
        messages.append(msg)
    return "🌎 美股行情報告\n\n" + "\n\n".join(messages)
# ====== 強化版 send_scheduled（自動定時推播用） ======
@app.route("/send_scheduled", methods=['GET', 'POST'])
def send_scheduled():
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        current_weekday = taiwan_time.weekday()  # 0 = Monday, 6 = Sunday
        print(f"[定時推播] 現在時間: {current_time} (週{current_weekday})")
        any_triggered = False

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
                elif schedule['days'] == '23456' and current_weekday in [1, 2, 3, 4, 5]:
                    should_send = True

                if should_send:
                    any_triggered = True
                    message_type = schedule['message']
                    message_functions = {
                        "morning_briefing": get_morning_briefing,
                        "commute_to_work": get_commute_to_work,
                        "market_open": get_market_open,
                        "market_mid": get_market_mid,
                        "market_close": get_market_close,
                        "evening_zhongzheng": get_evening_zhongzheng,
                        "evening_xindian": get_evening_xindian,
                        "us_market_report": get_us_market_report
                    }

                    print(f"[定時推播] 觸發排程: {schedule['time']} - {message_type}")
                    try:
                        message_func = message_functions.get(message_type)
                        if message_func:
                            message = message_func()
                            if not message.strip():
                                message = "⚠️ 查無資料，請確認關鍵字或稍後再試。"
                            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
                            print(f"[定時推播] 發送成功 ➜ {message_type}")
                        else:
                            print(f"[定時推播] 未知的 message_type: {message_type}")
                    except Exception as e:
                        print(f"[定時推播] 發送錯誤: {str(e)}")

        if not any_triggered:
            print(f"[定時推播] 此刻無排程觸發")
        return 'OK'
    except Exception as e:
        print(f"[定時推播] 整體錯誤: {str(e)}")
        return f"❌ 錯誤: {str(e)}"
# ====== 手動測試用 send_scheduled_test ======
@app.route("/send_scheduled_test", methods=['GET'])
def send_scheduled_test():
    try:
        test_time = request.args.get('time', '')
        if not test_time:
            return '❌ 請加參數 ?time=HH:MM，例如 ?time=07:10'

        taiwan_time = datetime.now(TAIWAN_TZ)
        current_weekday = taiwan_time.weekday()
        print(f"[手動測試] 模擬時間: {test_time} (週{current_weekday})")

        triggered = False
        for schedule in SCHEDULED_MESSAGES:
            if schedule['time'] == test_time:
                should_send = False
                if schedule['days'] == 'daily':
                    should_send = True
                elif schedule['days'] == 'weekdays' and current_weekday < 5:
                    should_send = True
                elif schedule['days'] == '135' and current_weekday in [0, 2, 4]:
                    should_send = True
                elif schedule['days'] == '24' and current_weekday in [1, 3]:
                    should_send = True
                elif schedule['days'] == '23456' and current_weekday in [1, 2, 3, 4, 5]:
                    should_send = True

                if should_send:
                    triggered = True
                    message_type = schedule['message']
                    message_functions = {
                        "morning_briefing": get_morning_briefing,
                        "commute_to_work": get_commute_to_work,
                        "market_open": get_market_open,
                        "market_mid": get_market_mid,
                        "market_close": get_market_close,
                        "evening_zhongzheng": get_evening_zhongzheng,
                        "evening_xindian": get_evening_xindian,
                        "us_market_report": get_us_market_report
                    }

                    print(f"[手動測試] 觸發排程: {schedule['time']} - {message_type}")
                    try:
                        message_func = message_functions.get(message_type)
                        if message_func:
                            message = message_func()
                            if not message.strip():
                                message = "⚠️ 查無資料，請確認關鍵字或稍後再試。"
                            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
                            print(f"[手動測試] 發送成功 ➜ {message_type}")
                            return f'✅ 手動測試發送成功 ➜ {message_type}'
                        else:
                            return f'❌ 未知的訊息類型: {message_type}'
                    except Exception as e:
                        print(f"[手動測試] 發送錯誤: {str(e)}")
                        return f'❌ 發送錯誤: {str(e)}'

        if not triggered:
            return f'⛔ 此時間 {test_time} 無排程觸發'
    except Exception as e:
        print(f"[手動測試] 整體錯誤: {str(e)}")
        return f"❌ 測試錯誤: {str(e)}"
