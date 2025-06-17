import os
import json
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import google.auth
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

# 股票代碼映射表
STOCK_MAPPING = {
    "輝達": "NVDA", "美超微": "SMCI", "google": "GOOGL", "谷歌": "GOOGL",
    "蘋果": "AAPL", "特斯拉": "TSLA", "微軟": "MSFT",
    "台積電": "2330.TW", "聯電": "2303.TW", "鴻準": "2354.TW",
    "00918": "00918.TW", "00878": "00878.TW", "00679B": "00679B.TW",
    "00723B": "00723B.TW", "仁寶": "2324.TW", "陽明": "2609.TW",
    "華航": "2610.TW", "長榮航": "2618.TW", "大盤": "^TWII",
    "2330": "2330.TW", "2303": "2303.TW", "2354": "2354.TW",
    "2324": "2324.TW", "2609": "2609.TW", "2610": "2610.TW",
    "2618": "2618.TW", "^TWII": "^TWII"
}
def get_stock_data(symbol):
    """查詢即時股價，優先使用 yfinance，其次 fallback 用 Fugle（限台股）"""
    try:
        # 先檢查代碼是否在對照表中
        query_symbol = STOCK_MAPPING.get(symbol.strip(), symbol.strip()).upper()
        
        # 嘗試使用 yfinance 取得即時資料
        stock = yf.Ticker(query_symbol)
        info = stock.info
        current_price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose")

        if current_price and prev_close:
            change = current_price - prev_close
            change_pct = (change / prev_close) * 100
            emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            return f"{emoji} {query_symbol}\n💰 ${current_price:.2f}\n{change:+.2f} ({change_pct:+.2f}%)"

        # 若 yfinance 無資料，改用 Fugle（限台股）
        if ".TW" in query_symbol:
            headers = {"X-API-KEY": FUGLE_API_KEY}
            res = requests.get(f"https://api.fugle.tw/realtime/v0/intraday/quote?symbolId={query_symbol}", headers=headers)
            data = res.json().get("data", {})
            if data:
                price = data["last"]["price"]
                change = data["change"]["point"]
                change_pct = data["change"]["percent"]
                emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                return f"{emoji} {query_symbol}\n💰 ${price:.2f}\n{change:+.2f} ({change_pct:+.2f}%) 🔸"

        return f"⚠️ 無法查詢 {query_symbol} 的股價"

    except Exception as e:
        return f"❌ 股價查詢錯誤: {e}"

def get_news():
    try:
        url = f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}"
        res = requests.get(url).json()
        articles = res.get("articles", [])[:3]
        if not articles:
            return "🗞️ 無法取得今日新聞"
        return "🗞️ 今日新聞摘要：\n" + "\n".join([f"• {a['title']}" for a in articles])
    except Exception as e:
        return f"❌ 新聞取得錯誤: {e}"

def get_exchange_rates():
    try:
        url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}"
        res = requests.get(url).json()
        rate = res["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
        return f"💱 匯率：1 USD ≒ {float(rate):.2f} TWD"
    except Exception as e:
        return f"❌ 匯率查詢錯誤: {e}"

def get_weather(city):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&lang=zh_tw&units=metric"
        res = requests.get(url).json()
        temp = res["main"]["temp"]
        desc = res["weather"][0]["description"]
        return f"🌤️ {city}天氣：{desc}，{temp:.1f}°C"
    except Exception as e:
        return f"❌ {city} 天氣錯誤: {e}"

def get_traffic(direction):
    """direction = 家到公司、公司到中正區、公司到家"""
    origin = ""
    destination = ""
    if direction == "家到公司":
        origin = "新店區"  # 可以替換成具體地址
        destination = "台北市中山區"
    elif direction == "公司到中正區":
        origin = "台北市中山區"
        destination = "中正區"
    elif direction == "公司到家":
        origin = "台北市中山區"
        destination = "新店區"

    try:
        url = (
            f"https://maps.googleapis.com/maps/api/directions/json"
            f"?origin={origin}&destination={destination}&departure_time=now"
            f"&traffic_model=best_guess&key={GOOGLE_MAPS_API_KEY}"
        )
        res = requests.get(url).json()
        if res["status"] == "OK":
            route = res["routes"][0]["legs"][0]
            duration = route["duration"]["text"]
            duration_in_traffic = route["duration_in_traffic"]["text"]

            time_diff = (
                route["duration_in_traffic"]["value"] - route["duration"]["value"]
            )
            if time_diff > 300:
                emoji = "🔴"
            elif time_diff > 120:
                emoji = "🟠"
            else:
                emoji = "🟢"

            return f"🚗 {origin} → {destination}\n{emoji} 車程：約 {duration_in_traffic}（平時約 {duration}）"
        else:
            return f"⚠️ 路況查詢失敗：{res['status']}"
    except Exception as e:
        return f"❌ 路況錯誤: {e}"
def get_calendar():
    try:
        creds_info = json.loads(GOOGLE_CREDS_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/calendar.readonly"]
        )
        service = build("calendar", "v3", credentials=credentials)
        now = datetime.utcnow().isoformat() + "Z"
        end = (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"
        events_result = (
            service.events()
            .list(calendarId="primary", timeMin=now, timeMax=end, singleEvents=True, orderBy="startTime")
            .execute()
        )
        events = events_result.get("items", [])
        if not events:
            return "📅 今日無排定行程"
        lines = ["📅 今日行程："]
        for event in events[:3]:
            start = event["start"].get("dateTime", event["start"].get("date", ""))
            title = event["summary"]
            time_str = start[11:16] if "T" in start else "整天"
            lines.append(f"• {time_str} - {title}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 行事曆錯誤: {e}"

def get_us_market_summary():
    """取得前一晚美股行情摘要：大盤 + 個股"""
    try:
        eastern = pytz.timezone("US/Eastern")
        now = datetime.now(eastern)
        weekday = now.weekday()
        days_back = 3 if weekday == 0 else 1
        target_date = now - timedelta(days=days_back)
        summary = f"📊 前一晚美股行情摘要（{target_date.strftime('%Y-%m-%d')}）\n\n"
        indices = {"道瓊": "^DJI", "S&P500": "^GSPC", "納斯達克": "^IXIC"}

        for name, symbol in indices.items():
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=target_date.strftime('%Y-%m-%d'), end=(target_date + timedelta(days=1)).strftime('%Y-%m-%d'))
            if not hist.empty:
                close = hist["Close"].iloc[0]
                open_price = hist["Open"].iloc[0]
                change = close - open_price
                change_pct = (change / open_price) * 100 if open_price else 0
                emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                summary += f"{emoji} {name}: {close:.2f} ({change:+.2f}, {change_pct:+.2f}%)\n"

        summary += "\n"
        focus_stocks = {"輝達": "NVDA", "美超微": "SMCI", "Google": "GOOGL"}
        for name, symbol in focus_stocks.items():
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=target_date.strftime('%Y-%m-%d'), end=(target_date + timedelta(days=1)).strftime('%Y-%m-%d'))
            if not hist.empty:
                close = hist["Close"].iloc[0]
                open_price = hist["Open"].iloc[0]
                change = close - open_price
                change_pct = (change / open_price) * 100 if open_price else 0
                emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                summary += f"{emoji} {name}: ${close:.2f} ({change:+.2f}, {change_pct:+.2f}%)\n"

        return summary
    except Exception as e:
        return f"❌ 美股行情取得失敗: {e}"

def get_morning_briefing():
    try:
        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei).strftime("%Y-%m-%d (%a)")
        weather = get_weather("台北市")
        news = get_news()
        calendar = get_calendar()
        exchange = get_exchange_rates()
        us_summary = get_us_market_summary()
        message = (
            f"🌅 早安！今天是 {now}\n\n"
            f"{weather}\n\n"
            f"{news}\n\n"
            f"{calendar}\n\n"
            f"{exchange}\n\n"
            f"{us_summary}"
        )
        return message
    except Exception as e:
        return f"❌ 晨間資訊產生失敗: {e}"
def send_scheduled():
    try:
        if not LINE_USER_ID:
            print("[定時推播] ❌ 缺少 LINE_USER_ID")
            return "未設定用戶 ID"

        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei)
        current_time = now.strftime("%H:%M")
        weekday = now.weekday()  # 0=週一, 6=週日

        print(f"[定時推播] 現在時間 {current_time}，週{weekday + 1}")

        # 07:10 每日晨間推播（天氣、新聞、行事曆、匯率、美股）
        if current_time == "07:10":
            msg = get_morning_briefing()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "07:10 晨間推播完成"

        # 08:00 通勤提醒（週一～五）中山區天氣、交通
        elif current_time == "08:00" and weekday < 5:
            traffic = get_traffic("家到公司")
            weather = get_weather("中山區")
            msg = f"🚶‍♂️ 通勤提醒\n\n{traffic}\n\n{weather}"
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "08:00 通勤提醒完成"

        # 09:30 台股開盤（大盤與台積電）
        elif current_time == "09:30" and weekday < 5:
            msg1 = get_stock_data("大盤")
            msg2 = get_stock_data("台積電")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"📈 台股開盤\n\n{msg1}\n\n{msg2}"))
            return "09:30 台股開盤推播完成"

        # 12:00 台股盤中（2330）
        elif current_time == "12:00" and weekday < 5:
            msg = get_stock_data("2330")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"📊 台股盤中快訊\n\n{msg}"))
            return "12:00 台股盤中推播完成"

        # 13:45 台股收盤（台積電）
        elif current_time == "13:45" and weekday < 5:
            msg = get_stock_data("台積電")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"🔚 台股收盤\n\n{msg}"))
            return "13:45 台股收盤推播完成"

        # 17:30 下班提醒：135 去中正區，24 回家（新店）
        elif current_time == "17:30":
            if weekday in [0, 2, 4]:  # 週一、三、五
                traffic = get_traffic("公司到中正區")
                weather = get_weather("中正區")
                oil = get_oil_price()
                msg = f"🏸 下班打球提醒（中正區）\n\n{traffic}\n\n{weather}\n\n{oil}"
                line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
                return "17:30 中正區下班提醒完成"
            elif weekday in [1, 3]:  # 週二、四
                traffic = get_traffic("公司到家")
                weather = get_weather("新店區")
                oil = get_oil_price()
                msg = f"🏡 下班回家提醒（新店）\n\n{traffic}\n\n{weather}\n\n{oil}"
                line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
                return "17:30 新店回家提醒完成"

        # 21:30 美股開盤速報
        elif current_time == "21:30" and weekday < 5:
            msg = get_stock_data("輝達") + "\n\n" + get_stock_data("美超微")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"🇺🇸 美股開盤速報\n\n{msg}"))
            return "21:30 美股速報完成"

        # 23:00 美股行情更新（輝達、美超微、Google）
        elif current_time == "23:00" and weekday < 5:
            msg1 = get_stock_data("輝達")
            msg2 = get_stock_data("美超微")
            msg3 = get_stock_data("google")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"📊 美股行情更新\n\n{msg1}\n\n{msg2}\n\n{msg3}"))
            return "23:00 美股行情推播完成"

        return "✅ 無需推播的時段"
    except Exception as e:
        print(f"[定時推播] 錯誤: {e}")
        return f"❌ 推播失敗: {e}"
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
    """手動測試特定時段推播"""
    test_time = request.args.get("time", "")
    try:
        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei)
        print(f"[測試推播] 模擬時間: {test_time}，實際時間: {now.strftime('%H:%M')}")
        return send_scheduled()
    except Exception as e:
        print(f"[測試推播] 錯誤: {e}")
        return f"❌ 測試推播錯誤: {e}"

@app.route("/send_scheduled")
def send_scheduled_endpoint():
    """Render 平台正式排程觸發端點"""
    try:
        return send_scheduled()
    except Exception as e:
        print(f"[Render /send_scheduled 錯誤] {e}")
        return f"❌ 排程失敗: {e}"

@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    scheduler.add_job(send_scheduled, "cron", minute="0,10,20,30,40,50")  # 防止平台休眠
    scheduler.start()
    app.run(host="0.0.0.0", port=10000)
