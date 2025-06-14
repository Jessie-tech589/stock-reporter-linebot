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

app = Flask(__name__)

# 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', 'dummy')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', 'dummy')
LINE_USER_ID = os.environ.get('LINE_USER_ID')
WEATHER_API_KEY = os.environ.get('WEATHER_API_KEY')
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')
ALPHA_VANTAGE_API_KEY = os.environ.get('ALPHA_VANTAGE_API_KEY')
FUGLE_API_KEY = os.environ.get('FUGLE_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY')
GOOGLE_CREDS_JSON = os.environ.get('GOOGLE_CREDS_JSON')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
# 股票代碼映射表 - 按照用戶指定的股票清單
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

    # 常用代碼
    "2330": "2330.TW",
    "2303": "2303.TW",
    "2354": "2354.TW",
    "2324": "2324.TW",
    "2609": "2609.TW",
    "2610": "2610.TW",
    "2618": "2618.TW"
}
def get_stock_data(query):
    try:
        original_query = query.strip()

        # 若使用者輸入「美股」或「台股」但未指明標的
        if original_query in ["美股", "台股"]:
            return "請輸入具體股票名稱，例如：\n美股 輝達\n台股 台積電"

        # 根據輸入自動對應股票代碼
        if "美股" in original_query:
            stock_name = original_query.replace("美股", "").strip()
            if not stock_name:
                return "請輸入股票名稱，例如：美股 輝達"
            symbol = STOCK_MAPPING.get(stock_name, stock_name.upper())
        elif "台股" in original_query:
            stock_name = original_query.replace("台股", "").strip()
            if not stock_name:
                return "請輸入股票名稱，例如：台股 台積電"
            symbol = STOCK_MAPPING.get(stock_name, f"{stock_name}.TW")
        else:
            symbol = STOCK_MAPPING.get(original_query, original_query.upper())

        # 檢查是否為週末
        now = datetime.now(pytz.timezone('Asia/Taipei'))
        if now.weekday() >= 5:
            return f"📊 {symbol}\n🕒 市場休市中（週末）\n請於交易日查詢即時股價"

        stock = yf.Ticker(symbol)

        # 優先使用 info 提供的即時資料
        try:
            info = stock.info
            current_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose')
            company_name = info.get('longName') or info.get('shortName') or symbol

            if current_price and prev_close:
                change = current_price - prev_close
                change_percent = (change / prev_close) * 100
                emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                return f"{emoji} {company_name}\n💰 ${current_price:.2f}\n{change:+.2f} ({change_percent:+.2f}%)"
        except:
            pass

        # 若 info 資料失敗，改用歷史資料
        hist = stock.history(period="2d")
        if not hist.empty:
            current = hist['Close'].iloc[-1]
            previous = hist['Close'].iloc[-2] if len(hist) > 1 else current
            change = current - previous
            percent = (change / previous) * 100 if previous != 0 else 0
            emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            return f"{emoji} {symbol}\n💰 ${current:.2f}\n{change:+.2f} ({percent:+.2f}%) ⚠️ 使用歷史資料"

        return f"❌ 無法取得 {symbol} 股價\n可能原因：股票代碼錯誤、無交易、API問題"

    except Exception as e:
        return f"❌ 查詢失敗：{e}"
def get_us_market_summary():
    """取得前一晚美股行情摘要：大盤 + 個股"""
    try:
        # 使用美東時間
        eastern = pytz.timezone('US/Eastern')
        now = datetime.now(eastern)
        weekday = now.weekday()
        
        # 若今天是週一，則回報上週五行情
        days_back = 3 if weekday == 0 else 1
        target_date = now - timedelta(days=days_back)

        summary = f"📊 前一晚美股行情摘要（{target_date.strftime('%Y-%m-%d')}）\n\n"

        # 大盤指數
        indices = {
            "道瓊": "^DJI",
            "S&P500": "^GSPC",
            "納斯達克": "^IXIC"
        }

        for name, symbol in indices.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(start=target_date.strftime('%Y-%m-%d'), end=(target_date + timedelta(days=1)).strftime('%Y-%m-%d'))
                if not hist.empty:
                    close = hist['Close'].iloc[0]
                    open_price = hist['Open'].iloc[0]
                    change = close - open_price
                    change_pct = (change / open_price) * 100 if open_price else 0
                    emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                    summary += f"{emoji} {name}: {close:.2f} ({change:+.2f}, {change_pct:+.2f}%)\n"
            except Exception as e:
                summary += f"❌ {name} 資料錯誤\n"

        summary += "\n"

        # 重點個股
        focus_stocks = {
            "輝達": "NVDA",
            "美超微": "SMCI",
            "Google": "GOOGL"
        }

        for name, symbol in focus_stocks.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(start=target_date.strftime('%Y-%m-%d'), end=(target_date + timedelta(days=1)).strftime('%Y-%m-%d'))
                if not hist.empty:
                    close = hist['Close'].iloc[0]
                    open_price = hist['Open'].iloc[0]
                    change = close - open_price
                    change_pct = (change / open_price) * 100 if open_price else 0
                    emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                    summary += f"{emoji} {name}: ${close:.2f} ({change:+.2f}, {change_pct:+.2f}%)\n"
            except Exception as e:
                summary += f"❌ {name} 資料錯誤\n"

        return summary

    except Exception as e:
        return f"❌ 美股行情取得失敗: {e}"
def get_morning_briefing():
    """早上 07:10 的晨間推播內容"""
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
    """依照時段推播不同資訊"""
    try:
        if not LINE_USER_ID:
            print("[定時推播] ❌ 缺少 LINE_USER_ID")
            return "未設定用戶 ID"

        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei)
        current_time = now.strftime("%H:%M")
        weekday = now.weekday()  # 0=週一, 6=週日

        print(f"[定時推播] 現在時間 {current_time}，週{weekday+1}")

        # 07:10 每天早安推播（含前一晚美股）
        if current_time == "07:10":
            msg = get_morning_briefing()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "07:10 推播完成"

        # 08:00 通勤提醒（週一到週五）
        elif current_time == "08:00" and weekday < 5:
            traffic = get_traffic("家到公司")
            weather = get_weather("台北市")
            msg = f"🚌 上班通勤提醒\n\n{traffic}\n\n{weather}"
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
            return "08:00 通勤推播完成"

        # 09:30 台股開盤（週一到週五）
        elif current_time == "09:30" and weekday < 5:
            msg = get_stock_data("台積電")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"📈 台股開盤\n\n{msg}"))
            return "09:30 台股開盤推播完成"

        # 12:00 台股盤中（週一到週五）
        elif current_time == "12:00" and weekday < 5:
            msg = get_stock_data("2330")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"📊 台股盤中快訊\n\n{msg}"))
            return "12:00 台股中場推播完成"

        # 13:45 台股收盤（週一到週五）
        elif current_time == "13:45" and weekday < 5:
            msg = get_stock_data("台積電")
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"🔚 台股收盤資訊\n\n{msg}"))
            return "13:45 台股收盤推播完成"

        # 17:30 下班提醒（週一三五中正區、週二四新店區）
        elif current_time == "17:30":
            if weekday in [0, 2, 4]:  # 一三五
                msg = f"🏸 打球提醒（中正區）\n\n{get_weather('中正區')}\n\n{get_oil_price()}"
                line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
                return "17:30 中正區提醒完成"
            elif weekday in [1, 3]:  # 二四
                msg = f"🏸 打球提醒（新店區）\n\n{get_weather('新店區')}\n\n{get_oil_price()}"
                line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg))
                return "17:30 新店區提醒完成"

        # 21:30 美股開盤速報（週一～週五）
        elif current_time == "21:30" and weekday < 5:
            msg = get_us_market_opening()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"🇺🇸 美股開盤速報\n\n{msg}"))
            return "21:30 美股速報推播完成"

        # 23:00 美股開盤行情（週一～週五）
        elif current_time == "23:00" and weekday < 5:
            msg = get_us_market_opening_detail()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=f"📊 美股行情更新\n\n{msg}"))
            return "23:00 美股行情推播完成"

        return "目前時段無推播內容"
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

# 測試 API：手動模擬特定時間觸發推播
@app.route("/send_scheduled_test")
def send_scheduled_test():
    """手動測試指定時段推播"""
    test_time = request.args.get("time", "")
    try:
        taipei = pytz.timezone("Asia/Taipei")
        now = datetime.now(taipei)
        print(f"[測試推播] 模擬時間: {test_time}, 實際時間: {now.strftime('%H:%M')}")
        return send_scheduled()
    except Exception as e:
        print(f"[測試推播] 錯誤: {e}")
        return f"❌ 測試推播錯誤: {e}"

@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    scheduler.add_job(send_scheduled, "cron", minute="0,10,20,30,40,50")  # 防止 render 休眠
    scheduler.start()

    app.run(host="0.0.0.0", port=10000)
