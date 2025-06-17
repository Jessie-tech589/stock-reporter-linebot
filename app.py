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

# 初始化
app = Flask(__name__)
scheduler = BackgroundScheduler()
tz = pytz.timezone("Asia/Taipei")

# 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY")
FUGLE_API_TOKEN = os.environ.get("FUGLE_API_TOKEN")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
user_id = LINE_USER_ID

# 台股對照表（照你指定）
STOCK_MAPPING = {
    "2330.TW": "台積電",
    "2303.TW": "聯電",
    "2354.TW": "鴻準",
    "2609.TW": "陽明",
    "2324.TW": "仁寶",
    "2610.TW": "華航",
    "2618.TW": "長榮航"
}
# 自定義交通路線（照你指定）
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
    try:
        res = requests.get(url, params=params).json()
        sec = res["routes"][0]["legs"][0]["duration_in_traffic"]["value"]
        text = res["routes"][0]["legs"][0]["duration_in_traffic"]["text"]
        emoji = "🟢" if sec < 1500 else "🟡" if sec < 2700 else "🔴"
        return f"{emoji} 預估交通時間：{text}"
    except:
        return "🚧 路況查詢失敗"

def get_weather(location):
    url = f"http://api.openweathermap.org/data/2.5/weather"
    params = {"q": location, "appid": WEATHER_API_KEY, "units": "metric", "lang": "zh_tw"}
    try:
        res = requests.get(url, params=params).json()
        desc = res["weather"][0]["description"]
        temp = res["main"]["temp"]
        return f"🌤 {location}：{desc}，{round(temp)}°C"
    except:
        return f"{location} 天氣查詢失敗"

def get_news():
    url = f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}"
    try:
        res = requests.get(url).json()
        articles = res["articles"][:3]
        return "📰 今日新聞：\n" + "\n".join([f"- {a['title']}" for a in articles])
    except:
        return "新聞查詢失敗"

def get_fx_rates():
    symbols = {"USD": "美元", "JPY": "日圓", "CNY": "人民幣", "HKD": "港幣"}
    result = []
    try:
        res = requests.get(f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}").json()
        usd = round(float(res["Realtime Currency Exchange Rate"]["5. Exchange Rate"]), 2)
        result.append(f"美元：{usd}")
        for code in ["JPY", "CNY", "HKD"]:
            res = requests.get(f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={code}&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}").json()
            rate = round(float(res["Realtime Currency Exchange Rate"]["5. Exchange Rate"]), 2)
            result.append(f"{symbols[code]}：{rate}")
        return "💱 匯率：\n" + "\n".join(result)
    except:
        return "匯率查詢失敗"

def get_gas_price():
    try:
        res = requests.get("https://vipmbr.cpc.com.tw/OpenData.aspx?SN=8A2E8F0E8B27415D").text
        data = res.strip().split(",")
        return f"⛽ 油價（今日）\n92：{data[1]} 元，95：{data[2]} 元，98：{data[3]} 元"
    except:
        return "油價查詢失敗"

def get_calendar_summary():
    try:
        # 假設你已處理好 Google Calendar Token 驗證（略）
        return ""  # 或簡單回傳今日行程摘要
    except:
        return ""
def push_message(text):
    line_bot_api.push_message(user_id, TextSendMessage(text=text))

def get_tw_stock(code, name):
    url = f"https://api.fugle.tw/realtime/v0.3/intraday/quote?symbolId={code}"
    headers = {"X-API-KEY": FUGLE_API_TOKEN}
    try:
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

    # 昨日美股行情
    us_codes = {
        "^DJI": "道瓊",
        "^GSPC": "S&P500",
        "^IXIC": "NASDAQ",
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google",
        "AAPL": "蘋果"
    }
    us_summary = [get_us_stock(code, name) for code, name in us_codes.items()]
    us_msg = "\n".join(us_summary)

    message = f"🌅 早安！\n{weather}\n\n{news}\n\n📅 今日行程：\n{calendar}\n\n{fx}\n\n📊 昨日美股行情：\n{us_msg}"
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
    stocks = [get_tw_stock(code, name) for code, name in STOCK_MAPPING.items()]
    message = "📈 台股開盤資訊（09:30）\n" + "\n".join(stocks)
    push_message(message)

def push_market_mid():
    weekday = datetime.now(tz).weekday()
    if weekday >= 5:
        return
    stocks = [get_tw_stock(code, name) for code, name in STOCK_MAPPING.items()]
    message = "⏰ 台股盤中快訊（12:00）\n" + "\n".join(stocks)
    push_message(message)

def push_market_close():
    weekday = datetime.now(tz).weekday()
    if weekday >= 5:
        return
    stocks = [get_tw_stock(code, name) for code, name in STOCK_MAPPING.items()]
    message = "🔔 台股收盤資訊（13:45）\n" + "\n".join(stocks)
    push_message(message)
def push_evening_commute():
    weekday = datetime.now(tz).weekday()

    if weekday in [0, 2, 4]:  # 週一三五 → 中正區打球
        weather = get_weather("台北市中正區")
        traffic = get_traffic_duration("公司到中正區")
        label = "🏀 打球提醒（中正區）"
    elif weekday in [1, 3]:  # 週二四 → 回新店
        weather = get_weather("新北市新店區")
        traffic = get_traffic_duration("公司到新店區")
        label = "🏠 下班回家提醒（新店區）"
    else:
        return

    gas = get_gas_price()
    message = f"{label}\n{weather}\n{gas}\n{traffic}"
    push_message(message)
def get_us_stock(symbol, name):
    try:
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
        res = requests.get(url).json()
        data = res["quoteResponse"]["result"][0]
        price = data.get("regularMarketPrice", 0)
        change = data.get("regularMarketChange", 0)
        percent = data.get("regularMarketChangePercent", 0)
        return f"{name}：{price}（{change:+.2f}, {percent:+.2f}％）"
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
    message = "🗽 美股開盤速報（21:30）\n" + "\n".join(info)
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
    message = "🌃 美股行情摘要（23:00）\n" + "\n".join(info)
    push_message(message)
@app.route("/send_scheduled_test", methods=["GET"])
def send_scheduled_test():
    time_str = request.args.get("time")
    if time_str == "07:10":
        push_morning_briefing()
    elif time_str == "08:00":
        push_commute_to_work()
    elif time_str == "09:30":
        push_market_open()
    elif time_str == "12:00":
        push_market_mid()
    elif time_str == "13:45":
        push_market_close()
    elif time_str == "17:30":
        push_evening_commute()
    elif time_str == "21:30":
        push_us_market_open1()
    elif time_str == "23:00":
        push_us_market_open2()
    else:
        return "❌ 不支援的時間", 400
    return f"✅ 已發送 {time_str} 推播"

# 別名：正式版路由
@app.route("/send_scheduled", methods=["GET"])
def send_scheduled():
    return send_scheduled_test()

# 保活排程（每10分鐘）
def keep_alive_trigger():
    print(f"⏰ keep-alive @ {datetime.now(tz).strftime('%H:%M:%S')}")

# 註冊所有排程
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

# Webhook for LINE
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
    if msg == "油價":
        push_message(get_gas_price())
    elif msg == "新聞":
        push_message(get_news())
    elif msg == "天氣":
        push_message(get_weather("新北市新店區"))
    elif msg == "匯率":
        push_message(get_fx_rates())
    else:
        push_message("請輸入：油價、新聞、天氣、匯率")

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    app.run()
