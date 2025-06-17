import os
import json
import requests
from datetime import datetime, timedelta
import pytz

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# 初始化 Flask 與排程
app = Flask(__name__)
scheduler = BackgroundScheduler()
tz = pytz.timezone("Asia/Taipei")

# API 金鑰與用戶 ID
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
LINE_USER_ID = os.environ.get('LINE_USER_ID')
WEATHER_API_KEY = os.environ.get('WEATHER_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY')
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')
ALPHA_VANTAGE_API_KEY = os.environ.get('ALPHA_VANTAGE_API_KEY')
FUGLE_API_TOKEN = os.environ.get('FUGLE_API_TOKEN')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
user_id = LINE_USER_ID

# 台股股票對照表（依使用者指定）
STOCK_MAPPING = {
    "2330.TW": "台積電",
    "2303.TW": "聯電",
    "2354.TW": "鴻準",
    "2609.TW": "陽明",
    "2324.TW": "仁寶",
    "2610.TW": "華航",
    "2618.TW": "長榮航"
}
# 自訂通勤路線設定（固定順序）
TRAFFIC_ROUTES = {
    "家到公司": {
        "origin": "新北市新店區建國路99巷",
        "destination": "台北市中山區南京東路三段131號",
        "waypoints": "新北市新店區民族路|新北市新店區北新路|台北市中正區羅斯福路|台北市大安區基隆路|台北市大安區辛亥路|台北市大安區復興南路"
    },
    "公司到中正區": {
        "origin": "台北市中山區南京東路三段131號", 
        "destination": "台北市中正區愛國東路216號",
        "waypoints": "台北市大安區復興南路|台北市大安區信義路"
    },
    "公司到新店區": {
        "origin": "台北市中山區南京東路三段131號",
        "destination": "新北市新店區建國路99巷", 
        "waypoints": "台北市大安區復興南路|台北市大安區辛亥路|台北市大安區基隆路|台北市中正區羅斯福路|新北市新店區北新路|新北市新店區民族路"
    }
}

def get_traffic_duration(route_key):
    route = TRAFFIC_ROUTES.get(route_key)
    if not route:
        return "❌ 找不到路線"
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": route["origin"],
        "destination": route["destination"],
        "waypoints": f"optimize:true|{route['waypoints']}",
        "departure_time": "now",
        "traffic_model": "best_guess",
        "key": GOOGLE_MAPS_API_KEY,
        "language": "zh-TW"
    }
    response = requests.get(url, params=params).json()
    if response.get("status") != "OK":
        return "🚧 路況查詢失敗"
    sec = response["routes"][0]["legs"][0]["duration_in_traffic"]["value"]
    text = response["routes"][0]["legs"][0]["duration_in_traffic"]["text"]
    emoji = "🟢" if sec < 1500 else "🟡" if sec < 2700 else "🔴"
    return f"{emoji} 預估交通時間：{text}"

def get_weather(location):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={WEATHER_API_KEY}&units=metric&lang=zh_tw"
    res = requests.get(url).json()
    if res.get("cod") != 200:
        return "☁️ 天氣查詢失敗"
    desc = res["weather"][0]["description"]
    temp = res["main"]["temp"]
    return f"🌤 {location} 天氣：{desc}，氣溫 {temp}°C"

def get_news():
    url = f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}"
    res = requests.get(url).json()
    if res.get("status") != "ok":
        return "📰 新聞查詢失敗"
    headlines = [a["title"] for a in res["articles"][:3]]
    return "📰 今日新聞：\n" + "\n".join(f"- {t}" for t in headlines)

def get_fx_rates():
    symbols = {"USD": "美金", "JPY": "日圓", "CNY": "人民幣", "HKD": "港幣"}
    url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={{}}&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}"
    results = []
    for code, name in symbols.items():
        res = requests.get(url.format(code)).json()
        try:
            rate = float(res["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
            results.append(f"{name}：{round(rate, 2)}")
        except:
            results.append(f"{name}：查詢失敗")
    return "💱 匯率資訊：\n" + "\n".join(results)

def get_gas_price():
    try:
        url = "https://vipmbr.cpc.com.tw/OpenData.aspx?type=fpios"
        res = requests.get(url).text
        lines = res.split("\n")
        today_line = [l for l in lines if "92無鉛" in l]
        if not today_line:
            return "⛽ 油價查詢失敗"
        parts = today_line[0].split(",")
        return f"⛽ 油價：92無鉛 {parts[1]} 元／公升"
    except:
        return "⛽ 油價查詢失敗"

# 行事曆（可選）這裡先略過，保留空位
def get_calendar_summary():
    return ""  # 後續可整合 Google Calendar API
def push_message(text):
    line_bot_api.push_message(user_id, TextSendMessage(text=text))

def get_taiwan_stock(code, name):
    try:
        url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote?symbolId={code}"
        headers = {"X-API-KEY": FUGLE_API_TOKEN}
        res = requests.get(url, headers=headers).json()
        price = res["data"]["lastDone"]
        change = res["data"]["change"]
        return f"{name}：{price}（{change}）"
    except:
        return f"{name}：查詢失敗"

def push_morning_briefing():
    weather = get_weather("新北市新店區")
    news = get_news()
    fx = get_fx_rates()
    calendar = get_calendar_summary()

    # 前一晚美股行情
    us_index = {
        "^DJI": "道瓊",
        "^GSPC": "S&P500",
        "^IXIC": "NASDAQ",
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google"
    }
    us_prices = []
    for code, name in us_index.items():
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}?range=1d&interval=1d"
        try:
            res = requests.get(url).json()
            price = res["chart"]["result"][0]["indicators"]["quote"][0]["close"][0]
            us_prices.append(f"{name}：{round(price, 2)}")
        except:
            us_prices.append(f"{name}：查詢失敗")

    message = f"🌅 早安！\n{weather}\n\n{news}\n\n{fx}\n\n📈 昨日美股：\n" + "\n".join(us_prices)
    if calendar:
        message += f"\n\n🗓 今日行程：\n{calendar}"
    push_message(message)

def push_commute_to_work():
    weekday = datetime.now(tz).weekday()
    if weekday >= 5:
        return
    weather = get_weather("台北市中山區")
    traffic = get_traffic_duration("家到公司")
    message = f"🚶‍♂️ 上班通勤提醒\n{weather}\n{traffic}"
    push_message(message)

def push_market_open():
    weekday = datetime.now(tz).weekday()
    if weekday >= 5:
        return
    stocks = [get_taiwan_stock(code, name) for code, name in STOCK_MAPPING.items()]
    message = "📢 台股開盤通知：\n" + "\n".join(stocks)
    push_message(message)

def push_market_mid():
    weekday = datetime.now(tz).weekday()
    if weekday >= 5:
        return
    stocks = [get_taiwan_stock(code, name) for code, name in STOCK_MAPPING.items()]
    message = "📊 台股盤中快訊：\n" + "\n".join(stocks)
    push_message(message)

def push_market_close():
    weekday = datetime.now(tz).weekday()
    if weekday >= 5:
        return
    stocks = [get_taiwan_stock(code, name) for code, name in STOCK_MAPPING.items()]
    message = "📉 台股收盤資訊：\n" + "\n".join(stocks)
    push_message(message)
def push_evening_commute():
    weekday = datetime.now(tz).weekday()
    if weekday in [0, 2, 4]:
        # 中正區推播（週一三五）
        weather = get_weather("台北市中正區")
        traffic = get_traffic_duration("公司到中正區")
        oil = get_gas_price()
        label = "🏀 中正區打球提醒"
    elif weekday in [1, 3]:
        # 新店區推播（週二四）
        weather = get_weather("新北市新店區")
        traffic = get_traffic_duration("公司到新店區")
        oil = get_gas_price()
        label = "🏠 下班回家提醒"
    else:
        return  # 週六日不推播

    message = f"{label}\n{weather}\n{traffic}\n{oil}"
    push_message(message)
def get_us_stock(code, name):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}?range=1d&interval=1m"
        res = requests.get(url).json()
        result = res["chart"]["result"][0]
        close = result["indicators"]["quote"][0]["close"]
        price = close[-1]
        return f"{name}：{round(price, 2)}"
    except:
        return f"{name}：查詢失敗"

def push_us_market_open1():
    codes = {
        "^DJI": "道瓊",
        "^GSPC": "S&P500",
        "^IXIC": "NASDAQ",
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google",
        "AAPL": "蘋果"
    }
    info = [get_us_stock(code, name) for code, name in codes.items()]
    message = "🕘 美股開盤速報（第一波）：\n" + "\n".join(info)
    push_message(message)

def push_us_market_open2():
    codes = {
        "^DJI": "道瓊",
        "^GSPC": "S&P500",
        "^IXIC": "NASDAQ",
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google",
        "AAPL": "蘋果"
    }
    info = [get_us_stock(code, name) for code, name in codes.items()]
    message = "📈 美股行情摘要（第二波）：\n" + "\n".join(info)
    push_message(message)
def keep_alive_trigger():
    print(f"✅ 保活排程觸發：{datetime.now(tz)}")

def schedule_all_jobs():
    scheduler.add_job(push_morning_briefing, CronTrigger(hour=7, minute=10))
    scheduler.add_job(push_commute_to_work, CronTrigger(hour=8, minute=0))
    scheduler.add_job(push_market_open, CronTrigger(hour=9, minute=30))
    scheduler.add_job(push_market_mid, CronTrigger(hour=12, minute=0))
    scheduler.add_job(push_market_close, CronTrigger(hour=13, minute=45))
    scheduler.add_job(push_evening_commute, CronTrigger(hour=17, minute=30))
    scheduler.add_job(push_us_market_open1, CronTrigger(hour=21, minute=30))
    scheduler.add_job(push_us_market_open2, CronTrigger(hour=23, minute=0))
    scheduler.add_job(keep_alive_trigger, CronTrigger(minute="0,10,20,30,40,45,50"))
    scheduler.start()

@app.route("/send_scheduled", methods=["GET"])
def send_scheduled_test():
    time_str = request.args.get("time")
    test_map = {
        "07:10": push_morning_briefing,
        "08:00": push_commute_to_work,
        "09:30": push_market_open,
        "12:00": push_market_mid,
        "13:45": push_market_close,
        "17:30": push_evening_commute,
        "21:30": push_us_market_open1,
        "23:00": push_us_market_open2
    }
    fn = test_map.get(time_str)
    if fn:
        fn()
        return f"✅ 已模擬觸發排程 {time_str}"
    return "❌ 無對應排程時間"

@app.route("/health", methods=["GET"])
def health_check():
    return "✅ LineBot 正常運作中"

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    if msg == "測試":
        push_message("✅ Bot 正常運作中")
    else:
        push_message("🤖 指令錯誤，請輸入「測試」")

if __name__ == "__main__":
    schedule_all_jobs()
    app.run()
