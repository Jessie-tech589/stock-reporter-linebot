import os
import requests
import yfinance as yf
from datetime import datetime, timedelta
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "dummy")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

STOCK_MAPPING = {
    "輝達": "NVDA", "美超微": "SMCI", "google": "GOOGL", "谷歌": "GOOGL",
    "蘋果": "AAPL", "特斯拉": "TSLA", "微軟": "MSFT",
    "台積電": "2330.TW", "聯電": "2303.TW", "鴻準": "2354.TW",
    "00918": "00918.TW", "00878": "00878.TW", "元大美債20年": "00679B.TW",
    "群益25年美債": "00723B.TW", "仁寶": "2324.TW", "陽明": "2609.TW",
    "華航": "2610.TW", "長榮航": "2618.TW", "大盤": "^TWII",
    "2330": "2330.TW", "2303": "2303.TW", "2354": "2354.TW",
    "2324": "2324.TW", "2609": "2609.TW", "2610": "2610.TW", "2618": "2618.TW"
}

STOCK_LIST = ["台積電", "聯電", "鴻準", "00918", "00878", "元大美債20年", "群益25年美債", "仁寶", "陽明", "華航", "長榮航"]

def get_weather(location):
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={WEATHER_API_KEY}&lang=zh_tw&units=metric"
        res = requests.get(url, timeout=10).json()
        temp = res["main"]["temp"]
        desc = res["weather"][0]["description"]
        humidity = res["main"]["humidity"]
        wind = res["wind"]["speed"]
        return f"🌤️ {location} 天氣：{desc}\n🌡️ 溫度：{temp}°C\n💧 濕度：{humidity}%\n💨 風速：{wind} m/s"
    except Exception as e:
        return f"❌ 天氣錯誤：{e}"

def get_traffic(label):
    try:
        # 使用 waypoints 自訂路線，完整市區名稱
        route_waypoints = {
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
            },
            "公司到郵局": {
                "origin": "台北市中山區南京東路三段131號",
                "destination": "台北市中正區愛國東路21巷",
                "waypoints": "台北市中山區林森北路|台北市中正區信義路|台北市中正區信義路二段10巷"
            }
        }
        
        if label not in route_waypoints:
            return "❌ 未知路線"
            
        route = route_waypoints[label]
        origin = route["origin"]
        destination = route["destination"] 
        waypoints = route["waypoints"]
        
        url = (
            f"https://maps.googleapis.com/maps/api/directions/json"
            f"?origin={origin}&destination={destination}&waypoints={waypoints}"
            f"&departure_time=now&mode=driving&key={GOOGLE_MAPS_API_KEY}"
        )
        
        res = requests.get(url, timeout=10).json()
        if not res.get("routes"):
            return f"❌ 找不到路線"
            
        route_info = res["routes"][0]
        total_duration = sum([l.get("duration_in_traffic", l["duration"])["value"] for l in route_info["legs"]])
        total_normal = sum([l["duration"]["value"] for l in route_info["legs"]])
        
        # 計算總時間
        total_duration_text = f"{total_duration // 60} 分鐘"
        
        # 路況燈號
        ratio = total_duration / total_normal if total_normal else 1
        if ratio > 1.25:
            light = "🔴 壅塞"
        elif ratio > 1.05:
            light = "🟡 緩慢" 
        else:
            light = "🟢 順暢"
            
        return (
            f"🚗 自訂路線：{label}\n"
            f"🛤️ 路徑：{origin} → {destination}\n"
            f"🚦 路況：{light}\n"
            f"⏰ 預估時間：{total_duration_text}"
        )
        
    except Exception as e:
        return f"❌ 路況錯誤：{e}"

def get_route_info(label):
    """顯示妳指定的機車路跡（純資訊顯示）"""
    routes = {
        "家到公司": "🏠→🏢 機車路跡：\n新北市新店區建國路 → 新北市新店區民族路 → 新北市新店區北新路 → 台北市羅斯福路 → 台北市基隆路 → 台北市辛亥路 → 台北市復興南路 → 台北市南京東路 → 公司",
        "公司到郵局": "🏢→📮 機車路跡：\n台北市南京東路 → 台北市林森北路 → 台北市信義路 → 台北市信義路二段10巷 → 台北市愛國東路21巷 → 金南郵局",
        "公司到新店區": "🏢→🏠 機車路跡：\n台北市南京東路 → 台北市復興南路 → 台北市辛亥路 → 台北市基隆路 → 台北市羅斯福路 → 新北市新店區北新路 → 新北市新店區民族路 → 新北市新店區建國路 → 家",
        "公司到中正區": "🏢→🏸 機車路跡：\n台北市南京東路 → 台北市復興南路 → 台北市信義路 → 台北市中正區愛國東路216號"
    }
    return routes.get(label, "❌ 路跡不存在")

def get_news():
    try:
        url = f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}"
        data = requests.get(url, timeout=10).json()
        articles = data.get("articles", [])[:3]
        if not articles:
            return "📭 今日無新聞"
        return "\n".join([f"• {a['title']}" for a in articles])
    except Exception as e:
        return f"❌ 新聞錯誤：{e}"

def get_exchange_rates():
    try:
        url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}"
        data = requests.get(url, timeout=10).json()
        rate = data["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
        return f"💵 美元匯率：1 USD ≒ {float(rate):.2f} TWD"
    except Exception as e:
        return f"❌ 匯率錯誤：{e}"

def get_stock_data(query):
    try:
        symbol = STOCK_MAPPING.get(query, query)
        stock = yf.Ticker(symbol)
        hist = stock.history(period="2d")
        if hist.empty:
            return f"❌ 找不到 {query} 的股價資料"
        today = hist.iloc[-1]
        yesterday = hist.iloc[-2] if len(hist) > 1 else today
        price = today['Close']
        diff = price - yesterday['Close']
        pct = (diff / yesterday['Close']) * 100 if yesterday['Close'] != 0 else 0
        emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        return f"{emoji} {query}（{symbol}）\n💰 {price:.2f}\n{diff:+.2f} ({pct:+.2f}%)"
    except Exception as e:
        return f"❌ 股價查詢錯誤：{e}"

def get_oil_price():
    try:
        url = "https://oil-price-api.vercel.app/api/taiwan/latest"
        res = requests.get(url, timeout=5)
        if res.status_code != 200:
            return "❌ 油價資料錯誤"
        data = res.json().get("prices", {})
        if not data:
            return "❌ 油價資料為空"
        return "⛽ 今日油價：\n" + "\n".join(f"{k}: {v} 元" for k, v in data.items())
    except Exception as e:
        return f"❌ 油價取得失敗：{e}"

def get_us_market_summary():
    try:
        eastern = pytz.timezone("US/Eastern")
        today = datetime.now(eastern)
        days_back = 3 if today.weekday() == 0 else 1
        target_date = (today - timedelta(days=days_back)).date()
        indices = {
            "道瓊": "^DJI", "S&P500": "^GSPC", "NASDAQ": "^IXIC"
        }
        stocks = {
            "NVDA": "輝達", "SMCI": "美超微", "GOOGL": "Google", "AAPL": "蘋果"
        }
        msg = f"📈 前一晚美股行情（{target_date}）\n\n"
        for name, code in indices.items():
            data = yf.Ticker(code).history(start=str(target_date), end=str(target_date + timedelta(days=1)))
            if not data.empty:
                open_price = data.iloc[0]['Open']
                close_price = data.iloc[0]['Close']
                diff = close_price - open_price
                pct = (diff / open_price) * 100 if open_price else 0
                emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                msg += f"{emoji} {name}: {close_price:.2f} ({diff:+.2f}, {pct:+.2f}%)\n"
        msg += "\n"
        for code, name in stocks.items():
            data = yf.Ticker(code).history(start=str(target_date), end=str(target_date + timedelta(days=1)))
            if not data.empty:
                open_price = data.iloc[0]['Open']
                close_price = data.iloc[0]['Close']
                diff = close_price - open_price
                pct = (diff / open_price) * 100 if open_price else 0
                emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                msg += f"{emoji} {name}: {close_price:.2f} ({diff:+.2f}, {pct:+.2f}%)\n"
        return msg.strip()
    except Exception as e:
        return f"❌ 美股資訊錯誤：{e}"

def get_us_market_opening():
    try:
        focus = {
            "NVDA": "輝達", "SMCI": "美超微", "GOOGL": "Google", "AAPL": "蘋果"
        }
        msg = ""
        for code, name in focus.items():
            t = yf.Ticker(code)
            info = t.info
            price = info.get("regularMarketPrice")
            prev = info.get("previousClose")
            if price and prev:
                diff = price - prev
                pct = (diff / prev) * 100
                emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                msg += f"{emoji} {name}: {price:.2f} ({diff:+.2f}, {pct:+.2f}%)\n"
        return msg or "❌ 美股開盤資料無法取得"
    except Exception as e:
        return f"❌ 美股開盤錯誤：{e}"

def get_us_market_opening_detail():
    return get_us_market_opening()

def get_calendar():
    try:
        events = ["09:00 專案會議", "14:00 用戶訪談"]
        return "\n".join(events) if events else "📭 今日無行程"
    except Exception as e:
        return f"❌ 行事曆錯誤：{e}"

def send_scheduled():
    try:
        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei)
        time_str = now.strftime("%H:%M")
        weekday = now.weekday()

        if not LINE_USER_ID:
            print("❌ 缺少 LINE_USER_ID")
            return

        if time_str == "07:10":
            date_str = now.strftime("%Y-%m-%d (%a)")
            text = f"🌅 早安，今天是 {date_str}\n\n"
            text += get_weather("新北市新店區") + "\n\n"
            text += get_news() + "\n\n"
            text += get_exchange_rates() + "\n\n"
            text += get_us_market_summary()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text))

        elif time_str == "08:00" and weekday < 5:
            text = f"🚌 通勤提醒\n\n"
            text += get_traffic("家到公司") + "\n\n"
            text += get_weather("中山區")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text))

        elif time_str == "09:30" and weekday < 5:
            text = "📈 台股開盤快訊\n\n"
            text += get_stock_data("大盤") + "\n\n"
            for k in STOCK_LIST:
                text += get_stock_data(k) + "\n"
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text.strip()))

        elif time_str == "12:00" and weekday < 5:
            text = "📊 台股盤中快訊\n\n"
            text += get_stock_data("大盤") + "\n\n"
            for k in STOCK_LIST:
                text += get_stock_data(k) + "\n"
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text.strip()))

        elif time_str == "13:45" and weekday < 5:
            text = "🔚 台股收盤資訊\n\n"
            text += get_stock_data("大盤") + "\n\n"
            for k in STOCK_LIST:
                text += get_stock_data(k) + "\n"
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text.strip()))

        elif time_str == "17:30":
            if weekday in [0, 2, 4]:
                text = "🏸 下班打球提醒（中正區）\n\n"
                text += get_traffic("公司到中正區") + "\n\n"
                text += get_weather("中正區") + "\n\n"
                text += get_oil_price()
            elif weekday in [1, 3]:
                text = "🏠 下班回家提醒（新店區）\n\n"
                text += get_traffic("公司到新店區") + "\n\n"
                text += get_weather("新店區") + "\n\n"
                text += get_oil_price()
            else:
                text = "🚫 無推播內容"
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text.strip()))

        elif time_str == "21:30" and weekday < 5:
            text = "🇺🇸 美股開盤速報\n\n" + get_us_market_opening()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text.strip()))

        elif time_str == "23:00" and weekday < 5:
            text = "📊 美股行情更新\n\n" + get_us_market_opening_detail()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text.strip()))

    except Exception as e:
        print(f"[推播錯誤] {e}")

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_message = event.message.text.strip()
        lower_message = user_message.lower()
        
        reply = "感謝妳的訊息！\n很抱歉，本機器人主要提供定時推播服務。\n敬請期待我們的推播內容😊"
        
        if lower_message in ["hi", "hello", "妳好", "哈囉", "嗨", "安安"]:
            reply = "🤖 妳好！\n\n📊 股票查詢：輸入股票名稱\n🌤️ 天氣查詢：輸入「天氣」\n🚗 交通查詢：輸入「交通」\n🛤️ 路跡查詢：輸入「路跡」\n📰 新聞查詢：輸入「新聞」\n⛽ 油價查詢：輸入「油價」\n💵 匯率查詢：輸入「匯率」"
            
        elif "天氣" in user_message:
            reply = get_weather("台北市")
        elif "交通" in user_message or "路況" in user_message:
            reply = get_traffic("家到公司")
        elif "路跡" in user_message:
            reply = get_route_info("家到公司")
        elif "新聞" in user_message:
            reply = get_news()
        elif "油價" in user_message:
            reply = get_oil_price()
        elif "匯率" in user_message:
            reply = get_exchange_rates()
        elif "美股" in user_message:
            reply = get_us_market_opening()
        elif user_message in STOCK_MAPPING:
            reply = get_stock_data(user_message)
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
        
    except Exception as e:
        print(f"Handle message error: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="系統處理訊息時發生錯誤，請稍後再試。")
        )

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/send_scheduled")
def send_scheduled_endpoint():
    send_scheduled()
    return "OK"

@app.route("/send_scheduled_test")
def send_scheduled_test():
    test_time = request.args.get("time")
    if not test_time:
        return "請指定 time=HH:MM"
    
    if test_time == "07:10":
        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei)
        date_str = now.strftime("%Y-%m-%d (%a)")
        text = f"🌅 [測試] 早安，今天是 {date_str}\n\n{get_weather('台北市')}"
        if LINE_USER_ID:
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=text))
        return f"已發送 {test_time} 測試訊息"
    else:
        return f"測試時間 {test_time} 功能開發中"

@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    scheduler.add_job(send_scheduled, "cron", minute="0,10,20,30,40,45,50")
    scheduler.start()
    app.run(host="0.0.0.0", port=10000)
