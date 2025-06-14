import os
import requests
import time
import json
import pytz
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
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
# ====== 新聞查詢 ======
def get_news():
    try:
        res = requests.get("https://udn.com/rssfeed/news/2/6638?ch=news", timeout=10)
        root = ET.fromstring(res.content)
        items = root.findall(".//item")
        reply = "📰 即時財經新聞\n\n"
        for item in items[:5]:
            title = item.find("title").text
            link = item.find("link").text
            reply += f"🔹 {title}\n{link}\n\n"
        return reply
    except Exception as e:
        return f"❌ 新聞取得失敗: {e}"

# ====== 行事曆查詢 ======
def get_calendar_events():
    try:
        SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
        creds = None
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(calendarId='primary', timeMin=now,
                                              maxResults=5, singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])
        if not events:
            return "📅 行事曆\n\n今天沒有預定行程"
        reply = "📅 行事曆\n\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            reply += f"🔹 {start[:16]} - {event['summary']}\n"
        return reply
    except Exception as e:
        return f"❌ 行事曆取得失敗: {e}"

# ====== 台股查詢 ======
def get_stock_price_tw(symbol):
    try:
        api = MarketData(token=FUGLE_API_TOKEN)
        data = api.intraday.quote(symbol=symbol)
        info = data["data"]["quote"]
        name = info["nameZh"]
        price = info["price"]["last"]
        change = info["change"]
        percent = info["changePercent"]
        sign = "📈" if change > 0 else "📉" if change < 0 else "📊"
        return (f"{sign} {name} ({symbol})\n"
                f"價格: {price:.2f}\n"
                f"漲跌: {change:+.2f}\n"
                f"漲跌幅: {percent:+.2f}%")
    except Exception as e:
        return f"❌ 台股查詢失敗: {e}"

# ====== 美股查詢 ======
def get_stock_price_us(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if hist.empty:
            return f"❌ 無法取得 {symbol} 資料"
        current = hist['Close'].iloc[-1]
        open_price = hist['Open'].iloc[-1]
        change = current - open_price
        percent = (change / open_price) * 100 if open_price else 0
        sign = "📈" if change > 0 else "📉" if change < 0 else "📊"
        return (f"{sign} {symbol}\n"
                f"價格: ${current:.2f}\n"
                f"漲跌: {change:+.2f}\n"
                f"漲跌幅: {percent:+.2f}%")
    except Exception as e:
        return f"❌ 美股查詢失敗: {e}"
# ====== 匯率查詢 ======
def get_exchange_rates():
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data["result"] != "success":
            return "❌ 匯率資料讀取失敗"
        rates = data["rates"]
        reply = "💱 匯率資訊 (以 1 單位外幣兌台幣)\n\n"
        currency_map = {
            "USD": "美元",
            "JPY": "日圓",
            "CNY": "人民幣",
            "HKD": "港幣",
            "GBP": "英鎊"
        }
        for code, name in currency_map.items():
            rate = rates.get("TWD") / rates.get(code)
            reply += f"🔸 {name} ({code}): {rate:.2f} TWD\n"
        return reply
    except Exception as e:
        return f"❌ 匯率查詢失敗: {e}"

# ====== 油價查詢 ======
def get_gasoline_price():
    try:
        url = "https://ethanlin.me/api/oil_tw"
        response = requests.get(url, timeout=10)
        data = response.json()
        prices = data["data"]
        reply = "⛽ 油價資訊 (台灣中油)\n\n"
        reply += f"92無鉛: {prices['gasoline_92']} 元/公升\n"
        reply += f"95無鉛: {prices['gasoline_95']} 元/公升\n"
        reply += f"98無鉛: {prices['gasoline_98']} 元/公升\n"
        reply += f"柴油: {prices['diesel']} 元/公升\n"
        return reply
    except Exception as e:
        return f"❌ 油價查詢失敗: {e}"

# ====== 用戶自訂美股名稱對應表 ======
us_stock_name_map = {
    "輝達": "NVDA",
    "蘋果": "AAPL",
    "谷歌": "GOOGL",
    "微軟": "MSFT",
    "特斯拉": "TSLA",
    "超微": "AMD",
    "超微電腦": "SMCI"
}
# ====== 處理 LINE Bot 訊息回應 ======
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        name = event.message.text.strip()
        lower_name = name.lower()

        if lower_name in ["hi", "你好", "哈囉", "安安"]:
            reply = "👋 哈囉，有什麼需要查詢的嗎？\n\n📊 股票\n🌏 匯率\n⛽ 油價\n☁️ 天氣\n📆 行事曆\n🗞️ 新聞"
        elif "天氣" in name:
            reply = get_weather()
        elif "行事曆" in name:
            reply = get_calendar()
        elif "新聞" in name:
            reply = get_news()
        elif "匯率" in name:
            reply = get_exchange_rates()
        elif "油價" in name:
            reply = get_gasoline_price()
        elif "美股" in name:
            reply = get_us_market_open()
        else:
            symbol = us_stock_name_map.get(name, name.upper())
            reply = get_stock_info(symbol)

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 查詢錯誤: {e}"))

# ====== 測試 API (手動觸發定時推播) ======
@app.route("/send_scheduled_test", methods=["GET"])
def send_scheduled_test():
    return send_scheduled()

# ====== 啟動應用程式 ======
if __name__ == "__main__":
    from apscheduler.schedulers.background import BackgroundScheduler
    import pytz

    scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Taipei"))

    # 定時排程推播 (每十分鐘一次以防止 render 休眠)
    scheduler.add_job(send_scheduled, "cron", minute="0,10,20,30,40,50")

    scheduler.start()
    app.run(host="0.0.0.0", port=10000)
