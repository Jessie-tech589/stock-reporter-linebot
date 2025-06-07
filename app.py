import os
import yfinance as yf
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import requests

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
YOUR_USER_ID = "U95eea3698b802603dd7f285a67c698b53"

# API Keys
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/", methods=['GET'])
def home():
    return "🟢 股市播報員 LINE Bot 運作中！"

@app.route("/", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 簡單的美股查詢
def get_us_stocks():
    try:
        symbols = ['NVDA', 'SMCI', 'GOOGL', 'AAPL', 'MSFT']
        stock_names = ['輝達', '美超微', 'Google', '蘋果', '微軟']
        results = []
        
        for i, symbol in enumerate(symbols):
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="2d")
                
                if len(hist) >= 1:
                    current_price = hist['Close'].iloc[-1]
                    results.append(f"• {stock_names[i]}: ${current_price:.2f}")
                else:
                    results.append(f"• {stock_names[i]}: 資料取得中...")
            except:
                results.append(f"• {stock_names[i]}: 取得失敗")
        
        return "📈 美股價格:\n" + "\n".join(results)
    except:
        return "❌ 美股資料暫時無法取得"

# 簡單的台股查詢
def get_taiwan_stocks():
    try:
        symbols = ['2330.TW', '2454.TW', '2317.TW']
        stock_names = ['台積電', '聯發科', '鴻海']
        results = []
        
        for i, symbol in enumerate(symbols):
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="2d")
                
                if len(hist) >= 1:
                    current_price = hist['Close'].iloc[-1]
                    results.append(f"• {stock_names[i]}: NT${current_price:.2f}")
                else:
                    results.append(f"• {stock_names[i]}: 資料取得中...")
            except:
                results.append(f"• {stock_names[i]}: 取得失敗")
        
        return "📊 台股價格:\n" + "\n".join(results)
    except:
        return "❌ 台股資料暫時無法取得"

# 簡單的天氣查詢
def get_weather(location):
    try:
        if not WEATHER_API_KEY:
            return f"❌ {location} 天氣: API Key 未設定"
        
        location_map = {
            "新店": "Xindian, New Taipei, Taiwan",
            "中山區": "Zhongshan District, Taipei, Taiwan",
            "中正區": "Zhongzheng District, Taipei, Taiwan"
        }
        
        search_location = location_map.get(location, location)
        today = datetime.now().strftime('%Y-%m-%d')
        
        url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{search_location}/{today}"
        params = {
            'key': WEATHER_API_KEY,
            'include': 'days',
            'elements': 'tempmax,tempmin,conditions'
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'days' in data and len(data['days']) > 0:
                day = data['days'][0]
                temp_max = day.get('tempmax', 0)
                temp_min = day.get('tempmin', 0)
                conditions = day.get('conditions', 'N/A')
                
                # 華氏轉攝氏
                temp_max_c = (temp_max - 32) * 5/9
                temp_min_c = (temp_min - 32) * 5/9
                
                return f"🌤️ {location} 天氣:\n高溫: {temp_max_c:.1f}°C\n低溫: {temp_min_c:.1f}°C\n狀況: {conditions}"
            else:
                return f"❌ {location} 天氣資料無法取得"
        else:
            return f"❌ {location} 天氣 API 錯誤"
    except:
        return f"❌ {location} 天氣查詢失敗"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_message = event.message.text
        reply = ""
        
        if user_message == "測試":
            reply = "✅ 系統正常運作！\n請輸入: 美股、台股、新店"
        
        elif user_message == "美股":
            reply = get_us_stocks()
        
        elif user_message == "台股":
            reply = get_taiwan_stocks()
        
        elif user_message == "新店":
            reply = get_weather("新店")
        
        elif user_message == "中山區":
            reply = get_weather("中山區")
        
        elif user_message == "中正區":
            reply = get_weather("中正區")
        
        elif user_message == "幫助":
            reply = """📋 可用功能:

• 測試 - 檢查系統
• 美股 - 輝達/美超微/Google等
• 台股 - 台積電/聯發科/鴻海
• 新店 - 新店天氣
• 中山區 - 中山區天氣
• 中正區 - 中正區天氣

🤖 系統簡化版，確保基本功能正常"""
        
        else:
            reply = f"❓ 不認識「{user_message}」\n請輸入「幫助」查看功能"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        
    except Exception as e:
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"系統錯誤: {str(e)}"))
        except:
            pass

if __name__ == "__main__":
    app.run()
