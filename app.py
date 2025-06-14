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

# 股票代碼映射表
STOCK_MAPPING = {
    "輝達": "NVDA",
    "蘋果": "AAPL", 
    "微軟": "MSFT",
    "谷歌": "GOOGL",
    "亞馬遜": "AMZN",
    "特斯拉": "TSLA",
    "台積電": "TSM",
    "聯發科": "2454.TW",
    "鴻海": "2317.TW",
    "中華電": "2412.TW",
    "2330": "2330.TW",
    "0050": "0050.TW",
    "0056": "0056.TW"
}

def get_stock_data(query):
    try:
        # 處理用戶輸入
        original_query = query
        if "美股" in query:
            stock_name = query.replace("美股", "").strip()
            symbol = STOCK_MAPPING.get(stock_name, stock_name)
        elif "台股" in query:
            stock_name = query.replace("台股", "").strip()
            if stock_name.isdigit():
                symbol = f"{stock_name}.TW"
            else:
                symbol = STOCK_MAPPING.get(stock_name, f"{stock_name}.TW")
        else:
            # 直接查詢
            symbol = STOCK_MAPPING.get(query, query)
        
        print(f"Original query: {original_query}, Mapped symbol: {symbol}")
        
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1d")
        
        if hist.empty:
            print(f"No data found for symbol: {symbol}")
            return f"❌ 找不到股票代碼：{symbol}"
        
        info = stock.info
        current_price = hist['Close'].iloc[-1]
        prev_close = info.get('previousClose', current_price)
        change = current_price - prev_close
        change_percent = (change / prev_close) * 100 if prev_close != 0 else 0
        
        change_emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
        
        company_name = info.get('longName', info.get('shortName', symbol))
        
        return f"📊 {company_name}\n💰 ${current_price:.2f}\n{change_emoji} {change:+.2f} ({change_percent:+.1f}%)"
        
    except Exception as e:
        print(f"Failed to get ticker '{query}' reason: {e}")
        return f"❌ 無法取得 {query} 股價資訊"

def get_oil_price():
    try:
        url = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
        params = {
            'frequency': 'weekly',
            'data[0]': 'value',
            'facets[product][]': 'EPD2DXL0',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'desc',
            'offset': 0,
            'length': 1,
            'api_key': 'YOUR_EIA_API_KEY'  # 需要申請 EIA API Key
        }
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('response', {}).get('data'):
                price = data['response']['data'][0]['value']
                return f"⛽ 美國汽油價格: ${price:.2f}/加侖"
        
        # 備用方案：使用固定回覆
        return "⛽ 油價查詢服務暫時無法使用"
        
    except Exception as e:
        print(f"Oil price error: {e}")
        return "⛽ 油價查詢失敗"

def get_weather(city="台北市"):
    try:
        if not WEATHER_API_KEY:
            return "❌ 天氣服務未設定"
            
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
        else:
            print(f"Weather API error: {response.status_code}, {response.text}")
            return f"❌ {city}天氣查詢失敗"
            
    except Exception as e:
        print(f"Weather error: {e}")
        return f"❌ {city}天氣取得失敗: {str(e)}"

def get_daily_stock_summary():
    """取得每日股市摘要"""
    try:
        # 主要指數
        indices = {
            "道瓊": "^DJI",
            "納斯達克": "^IXIC", 
            "S&P500": "^GSPC",
            "台股加權": "^TWII"
        }
        
        summary = "📈 今日股市摘要\n\n"
        
        for name, symbol in indices.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d")
                if not hist.empty:
                    current = hist['Close'].iloc[-1]
                    prev = ticker.info.get('previousClose', current)
                    change = current - prev
                    change_pct = (change / prev) * 100 if prev != 0 else 0
                    
                    emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                    summary += f"{emoji} {name}: {current:.2f} ({change_pct:+.1f}%)\n"
            except:
                summary += f"❌ {name}: 資料無法取得\n"
        
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
        
        if lower_name in ["hi", "妳好", "哈囉", "嗨", "安安"]:
            reply = "🤖 妳好！有什麼需要幫忙的嗎？\n\n📊 股票查詢：輸入公司名稱或代碼\n🌤️ 天氣查詢：輸入「天氣」\n⛽ 油價查詢：輸入「油價」\n📈 股市摘要：輸入「股市」"
            
        elif "天氣" in user_message:
            if "台北" in user_message:
                reply = get_weather("台北市")
            else:
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
