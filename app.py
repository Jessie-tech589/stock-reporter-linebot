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
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
TZ = pytz.timezone('Asia/Taipei')
app = Flask(__name__)

# 讀取環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
ACCUWEATHER_API_KEY = os.getenv("ACCUWEATHER_API_KEY")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# GOOGLE_CREDS_JSON 自動判斷是否為 base64，否則自動轉換
def get_google_creds_json_b64():
    raw = os.getenv("GOOGLE_CREDS_JSON")
    if not raw:
        return None
    try:
        # 嘗試 base64 decode
        base64.b64decode(raw)
        return raw
    except Exception:
        try:
            # 嘗試 JSON parse，若成功則轉換為 base64
            json.loads(raw)
            encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
            logging.warning("GOOGLE_CREDS_JSON 已自動轉換為 base64 格式")
            return encoded
        except Exception as e:
            logging.error(f"GOOGLE_CREDS_JSON 格式錯誤，無法解析: {e}")
            return None

GOOGLE_CREDS_JSON_B64 = get_google_creds_json_b64()

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

LOCATION_COORDS = {
    "新店區": (24.972, 121.539),
    "中山區": (25.063, 121.526),
    "中正區": (25.033, 121.519),
}

STOCK = {
    "台積電": "2330.TW", "聯電": "2303.TW", "鴻準": "2354.TW", "仁寶": "2324.TW",
    "陽明": "2609.TW", "華航": "2610.TW", "長榮航": "2618.TW", "大盤": "^TWII",
    "道瓊": "^DJI", "S&P500": "^GSPC", "NASDAQ": "^IXIC",
    "輝達": "NVDA", "美超微": "SMCI", "GOOGL": "GOOGL", "Google": "GOOGL",
    "蘋果": "AAPL"
}
stock_list_tpex = ["大盤", "台積電", "聯電", "鴻準", "仁寶", "陽明", "華航", "長榮航"]
stock_list_us = ["道瓊", "S&P500", "NASDAQ", "輝達", "美超微", "GOOGL", "蘋果"]

ROUTE_CONFIG = {
    "家到公司": dict(
        o="新北市新店區建國路99巷", d="台北市中山區南京東路三段131號",
        waypoints=[
            "新北市新店區民族路", "新北市新店區北新路", "台北市羅斯福路", "台北市基隆路",
            "台北市辛亥路", "台北市復興南路", "台北市南京東路"
        ]
    ),
    "公司到郵局": dict(
        o="台北市中山區南京東路三段131號", d="台北市中正區愛國東路216號",
        waypoints=["台北市林森北路", "台北市信義路", "台北市信義二段10巷", "台北市愛國東21巷"]
    ),
    "公司到家": dict(
        o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
        waypoints=[
            "台北市復興南路", "台北市辛亥路", "台北市基隆路", "台北市羅斯福路",
            "新北市新店區北新路", "新北市新店區民族路", "新北市新店區建國路"
        ]
    ),
}

WEATHER_ICON = {
    "Sunny": "☀️", "Clear": "🌕", "Cloudy": "☁️", "Partly cloudy": "⛅",
    "Rain": "🌧️", "Thunderstorm": "⛈️", "Fog": "🌫️", "Snow": "🌨️",
    "晴": "☀️", "多雲": "☁️", "陰": "☁️", "有雨": "🌧️", "雷雨": "⛈️",
    "陣雨": "🌧️", "多雲時晴": "⛅", "多雲短暫雨": "🌦️", "晴時多雲": "⛅"
}

def now_tw():
    return datetime.now(TZ)

# 天氣查詢
def weather(city, lat, lon):
    try:
        url_loc = f"https://dataservice.accuweather.com/locations/v1/cities/geoposition/search?apikey={ACCUWEATHER_API_KEY}&q={lat},{lon}&language=zh-tw"
        loc_res = requests.get(url_loc, timeout=8)
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
        return (f"{icon} {loc_name} ({city})\n"
                f"{wxtext}，溫度 {temp}°C，體感 {realfeel}°C\n來源: AccuWeather")
    except Exception as e:
        logging.warning(f"[WX-ACC-ERR] {e}")
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric&lang=zh_tw"
        js = requests.get(url, timeout=8).json()
        temp = js["main"]["temp"]
        feels = js["main"]["feels_like"]
        desc = js["weather"][0]["description"]
        cityname = js.get("name", city)
        icon = WEATHER_ICON.get(desc, "🌤️")
        return f"{icon} {cityname}（{city}）\n{desc}，溫度 {temp}°C，體感 {feels}°C\n來源: OWM"
    except Exception as e:
        logging.warning(f"[WX-OWM-ERR] {e}")
    return f"天氣查詢失敗（{city}）"

# 匯率查詢
def fx():
    try:
        url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        rows = table.find_all("tr")
        mapping = {"美元 (USD)": ("USD","🇺🇸"), "日圓 (JPY)": ("JPY","🇯🇵"),
                   "人民幣 (CNY)": ("CNY","🇨🇳"), "港幣 (HKD)": ("HKD","🇭🇰")}
        result = []
        for row in rows:
            cells = row.find_all("td")
            if cells and cells[0].text.strip() in mapping:
                code, flag = mapping[cells[0].text.strip()]
                rate = cells[2].text.strip()
                result.append(f"{flag} {code}: {rate}")
        if result:
            return "💱 今日匯率（現金賣出，台銀）\n" + "\n".join(result)
        else:
            logging.warning("[FX-TWBANK-ERR] 台銀匯率解析失敗或無資料")
            raise Exception("No data from TWBANK")
    except Exception as e:
        logging.warning(f"[FX-TWBANK-ERR] {e}")
    try:
        url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}"
        js = requests.get(url, timeout=8).json()
        if "Realtime Currency Exchange Rate" in js:
            rate = js["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
            return f"💱 USD/TWD: {float(rate):.2f}\n來源: AlphaVantage"
        else:
            raise ValueError("AlphaVantage response invalid")
    except Exception as e:
        logging.warning(f"[FX-AV-ERR] {e}")
    return "匯率查詢失敗"

# 油價查詢
def get_taiwan_oil_price():
    try:
        url = "https://www2.moeaea.gov.tw/oil111/"
        r = requests.get(url, timeout=10)
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
            p95 = prices.get("95無鉛汽油", "N/A")
            p98 = prices.get("98無鉛汽油", "N/A")
            pd = prices.get("超級柴油", "N/A")
            return (f"⛽️ 最新油價（能源局）\n"
                    f"92: {p92} 元/公升\n"
                    f"95: {p95} 元/公升\n"
                    f"98: {p98} 元/公升\n"
                    f"柴油: {pd} 元/公升")
        else:
            logging.warning("[OIL-ENB-PARSE-ERR] 未找到油價表格")
            raise Exception("無法從能源局網站解析油價")
    except Exception as e:
        logging.warning(f"[OIL-ENB-ERR] {e}")
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
        logging.warning(f"[CAL-ERR] {e}")
        return "行事曆查詢失敗（請檢查憑證和日曆 ID）"

# Google Maps Directions API
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
        url = (f"https://maps.googleapis.com/maps/api/directions/json?"
               f"origin={origin}&destination={destination}"
               f"&key={GOOGLE_MAPS_API_KEY}&mode=driving&language=zh-TW"
               f"&units=metric{waypoints_str}")
        response = requests.get(url, timeout=10).json()
        if response["status"] == "OK" and response["routes"]:
            leg = response["routes"][0]["legs"][0]
            duration_text = leg["duration"]["text"]
            distance_text = leg["distance"]["text"]
            summary = response["routes"][0]["summary"]
            return (f"🚗 {route_name} 路況：\n"
                    f"摘要: {summary}\n"
                    f"距離: {distance_text}\n"
                    f"預計時間: {duration_text}")
        else:
            status = response.get("status", "未知狀態")
            error_message = response.get("error_message", "無詳細錯誤訊息")
            logging.warning(f"[TRAFFIC-ERR] Status: {status}, Message: {error_message}")
            return f"交通資訊查詢失敗 ({route_name})：{status}"
    except Exception as e:
        logging.error(f"[TRAFFIC-EXCEPTION] {e}")
        return f"交通資訊查詢失敗 ({route_name})"

# 【修正】美股批次查詢
def us():
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
            except Exception:
                 result.append(f"❌ {name}：部分資料查詢失敗")

        return "\n".join(result)
    except Exception as e:
        logging.warning(f"[US-STOCK-BATCH-ERR] {e}")
        return "美股資訊批次查詢失敗。"

def get_today_events():
    return cal()

# 單一股票查詢 (for 手動輸入)
def stock(name: str) -> str:
    code = STOCK.get(name)
    if not code:
        return f"❌ 找不到股票: {name}"
    
    # 這裡仍然使用單一查詢，因為是使用者手動觸發，不會有頻率問題
    try:
        tkr = yf.Ticker(code)
        info = tkr.info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev = info.get("previousClose")
        if price is not None and prev is not None:
            diff = price - prev
            pct = (diff / prev * 100) if prev != 0 else 0
            emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            return f"{emo} {name}（yfinance）\n💰 {price:.2f}（{diff:+.2f}, {pct:+.2f}%)"
        else:
            return f"❌ {name}（yfinance） 查無資料"
    except Exception as e:
        if "429" in str(e):
            return f"❌ {name}（yfinance）: 來源被限制流量，請稍後再查"
        logging.warning(f"[STOCK-YF-ERR] {name} {e}")
        return f"❌ {name}（yfinance） 查詢失敗"

# 【修正】台股批次查詢
def stock_all():
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
            except Exception:
                 result.append(f"❌ {name}：部分資料查詢失敗")

        return "\n".join(result)
    except Exception as e:
        logging.warning(f"[TW-STOCK-BATCH-ERR] {e}")
        return "台股資訊批次查詢失敗。"


def get_news():
    return "📚 暫無新聞資訊（請設定新聞 API 並實作）"

def get_exchange_rate():
    return fx()

def get_us_market_summary():
    return us()

def push(message):
    if not LINE_USER_ID or not line_bot_api:
        logging.error("[LineBot] 推播失敗：未設定 USER_ID 或 line_bot_api")
        return
    logging.info(f"[LineBot] 推播給 {LINE_USER_ID}：{message[:50]}...")
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
    except Exception as e:
        logging.error(f"[LineBot] 推播失敗：{e}")

# ========== 定時推播任務 ==========

# 【修正】合併早安訊息以節省額度
def morning_briefing():
    logging.info("[Push] 07:10 Morning briefing 推播開始")
    try:
        weather_info = weather("新店區", *LOCATION_COORDS["新店區"])
        calendar_info = get_today_events()
        
        # 將多則訊息合併為一則
        full_message = (
            f"【早安天氣與行程】\n\n"
            f"{weather_info}\n\n"
            f"----------\n\n"
            f"【行事曆提醒】\n{calendar_info}"
        )
        
        push(full_message) # 一次性推播
        logging.info("[Push] 07:10 Morning briefing 推播完成")
    except Exception as e:
        logging.error(f"[MorningBriefingError] {e}")

def commute_to_work():
    msg = [
        "【通勤提醒/中山區】",
        weather("中山區", *LOCATION_COORDS["中山區"]),
        traffic("家到公司")
    ]
    push("\n\n".join(msg))

def market_open():
    msg = ["【台股開盤】", stock_all()]
    push("\n\n".join(msg))

def market_mid():
    msg = ["【台股盤中快訊】", stock_all()]
    push("\n\n".join(msg))

def market_close():
    msg = [
        "【台股收盤】",
        stock_all(),
        fx()
    ]
    push("\n\n".join(msg))

def evening_zhongzheng():
    msg = [
        "【下班打球提醒/中正區】",
        weather("中正區", *LOCATION_COORDS["中正區"]),
        get_taiwan_oil_price(),
        traffic("公司到郵局")
    ]
    push("\n\n".join(msg))

def evening_xindian():
    msg = [
        "【回家/新店區】",
        weather("新店區", *LOCATION_COORDS["新店區"]),
        get_taiwan_oil_price(),
        traffic("公司到家")
    ]
    push("\n\n".join(msg))

def us_market_open1():
    push("【美股開盤速報】\n" + us())

def us_market_open2():
    push("【美股盤後行情】\n" + us())

# ========== Scheduler ==========
scheduler = BackgroundScheduler(timezone=TZ)
def keep_alive():
    logging.info(f"[Scheduler] 定時喚醒維持運作 {now_tw()}")

def register_jobs():
    scheduler.add_job(keep_alive, CronTrigger(minute="0,10,20,30,40,50"))
    scheduler.add_job(morning_briefing, CronTrigger(hour=7, minute=10))
    scheduler.add_job(commute_to_work, CronTrigger(day_of_week="mon-fri", hour=8, minute=0))
    scheduler.add_job(market_open, CronTrigger(day_of_week="mon-fri", hour=9, minute=30))
    scheduler.add_job(market_mid, CronTrigger(day_of_week="mon-fri", hour=12, minute=0))
    scheduler.add_job(market_close, CronTrigger(day_of_week="mon-fri", hour=13, minute=45))
    scheduler.add_job(evening_zhongzheng, CronTrigger(day_of_week="mon,wed,fri", hour=18, minute=0))
    scheduler.add_job(evening_xindian, CronTrigger(day_of_week="tue,thu", hour=18, minute=0))
    scheduler.add_job(us_market_open1, CronTrigger(day_of_week="mon-fri", hour=21, minute=30))
    scheduler.add_job(us_market_open2, CronTrigger(day_of_week="mon-fri", hour=23, minute=0))

register_jobs()
scheduler.start()

# ========== Flask Routes ==========
@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/health")
def health():
    return "OK"

@app.route("/send_scheduled_test")
def send_scheduled_test():
    time_str = request.args.get("time", "").strip()
    job_map = {
        "07:10": morning_briefing,
        "08:00": commute_to_work,
        "09:30": market_open,
        "12:00": market_mid,
        "13:45": market_close,
        "21:30": us_market_open1,
        "23:00": us_market_open2,
    }
    try:
        if time_str in job_map:
            job_map[time_str]()
        elif time_str == "18:00":
            now_wd = now_tw().weekday()
            if now_wd in [0, 2, 4]: # Mon, Wed, Fri
                evening_zhongzheng()
            elif now_wd in [1, 3]: # Tue, Thu
                evening_xindian()
            else:
                return f"❌ 今日非指定星期 ({time_str})"
        else:
            return f"❌ 不支援時間 {time_str}"
    except Exception as e:
        logging.error(f"[TestTrigger] {e}")
        return f"❌ 發送時發生錯誤: {e}"
    return f"✅ 模擬推播 {time_str} 完成"

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
    txt = event.message.text.strip()
    reply = ""
    
    if txt == "天氣":
        reply = weather("新店區", *LOCATION_COORDS["新店區"])
    elif txt == "油價":
        reply = get_taiwan_oil_price()
    elif txt == "匯率":
        reply = fx()
    elif txt == "美股":
        reply = us()
    elif txt == "行事曆":
        reply = get_today_events()
    elif txt.startswith("股票"):
        parts = txt.split(" ", 1)
        if len(parts) > 1:
            stock_name = parts[1]
            reply = stock(stock_name) # 使用單一查詢函式
        else:
            reply = "請輸入股票名稱或代碼，例如：股票 台積電"
    elif txt == "台股":
        reply = stock_all() # 使用批次查詢函式
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
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
