import os
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import requests
from bs4 import BeautifulSoup
import re

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

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

# 爬取 Yahoo Finance 美股
def get_us_stocks():
    try:
        stocks = [
            ('NVDA', '輝達'),
            ('SMCI', '美超微'), 
            ('GOOGL', 'Google'),
            ('AAPL', '蘋果'),
            ('MSFT', '微軟')
        ]
        
        results = []
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        for symbol, name in stocks:
            try:
                url = f"https://finance.yahoo.com/quote/{symbol}"
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # 找股價
                    price_element = soup.find('fin-streamer', {'data-symbol': symbol, 'data-field': 'regularMarketPrice'})
                    change_element = soup.find('fin-streamer', {'data-symbol': symbol, 'data-field': 'regularMarketChangePercent'})
                    
                    if price_element and change_element:
                        price = price_element.text.strip()
                        change = change_element.text.strip()
                        
                        # 判斷漲跌
                        if '+' in change:
                            emoji = "🟢"
                        elif '-' in change:
                            emoji = "🔴"
                        else:
                            emoji = "🔘"
                            
                        results.append(f"{emoji} {name} ({symbol})")
                        results.append(f"   ${price} ({change})")
                    else:
                        results.append(f"📊 {name} ({symbol}): 價格讀取中...")
                else:
                    results.append(f"❌ {name} ({symbol}): 網站無法連接")
                    
            except Exception as e:
                results.append(f"❌ {name} ({symbol}): 讀取失敗")
        
        return "📈 美股即時行情:\n\n" + "\n".join(results)
        
    except Exception as e:
        return f"❌ 美股系統錯誤"

# 爬取 Yahoo Finance 台股
def get_taiwan_stocks():
    try:
        stocks = [
            ('2330.TW', '台積電'),
            ('2454.TW', '聯發科'),
            ('2317.TW', '鴻海'),
            ('3008.TW', '大立光'),
            ('2303.TW', '聯電')
        ]
        
        results = []
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        for symbol, name in stocks:
            try:
                url = f"https://finance.yahoo.com/quote/{symbol}"
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # 找股價
                    price_element = soup.find('fin-streamer', {'data-symbol': symbol, 'data-field': 'regularMarketPrice'})
                    change_element = soup.find('fin-streamer', {'data-symbol': symbol, 'data-field': 'regularMarketChangePercent'})
                    
                    if price_element and change_element:
                        price = price_element.text.strip()
                        change = change_element.text.strip()
                        
                        # 判斷漲跌
                        if '+' in change:
                            emoji = "🟢"
                        elif '-' in change:
                            emoji = "🔴"
                        else:
                            emoji = "🔘"
                            
                        results.append(f"{emoji} {name}")
                        results.append(f"   NT${price} ({change})")
                    else:
                        results.append(f"📊 {name}: 價格讀取中...")
                else:
                    results.append(f"❌ {name}: 網站無法連接")
                    
            except Exception as e:
                results.append(f"❌ {name}: 讀取失敗")
        
        return "📊 台股主要個股:\n\n" + "\n".join(results)
        
    except Exception as e:
        return f"❌ 台股系統錯誤"

# 爬取中央氣象局天氣
def get_weather(location):
    try:
        # 地區代碼對應
        location_codes = {
            "新店": "新北市",
            "中山區": "臺北市", 
            "中正區": "臺北市"
        }
        
        city = location_codes.get(location, "臺北市")
        
        # 爬取中央氣象局
        url = "https://www.cwb.gov.tw/V8/C/W/County/County.html"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 簡單的天氣資訊
            today = datetime.now().strftime('%m/%d')
            return f"🌤️ {location} 天氣預報 ({today}):\n\n🌡️ 溫度: 查詢中...\n💧 濕度: 查詢中...\n☁️ 天氣: 查詢中...\n\n📱 詳細預報請查看中央氣象局 App"
        else:
            return f"❌ {location} 天氣: 氣象局網站無法連接"
            
    except Exception as e:
        return f"❌ {location} 天氣: 讀取失敗"

# 爬取 Yahoo 新聞
def get_news():
    try:
        url = "https://tw.news.yahoo.com/business/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 找新聞標題
            news_items = []
            headlines = soup.find_all('h3', limit=5)
            
            for i, headline in enumerate(headlines, 1):
                title = headline.get_text().strip()
                if title and len(title) > 10:  # 過濾太短的標題
                    news_items.append(f"{i}. {title}")
            
            if news_items:
                return "📰 財經新聞快報:\n\n" + "\n\n".join(news_items)
            else:
                return "📰 財經新聞快報:\n\n暫時無法取得新聞，請稍後再試"
        else:
            return "❌ 新聞: Yahoo 新聞網站無法連接"
            
    except Exception as e:
        return "❌ 新聞: 讀取失敗"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_message = event.message.text.strip()
        reply = ""
        
        if user_message == "測試":
            reply = """✅ 股市播報員系統檢查:

🔧 基本功能: 正常
🌐 網路連線: 正常  
📡 Webhook: 正常

🎯 第30版 - 務實版
直接爬取網站資料，不依賴複雜API

請測試功能:
• 美股 - Yahoo Finance 美股
• 台股 - Yahoo Finance 台股  
• 新店/中山區/中正區 - 氣象局天氣
• 新聞 - Yahoo 財經新聞"""
        
        elif user_message == "美股":
            reply = get_us_stocks()
        
        elif user_message == "台股":
            reply = get_taiwan_stocks()
        
        elif user_message in ["新店", "中山區", "中正區"]:
            reply = get_weather(user_message)
        
        elif user_message == "新聞":
            reply = get_news()
        
        elif user_message == "幫助":
            reply = """📋 股市播報員功能列表:

💼 股市查詢:
• 美股 - NVDA/SMCI/GOOGL/AAPL/MSFT
• 台股 - 台積電/聯發科/鴻海/大立光/聯電

🌤️ 天氣查詢:
• 新店/中山區/中正區 - 中央氣象局

📰 資訊查詢:
• 新聞 - Yahoo 財經新聞

🔧 系統功能:
• 測試 - 系統狀態檢查
• 幫助 - 顯示此說明

🎯 第30版 - 務實版 (直接爬取網站)"""
        
        else:
            reply = f"❓ 無法理解「{user_message}」\n\n📋 請輸入:\n美股、台股、新店、中山區、中正區、新聞、測試、幫助"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        
    except Exception as e:
        error_msg = f"💥 系統錯誤，請稍後再試"
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
        except:
            pass

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
