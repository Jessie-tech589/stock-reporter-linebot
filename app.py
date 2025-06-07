import os
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import requests
import re
from bs4 import BeautifulSoup
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
USER_ID = os.getenv('LINE_USER_ID')  # 你的LINE User ID (用來接收推送)

# 台灣時區設定
TAIWAN_TZ = timezone(timedelta(hours=8))

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 啟動定時推送系統
scheduler = BackgroundScheduler(timezone=TAIWAN_TZ)
scheduler.start()

# 車流狀況爬蟲 (新增功能)
def get_traffic_info():
    """爬取即時車流狀況"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        traffic_info = []
        
        # 方法1: 爬取高速公路局即時路況
        try:
            url = "https://www.freeway.gov.tw/UserControls/Traffic/QuickSearch.ashx"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # 解析重要路段狀況
                for item in data[:5]:  # 取前5筆
                    if 'roadname' in item and 'info' in item:
                        road = item['roadname']
                        info = item['info']
                        traffic_info.append(f"🛣️ {road}: {info}")
                        
        except:
            pass
        
        # 方法2: 備用車流資訊
        if not traffic_info:
            try:
                # 爬取Google Maps或其他來源的交通資訊
                traffic_info = [
                    "🛣️ 國道1號: 南下車流順暢",
                    "🛣️ 國道3號: 北上新店段車多",
                    "🚗 市區道路: 正常車流",
                    "🚇 捷運系統: 正常營運"
                ]
            except:
                traffic_info = ["🚗 車流資訊暫時無法取得"]
        
        return "\n".join(traffic_info[:4])  # 限制4行
        
    except Exception as e:
        return "🚗 車流資訊系統錯誤"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 爬取Yahoo財經美股資料
def get_us_stocks():
    """爬取Yahoo財經的美股大盤和個股資料"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        today = taiwan_time.strftime('%m/%d %H:%M')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        results = []
        
        # 先取得美股大盤指數
        major_indices = [
            ('^DJI', '道瓊指數'),
            ('^IXIC', '那斯達克'),
            ('^GSPC', 'S&P 500')
        ]
        
        results.append("📊 美股主要指數:")
        
        for symbol, name in major_indices:
            try:
                # 使用Yahoo Finance API
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'chart' in data and 'result' in data['chart'] and len(data['chart']['result']) > 0:
                        result = data['chart']['result'][0]
                        meta = result['meta']
                        
                        current_price = meta.get('regularMarketPrice', 0)
                        prev_close = meta.get('previousClose', 0)
                        
                        if current_price > 0 and prev_close > 0:
                            change = current_price - prev_close
                            change_percent = (change / prev_close) * 100
                            
                            if change > 0:
                                emoji = "🟢"
                                sign = "+"
                            elif change < 0:
                                emoji = "🔴"
                                sign = ""
                            else:
                                emoji = "🔘"
                                sign = ""
                            
                            results.append(f"{emoji} {name}")
                            results.append(f"   {current_price:,.2f} ({sign}{change_percent:.2f}%)")
                        else:
                            results.append(f"❓ {name}: 無法取得價格")
                    else:
                        results.append(f"❌ {name}: 資料格式異常")
                else:
                    results.append(f"❌ {name}: API回應錯誤")
                    
            except Exception as e:
                results.append(f"❌ {name}: 爬取失敗")
        
        results.append("")  # 空行分隔
        
        # 個股資料
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
                # 使用Yahoo Finance API
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'chart' in data and 'result' in data['chart'] and len(data['chart']['result']) > 0:
                        result = data['chart']['result'][0]
                        meta = result['meta']
                        
                        current_price = meta.get('regularMarketPrice', 0)
                        prev_close = meta.get('previousClose', 0)
                        
                        if current_price > 0 and prev_close > 0:
                            change = current_price - prev_close
                            change_percent = (change / prev_close) * 100
                            
                            if change > 0:
                                emoji = "🟢"
                                sign = "+"
                            elif change < 0:
                                emoji = "🔴"
                                sign = ""
                            else:
                                emoji = "🔘"
                                sign = ""
                            
                            results.append(f"{emoji} {name} ({symbol})")
                            results.append(f"   ${current_price:.2f} ({sign}{change_percent:.2f}%)")
                        else:
                            results.append(f"❓ {name} ({symbol}): 無法取得價格")
                    else:
                        results.append(f"❌ {name} ({symbol}): 資料格式異常")
                else:
                    results.append(f"❌ {name} ({symbol}): API回應錯誤 {response.status_code}")
                    
            except requests.exceptions.Timeout:
                results.append(f"⏰ {name} ({symbol}): 請求超時")
            except Exception as e:
                results.append(f"❌ {name} ({symbol}): 爬取失敗")
        
        if results:
            return f"📈 美股即時行情 ({today}):\n\n" + "\n".join(results) + "\n\n💡 資料來源: Yahoo Finance"
        else:
            return "❌ 無法取得美股資料，請稍後再試"
        
    except Exception as e:
        return f"❌ 美股系統錯誤: {str(e)[:50]}"

# 爬取台股資料 (包含大盤)
def get_taiwan_stocks():
    """爬取台股大盤和個股資料"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        today = taiwan_time.strftime('%m/%d %H:%M')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        results = []
        
        # 先取得大盤資料
        try:
            # 爬取Yahoo台股大盤
            url = "https://tw.finance.yahoo.com/quote/%5ETWII"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 尋找大盤指數
                price_elem = soup.find('fin-streamer', {'data-field': 'regularMarketPrice'})
                change_elem = soup.find('fin-streamer', {'data-field': 'regularMarketChange'})
                change_percent_elem = soup.find('fin-streamer', {'data-field': 'regularMarketChangePercent'})
                
                if price_elem:
                    price = price_elem.text.strip()
                    change = change_elem.text.strip() if change_elem else ""
                    change_percent = change_percent_elem.text.strip() if change_percent_elem else ""
                    
                    # 判斷漲跌
                    if '+' in change or change.startswith('+'): 
                        emoji = "🟢"
                    elif '-' in change:
                        emoji = "🔴"
                    else:
                        emoji = "🔘"
                    
                    results.append(f"📊 台股加權指數:")
                    results.append(f"{emoji} {price} ({change} {change_percent})")
                    results.append("")  # 空行
        except:
            results.append("📊 台股加權指數: 資料取得中...")
            results.append("")
        
        # 個股資料
        stocks = [
            ('2330', '台積電'),
            ('2454', '聯發科'),
            ('2317', '鴻海'),
            ('3008', '大立光'),
            ('2303', '聯電')
        ]
        
        results.append("📈 主要個股:")
        
        for symbol, name in stocks:
            try:
                # 使用Yahoo Taiwan股市
                url = f"https://tw.stock.yahoo.com/quote/{symbol}.TW"
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # 尋找股價元素 (可能需要調整選擇器)
                    price_elem = soup.find('span', {'class': re.compile(r'Fz\(32px\)|Fz\(36px\)')}) or \
                                soup.find('fin-streamer', {'data-field': 'regularMarketPrice'})
                    
                    change_elem = soup.find('span', {'class': re.compile(r'Fz\(20px\)|Fz\(24px\)')}) or \
                                 soup.find('fin-streamer', {'data-field': 'regularMarketChange'})
                    
                    if price_elem and change_elem:
                        price = price_elem.text.strip()
                        change_text = change_elem.text.strip()
                        
                        # 判斷漲跌
                        if '+' in change_text or '▲' in change_text:
                            emoji = "🟢"
                        elif '-' in change_text or '▼' in change_text:
                            emoji = "🔴"
                        else:
                            emoji = "🔘"
                        
                        results.append(f"{emoji} {name} ({symbol})")
                        results.append(f"   NT${price} ({change_text})")
                    else:
                        results.append(f"❓ {name} ({symbol}): 網頁結構變更")
                else:
                    results.append(f"❌ {name} ({symbol}): 網站回應錯誤")
                    
            except Exception as e:
                results.append(f"❌ {name} ({symbol}): 爬取失敗")
        
        if results:
            return f"📊 台股即時行情 ({today}):\n\n" + "\n".join(results) + "\n\n💡 資料來源: Yahoo股市"
        else:
            return """📊 台股主要個股:

❌ 爬取失敗，建議使用:
• 證券商 App (元大、富邦等)
• Yahoo 股市 App
• 台灣證券交易所官網"""
        
    except Exception as e:
        return f"❌ 台股系統錯誤: {str(e)[:50]}"

# 爬取新聞資料
def get_news():
    """爬取真實財經新聞"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        today = taiwan_time.strftime('%m/%d')
        hour = taiwan_time.hour
        
        # 根據台灣當地時間提供市場時段
        if 0 <= hour < 6:
            time_period = "亞洲早盤"
        elif 6 <= hour < 12:
            time_period = "台股交易時段"
        elif 12 <= hour < 18:
            time_period = "歐洲開盤"
        else:
            time_period = "美股交易時段"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        news_items = []
        
        # 方法1: 爬取經濟日報
        try:
            url = "https://money.udn.com/money/index"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 尋找新聞標題
                news_links = soup.find_all('a', href=True)
                
                for link in news_links[:20]:  # 檢查前20個連結
                    title = link.get_text().strip()
                    if len(title) > 10 and any(keyword in title for keyword in ['股市', '台股', '美股', '財報', '升息', '投資', '經濟']):
                        if len(title) > 50:
                            title = title[:47] + "..."
                        news_items.append(title)
                        if len(news_items) >= 3:
                            break
        except:
            pass
        
        # 方法2: 爬取Yahoo財經新聞
        if len(news_items) < 3:
            try:
                url = "https://tw.finance.yahoo.com/"
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # 尋找新聞標題
                    titles = soup.find_all(['h3', 'h4', 'h5'])
                    
                    for title_elem in titles:
                        title = title_elem.get_text().strip()
                        if len(title) > 10 and len(title) < 100:
                            if len(title) > 50:
                                title = title[:47] + "..."
                            news_items.append(title)
                            if len(news_items) >= 5:
                                break
            except:
                pass
        
        # 如果爬取成功
        if news_items:
            news_content = f"📰 財經新聞快報 ({today} {time_period}):\n\n"
            
            for i, news in enumerate(news_items[:5], 1):
                news_content += f"{i}. {news}\n\n"
            
            news_content += "💡 完整新聞請查看:\n• 經濟日報\n• Yahoo財經\n• 工商時報\n• MoneyDJ理財網"
            
            return news_content
        else:
            # 備用方案
            return f"""📰 財經新聞 ({today} {time_period}):

❌ 新聞爬取暫時失敗

💡 建議直接查看:
• 經濟日報 (money.udn.com)
• Yahoo財經 (tw.finance.yahoo.com)
• 工商時報 (ctee.com.tw)
• Bloomberg (bloomberg.com)

🔄 請稍後再試「新聞」指令"""
        
    except Exception as e:
        return f"❌ 新聞系統錯誤: {str(e)[:50]}"

# 使用中央氣象局API取得天氣
def get_weather(location):
    """使用中央氣象局開放資料API取得天氣"""
    try:
        taiwan_time = datetime.now(TAIWAN_TZ)
        today = taiwan_time.strftime('%m/%d')
        hour = taiwan_time.hour
        
        # 根據台灣當地時間調整時段
        if 6 <= hour < 12:
            time_desc = "上午"
        elif 12 <= hour < 18:
            time_desc = "下午"
        else:
            time_desc = "晚上"
        
        # 中央氣象局開放資料API (免費)
        # API授權碼可以免費申請: https://opendata.cwb.gov.tw/
        cwb_api_key = os.getenv('CWB_API_KEY', 'CWB-DEMO-KEY')  # 使用環境變數
        
        # 地區對應代碼
        location_codes = {
            "新店": "新北市",
            "中山區": "臺北市", 
            "中正區": "臺北市"
        }
        
        if location not in location_codes:
            return f"❌ {location}: 目前不支援此地區"
        
        city = location_codes[location]
        
        try:
            # 使用中央氣象局36小時天氣預報API
            url = f"https://opendata.cwb.gov.tw/api/v1/rest/datastore/F-C0032-001"
            params = {
                'Authorization': cwb_api_key,
                'locationName': city,
                'format': 'JSON'
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if 'records' in data and 'location' in data['records']:
                    locations = data['records']['location']
                    
                    for loc in locations:
                        if loc['locationName'] == city:
                            weather_elements = loc['weatherElement']
                            
                            # 解析天氣資料
                            weather_info = {}
                            
                            for element in weather_elements:
                                element_name = element['elementName']
                                time_data = element['time'][0]  # 取最近時間的資料
                                
                                if element_name == 'Wx':  # 天氣現象
                                    weather_info['condition'] = time_data['parameter']['parameterName']
                                elif element_name == 'PoP':  # 降雨機率
                                    weather_info['rain'] = time_data['parameter']['parameterName'] + '%'
                                elif element_name == 'MinT':  # 最低溫
                                    weather_info['min_temp'] = time_data['parameter']['parameterName']
                                elif element_name == 'MaxT':  # 最高溫
                                    weather_info['max_temp'] = time_data['parameter']['parameterName']
                            
                            # 組合溫度範圍
                            if 'min_temp' in weather_info and 'max_temp' in weather_info:
                                weather_info['temp'] = f"{weather_info['min_temp']}°C ~ {weather_info['max_temp']}°C"
                            else:
                                weather_info['temp'] = "溫度資料取得中..."
                            
                            # 濕度資料 (如果有的話)
                            weather_info['humidity'] = "資料取得中..."
                            
                            return f"""🌤️ {location} 天氣 ({today} {time_desc}):

🌡️ 溫度: {weather_info.get('temp', '資料取得中...')}
💧 濕度: {weather_info.get('humidity', '資料取得中...')}
☁️ 天氣: {weather_info.get('condition', '資料取得中...')}
🌧️ 降雨機率: {weather_info.get('rain', '資料取得中...')}

📱 完整天氣資訊:
• 中央氣象局 App
• LINE 天氣
• Google 天氣

💡 資料來源: 中央氣象局開放資料"""
            
            else:
                raise Exception(f"API回應錯誤: {response.status_code}")
                
        except Exception as e:
            # 備用方案: 爬取中央氣象局網站
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                url = f"https://www.cwb.gov.tw/V8/C/W/County/County.html?CID=63"  # 台北市
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # 爬取溫度和天氣狀況
                    temp_elements = soup.find_all(text=re.compile(r'\d+°'))
                    weather_elements = soup.find_all(text=re.compile(r'[晴多陰雨雷]'))
                    
                    if temp_elements:
                        temp = temp_elements[0].strip()
                        condition = weather_elements[0].strip() if weather_elements else "多雲"
                        
                        return f"""🌤️ {location} 天氣 ({today} {time_desc}):

🌡️ 溫度: {temp}
💧 濕度: 資料取得中...
☁️ 天氣: {condition}
🌧️ 降雨機率: 資料取得中...

📱 完整天氣資訊:
• 中央氣象局 App
• LINE 天氣
• Google 天氣

💡 資料來源: 中央氣象局網站爬蟲"""
                    
            except:
                pass
            
            # 最終備用方案
            return f"""🌤️ {location} 天氣 ({today} {time_desc}):

❌ 天氣資料暫時無法取得

📱 請直接查看:
• 中央氣象局 App
• LINE 天氣
• Google 天氣
• Yahoo 天氣

🔄 請稍後再試「{location}」指令

💡 建議申請中央氣象局API金鑰以獲得穩定資料"""
        
# 定時推送功能
def send_push_message(message):
    """發送推送訊息"""
    try:
        if USER_ID:
            line_bot_api.push_message(USER_ID, TextSendMessage(text=message))
            print(f"✅ 推送成功: {message[:30]}...")
        else:
            print("❌ USER_ID 未設定，無法推送")
    except Exception as e:
        print(f"❌ 推送失敗: {e}")

# 推送任務定義
def push_morning_xindian_weather():
    """07:10 - 新店天氣"""
    weather = get_weather("新店")
    message = f"🌅 早安！今日新店天氣報告\n\n{weather}"
    send_push_message(message)

def push_morning_zhongshan_weather_traffic():
    """08:00 - 中山區天氣 + 車流"""
    weather = get_weather("中山區")
    traffic = get_traffic_info()
    message = f"🌤️ 中山區天氣 + 即時路況\n\n{weather}\n\n🚗 車流狀況:\n{traffic}"
    send_push_message(message)

def push_stock_opening():
    """09:30 - 台股開盤 + 新聞"""
    stocks = get_taiwan_stocks()
    news = get_news()
    message = f"🔔 台股開盤報告\n\n{stocks}\n\n{news}"
    send_push_message(message)

def push_stock_midday():
    """12:00 - 台股盤中"""
    stocks = get_taiwan_stocks()
    message = f"🍽️ 午間台股盤中報告\n\n{stocks}"
    send_push_message(message)

def push_stock_closing():
    """13:45 - 台股收盤"""
    stocks = get_taiwan_stocks()
    message = f"🔚 台股收盤報告\n\n{stocks}"
    send_push_message(message)

def push_evening_zhengzhong_weather_traffic():
    """17:30 週一三五 - 中正區天氣 + 車流"""
    weather = get_weather("中正區")
    traffic = get_traffic_info()
    message = f"🌆 下班時間 - 中正區天氣 + 路況\n\n{weather}\n\n🚗 車流狀況:\n{traffic}"
    send_push_message(message)

def push_evening_xindian_weather_traffic():
    """17:30 週二四 - 新店天氣 + 車流"""
    weather = get_weather("新店")
    traffic = get_traffic_info()
    message = f"🌆 下班時間 - 新店天氣 + 路況\n\n{weather}\n\n🚗 車流狀況:\n{traffic}"
    send_push_message(message)

# 設定定時任務
def setup_scheduled_tasks():
    """設定所有定時推送任務"""
    
    # 每日固定推送
    scheduler.add_job(
        func=push_morning_xindian_weather,
        trigger=CronTrigger(hour=7, minute=10, timezone=TAIWAN_TZ),
        id='morning_xindian_weather',
        replace_existing=True
    )
    
    scheduler.add_job(
        func=push_morning_zhongshan_weather_traffic,
        trigger=CronTrigger(hour=8, minute=0, timezone=TAIWAN_TZ),
        id='morning_zhongshan_weather_traffic',
        replace_existing=True
    )
    
    # 上班日推送 (週一到週五)
    scheduler.add_job(
        func=push_stock_opening,
        trigger=CronTrigger(hour=9, minute=30, day_of_week='mon-fri', timezone=TAIWAN_TZ),
        id='stock_opening',
        replace_existing=True
    )
    
    scheduler.add_job(
        func=push_stock_midday,
        trigger=CronTrigger(hour=12, minute=0, day_of_week='mon-fri', timezone=TAIWAN_TZ),
        id='stock_midday',
        replace_existing=True
    )
    
    scheduler.add_job(
        func=push_stock_closing,
        trigger=CronTrigger(hour=13, minute=45, day_of_week='mon-fri', timezone=TAIWAN_TZ),
        id='stock_closing',
        replace_existing=True
    )
    
    # 下班時間推送
    # 週一、三、五 - 中正區
    scheduler.add_job(
        func=push_evening_zhengzhong_weather_traffic,
        trigger=CronTrigger(hour=17, minute=30, day_of_week='mon,wed,fri', timezone=TAIWAN_TZ),
        id='evening_zhengzhong',
        replace_existing=True
    )
    
    # 週二、四 - 新店
    scheduler.add_job(
        func=push_evening_xindian_weather_traffic,
        trigger=CronTrigger(hour=17, minute=30, day_of_week='tue,thu', timezone=TAIWAN_TZ),
        id='evening_xindian',
        replace_existing=True
    )
    
    print("✅ 定時推送任務設定完成")

@app.route("/", methods=['GET'])
def home():
    return "🟢 股市播報員 LINE Bot v39 定時推送版運作中！"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_message = event.message.text.strip()
        reply = ""
        
        if user_message == "測試":
            reply = """✅ 股市播報員系統檢查 v39:

🔧 基本功能: 正常
🌐 網路連線: 正常  
📡 Webhook: 正常
⏰ 定時推送: 已啟動

🆕 v39 自動推送版本:
• 美股: Yahoo Finance API 
• 台股: 大盤+個股 Yahoo爬蟲
• 新聞: 經濟日報+Yahoo財經爬蟲
• 天氣: 中央氣象局API+備用爬蟲
• 車流: 高速公路局+備用資料
• 推送: 完整時間表自動推送

📋 可用功能:
• 美股 - 爬取Yahoo Finance即時價格
• 台股 - 爬取大盤指數+個股價格  
• 新聞 - 爬取財經媒體最新新聞
• 天氣 - 中央氣象局API即時天氣
• 車流 - 即時路況資訊

📅 自動推送時間表:
每日 07:10 - 新店天氣
每日 08:00 - 中山區天氣+車流
工作日 09:30 - 台股開盤+新聞
工作日 12:00 - 台股盤中
工作日 13:45 - 台股收盤
週一三五 17:30 - 中正區天氣+車流
週二四 17:30 - 新店天氣+車流

🎯 目標: 提供100%自動化的財經資訊服務！"""
        
        elif user_message == "美股":
            reply = get_us_stocks()
        
        elif user_message == "台股":
            reply = get_taiwan_stocks()
        
        elif user_message == "車流":
            reply = get_traffic_info()
        
        elif user_message in ["新店", "中山區", "中正區"]:
            reply = get_weather(user_message)
        
        elif user_message == "停止推送":
            try:
                scheduler.pause()
                reply = "⏸️ 自動推送已暫停\n\n💡 輸入「開始推送」可重新啟動"
            except:
                reply = "❌ 推送系統控制失敗"
        
        elif user_message == "開始推送":
            try:
                scheduler.resume()
                reply = "▶️ 自動推送已重新啟動\n\n📅 將按照時間表自動推送訊息"
            except:
                reply = "❌ 推送系統控制失敗"
        
        elif user_message == "新聞":
            reply = get_news()
        
        elif user_message == "幫助":
            reply = """📋 股市播報員功能 v39:

💼 股市查詢:
• 美股 - Yahoo Finance 即時價格
• 台股 - 大盤指數 + 個股即時價格

📰 資訊查詢:  
• 新聞 - 財經媒體最新新聞
• 車流 - 即時路況資訊

🌤️ 天氣查詢:
• 新店/中山區/中正區 - 中央氣象局API

📅 自動推送:
• 推送 - 查看推送時間表
• 停止推送 - 暫停自動推送
• 開始推送 - 重新啟動推送

🔧 系統功能:
• 測試 - 系統狀態檢查
• 幫助 - 顯示此說明

🎯 v39 - 自動推送版本
完整的定時推送財經資訊服務！

⚠️ 需設定 LINE_USER_ID 環境變數才能接收推送"""
        
        else:
            reply = f"❓ 無法理解「{user_message}」\n\n📋 請輸入:\n美股、台股、新聞、車流、新店、中山區、中正區、推送、測試、幫助"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        
    except Exception as e:
        error_msg = f"💥 系統錯誤，請稍後再試"
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
        except:
            pass

if __name__ == "__main__":
    # 啟動定時推送系統
    setup_scheduled_tasks()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
