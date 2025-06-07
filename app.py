import os
import requests
from datetime import datetime
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# LINE Bot 設定
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# 台灣時區
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

# 定時推送設定
SCHEDULED_MESSAGES = [
    {"time": "07:10", "message": "morning_briefing", "days": "daily"},      # 新店天氣+美股+行程+節假日
    {"time": "08:00", "message": "commute_to_work", "days": "weekdays"},    # 中山區天氣+家→公司車流
    {"time": "09:30", "message": "market_open", "days": "weekdays"},        # 台股開盤+國內外新聞
    {"time": "12:00", "message": "market_mid", "days": "weekdays"},         # 台股盤中
    {"time": "13:45", "message": "market_close", "days": "weekdays"},       # 台股收盤
    {"time": "17:30", "message": "evening_zhongzheng", "days": "135"},      # 中正區天氣+公司→郵局車流(一三五)
    {"time": "17:30", "message": "evening_xindian", "days": "24"}           # 新店天氣+公司→家車流(二四)
]

# 固定地址
ADDRESSES = {
    "home": "新店區建國路99巷",
    "office": "台北市南京東路三段131號", 
    "post_office": "台北市愛國東路216號"
}

# ==================== 核心功能函數 ====================

def get_weather(location):
    """取得指定地區天氣"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%m/%d %H:%M')
        
        # 實際需要串接氣象局API
        weather_data = f"☀️ {location}天氣 ({current_time}):\n\n🌡️ 溫度: 25°C\n💨 微風\n☁️ 多雲\n🌧️ 降雨機率: 20%\n\n⚠️ 氣象局API整合開發中..."
        return weather_data
        
    except Exception as e:
        return f"❌ {location}天氣查詢失敗: {str(e)}"

def get_us_stocks():
    """爬取美股即時資料"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        today = taiwan_time.strftime('%m/%d %H:%M')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        results = []
        
        # 大盤指數
        indices = [
            ('^DJI', '道瓊指數'),
            ('^IXIC', '那斯達克'),
            ('^GSPC', 'S&P 500')
        ]
        
        results.append("📊 美股大盤指數:")
        
        for symbol, name in indices:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
                response = requests.get(url, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if ('chart' in data and 'result' in data['chart'] and 
                        data['chart']['result'] and len(data['chart']['result']) > 0):
                        
                        meta = data['chart']['result'][0]['meta']
                        current_price = meta.get('regularMarketPrice')
                        prev_close = meta.get('previousClose')
                        
                        if current_price is not None and prev_close is not None and current_price > 0:
                            change = current_price - prev_close
                            change_percent = (change / prev_close) * 100
                            
                            emoji = "🟢" if change > 0 else "🔴" if change < 0 else "🔘"
                            sign = "+" if change > 0 else ""
                            
                            results.append(f"{emoji} {name}")
                            results.append(f"   {current_price:,.2f} ({sign}{change_percent:.2f}%)")
                        else:
                            results.append(f"❌ {name}: 價格資料不完整")
                    else:
                        results.append(f"❌ {name}: 無效回應格式")
                else:
                    results.append(f"❌ {name}: HTTP {response.status_code}")
                    
            except Exception as e:
                results.append(f"❌ {name}: 連線失敗")
        
        results.append("")
        
        # 個股 - 指定的5檔股票
        stocks = [
            ('NVDA', '輝達'),
            ('SMCI', '美超微'),
            ('GOOGL', 'Google'),
            ('AAPL', '蘋果'),
            ('MSFT', '微軟')
        ]
        
        results.append("📈 主要個股:")
        
        for symbol, name in stocks:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
                response = requests.get(url, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if ('chart' in data and 'result' in data['chart'] and 
                        data['chart']['result'] and len(data['chart']['result']) > 0):
                        
                        meta = data['chart']['result'][0]['meta']
                        current_price = meta.get('regularMarketPrice')
                        prev_close = meta.get('previousClose')
                        
                        if current_price is not None and prev_close is not None and current_price > 0:
                            change = current_price - prev_close
                            change_percent = (change / prev_close) * 100
                            
                            emoji = "🟢" if change > 0 else "🔴" if change < 0 else "🔘"
                            sign = "+" if change > 0 else ""
                            
                            results.append(f"{emoji} {name} ({symbol})")
                            results.append(f"   ${current_price:.2f} ({sign}{change_percent:.2f}%)")
                        else:
                            results.append(f"❌ {name}: 價格資料不完整")
                    else:
                        results.append(f"❌ {name}: 無效回應格式")
                else:
                    results.append(f"❌ {name}: HTTP {response.status_code}")
                    
            except Exception as e:
                results.append(f"❌ {name}: 連線失敗")
        
        # 檢查是否有成功取得的資料
        success_count = sum(1 for line in results if line.startswith(('🟢', '🔴', '🔘')))
        
        if success_count > 0:
            return f"📈 美股即時行情 ({today}):\n\n" + "\n".join(results) + f"\n\n✅ 成功取得 {success_count} 筆真實資料"
        else:
            return "❌ 無法取得任何美股真實資料，API可能暫時無法使用"
        
    except Exception as e:
        return f"❌ 美股爬蟲系統錯誤: {str(e)}"

def get_taiwan_stocks():
    """爬取台股即時資料"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        today = taiwan_time.strftime('%m/%d %H:%M')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        results = []
        
        # 台股加權指數
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1m&range=1d"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                if ('chart' in data and 'result' in data['chart'] and 
                    data['chart']['result'] and len(data['chart']['result']) > 0):
                    
                    meta = data['chart']['result'][0]['meta']
                    current_price = meta.get('regularMarketPrice')
                    prev_close = meta.get('previousClose')
                    
                    if current_price is not None and prev_close is not None and current_price > 0:
                        change = current_price - prev_close
                        change_percent = (change / prev_close) * 100
                        
                        emoji = "🟢" if change > 0 else "🔴" if change < 0 else "🔘"
                        sign = "+" if change > 0 else ""
                        
                        results.append("📊 台股大盤:")
                        results.append(f"{emoji} 加權指數")
                        results.append(f"   {current_price:.2f} ({sign}{change_percent:.2f}%)")
                        results.append("")
                    else:
                        results.append("❌ 台股加權指數: 價格資料不完整")
                        results.append("")
                else:
                    results.append("❌ 台股加權指數: 無效回應格式")
                    results.append("")
            else:
                results.append(f"❌ 台股加權指數: HTTP {response.status_code}")
                results.append("")
                
        except Exception as e:
            results.append(f"❌ 台股加權指數: 連線失敗")
            results.append("")
        
        # 個股資料
        stocks = [
            ('2330.TW', '台積電'),
            ('2454.TW', '聯發科'),
            ('2317.TW', '鴻海'),
            ('3008.TW', '大立光'),
            ('2303.TW', '聯電')
        ]
        
        results.append("📈 主要個股:")
        
        for symbol, name in stocks:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
                response = requests.get(url, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if ('chart' in data and 'result' in data['chart'] and 
                        data['chart']['result'] and len(data['chart']['result']) > 0):
                        
                        meta = data['chart']['result'][0]['meta']
                        current_price = meta.get('regularMarketPrice')
                        prev_close = meta.get('previousClose')
                        
                        if current_price is not None and prev_close is not None and current_price > 0:
                            change = current_price - prev_close
                            change_percent = (change / prev_close) * 100
                            
                            emoji = "🟢" if change > 0 else "🔴" if change < 0 else "🔘"
                            sign = "+" if change > 0 else ""
                            
                            stock_code = symbol.replace('.TW', '')
                            results.append(f"{emoji} {name} ({stock_code})")
                            results.append(f"   NT${current_price:.2f} ({sign}{change_percent:.1f}%)")
                        else:
                            stock_code = symbol.replace('.TW', '')
                            results.append(f"❌ {name} ({stock_code}): 價格資料不完整")
                    else:
                        stock_code = symbol.replace('.TW', '')
                        results.append(f"❌ {name} ({stock_code}): 無效回應格式")
                else:
                    stock_code = symbol.replace('.TW', '')
                    results.append(f"❌ {name} ({stock_code}): HTTP {response.status_code}")
                    
            except Exception as e:
                stock_code = symbol.replace('.TW', '')
                results.append(f"❌ {name} ({stock_code}): 連線失敗")
        
        # 檢查是否有成功取得的資料
        success_count = sum(1 for line in results if line.startswith(('🟢', '🔴', '🔘')))
        
        if success_count > 0:
            return f"📈 台股即時行情 ({today}):\n\n" + "\n".join(results) + f"\n\n✅ 成功取得 {success_count} 筆真實資料"
        else:
            return "❌ 無法取得任何台股真實資料，API可能暫時無法使用"
        
    except Exception as e:
        return f"❌ 台股爬蟲系統錯誤: {str(e)}"

def get_news():
    """取得新聞資訊"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%m/%d %H:%M')
        
        # 實際需要串接新聞API
        news_data = f"📰 國內外新聞 ({current_time}):\n\n🇹🇼 台灣新聞:\n• 新聞1標題\n• 新聞2標題\n\n🌍 國際新聞:\n• 國際新聞1\n• 國際新聞2\n\n⚠️ 新聞API整合開發中..."
        return news_data
        
    except Exception as e:
        return f"❌ 新聞查詢失敗: {str(e)}"

def get_route_traffic(origin, destination, route_name):
    """查詢機車路線車流 - Google Maps API版本"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        
        # 實際需要Google Maps API
        route_info = f"🏍️ {route_name} ({current_time})\n\n📍 起點: {origin}\n📍 終點: {destination}\n\n⚠️ Google Maps API整合開發中...\n💡 需要申請API金鑰取得即時車流"
        return route_info
        
    except Exception as e:
        return f"❌ 路線查詢失敗: {str(e)}"

def get_traffic():
    """機車路線車流總覽"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        
        results = []
        results.append(f"🏍️ 機車路線車流 ({current_time}):")
        results.append("")
        
        # 三條主要路線
        routes = [
            ("🏠→🏢 家→公司", ADDRESSES["home"], ADDRESSES["office"]),
            ("🏢→🏠 公司→家", ADDRESSES["office"], ADDRESSES["home"]),
            ("🏢→📮 公司→郵局", ADDRESSES["office"], ADDRESSES["post_office"])
        ]
        
        for route_name, origin, destination in routes:
            results.append(f"{route_name}")
            results.append(f"📍 {origin}")
            results.append(f"📍 {destination}")
            results.append("⚠️ Google Maps API整合開發中...")
            results.append("")
        
        results.append("🔧 待整合功能:")
        results.append("• Google Maps Directions API")
        results.append("• 即時交通狀況")
        results.append("• 機車路線優化")
        
        return "\n".join(results)
        
    except Exception as e:
        return f"❌ 車流查詢錯誤: {str(e)}"

# ==================== 綜合推送函數 ====================

def get_morning_briefing():
    """07:10 早安綜合資訊"""
    weather = get_weather("新店")
    us_stocks = get_us_stocks()
    calendar_info = "📅 今日行程: (Google Calendar整合開發中...)"
    holidays = "🎉 節假日: (節假日API整合開發中...)"
    
    return f"🌅 早安！今日綜合資訊\n\n{weather}\n\n{us_stocks}\n\n{calendar_info}\n\n{holidays}"

def get_commute_to_work():
    """08:00 上班通勤資訊"""
    weather = get_weather("中山區")
    traffic = get_route_traffic(ADDRESSES["home"], ADDRESSES["office"], "🏠→🏢 家→公司")
    
    return f"🏃‍♂️ 上班通勤資訊\n\n{weather}\n\n{traffic}"

def get_market_open():
    """09:30 開盤資訊"""
    taiwan_stocks = get_taiwan_stocks()
    news = get_news()
    
    return f"📈 開盤資訊\n\n{taiwan_stocks}\n\n{news}"

def get_evening_zhongzheng():
    """17:30 下班資訊(一三五)"""
    weather = get_weather("中正區")
    traffic = get_route_traffic(ADDRESSES["office"], ADDRESSES["post_office"], "🏢→📮 公司→郵局")
    
    return f"🌆 下班資訊 (一三五)\n\n{weather}\n\n{traffic}"

def get_evening_xindian():
    """17:30 下班資訊(二四)"""
    weather = get_weather("新店")
    traffic = get_route_traffic(ADDRESSES["office"], ADDRESSES["home"], "🏢→🏠 公司→家")
    
    return f"🌆 下班資訊 (二四)\n\n{weather}\n\n{traffic}"

# ==================== LINE Bot 處理 ====================

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
    user_message = event.message.text.strip()
    reply = ""
    
    try:
        # 綜合推送訊息
        if user_message == "morning_briefing":
            reply = get_morning_briefing()
        elif user_message == "commute_to_work":
            reply = get_commute_to_work()
        elif user_message == "market_open":
            reply = get_market_open()
        elif user_message == "market_mid":
            reply = get_taiwan_stocks()
        elif user_message == "market_close":
            reply = get_taiwan_stocks()
        elif user_message == "evening_zhongzheng":
            reply = get_evening_zhongzheng()
        elif user_message == "evening_xindian":
            reply = get_evening_xindian()
        
        # 單項功能查詢
        elif user_message == "美股":
            reply = get_us_stocks()
        elif user_message == "台股":
            reply = get_taiwan_stocks()
        elif user_message == "新聞":
            reply = get_news()
        elif user_message == "車流":
            reply = get_traffic()
        elif user_message in ["新店", "中山區", "中正區"]:
            reply = get_weather(user_message)
        
        # 測試功能
        elif user_message == "測試":
            reply = "🤖 系統測試 v42\n\n✅ 連線正常\n✅ 推送系統運作中\n✅ 重寫版本\n\n📋 功能列表:\n• 美股、台股 (真實API)\n• 天氣 (新店/中山區/中正區)\n• 車流 (機車路線)\n• 新聞\n\n⏰ 定時推送:\n• 07:10 早安綜合\n• 08:00 上班通勤\n• 09:30 開盤+新聞\n• 12:00 台股盤中\n• 13:45 台股收盤\n• 17:30 下班資訊"
        
        # 說明功能
        elif user_message == "幫助":
            reply = "📚 LINE Bot 功能說明:\n\n🔍 單項查詢:\n• 美股 - 道瓊+個股(NVDA/SMCI/GOOGL/AAPL/MSFT)\n• 台股 - 加權指數+個股\n• 新聞 - 國內外新聞\n• 車流 - 機車路線車流\n• 新店/中山區/中正區 - 天氣\n\n⏰ 自動推送:\n• 每日07:10 - 早安綜合資訊\n• 上班日推送 - 通勤/開盤/收盤資訊\n\n🏍️ 機車路線:\n• 家 ↔ 公司\n• 公司 → 郵局"
        
        else:
            reply = "❓ 無法識別指令。\n\n輸入「幫助」查看功能列表\n輸入「測試」檢查系統狀態"
        
        # 發送回覆
        if reply:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply)
            )
    
    except Exception as e:
        error_message = f"❌ 系統錯誤: {str(e)[:100]}"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=error_message)
        )

# ==================== 定時推送系統 ====================

@app.route("/send_scheduled", methods=['POST'])
def send_scheduled():
    """處理定時推送請求"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        current_time = taiwan_time.strftime('%H:%M')
        current_weekday = taiwan_time.weekday()  # 0=Monday, 6=Sunday
        
        for schedule in SCHEDULED_MESSAGES:
            if schedule['time'] == current_time:
                should_send = False
                
                if schedule['days'] == 'daily':
                    should_send = True
                elif schedule['days'] == 'weekdays' and current_weekday < 5:
                    should_send = True
                elif schedule['days'] == '135' and current_weekday in [0, 2, 4]:  # 一三五
                    should_send = True
                elif schedule['days'] == '24' and current_weekday in [1, 3]:  # 二四
                    should_send = True
                
                if should_send:
                    message_type = schedule['message']
                    
                    # 根據訊息類型產生內容
                    if message_type == "morning_briefing":
                        message = get_morning_briefing()
                    elif message_type == "commute_to_work":
                        message = get_commute_to_work()
                    elif message_type == "market_open":
                        message = get_market_open()
                    elif message_type == "market_mid":
                        message = get_taiwan_stocks()
                    elif message_type == "market_close":
                        message = get_taiwan_stocks()
                    elif message_type == "evening_zhongzheng":
                        message = get_evening_zhongzheng()
                    elif message_type == "evening_xindian":
                        message = get_evening_xindian()
                    else:
                        continue
                    
                    # 發送訊息 (需要設定USER_ID)
                    user_id = os.environ.get('LINE_USER_ID')
                    if user_id:
                        line_bot_api.push_message(user_id, TextSendMessage(text=message))
        
        return 'OK'
    
    except Exception as e:
        return f'Error: {str(e)}'

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
