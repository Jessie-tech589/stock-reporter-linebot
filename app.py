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
from fugle_marketdata import RestClient
import time

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
        "waypoints": [
            "新北市 民族路", "新北市 北新路", "台北市 羅斯福路",
            "台北市 基隆路", "台北市 辛亥路", "台北市 復興南路"
        ]
    },
    "公司到家": {
        "origin": "台北市中山區南京東路三段131號",
        "destination": "新北市新店區建國路",
        "waypoints": [
            "台北市 復興南路", "台北市 辛亥路", "台北市 基隆路",
            "台北市 羅斯福路", "新北市 北新路", "新北市 民族路"
        ]
    },
    "公司到郵局": {
        "origin": "台北市中山區南京東路三段131號",
        "destination": "台北市中正區愛國東路216號",
        "waypoints": ["林森北路", "林森南路", "信義路二段10巷", "愛國東路21巷"]
    }
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
        weather_elements = {e['elementName']: e['time'][0]['elementValue'][0]['value'] for e in weather['weatherElement']}
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
# ====== 匯率查詢 ======
def get_exchange_rate():
    try:
        url = "https://tw.rter.info/capi.php"
        res = requests.get(url, timeout=10)
        data = res.json()
        pairs = {
            "USD": "USD/TWD",
            "JPY": "JPY/TWD",
            "CNY": "CNY/TWD",
            "HKD": "HKD/TWD",
            "GBP": "GBP/TWD"
        }
        reply = "💱 匯率資訊 (1單位兌換新台幣)：\n"
        for k, v in pairs.items():
            rate = data.get(v, {}).get("Exrate")
            if rate:
                reply += f"• {k} ➜ {rate:.2f} TWD\n"
            else:
                reply += f"• {k} ➜ ❌ 無資料\n"
        reply += "\n資料來源: https://www.rter.info"
        return reply
    except Exception as e:
        return f"❌ 匯率查詢失敗：{str(e)}"

# ====== 油價查詢 ======
def get_gasoline_price():
    try:
        url = "https://vipmember.tmtd.cpc.com.tw/OpenData.aspx?ids=9&mime=csv"
        res = requests.get(url, timeout=10)
        content = res.content.decode("utf-8")
        lines = content.split("\n")
        reply = "⛽ 最新油價：\n"
        for line in lines[1:]:
            fields = line.strip().split(",")
            if len(fields) < 5:
                continue
            prod, price = fields[1], fields[2]
            reply += f"• {prod} ➜ {price} 元\n"
        reply += "\n資料來源: 中油"
        return reply
    except Exception as e:
        return f"❌ 油價查詢失敗：{str(e)}"

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
# ====== 組合訊息 ======
def get_morning_briefing():
    weather = get_weather("新北市新店區")
    news = get_news()
    calendar = get_google_calendar_events()
    return f"🌞 早安！\n\n{weather}\n\n{news}\n\n{calendar}"

def get_commute_to_work():
    traffic = get_custom_traffic("家到公司")
    weather = get_weather("台北市中山區")
    exchange = get_exchange_rate()
    return f"🚗 上班通勤\n\n{weather}\n\n{traffic}\n\n{exchange}"

def get_market_open():
    return "📈 台股開盤通知（可自訂內容）"

def get_market_mid():
    return "📊 台股盤中快訊（可自訂內容）"

def get_market_close():
    return "📉 台股收盤資訊（可自訂內容）"

def get_evening_zhongzheng():
    traffic = get_custom_traffic("公司到郵局")
    weather = get_weather("台北市中正區")
    exchange = get_exchange_rate()
    gasoline = get_gasoline_price()
    return f"🌆 下班（郵局）\n\n{weather}\n\n{traffic}\n\n{exchange}\n\n{gasoline}"

def get_evening_xindian():
    traffic = get_custom_traffic("公司到家")
    weather = get_weather("新北市新店區")
    exchange = get_exchange_rate()
    gasoline = get_gasoline_price()
    return f"🌆 下班（返家）\n\n{weather}\n\n{traffic}\n\n{exchange}\n\n{gasoline}"

def get_us_market_report():
    symbols = ["NVDA", "TSLA", "AAPL", "GOOGL", "MSFT", "SMCI"]
    messages = []
    for symbol in symbols:
        msg = get_us_stock_info(symbol)
        messages.append(msg)
    exchange = get_exchange_rate()
    return "🌎 美股行情報告\n\n" + "\n\n".join(messages) + f"\n\n{exchange}"

# ====== 強化版 send_scheduled ======
@app.route("/send_scheduled", methods=['GET', 'POST'])
def send_scheduled():
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        current_weekday = taiwan_time.weekday()
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
                            if not message or message.strip() == "":
                                message = "⚠️ 查無資料，請確認關鍵字或稍後再試。"
                            try:
                                line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
                                print(f"[定時推播] 發送成功 ➜ {message_type}")
                            except Exception as e:
                                print(f"[定時推播] 發送失敗 ➜ {message_type}: {str(e)}")
                        else:
                            print(f"[定時推播] 未知的 message_type: {message_type}")
                    except Exception as e:
                        print(f"[定時推播] 處理 {message_type} 發生錯誤: {str(e)}")
        if not any_triggered:
            print(f"[定時推播] 此刻無排程觸發")
        return 'OK'
    except Exception as e:
        print(f"[定時推播] 整體錯誤: {str(e)}")
        return f"❌ 錯誤: {str(e)}"
# ====== LINE webhook & 指令處理 ======
@app.route("/", methods=['GET'])
def home():
    return "Line Bot is running!"

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
    msg = event.message.text.strip()
    reply = ""
    if msg in CUSTOM_ROUTES:
        reply = get_custom_traffic(msg)
    elif msg.startswith("天氣"):
        location = msg.replace("天氣", "").strip()
        if not location:
            location = "臺北市"
        reply = get_weather(location)
    elif msg.startswith("新聞"):
        keyword = msg.replace("新聞", "").strip()
        reply = get_news(keyword)
    elif msg.startswith("台股 "):
        name = msg.split(" ")[1].strip()
        code = stock_name_map.get(name, name)
        reply = get_taiwan_stock_info(code)
    elif msg.startswith("美股 "):
        name = msg.split(" ")[1].strip().lower()
        symbol = us_stock_name_map.get(name, name.upper())
        reply = get_us_stock_info(symbol)
    elif msg == "行事曆":
        reply = get_google_calendar_events()
    elif msg == "匯率":
        reply = get_exchange_rate()
    elif msg == "油價":
        reply = get_gasoline_price()
    else:
        reply = (
            "👋 功能：\n"
            "• 「家到公司」「公司到家」「公司到郵局」查詢機車路線\n"
            "• 「天氣區名」「新聞關鍵字」\n"
            "• 「台股 名稱」/「美股 名稱」查即時股價\n"
            "• 「匯率」/「油價」查詢\n"
            "• 「行事曆」查今日Google行程\n"
            "\n⏰ 早中晚有自動推播"
        )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
