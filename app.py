import os
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import requests
import json

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

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 改用 Yahoo Finance API (不透過 yfinance)
def get_us_stocks():
    try:
        # 使用 Yahoo Finance 的公開 API
        symbols = ['NVDA', 'SMCI', 'GOOGL', 'AAPL', 'MSFT']
        stock_names = ['輝達 NVIDIA', '美超微 SMCI', 'Google Alphabet', '蘋果 Apple', '微軟 Microsoft']
        results = []
        
        for i, symbol in enumerate(symbols):
            try:
                # Yahoo Finance API v8
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'chart' in data and data['chart']['result']:
                        chart_data = data['chart']['result'][0]
                        
                        # 取得最新價格
                        if 'meta' in chart_data and 'regularMarketPrice' in chart_data['meta']:
                            current_price = chart_data['meta']['regularMarketPrice']
                            prev_close = chart_data['meta'].get('previousClose', current_price)
                            
                            # 計算漲跌
                            change = current_price - prev_close
                            change_percent = (change / prev_close) * 100 if prev_close != 0 else 0
                            
                            emoji = "🟢" if change >= 0 else "🔴"
                            
                            results.append(f"{emoji} {stock_names[i]}")
                            results.append(f"   ${current_price:.2f} ({change_percent:+.2f}%)")
                        else:
                            results.append(f"❌ {stock_names[i]}: 價格資料不完整")
                    else:
                        results.append(f"❌ {stock_names[i]}: 無效的資料格式")
                else:
                    results.append(f"❌ {stock_names[i]}: API 回應錯誤 ({response.status_code})")
                    
            except requests.exceptions.Timeout:
                results.append(f"⏰ {stock_names[i]}: 請求超時")
            except Exception as e:
                results.append(f"❌ {stock_names[i]}: {str(e)[:30]}...")
        
        return "📈 美股即時價格:\n\n" + "\n".join(results)
        
    except Exception as e:
        return f"❌ 美股系統錯誤: {str(e)}"

# 改用台股 API
def get_taiwan_stocks():
    try:
        # 台股代號對應
        symbols = ['2330', '2454', '2317', '3008', '2303']
        stock_names = ['台積電', '聯發科', '鴻海', '大立光', '聯電']
        results = []
        
        for i, symbol in enumerate(symbols):
            try:
                # 使用台股 API (TWSE 或第三方)
                url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{symbol}.tw"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'msgArray' in data and len(data['msgArray']) > 0:
                        stock_data = data['msgArray'][0]
                        
                        current_price = float(stock_data.get('z', 0))  # 成交價
                        prev_close = float(stock_data.get('y', 0))     # 昨收
                        
                        if current_price > 0 and prev_close > 0:
                            change = current_price - prev_close
                            change_percent = (change / prev_close) * 100
                            
                            emoji = "🟢" if change >= 0 else "🔴"
                            
                            results.append(f"{emoji} {stock_names[i]} ({symbol})")
                            results.append(f"   NT${current_price:.2f} ({change_percent:+.2f}%)")
                        else:
                            results.append(f"📊 {stock_names[i]} ({symbol}): 休市中")
                    else:
                        results.append(f"❌ {stock_names[i]} ({symbol}): 無資料")
                else:
                    results.append(f"❌ {stock_names[i]} ({symbol}): API 錯誤")
                    
            except Exception as e:
                results.append(f"❌ {stock_names[i]} ({symbol}): {str(e)[:30]}...")
        
        # 判斷是否為交易時間
        now = datetime.now()
        if now.weekday() >= 5:  # 週末
            status_msg = "📊 台股主要個股 (週末休市):\n\n"
        elif now.hour < 9 or now.hour >= 14:  # 非交易時間
            status_msg = "📊 台股主要個股 (收盤後):\n\n"
        else:
            status_msg = "📊 台股主要個股 (交易中):\n\n"
            
        return status_msg + "\n".join(results)
        
    except Exception as e:
        return f"❌ 台股系統錯誤: {str(e)}"

# 改善天氣 API
def get_weather(location):
    try:
        # 檢查 API Key
        if not WEATHER_API_KEY or WEATHER_API_KEY == "":
            return f"❌ {location} 天氣: Weather API Key 未設定或為空"
        
        # 地點映射
        location_map = {
            "新店": "Xindian District, New Taipei City, Taiwan",
            "中山區": "Zhongshan District, Taipei City, Taiwan", 
            "中正區": "Zhongzheng District, Taipei City, Taiwan"
        }
        
        search_location = location_map.get(location, f"{location}, Taiwan")
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Visual Crossing API
        url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{search_location}/{today}"
        
        params = {
            'key': WEATHER_API_KEY,
            'include': 'days,current',
            'elements': 'temp,tempmax,tempmin,humidity,conditions,description',
            'unitGroup': 'metric'  # 使用攝氏度
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        print(f"天氣 API 回應: {response.status_code}")  # 除錯用
        
        if response.status_code == 200:
            data = response.json()
            
            if 'days' in data and len(data['days']) > 0:
                day_data = data['days'][0]
                current_data = data.get('currentConditions', {})
                
                # 取得溫度資料
                current_temp = current_data.get('temp')
                temp_max = day_data.get('tempmax')
                temp_min = day_data.get('tempmin')
                humidity = day_data.get('humidity', 0)
                conditions = day_data.get('conditions', 'N/A')
                
                result = f"🌤️ {location} 天氣 ({today}):\n\n"
                
                if current_temp is not None:
                    result += f"🌡️ 現在溫度: {current_temp:.1f}°C\n"
                if temp_max is not None and temp_min is not None:
                    result += f"🌡️ 高低溫: {temp_max:.1f}°C / {temp_min:.1f}°C\n"
                result += f"💧 濕度: {humidity:.0f}%\n"
                result += f"☁️ 天氣狀況: {conditions}"
                
                return result
            else:
                return f"❌ {location} 天氣: API 回傳資料格式錯誤"
        elif response.status_code == 401:
            return f"❌ {location} 天氣: API Key 無效或過期"
        elif response.status_code == 429:
            return f"❌ {location} 天氣: API 使用量超過限制"
        else:
            return f"❌ {location} 天氣: API 錯誤 (狀態碼: {response.status_code})"
            
    except requests.exceptions.Timeout:
        return f"⏰ {location} 天氣: API 請求超時"
    except requests.exceptions.RequestException as e:
        return f"❌ {location} 天氣: 網路錯誤 - {str(e)[:50]}..."
    except Exception as e:
        return f"❌ {location} 天氣: 系統錯誤 - {str(e)[:50]}..."

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_message = event.message.text.strip()
        reply = ""
        
        # 除錯：記錄收到的訊息
        print(f"收到訊息: '{user_message}'")
        
        if user_message == "測試":
            reply = """✅ 股市播報員系統檢查:

🔧 基本功能: 正常
🌐 網路連線: 正常  
📡 Webhook: 正常

請測試功能:
• 美股 - 即時美股價格
• 台股 - 台灣股市行情
• 新店 - 新店區天氣"""
        
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
            reply = """📋 股市播報員功能列表:

💼 股市查詢:
• 美股 - NVDA/SMCI/GOOGL/AAPL/MSFT
• 台股 - 台積電/聯發科/鴻海/大立光/聯電

🌤️ 天氣查詢:
• 新店 - 新店區天氣預報
• 中山區 - 中山區天氣預報
• 中正區 - 中正區天氣預報

🔧 系統功能:
• 測試 - 系統狀態檢查
• 幫助 - 顯示此說明

🤖 第29版 - 完全重構版"""
        
        else:
            reply = f"❓ 無法理解「{user_message}」\n\n📋 請輸入以下指令:\n美股、台股、新店、中山區、中正區、測試、幫助"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        
    except Exception as e:
        error_msg = f"💥 系統錯誤: {str(e)[:100]}...\n\n請稍後再試或聯絡管理員"
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
        except:
            print(f"回覆錯誤訊息失敗: {e}")

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
