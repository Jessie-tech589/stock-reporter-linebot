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
        # 處理用戶輸入
        original_query = query.strip()
        
        # 如果只輸入「美股」或「台股」，給予提示
        if original_query in ["美股", "台股"]:
            return f"請輸入具體股票名稱，例如：\n美股 輝達\n台股 台積電\n或直接輸入：輝達、台積電"
        
        # 處理股票查詢
        if "美股" in original_query:
            stock_name = original_query.replace("美股", "").strip()
            if not stock_name:
                return "請輸入股票名稱，例如：美股 輝達"
            symbol = STOCK_MAPPING.get(stock_name, stock_name)
        elif "台股" in original_query:
            stock_name = original_query.replace("台股", "").strip()
            if not stock_name:
                return "請輸入股票名稱，例如：台股 台積電"
            if stock_name.isdigit():
                symbol = f"{stock_name}.TW"
            else:
                symbol = STOCK_MAPPING.get(stock_name, f"{stock_name}.TW")
        else:
            # 直接查詢
            symbol = STOCK_MAPPING.get(original_query, original_query)
        
        print(f"Original query: {original_query}, Mapped symbol: {symbol}")
        
        # 檢查是否為週末（美股和台股都休市）
        now = datetime.now(pytz.timezone('US/Eastern'))
        if now.weekday() >= 5:  # 週六、週日
            return f"📊 {symbol}\n🕒 市場休市中（週末）\n請於交易日查詢即時股價"
        
        # 使用更穩定的方式取得股票資料
        stock = yf.Ticker(symbol)
        
        # 嘗試多種方式取得資料
        try:
            # 方法1：取得即時資料
            info = stock.info
            current_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose')
            company_name = info.get('longName') or info.get('shortName') or symbol
            
            if current_price and prev_close:
                change = current_price - prev_close
                change_percent = (change / prev_close) * 100
                change_emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                
                return f"📊 {company_name}\n💰 ${current_price:.2f}\n{change_emoji} {change:+.2f} ({change_percent:+.1f}%)"
        except:
            pass
        
        # 方法2：使用歷史資料
        try:
            hist = stock.history(period="5d")
            if not hist.empty:
                current_price = hist['Close'].iloc[-1]
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
                change = current_price - prev_close
                change_percent = (change / prev_close) * 100 if prev_close != 0 else 0
                change_emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                
                return f"📊 {symbol}\n💰 ${current_price:.2f}\n{change_emoji} {change:+.2f} ({change_percent:+.1f}%)\n⚠️ 使用歷史資料"
        except:
            pass
        
        return f"❌ 無法取得 {symbol} 股價\n可能原因：\n• 股票代碼錯誤\n• 市場休市\n• 網路連線問題"
        
    except Exception as e:
        print(f"Stock data error for '{query}': {e}")
        return f"❌ 股價查詢發生錯誤"

def get_oil_price():
    try:
        # 使用替代的油價 API 或固定回覆
        return "⛽ 油價查詢功能維護中\n請至相關財經網站查詢最新油價"
        
    except Exception as e:
        print(f"Oil price error: {e}")
        return "⛽ 油價查詢失敗"

def get_weather(city="台北市"):
    try:
        if not WEATHER_API_KEY or WEATHER_API_KEY == 'dummy':
            return f"❌ {city}天氣服務需要設定 API Key\n請聯繫管理員設定 OpenWeatherMap API"
            
        # OpenWeatherMap API
        url = f"http://api.openweathermap.org/data/2.5/weather"
        params = {
            'q': city,
            'appid': WEATHER_API_KEY,
            'units': 'metric',
            'lang': 'zh_tw'
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            temp = data['main']['temp']
            feels_like = data['main']['feels_like']
            humidity = data['main']['humidity']
            description = data['weather'][0]['description']
            
            return f"🌤️ {city}天氣\n🌡️ 溫度: {temp}°C (體感 {feels_like}°C)\n💧 濕度: {humidity}%\n☁️ {description}"
        elif response.status_code == 401:
            return f"❌ 天氣 API Key 無效"
        else:
            print(f"Weather API error: {response.status_code}, {response.text}")
            return f"❌ {city}天氣查詢失敗"
            
    except Exception as e:
        print(f"Weather error: {e}")
        return f"❌ {city}天氣取得失敗"

def get_daily_stock_summary():
    """取得每日股市摘要"""
    try:
        # 檢查是否為週末
        now = datetime.now(pytz.timezone('US/Eastern'))
        if now.weekday() >= 5:
            return "📈 股市摘要\n🕒 週末市場休市\n下週一恢復交易"
        
        # 主要指數
        indices = {
            "道瓊": "^DJI",
            "納斯達克": "^IXIC", 
            "S&P500": "^GSPC",
            "台股加權": "^TWII"
        }
        
        summary = "📈 今日股市摘要\n\n"
        success_count = 0
        
        for name, symbol in indices.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="2d")
                if not hist.empty:
                    current = hist['Close'].iloc[-1]
                    if len(hist) > 1:
                        prev = hist['Close'].iloc[-2]
                        change = current - prev
                        change_pct = (change / prev) * 100
                        emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                        summary += f"{emoji} {name}: {current:.2f} ({change_pct:+.1f}%)\n"
                        success_count += 1
                    else:
                        summary += f"📊 {name}: {current:.2f}\n"
                        success_count += 1
            except Exception as e:
                print(f"Index {name} error: {e}")
                summary += f"❌ {name}: 資料無法取得\n"
        
        if success_count == 0:
            return "📈 股市摘要\n❌ 目前無法取得股市資料\n請稍後再試"
        
        return summary
        
    except Exception as e:
        print(f"Stock summary error: {e}")
        return "📈 股市摘要暫時無法取得"

def send_scheduled():
    """定時推播功能"""
    try:
        if not LINE_USER_ID:
            print("LINE_USER_ID not set")
            return
            
        taipei_tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(taipei_tz)
        
        # 工作日早上 7:10 發送股市摘要
        if now.weekday() < 5 and now.hour == 7 and now.minute == 10:
            message = get_daily_stock_summary()
            line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
            print(f"[定時推播] 已發送股市摘要: {now}")
            
    except Exception as e:
        print(f"[定時推播] 錯誤: {e}")

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
    try:
        user_message = event.message.text.strip()
        lower_name = user_message.lower()
        
        reply = "感謝您的訊息！\n很抱歉，本機器人無法辨別回覆用戶的訊息。\n敬請期待我們下次發送的內容喔😊"
        
        if lower_name in ["hi", "妳好", "哈囉", "嗨", "安安", "你好"]:
            reply = "🤖 妳好！有什麼需要幫忙的嗎？\n\n📊 美股查詢：\n• 輝達、美超微、google\n\n📊 台股查詢：\n• 台積電、聯電、鴻準\n• 00918、00878\n• 元大美債20年、群益25年美債\n• 仁寶、陽明、華航、長榮航\n\n🌤️ 天氣查詢：輸入「天氣」\n⛽ 油價查詢：輸入「油價」\n📈 股市摘要：輸入「股市」"
            
        elif "天氣" in user_message:
            reply = get_weather("台北市")
                
        elif "油價" in user_message:
            reply = get_oil_price()
            
        elif "股市" in user_message or "大盤" in user_message:
            reply = get_daily_stock_summary()
            
        else:
            # 嘗試股票查詢
            stock_reply = get_stock_data(user_message)
            if "❌" not in stock_reply:
                reply = stock_reply
        
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

@app.route('/send_scheduled_test')
def send_scheduled_test():
    """測試定時推播"""
    try:
        taipei_tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(taipei_tz)
        print(f"[定時推播] 現在時間: {now.strftime('%H:%M')} (週{now.weekday()+1})")
        
        test_time = request.args.get('time', '07:10')
        hour, minute = map(int, test_time.split(':'))
        
        if now.weekday() < 5 and now.hour == hour and now.minute == minute:
            if LINE_USER_ID:
                message = get_daily_stock_summary()
                line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
                print(f"[定時推播] 已發送測試訊息")
                return "已發送"
            else:
                print(f"[定時推播] LINE_USER_ID 未設定")
                return "未設定用戶ID"
        else:
            print(f"[定時推播] 此刻無排程觸發")
            return "無排程"
            
    except Exception as e:
        print(f"[定時推播] 測試錯誤: {e}")
        return f"錯誤: {e}"

@app.route('/send_scheduled', methods=['GET'])
def send_scheduled_endpoint():
    """定時推播端點（供外部 cron 服務使用）"""
    return send_scheduled_test()

@app.route('/')
def home():
    return "LINE Bot is running!"

@app.route('/health')
def health():
    return "OK"

if __name__ == "__main__":
    # 啟動排程器
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="Asia/Taipei")
        scheduler.add_job(send_scheduled, "cron", minute="0,10,20,30,40,50")
        scheduler.start()
        app.run(host="0.0.0.0", port=10000)
    except Exception as e:
        print(f"啟動失敗: {e}")
        app.run(host="0.0.0.0", port=10000)
