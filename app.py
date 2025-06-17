import os
import json
import requests
import yfinance as yf
import pytz
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 股票代碼映射
STOCK_MAPPING = {
    # 美股
    "輝達": "NVDA",
    "美超微": "SMCI",
    "google": "GOOGL",
    "谷歌": "GOOGL",
    "蘋果": "AAPL",
    "特斯拉": "TSLA",
    "微軟": "MSFT",
    # 台股
    "台積電": "2330.TW",
    "聯電": "2303.TW",
    "鴻準": "2354.TW",
    "00918": "00918.TW",
    "00878": "00878.TW",
    "元大美債20年": "00679B.TW",
    "群益25年美債": "00723B.TW",
    "仁寶": "2324.TW",
    "陽明": "2609.TW",
    "華航": "2610.TW",
    "長榮航": "2618.TW",
    "大盤": "^TWII",
    "2330": "2330.TW",
    "2303": "2303.TW",
    "2354": "2354.TW",
    "2324": "2324.TW",
    "2609": "2609.TW",
    "2610": "2610.TW",
    "2618": "2618.TW"
}
def get_stock_data(symbol):
    try:
        query = STOCK_MAPPING.get(symbol.strip(), symbol.strip()).upper()
        stock = yf.Ticker(query)
        info = stock.info
        current_price = info.get("regularMarketPrice")
        prev_close = info.get("previousClose")

        if current_price and prev_close:
            diff = current_price - prev_close
            pct = (diff / prev_close) * 100
            emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            return f"{emoji} {query}\n💰 ${current_price:.2f}\n{diff:+.2f} ({pct:+.2f}%)"

        # 若為台股失敗，fallback 使用 Fugle
        if ".TW" in query:
            url = f"https://api.fugle.tw/realtime/v0/intraday/quote?symbolId={query}"
            headers = {"X-API-KEY": FUGLE_API_KEY}
            res = requests.get(url, headers=headers)
            if res.status_code != 200 or not res.text.strip().startswith("{"):
                return f"❌ Fugle API 錯誤：{res.status_code}"
            json_data = res.json()
            data = json_data.get("data", {})
            if not data or "last" not in data:
                return "❌ Fugle 無資料"

            price = data["last"]["price"]
            change = data["change"]["point"]
            pct = data["change"]["percent"]
            emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            return f"{emoji} {query}\n💰 ${price:.2f}\n{change:+.2f} ({pct:+.2f}%) 🔸"

        return f"⚠️ 無法取得 {query} 股價"
    except Exception as e:
        return f"❌ 股價查詢錯誤: {e}"

def get_weather(city):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&lang=zh_tw&units=metric"
        res = requests.get(url).json()
        temp = res["main"]["temp"]
        desc = res["weather"][0]["description"]
        return f"🌤️ {city}天氣：{desc}，{temp:.1f}°C"
    except Exception as e:
        return f"❌ {city} 天氣錯誤: {e}"

def get_news():
    try:
        url = f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}"
        res = requests.get(url).json()
        articles = res.get("articles", [])[:3]
        if not articles:
            return "🗞️ 今日無新聞資料"
        return "🗞️ 今日新聞：\n" + "\n".join([f"• {a['title']}" for a in articles])
    except Exception as e:
        return f"❌ 新聞錯誤: {e}"

def get_exchange_rates():
    try:
        url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}"
        res = requests.get(url).json()
        rate = res["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
        return f"💱 匯率：1 USD ≒ {float(rate):.2f} TWD"
    except Exception as e:
        return f"❌ 匯率查詢錯誤: {e}"

def get_calendar():
    try:
        creds_info = json.loads(GOOGLE_CREDS_JSON)
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/calendar.readonly"]
        )
        service = build("calendar", "v3", credentials=creds)
        now = datetime.utcnow().isoformat() + "Z"
        end = (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"
        events = (
            service.events()
            .list(calendarId="primary", timeMin=now, timeMax=end, singleEvents=True, orderBy="startTime")
            .execute()
            .get("items", [])
        )
        if not events:
            return "📅 今日無排定行程"
        return "📅 今日行程：\n" + "\n".join(
            [f"• {e['start'].get('dateTime', e['start'].get('date'))[11:16]} - {e['summary']}" for e in events[:3]]
        )
    except Exception as e:
        return f"❌ 行事曆錯誤: {e}"

def get_traffic(direction):
    origin = ""
    destination = ""
    if direction == "家到公司":
        origin = "新店區"
        destination = "台北市中山區"
    elif direction == "公司到中正區":
        origin = "台北市中山區"
        destination = "中正區"
    elif direction == "公司到家":
        origin = "台北市中山區"
        destination = "新店區"

    try:
        url = (
            f"https://maps.googleapis.com/maps/api/directions/json?"
            f"origin={origin}&destination={destination}&departure_time=now"
            f"&traffic_model=best_guess&key={GOOGLE_MAPS_API_KEY}"
        )
        res = requests.get(url).json()
        if res["status"] != "OK":
            return f"⚠️ 路況查詢失敗：{res.get('status')}"
        leg = res["routes"][0]["legs"][0]
        normal = leg["duration"]["value"]
        traffic = leg["duration_in_traffic"]["value"]
        diff = traffic - normal
        emoji = "🔴" if diff > 300 else "🟠" if diff > 120 else "🟢"
        return f"🚗 {origin} → {destination}\n{emoji} 車程：約 {leg['duration_in_traffic']['text']}（平常約 {leg['duration']['text']}）"
    except Exception as e:
        return f"❌ 路況錯誤: {e}"
def get_us_market_summary():
    try:
        eastern = pytz.timezone("US/Eastern")
        now = datetime.now(eastern)
        weekday = now.weekday()
        days_back = 3 if weekday == 0 else 1
        target_date = now - timedelta(days=days_back)

        summary = f"📊 前一晚美股行情（{target_date.strftime('%Y-%m-%d')}）\n"

        indices = {
            "道瓊": "^DJI",
            "S&P500": "^GSPC",
            "納斯達克": "^IXIC"
        }

        for name, symbol in indices.items():
            data = yf.Ticker(symbol).history(start=target_date.strftime('%Y-%m-%d'), end=(target_date + timedelta(days=1)).strftime('%Y-%m-%d'))
            if data.empty:
                summary += f"❌ {name} 無資料\n"
                continue
            open_price = data['Open'][0]
            close_price = data['Close'][0]
            diff = close_price - open_price
            pct = (diff / open_price) * 100
            emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            summary += f"{emoji} {name}: {close_price:.2f} ({diff:+.2f}, {pct:+.2f}%)\n"

        summary += "\n"

        focus_stocks = {
            "輝達": "NVDA",
            "美超微": "SMCI",
            "Google": "GOOGL"
        }

        for name, symbol in focus_stocks.items():
            data = yf.Ticker(symbol).history(start=target_date.strftime('%Y-%m-%d'), end=(target_date + timedelta(days=1)).strftime('%Y-%m-%d'))
            if data.empty:
                summary += f"❌ {name} 無資料\n"
                continue
            open_price = data['Open'][0]
            close_price = data['Close'][0]
            diff = close_price - open_price
            pct = (diff / open_price) * 100
            emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            summary += f"{emoji} {name}: ${close_price:.2f} ({diff:+.2f}, {pct:+.2f}%)\n"

        return summary
    except Exception as e:
        return f"❌ 美股摘要錯誤: {e}"


def get_morning_briefing():
    try:
        taipei = pytz.timezone("Asia/Taipei")
        now_str = datetime.now(taipei).strftime("%Y-%m-%d (%a)")
        weather = get_weather("台北市")
        news = get_news()
        calendar = get_calendar()
        exchange = get_exchange_rates()
        us = get_us_market_summary()

        return (
            f"🌅 早安！今天是 {now_str}\n\n"
            f"{weather}\n\n"
            f"{news}\n\n"
            f"{calendar}\n\n"
            f"{exchange}\n\n"
            f"{us}"
        )
    except Exception as e:
        return f"❌ 晨間彙整錯誤: {e}"
def send_scheduled():
    try:
        if not LINE_USER_ID:
            return "❌ 未設定 LINE_USER_ID"

        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei)
        current_time = now.strftime("%H:%M")
        weekday = now.weekday()  # 0 = 週一, 6 = 週日
        print(f"[定時推播] 現在時間 {current_time}，週{weekday + 1}")

        # 07:10 每日晨間摘要
        if current_time == "07:10":
            date = now.strftime("%Y-%m-%d (%a)")
            weather = get_weather("台北市")
            news = get_news()
            calendar = get_calendar()
            exchange = get_exchange_rates()
            us_summary = get_us_market_summary()
            msg = (
                f"🌅 早安！今天是 {date}\n\n"
                f"{weather}\n\n"
                f"{news}\n\n"
                f"{calendar}\n\n"
                f"{exchange}\n\n"
                f"{us_summary}"
            )
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 07:10 晨間推播完成"

        # 08:00 通勤提醒（週一～週五）
        elif current_time == "08:00" and weekday < 5:
            weather = get_weather("中山區")
            traffic = get_traffic("家到公司")
            msg = (
                f"🚌 通勤提醒\n\n"
                f"🚦 路況（家→公司）\n{traffic}\n\n"
                f"🌤️ 天氣\n{weather}"
            )
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 08:00 通勤推播完成"

        # 09:30 台股開盤（週一～週五）
        elif current_time == "09:30" and weekday < 5:
            twii = get_stock_data("大盤")
            tsmc = get_stock_data("台積電")
            msg = (
                f"📈 台股開盤快訊\n\n"
                f"{twii}\n\n"
                f"{tsmc}"
            )
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 09:30 台股開盤完成"

        # 12:00 台股盤中（週一～週五）
        elif current_time == "12:00" and weekday < 5:
            tsmc = get_stock_data("2330")
            fund = get_stock_data("00918")
            msg = (
                f"📊 台股盤中快訊\n\n"
                f"{tsmc}\n\n"
                f"{fund}"
            )
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 12:00 台股中場完成"

        # 13:45 台股收盤（週一～週五）
        elif current_time == "13:45" and weekday < 5:
            tsmc = get_stock_data("台積電")
            fund = get_stock_data("00878")
            msg = (
                f"🔚 台股收盤資訊\n\n"
                f"{tsmc}\n\n"
                f"{fund}"
            )
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 13:45 台股收盤完成"

        # 17:30 下班提醒（135打球，24回家）
        elif current_time == "17:30":
            oil = get_oil_price()
            if weekday in [0, 2, 4]:  # 週一三五：打球
                traffic = get_traffic("公司到中正區")
                weather = get_weather("中正區")
                msg = (
                    f"🏸 下班打球提醒（中正區）\n\n"
                    f"🚦 交通路況：\n{traffic}\n\n"
                    f"🌤️ 天氣：\n{weather}\n\n"
                    f"⛽ 油價：\n{oil}"
                )
            elif weekday in [1, 3]:  # 週二四：回家
                traffic = get_traffic("公司到家")
                weather = get_weather("新店區")
                msg = (
                    f"🏠 下班回家提醒（新店）\n\n"
                    f"🚦 交通路況：\n{traffic}\n\n"
                    f"🌤️ 天氣：\n{weather}\n\n"
                    f"⛽ 油價：\n{oil}"
                )
            else:
                msg = "（週末不推播）"
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 17:30 下班推播完成"

        # 21:30 美股開盤速報（大盤＋個股）
        elif current_time == "21:30" and weekday < 5:
            us_index = get_stock_data("^DJI") + "\n" + get_stock_data("^GSPC") + "\n" + get_stock_data("^IXIC")
            nvda = get_stock_data("NVDA")
            smci = get_stock_data("SMCI")
            googl = get_stock_data("GOOGL")
            aapl = get_stock_data("AAPL")
            msg = (
                f"🇺🇸 美股開盤速報\n\n"
                f"{us_index}\n\n"
                f"{nvda}\n\n"
                f"{smci}\n\n"
                f"{googl}\n\n"
                f"{aapl}"
            )
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 21:30 美股速報完成"

        # 23:00 美股行情更新（同樣一段）
        elif current_time == "23:00" and weekday < 5:
            us_index = get_stock_data("^DJI") + "\n" + get_stock_data("^GSPC") + "\n" + get_stock_data("^IXIC")
            nvda = get_stock_data("NVDA")
            smci = get_stock_data("SMCI")
            googl = get_stock_data("GOOGL")
            aapl = get_stock_data("AAPL")
            msg = (
                f"📊 美股行情更新\n\n"
                f"{us_index}\n\n"
                f"{nvda}\n\n"
                f"{smci}\n\n"
                f"{googl}\n\n"
                f"{aapl}"
            )
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "✅ 23:00 美股行情完成"

        return "ℹ️ 無推播內容"

    except Exception as e:
        print(f"[推播錯誤] {e}")
        return f"❌ 推播錯誤: {e}"
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@app.route("/send_scheduled_test")
def send_scheduled_test():
    """手動測試某時段推播，網址加 ?time=HH:MM 可模擬時間"""
    test_time = request.args.get("time", "")
    print(f"[測試推播] 模擬時間：{test_time}")
    return send_scheduled()


@app.route("/send_scheduled")
def send_scheduled_endpoint():
    """Render 平台正式定時喚醒路由（每10分鐘）"""
    return send_scheduled()


@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"


@app.route("/health")
def health():
    return "OK"
if __name__ == "__main__":
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")

    # 每 10 分鐘觸發一次排程檢查，避免平台休眠
    scheduler.add_job(send_scheduled, "cron", minute="0,10,20,30,40,50")

    scheduler.start()
    print("✅ 定時排程啟動完成")

    app.run(host="0.0.0.0", port=10000)
