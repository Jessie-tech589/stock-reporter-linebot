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

app = Flask(__name__)
tz = pytz.timezone("Asia/Taipei")
scheduler = BackgroundScheduler(timezone=tz)

# 環境變數與金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY")
FUGLE_API_TOKEN = os.environ.get("FUGLE_API_TOKEN")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 自訂交通路線
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

# Google Maps 路況查詢（帶 emoji）
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
    res = requests.get(url, params=params).json()
    if res.get("status") != "OK":
        return "🚧 路況查詢失敗"
    sec = res["routes"][0]["legs"][0]["duration_in_traffic"]["value"]
    txt = res["routes"][0]["legs"][0]["duration_in_traffic"]["text"]
    color = "🟢" if sec < 1500 else "🟡" if sec < 2700 else "🔴"
    return f"{color} 預估交通時間：{txt}"

# 推播
def push_message(text):
    if LINE_USER_ID:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text))

# 保活
def keep_alive_trigger():
    print(f"[KeepAlive] {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')}")
def get_weather(location="新北市新店區"):
    url = f"https://api.openweathermap.org/data/2.5/weather?q={location}&appid={WEATHER_API_KEY}&units=metric&lang=zh_tw"
    res = requests.get(url).json()
    try:
        desc = res["weather"][0]["description"]
        temp = round(res["main"]["temp"])
        return f"🌤 {location} 天氣：{desc}，{temp}°C"
    except:
        return f"🌤 {location} 天氣查詢失敗"

def get_news():
    url = f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}"
    res = requests.get(url).json()
    try:
        articles = res["articles"][:5]
        return "📰 新聞摘要：\n" + "\n".join([f"• {a['title']}" for a in articles])
    except:
        return "📰 新聞查詢失敗"

def get_exchange_rates():
    currencies = ["USD", "JPY", "CNY", "HKD"]
    result = ["💱 匯率："]
    for cur in currencies:
        try:
            url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={cur}&to_currency=TWD&apikey={ALPHA_VANTAGE_KEY}"
            res = requests.get(url).json()
            rate = res["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
            result.append(f"{cur}/TWD：{float(rate):.2f}")
        except:
            result.append(f"{cur}/TWD：查詢失敗")
    return "\n".join(result)

def get_us_stock_summary():
    symbols = {
        "^DJI": "道瓊",
        "^GSPC": "S&P500",
        "^IXIC": "NASDAQ",
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google",
        "AAPL": "Apple"
    }
    result = ["💹 美股昨晚行情："]
    for sym, name in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d"
            res = requests.get(url).json()
            close = res["chart"]["result"][0]["indicators"]["quote"][0]["close"][0]
            result.append(f"{name}：{round(close, 2)}")
        except:
            result.append(f"{name}：查詢失敗")
    return "\n".join(result)

def push_morning_briefing():
    weather = get_weather("新北市新店區")
    news = get_news()
    rates = get_exchange_rates()
    stocks = get_us_stock_summary()
    content = f"☀️ 早安！以下是今日摘要：\n{weather}\n\n{news}\n\n{rates}\n\n{stocks}"
    push_message(content)
def get_weather(location="台北市中山區"):
    url = f"https://api.openweathermap.org/data/2.5/weather?q={location}&appid={WEATHER_API_KEY}&units=metric&lang=zh_tw"
    res = requests.get(url).json()
    try:
        desc = res["weather"][0]["description"]
        temp = round(res["main"]["temp"])
        return f"🌤 {location} 天氣：{desc}，{temp}°C"
    except:
        return f"🌤 {location} 天氣查詢失敗"

def push_commute_to_work():
    weekday = datetime.now(tz).weekday()
    if weekday >= 5:
        return
    weather = get_weather("台北市中山區")
    traffic = get_traffic_duration("家到公司")
    content = f"🚶‍♂️ 上班通勤提醒\n\n{weather}\n\n{traffic}"
    push_message(content)

def get_taiwan_stock(symbol):
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/{symbol}?apikey={FUGLE_API_TOKEN}"
    try:
        res = requests.get(url).json()
        data = res["data"]["deal"]
        price = data["price"]
        change = data["changePrice"]
        percent = data["changeRate"]
        return f"{STOCK_MAPPING[symbol]}：{price}（{change:+.2f}, {percent:+.2f}%）"
    except:
        return f"{STOCK_MAPPING.get(symbol, symbol)}：查詢失敗"

def get_all_tw_stocks():
    return "\n".join([get_taiwan_stock(sym) for sym in STOCK_MAPPING])

def push_market_open():
    content = f"📈 台股開盤\n{get_all_tw_stocks()}"
    push_message(content)

def push_market_mid():
    content = f"📊 台股盤中快訊\n{get_all_tw_stocks()}"
    push_message(content)

def push_market_close():
    content = f"📉 台股收盤資訊\n{get_all_tw_stocks()}"
    push_message(content)
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

def push_evening_commute():
    weekday = datetime.now(tz).weekday()
    if weekday in [0, 2, 4]:  # 週一三五
        label = "🏀 下班提醒（中正區打球）"
        weather = get_weather("台北市中正區")
        traffic = get_traffic_duration("公司到中正區")
    elif weekday in [1, 3]:  # 週二四
        label = "🏠 下班提醒（回新店區）"
        weather = get_weather("新北市新店區")
        traffic = get_traffic_duration("公司到新店區")
    else:
        return  # 週六日不推播

    gas = get_gas_price()
    content = f"{label}\n\n{weather}\n\n{traffic}\n\n{gas}"
    push_message(content)
def get_us_stock_price(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
        res = requests.get(url).json()
        price = res["chart"]["result"][0]["indicators"]["quote"][0]["close"][0]
        return round(price, 2)
    except:
        return None

def push_us_market_open1():
    symbols = {
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google",
        "AAPL": "Apple"
    }
    result = ["💥 美股開盤速報："]
    for sym, name in symbols.items():
        price = get_us_stock_price(sym)
        if price:
            result.append(f"{name}：{price}")
        else:
            result.append(f"{name}：查詢失敗")
    push_message("\n".join(result))

def push_us_market_open2():
    symbols = {
        "^DJI": "道瓊",
        "^GSPC": "S&P500",
        "^IXIC": "NASDAQ",
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google",
        "AAPL": "Apple"
    }
    result = ["🌙 美股行情摘要："]
    for sym, name in symbols.items():
        price = get_us_stock_price(sym)
        if price:
            result.append(f"{name}：{price}")
        else:
            result.append(f"{name}：查詢失敗")
    push_message("\n".join(result))
def schedule_all_jobs():
    scheduler.add_job(push_morning_briefing, CronTrigger(hour=7, minute=10))
    scheduler.add_job(push_commute_to_work, CronTrigger(hour=8, minute=0))
    scheduler.add_job(push_market_open, CronTrigger(hour=9, minute=30))
    scheduler.add_job(push_market_mid, CronTrigger(hour=12, minute=0))
    scheduler.add_job(push_market_close, CronTrigger(hour=13, minute=45))
    scheduler.add_job(push_evening_commute, CronTrigger(hour=17, minute=30))
    scheduler.add_job(push_us_market_open1, CronTrigger(hour=21, minute=30))
    scheduler.add_job(push_us_market_open2, CronTrigger(hour=23, minute=0))
    scheduler.add_job(keep_alive_trigger, CronTrigger(minute="0,10,20,30,40,50"))
    scheduler.start()

@app.route("/send_scheduled_test", methods=["GET"])
def send_scheduled_test():
    time_str = request.args.get("time")
    if not time_str:
        return "請提供 time 參數，例如 /send_scheduled_test?time=07:10"
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
        return f"已觸發模擬排程 {time_str}"
    return "❌ 時間不正確或未定義"

@app.route("/health", methods=["GET"])
def health_check():
    return "✅ LineBot 正常運作中"

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
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
        push_message("🤖 指令錯誤，可輸入「測試」")

if __name__ == "__main__":
    schedule_all_jobs()
    app.run()
