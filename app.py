import os
import base64
import json
import time
import logging
import requests
import yfinance as yf
import pytz
import re
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

# ====== 設定 ======
# 配置日誌，以便在控制台看到詳細的運行信息
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
TZ = pytz.timezone('Asia/Taipei') # 設定時區為台北時間
app = Flask(__name__)

# 從環境變數讀取必要的 API 金鑰和設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID") # 用於推播訊息的用戶ID
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") # OpenWeatherMap API 金鑰
ACCUWEATHER_API_KEY = os.getenv("ACCUWEATHER_API_KEY") # AccuWeather API 金鑰
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY") # Google Maps API 金鑰
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY") # Alpha Vantage API 金鑰
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary") # Google 行事曆 ID

# GOOGLE_CREDS_JSON 自動判斷是否為 base64 編碼，否則自動轉換
def get_google_creds_json_b64():
    raw = os.getenv("GOOGLE_CREDS_JSON")
    if not raw:
        logging.warning("GOOGLE_CREDS_JSON 環境變數未設定。行事曆功能將無法使用。")
        return None
    try:
        # 嘗試 base64 decode
        decoded_bytes = base64.b64decode(raw)
        # 嘗試 JSON parse 驗證是否為有效 JSON
        json.loads(decoded_bytes.decode("utf-8"))
        return raw # 如果是有效的 base64 編碼 JSON，直接返回
    except Exception:
        try:
            # 嘗試 JSON parse，若成功則轉換為 base64
            json.loads(raw)
            encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
            logging.warning("GOOGLE_CREDS_JSON 已自動轉換為 base64 格式。請考慮直接設定 base64 編碼的字串。")
            return encoded
        except Exception as e:
            logging.error(f"GOOGLE_CREDS_JSON 格式錯誤，無法解析: {e}")
            return None

GOOGLE_CREDS_JSON_B64 = get_google_creds_json_b64()

# 初始化 Line Bot API 和 Webhook Handler
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 地點座標配置，用於天氣查詢
LOCATION_COORDS = {
    "新店區": (24.972, 121.539),
    "中山區": (25.063, 121.526),
    "中正區": (25.033, 121.519),
}

# 股票代碼映射表
STOCK = {
    "台積電": "2330.TW", "聯電": "2303.TW", "鴻準": "2354.TW",
    "陽明": "2609.TW", "華航": "2610.TW", "長榮航": "2618.TW",
    "大盤": "^TWII", # 台股大盤
    "美股大盤指數": "^IXIC", # 那斯達克綜合指數作為美股大盤代表 (輝達和美超微都在此指數中)
    "輝達": "NVDA", "美超微": "SMCI",
}

# 【更新】股票清單，符合最新需求
stock_list_tpex = ["大盤", "台積電", "聯電", "鴻準", "陽明", "華航", "長榮航"]
stock_list_us = ["美股大盤指數", "輝達", "美超微"] # 移除 GOOGL

# 行車路線配置
ROUTE_CONFIG = {
    "家到公司": dict(
        o="新北市新店區建國路99巷", d="台北市中山區南京東路三段131號",
        waypoints=[
            "新北市新店區民族路", "新北市新店區北新路", "台北市羅斯福路", "台北市基隆路",
            "台北市辛亥路", "台北市復興南路", "台北市南京東路"
        ]
    ),
    "公司到郵局": dict( # 18:00 單數日行車資訊
        o="台北市中山區南京東路三段131號", d="台北市中正區愛國東路216號",
        waypoints=["台北市林森北路", "台北市信義路", "台北市信義二段10巷", "台北市愛國東21巷"]
    ),
    "公司到家": dict( # 18:00 雙數日行車資訊
        o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
        waypoints=[
            "台北市復興南路", "台北市辛亥路", "台北市基隆路", "台北市羅斯福路",
            "新北市新店區北新路", "新北市新店區民族路", "新北市新店區建國路"
        ]
    ),
}

# 天氣圖示映射
WEATHER_ICON = {
    "Sunny": "☀️", "Clear": "🌕", "Cloudy": "☁️", "Partly cloudy": "⛅",
    "Rain": "🌧️", "Thunderstorm": "⛈️", "Fog": "🌫️", "Snow": "🌨️",
    "晴": "☀️", "多雲": "☁️", "陰": "☁️", "有雨": "🌧️", "雷雨": "⛈️",
    "陣雨": "🌧️", "多雲時晴": "⛅", "多雲短暫雨": "🌦️", "晴時多雲": "⛅"
}

def now_tw():
    """獲取當前台北時間"""
    return datetime.now(TZ)

# 天氣查詢函數，優先使用 AccuWeather，失敗則嘗試 OpenWeatherMap
def weather(city_name, lat, lon):
    try:
        # 嘗試使用 AccuWeather
        url_loc = f"https://dataservice.accuweather.com/locations/v1/cities/geoposition/search?apikey={ACCUWEATHER_API_KEY}&q={lat},{lon}&language=zh-tw"
        loc_res = requests.get(url_loc, timeout=8)
        loc_res.raise_for_status() # 檢查 HTTP 錯誤
        loc_data = loc_res.json()
        if not loc_data:
            raise ValueError("AccuWeather location not found")
        key = loc_data[0]["Key"]
        loc_name = loc_data[0]["LocalizedName"]
        url_wx = f"https://dataservice.accuweather.com/currentconditions/v1/{key}?apikey={ACCUWEATHER_API_KEY}&details=true&language=zh-tw"
        wx = requests.get(url_wx, timeout=8).json()[0]
        temp = wx['Temperature']['Metric']['Value']
        realfeel = wx['RealFeelTemperature']['Metric']['Value']
        wxtext = wx['WeatherText']
        icon = WEATHER_ICON.get(wxtext, "🌦️")
        return (f"{icon} {loc_name} ({city_name})\n"
                f"{wxtext}，溫度 {temp}°C，體感 {realfeel}°C\n來源: AccuWeather")
    except Exception as e:
        logging.warning(f"[WX-ACC-ERR] AccuWeather 查詢失敗 ({city_name}): {e}")
    
    try:
        # 嘗試使用 OpenWeatherMap 作為備用
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric&lang=zh_tw"
        js = requests.get(url, timeout=8)
        js.raise_for_status() # 檢查 HTTP 錯誤
        js = js.json()
        temp = js["main"]["temp"]
        feels = js["main"]["feels_like"]
        desc = js["weather"][0]["description"]
        cityname = js.get("name", city_name)
        icon = WEATHER_ICON.get(desc, "🌤️")
        return f"{icon} {cityname}（{city_name}）\n{desc}，溫度 {temp}°C，體感 {feels}°C\n來源: OWM"
    except Exception as e:
        logging.warning(f"[WX-OWM-ERR] OpenWeatherMap 查詢失敗 ({city_name}): {e}")
    
    return f"天氣查詢失敗（{city_name}）"

# 匯率查詢函數 (保留台銀匯率，並優化錯誤處理)
def fx():
    try:
        url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status() # 檢查 HTTP 錯誤
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        
        # 【更新】明確指定要獲取的四種匯率
        # 這裡假設您希望保留的是美元、日圓、人民幣、港幣
        mapping = {
            "美元 (USD)": ("USD","🇺🇸"),
            "日圓 (JPY)": ("JPY","🇯🇵"),
            "人民幣 (CNY)": ("CNY","🇨🇳"),
            "港幣 (HKD)": ("HKD","🇭🇰")
        }
        result = []
        
        if table:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if cells and cells[0].text.strip() in mapping:
                    code, flag = mapping[cells[0].text.strip()]
                    # 確保現金賣出匯率存在
                    if len(cells) > 2:
                        rate = cells[2].text.strip()
                        result.append(f"{flag} {code}: {rate}")
        
        if result:
            return "💱 今日匯率（現金賣出，台銀）\n" + "\n".join(result)
        else:
            logging.warning("[FX-TWBANK-PARSE-ERR] 台銀匯率解析失敗或無資料")
            return "匯率查詢失敗（台銀）"
    except Exception as e:
        logging.error(f"[FX-TWBANK-ERR] 台銀匯率查詢失敗: {e}")
    
    # 【移除 AlphaVantage 備用，因為日誌顯示其也常出錯，若需要可自行加回】
    return "匯率查詢失敗"

# 油價查詢函數 (只保留 92 無鉛汽油)
def get_taiwan_oil_price():
    try:
        url = "https://www2.moeaea.gov.tw/oil111/"
        r = requests.get(url, timeout=10)
        r.raise_for_status() # 檢查 HTTP 錯誤
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, "lxml")
        price_table = soup.find("table", class_="tab_style_1")
        prices = {}
        if price_table:
            rows = price_table.find_all("tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) >= 3:
                    oil_type = cols[1].get_text(strip=True)
                    price = cols[2].get_text(strip=True)
                    prices[oil_type] = price
            
            p92 = prices.get("92無鉛汽油", "N/A")
            return f"⛽️ 最新油價（能源局）\n92無鉛汽油: {p92} 元/公升"
        else:
            logging.warning("[OIL-ENB-PARSE-ERR] 未找到油價表格或解析失敗")
            return "⛽️ 油價查詢失敗（能源局）"
    except Exception as e:
        logging.error(f"[OIL-ENB-ERR] 油價查詢失敗: {e}")
        return "⛽️ 油價查詢失敗（能源局）"

# Google Calendar 查詢
def cal():
    try:
        if not GOOGLE_CREDS_JSON_B64:
            return "行事曆查詢失敗：未設定 Google 憑證"
        info = json.loads(base64.b64decode(GOOGLE_CREDS_JSON_B64))
        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        today = now_tw().date()
        start = datetime.combine(today, datetime.min.time(), TZ).isoformat()
        end = datetime.combine(today, datetime.max.time(), TZ).isoformat()
        items = svc.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start, timeMax=end,
            singleEvents=True, orderBy="startTime", maxResults=10
        ).execute().get("items", [])
        if not items:
            return "今日無行程"
        events_str = []
        for event in items:
            event_summary = event.get("summary", "無標題事件")
            start_time = ""
            if "dateTime" in event["start"]:
                dt = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(TZ)
                start_time = dt.strftime("%H:%M") + " "
            events_str.append(f"🗓️ {start_time}{event_summary}")
        return "\n".join(events_str)
    except Exception as e:
        logging.error(f"[CAL-ERR] 行事曆查詢失敗: {e} (請檢查憑證和日曆 ID)")
        return "行事曆查詢失敗"

# Google Maps Directions API (行車資訊)
def traffic(route_name):
    try:
        if not GOOGLE_MAPS_API_KEY:
            return "交通資訊查詢失敗：未設定 Google Maps API 金鑰"
        route = ROUTE_CONFIG.get(route_name)
        if not route:
            return f"找不到 {route_name} 的路線配置。"
        origin = quote_plus(route["o"])
        destination = quote_plus(route["d"])
        waypoints_str = ""
        if route.get("waypoints"):
            waypoints_str = "|".join([quote_plus(wp) for wp in route["waypoints"]])
            waypoints_str = f"&waypoints={waypoints_str}"

        # 加入 departure_time 參數以獲取交通狀況下的預計時間
        departure_time = int(time.time()) # 當前時間戳

        url = (f"https://maps.googleapis.com/maps/api/directions/json?"
               f"origin={origin}&destination={destination}"
               f"&key={GOOGLE_MAPS_API_KEY}&mode=driving&language=zh-TW"
               f"&units=metric{waypoints_str}"
               f"&departure_time={departure_time}") # 為獲取交通資訊而添加

        response = requests.get(url, timeout=10)
        response.raise_for_status() # 檢查 HTTP 錯誤
        response = response.json()

        if response["status"] == "OK" and response["routes"]:
            leg = response["routes"][0]["legs"][0]
            duration_text = leg["duration"]["text"]
            distance_text = leg["distance"]["text"]
            summary = response["routes"][0]["summary"]

            # 獲取交通狀況下的預計時間並計算交通狀態
            duration_in_traffic_seconds = leg.get("duration_in_traffic", {}).get("value")
            duration_seconds = leg["duration"]["value"]

            traffic_emoji = "🟢" # 綠色：正常交通
            if duration_in_traffic_seconds is not None and duration_seconds is not None and duration_seconds > 0:
                traffic_increase_pct = ((duration_in_traffic_seconds - duration_seconds) / duration_seconds) * 100
                if traffic_increase_pct > 30: # 超過 30% 增加
                    traffic_emoji = "🔴" # 紅色：嚴重堵塞
                elif traffic_increase_pct > 10: # 10% 到 30% 增加
                    traffic_emoji = "🟠" # 橘色：中度堵塞
                # 如果 traffic_increase_pct <= 10，則保持綠色

            return (f"🚗 {route_name} 路況 {traffic_emoji}：\n"
                    f"摘要: {summary}\n"
                    f"距離: {distance_text}\n"
                    f"預計時間: {duration_text}")
        else:
            status = response.get("status", "未知狀態")
            error_message = response.get("error_message", "無詳細錯誤訊息")
            logging.warning(f"[TRAFFIC-ERR] 交通資訊 API 回應錯誤: Status: {status}, Message: {error_message}")
            return f"交通資訊查詢失敗 ({route_name})"
    except Exception as e:
        logging.error(f"[TRAFFIC-EXCEPTION] 交通資訊查詢發生例外: {e}")
        return f"交通資訊查詢失敗 ({route_name})"

# 【修正】美股批次查詢 (使用 yfinance)
def us_stocks_info():
    result = []
    # 建立名稱到代碼的映射
    us_stock_map = {name: STOCK[name] for name in stock_list_us}
    tickers_str = " ".join(us_stock_map.values())
    
    try:
        # 一次性抓取所有股票數據
        data = yf.Tickers(tickers_str)
        
        for name, code in us_stock_map.items():
            try:
                info = data.tickers[code].info
                price = info.get("regularMarketPrice")
                prev = info.get("previousClose")

                if price is not None and prev is not None:
                    diff = price - prev
                    pct = (diff / prev * 100) if prev != 0 else 0
                    emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                    result.append(f"{emo} {name}：{price:.2f} ({diff:+.2f}, {pct:+.2f}%)")
                else:
                    result.append(f"❌ {name}：查無價格資料")
            except Exception as e:
                logging.warning(f"❌ {name} ({code}) 美股資料查詢失敗: {e}")
                result.append(f"❌ {name}：部分資料查詢失敗")
        
        return "【美股資訊】\n" + "\n".join(result)
    except Exception as e:
        logging.error(f"[US-STOCK-BATCH-ERR] 美股批次查詢失敗: {e}")
        return "美股資訊批次查詢失敗。"

# 【修正】台股批次查詢 (使用 yfinance)
def tw_stocks_info():
    result = []
    # 建立名稱到代碼的映射
    tw_stock_map = {name: STOCK[name] for name in stock_list_tpex}
    tickers_str = " ".join(tw_stock_map.values())
    
    try:
        # 一次性抓取所有股票數據
        data = yf.Tickers(tickers_str)
        
        for name, code in tw_stock_map.items():
            try:
                # 對於台股，'regularMarketPrice' 可能不存在，嘗試 'currentPrice'
                info = data.tickers[code].info
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                prev = info.get("previousClose")

                if price is not None and prev is not None:
                    diff = price - prev
                    pct = (diff / prev * 100) if prev != 0 else 0
                    emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                    result.append(f"{emo} {name}：{price:.2f} ({diff:+.2f}, {pct:+.2f}%)")
                else:
                    result.append(f"❌ {name}：查無價格資料")
            except Exception as e:
                logging.warning(f"❌ {name} ({code}) 台股資料查詢失敗: {e}")
                result.append(f"❌ {name}：部分資料查詢失敗")
        
        return "【台股資訊】\n" + "\n".join(result)
    except Exception as e:
        logging.error(f"[TW-STOCK-BATCH-ERR] 台股批次查詢失敗: {e}")
        return "台股資訊批次查詢失敗。"

def push(message):
    """推播訊息到 LINE 指定用戶"""
    if not LINE_USER_ID or not line_bot_api:
        logging.error("[LineBot] 推播失敗：未設定 USER_ID 或 line_bot_api")
        return
    logging.info(f"[LineBot] 推播給 {LINE_USER_ID}：{message[:50]}...") # 記錄訊息前50字
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
    except LineBotApiError as e:
        logging.error(f"[LineBot] 推播失敗 (Line API Error): {e.status_code}, {e.error.message}")
    except Exception as e:
        logging.error(f"[LineBot] 推播失敗 (General Error): {e}")

# ========== 定時推播任務 ==========

# 【新增】8:00 早上更新 (美股、匯率、行車、天氣)
def send_8am_update():
    logging.info("[Push] 08:00 早上更新推播開始")
    messages = []
    
    # 美股資訊
    messages.append(us_stocks_info())
    
    # 匯率資訊
    messages.append(fx())
    
    # 行車資訊 (固定)
    messages.append(traffic("家到公司"))
    
    # 天氣資訊 (新店區, 中山區)
    messages.append(weather("新店區", *LOCATION_COORDS["新店區"]))
    messages.append(weather("中山區", *LOCATION_COORDS["中山區"]))
    
    full_message = "\n\n----------\n\n".join(messages)
    push(f"【早安資訊】\n\n{full_message}")
    logging.info("[Push] 08:00 早上更新推播完成")

# 【更新】9:30 台股開盤
def send_930am_update():
    logging.info("[Push] 09:30 台股開盤推播開始")
    messages = []
    messages.append("【台股開盤】")
    messages.append(tw_stocks_info())
    messages.append(fx()) # 匯率
    full_message = "\n\n".join(messages)
    push(full_message)
    logging.info("[Push] 09:30 台股開盤推播完成")

# 【更新】13:45 台股收盤
def send_1345pm_update():
    logging.info("[Push] 13:45 台股收盤推播開始")
    messages = []
    messages.append("【台股收盤】")
    messages.append(tw_stocks_info())
    messages.append(fx()) # 匯率
    full_message = "\n\n".join(messages)
    push(full_message)
    logging.info("[Push] 13:45 台股收盤推播完成")

# 【新增】18:00 傍晚更新 (匯率、油價、行車、天氣 - 單雙日判斷)
def send_18pm_update():
    logging.info("[Push] 18:00 傍晚更新推播開始")
    messages = []
    
    # 匯率資訊
    messages.append(fx())
    
    # 油價資訊 (一天一次)
    messages.append(get_taiwan_oil_price())
    
    # 根據日期單雙數判斷行車和天氣
    today_day = now_tw().day
    if today_day % 2 != 0: # 單數日 (1, 3, 5...)
        # 行車資訊: 公司到郵局
        messages.append(traffic("公司到郵局"))
        # 天氣: 中正區
        messages.append(weather("中正區", *LOCATION_COORDS["中正區"]))
    else: # 雙數日 (2, 4...)
        # 行車資訊: 公司到家
        messages.append(traffic("公司到家"))
        # 天氣: 新店區
        messages.append(weather("新店區", *LOCATION_COORDS["新店區"]))
    
    full_message = "\n\n----------\n\n".join(messages)
    push(f"【傍晚資訊】\n\n{full_message}")
    logging.info("[Push] 18:00 傍晚更新推播完成")

# 【更新】23:00 美股盤中/收盤 (不含匯率)
def send_23pm_update():
    logging.info("[Push] 23:00 美股盤中/收盤推播開始")
    messages = []
    messages.append("【美股盤中/收盤】")
    messages.append(us_stocks_info())
    full_message = "\n\n".join(messages)
    push(full_message)
    logging.info("[Push] 23:00 美股盤中/收盤推播完成")

# ========== Scheduler ==========
scheduler = BackgroundScheduler(timezone=TZ)

def keep_alive():
    """定時喚醒，防止 Render.com 免費服務閒置關閉"""
    logging.info(f"[Scheduler] 定時喚醒維持運作 {now_tw()}")
    # 這裡可以考慮發送一個輕量級的 HTTP 請求到自己的 /health 端點，
    # 確保 Render 認為服務是活躍的。
    try:
        requests.get(f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/health", timeout=5)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Keep-alive health check failed: {e}")

def register_jobs():
    """註冊所有定時任務"""
    # 每10分鐘喚醒一次
    scheduler.add_job(keep_alive, CronTrigger(minute="0,10,20,30,40,50"))
    
    # 【更新】調整為新的排程和函數名稱，並確保只在工作日運行 (mon-fri)
    scheduler.add_job(send_8am_update, CronTrigger(day_of_week="mon-fri", hour=8, minute=0))
    scheduler.add_job(send_930am_update, CronTrigger(day_of_week="mon-fri", hour=9, minute=30))
    scheduler.add_job(send_1345pm_update, CronTrigger(day_of_week="mon-fri", hour=13, minute=45))
    scheduler.add_job(send_18pm_update, CronTrigger(day_of_week="mon-fri", hour=18, minute=0))
    scheduler.add_job(send_23pm_update, CronTrigger(day_of_week="mon-fri", hour=23, minute=0))

    logging.info("所有排程任務已註冊。")

# 啟動排程器
register_jobs()
scheduler.start()

# ========== Flask Routes ==========
@app.route("/")
def home():
    """首頁，用於確認服務是否運行"""
    return "✅ LINE Bot 正常運作中"

@app.route("/health")
def health():
    """健康檢查端點，用於 Render.com 或 keep-alive 任務"""
    return "OK"

@app.route("/send_scheduled_test")
def send_scheduled_test():
    """手動觸發排程任務的測試端點"""
    time_str = request.args.get("time", "").strip()
    job_map = {
        "08:00": send_8am_update,
        "09:30": send_930am_update,
        "13:45": send_1345pm_update,
        "18:00": send_18pm_update, # 18:00 任務已包含單雙日邏輯
        "23:00": send_23pm_update,
    }
    try:
        if time_str in job_map:
            job_map[time_str]()
        else:
            return f"❌ 不支援時間 {time_str} 或該時間無對應排程任務"
    except Exception as e:
        logging.error(f"[TestTrigger] 模擬推播 {time_str} 時發生錯誤: {e}")
        return f"❌ 發送時發生錯誤: {e}"
    return f"✅ 模擬推播 {time_str} 完成"

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Bot Webhook 回調接口"""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logging.error("Webhook 簽名驗證失敗，請檢查 LINE Channel Secret。")
        abort(400) # 返回 400 錯誤
    except Exception as e:
        logging.error(f"Webhook 處理時發生錯誤: {e}")
        abort(500) # 返回 500 內部伺服器錯誤
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """處理 LINE 接收到的文字訊息"""
    txt = event.message.text.strip()
    reply = ""
    
    # 根據用戶輸入的關鍵字提供資訊
    if txt == "天氣":
        # 預設提供新店區天氣，可根據需求調整或讓用戶指定地區
        reply = weather("新店區", *LOCATION_COORDS["新店區"])
    elif txt == "油價":
        reply = get_taiwan_oil_price()
    elif txt == "匯率":
        reply = fx()
    elif txt == "美股":
        reply = us_stocks_info() # 使用新的美股批次查詢函數
    elif txt == "行事曆":
        reply = cal()
    elif txt.startswith("股票"):
        parts = txt.split(" ", 1)
        if len(parts) > 1:
            stock_name = parts[1]
            # 這裡的 stock 函數仍然是單一查詢，如果需要，可以考慮用 yfinance 替換
            # 為了避免混淆，這裡直接使用 yfinance.Ticker
            try:
                code = STOCK.get(stock_name) or stock_name.upper() # 允許直接輸入代碼
                if not code:
                    reply = f"❌ 找不到股票: {stock_name}"
                else:
                    tkr = yf.Ticker(code)
                    info = tkr.info
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                    prev = info.get("previousClose")
                    if price is not None and prev is not None:
                        diff = price - prev
                        pct = (diff / prev * 100) if prev != 0 else 0
                        emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                        reply = f"{emo} {stock_name}（yfinance）\n💰 {price:.2f}（{diff:+.2f}, {pct:+.2f}%)"
                    else:
                        reply = f"❌ {stock_name}（yfinance） 查無資料"
            except Exception as e:
                logging.warning(f"[STOCK-YF-MANUAL-ERR] {stock_name} {e}")
                reply = f"❌ {stock_name}（yfinance） 查詢失敗"
        else:
            reply = "請輸入股票名稱或代碼，例如：股票 台積電"
    elif txt == "台股":
        reply = tw_stocks_info() # 使用新的台股批次查詢函數
    elif txt.startswith("路況"):
        parts = txt.split(" ", 1)
        if len(parts) > 1:
            route_name = parts[1]
            reply = traffic(route_name)
        else:
            reply = "請輸入路線名稱，例如：路況 家到公司"
            
    if not reply:
        reply = ("您好！我可以提供以下資訊：\n"
                 "天氣 / 油價 / 匯率 / 美股 / 行事曆 / 台股\n"
                 "路況 [路線名稱]\n"
                 "股票 [股票名稱或代碼]")
                 
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    # 從環境變數獲取 PORT，預設為 10000
    port = int(os.environ.get("PORT", 10000))
    # 在所有網絡接口上運行 Flask 應用，以便 Render.com 可以訪問
    app.run(host="0.0.0.0", port=port)
