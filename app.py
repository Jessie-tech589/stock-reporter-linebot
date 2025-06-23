import os
import base64
import json
import requests
import yfinance as yf
from datetime import datetime, timedelta, date
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from google.oauth2 import service_account
from googleapiclient.discovery import build
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

# ========== 環境變數 ==========
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID             = os.getenv("LINE_USER_ID")
WEATHER_API_KEY          = os.getenv("WEATHER_API_KEY")
GOOGLE_MAPS_API_KEY      = os.getenv("GOOGLE_MAPS_API_KEY")
NEWS_API_KEY             = os.getenv("NEWS_API_KEY")
GOOGLE_CREDS_JSON_B64    = os.getenv("GOOGLE_CREDS_JSON")
GOOGLE_CALENDAR_ID       = os.getenv("GOOGLE_CALENDAR_ID","primary")
FUGLE_API_KEY            = os.getenv("FUGLE_API_KEY")
FINNHUB_API_KEY          = os.getenv("FINNHUB_API_KEY")
CWA_API_KEY              = os.getenv("CWA_API_KEY", WEATHER_API_KEY)
ACCUWEATHER_API_KEY      = os.getenv("ACCUWEATHER_API_KEY")

# ========== 時區設定（重要修正）==========
tz = pytz.timezone("Asia/Taipei")

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ========== 經緯度設定 ==========
LOCATION_COORDS = {
    "新店區": (24.972, 121.539),
    "中山區": (25.063, 121.526),
    "中正區": (25.033, 121.519),
    "大安區": (25.033, 121.543),
}

# ========== STOCK MAPPING ==========
STOCK = {
    "台積電":"2330.TW","聯電":"2303.TW","鴻準":"2354.TW","仁寶":"2324.TW",
    "陽明":"2609.TW","華航":"2610.TW","長榮航":"2618.TW",
    "00918":"00918.TW","00878":"00878.TW",
    "元大美債20年":"00679B.TW","群益25年美債":"00723B.TW",
    "大盤":"^TWII",
    "輝達":"NVDA","美超微":"SMCI","GOOGL":"GOOGL","Google":"GOOGL",
    "蘋果":"AAPL","特斯拉":"TSLA","微軟":"MSFT"
}

# ========== 股票清單 ==========
stock_list_tpex = [
    "台積電","聯電","鴻準","仁寶","陽明","華航","長榮航",
    "00918","00878","元大美債20年","群益25年美債","大盤"
]

# ========== 路線對照 ==========
ROUTE_CONFIG = {
    "家到公司": dict(
        o="新北市新店區建國路99巷", d="台北市中山區南京東路三段131號",
        waypoints=[
            "新北市新店區民族路",
            "新北市新店區北新路",
            "台北市羅斯福路",
            "台北市基隆路",
            "台北市辛亥路",
            "台北市復興南路",
            "台北市南京東路"
        ]
    ),
    "公司到郵局": dict(
        o="台北市中山區南京東路三段131號", d="台北市中正區愛國東路216號",
        waypoints=[
            "台北市南京東路",
            "台北市林森北路",
            "台北市信義路",
            "台北市信義二段10巷",
            "台北市愛國東21巷"
        ]
    ),
    "公司到家": dict(
        o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
        waypoints=[
            "台北市南京東路",
            "台北市復興南路",
            "台北市辛亥路",
            "台北市基隆路",
            "台北市羅斯福路",
            "新北市新店區北新路",
            "新北市新店區民族路",
            "新北市新店區建國路"
        ]
    )
}

# ========== Emoji ==========
WEATHER_ICON = {
    "Sunny": "☀️", "Clear": "🌕", "Cloudy": "☁️", "Partly cloudy": "⛅",
    "Rain": "🌧️", "Thunderstorm": "⛈️", "Fog": "🌫️", "Snow": "🌨️",
}
TRAFFIC_EMOJI = { "RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢" }

# =====================[API安全封裝]=====================
def safe_get(url, timeout=10):
    print(f"[REQ] {url}")
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        print(f"[RESP] {r.status_code}")
        return r if r.status_code==200 else None
    except Exception as e:
        print("[REQ-ERR]", url, e)
        return None

# ========== 天氣查詢（修正版）==========
def weather_accu(city, lat, lon):
    if not ACCUWEATHER_API_KEY:
        return f"❌ {city} 天氣查詢失敗（無API Key）"
    
    try:
        # 位置查詢
        url_loc = f"https://dataservice.accuweather.com/locations/v1/cities/geoposition/search?apikey={ACCUWEATHER_API_KEY}&q={lat},{lon}&language=zh-tw"
        loc_res = requests.get(url_loc, timeout=10)
        
        if loc_res.status_code != 200:
            print(f"[WX-LOC-ERR] Status: {loc_res.status_code}, Response: {loc_res.text}")
            return f"❌ {city} 位置查詢失敗"
            
        loc_data = loc_res.json()
        key = loc_data["Key"]
        loc_name = loc_data["LocalizedName"]
        
        # 天氣查詢
        url_wx = f"https://dataservice.accuweather.com/currentconditions/v1/{key}?apikey={ACCUWEATHER_API_KEY}&details=true&language=zh-tw"
        wx_res = requests.get(url_wx, timeout=10)
        
        if wx_res.status_code != 200:
            print(f"[WX-ERR] Status: {wx_res.status_code}, Response: {wx_res.text}")
            return f"❌ {city} 天氣查詢失敗"
            
        wx = wx_res.json()[0]
        temp = wx['Temperature']['Metric']['Value']
        realfeel = wx['RealFeelTemperature']['Metric']['Value']
        wxtext = wx['WeatherText']
        icon = WEATHER_ICON.get(wxtext, "🌦️")
        
        return (f"{icon} {loc_name} ({city})\n"
                f"{wxtext}，溫度 {temp}°C，體感 {realfeel}°C")
    except Exception as e:
        print("[WX-ERR]", e)
        return f"❌ {city} 天氣查詢失敗"

# ========== 匯率（修正版）==========
def fx():
    url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        })
        
        if r.status_code != 200:
            return "❌ 匯率查詢失敗（網站無回應）"
            
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", class_="table table-striped table-bordered table-condensed table-hover")
        
        if not table:
            return "❌ 匯率查詢失敗（找不到資料表格）"
            
        rows = table.find_all("tr")
        mapping = {
            "美元 (USD)": ("USD","🇺🇸"),
            "日圓 (JPY)": ("JPY","🇯🇵"),
            "人民幣 (CNY)": ("CNY","🇨🇳"),
            "港幣 (HKD)": ("HKD","🇭🇰"),
        }
        result = []
        
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 3:
                currency_name = cells[0].text.strip()
                if currency_name in mapping:
                    code, flag = mapping[currency_name]
                    rate = cells[2].text.strip()  # 現金賣出
                    result.append(f"{flag} {code}: {rate}")
                    
        return "💱 今日匯率（現金賣出）\n" + "\n".join(result) if result else "❌ 查無匯率資料"
    except Exception as e:
        print("[FX-ERR]", e)
        return "❌ 匯率查詢失敗"

# ========== 油價（修正版）==========
def get_taiwan_oil_price():
    url = "https://vipmbr.cpc.com.tw/mbwebs/ShowHistoryPrice.do"
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        r.encoding = "utf-8"
        
        if r.status_code != 200:
            return "❌ 油價查詢失敗（網站無回應）"
            
        soup = BeautifulSoup(r.text, "html.parser")
        
        # 尋找價格表格
        tables = soup.find_all("table")
        price_table = None
        
        for table in tables:
            if "92無鉛汽油" in table.get_text() or "tablePrice" in str(table.get("class", [])):
                price_table = table
                break
                
        if not price_table:
            return "❌ 油價查詢失敗（找不到價格表格）"
            
        rows = price_table.find_all("tr")
        if len(rows) < 2:
            return "❌ 油價查詢失敗（資料不完整）"
            
        # 找到最新價格行
        for row in rows[1:]:  # 跳過標題行
            cols = row.find_all("td")
            if len(cols) >= 5:
                gas_92 = cols[1].text.strip()
                gas_95 = cols[2].text.strip()
                gas_98 = cols[3].text.strip()
                diesel = cols[4].text.strip()
                
                if gas_92 and gas_95:  # 確保有資料
                    return (
                        f"⛽️ 最新油價：\n"
                        f"92無鉛: {gas_92} 元\n"
                        f"95無鉛: {gas_95} 元\n"
                        f"98無鉛: {gas_98} 元\n"
                        f"柴油: {diesel} 元"
                    )
                    
        return "❌ 油價查詢失敗（找不到有效價格）"
    except Exception as e:
        print("[GAS-ERR]", e)
        return "❌ 油價查詢失敗"

# ========== 新聞（修正版）==========
def news():
    if not NEWS_API_KEY:
        return "❌ 新聞查詢失敗（無API Key）"
        
    sources = [
        ("台灣", "tw"),
        ("香港", "hk"),  # 改用香港替代大陸
        ("國際", "us"),
    ]
    result = []
    
    for label, code in sources:
        url = f"https://newsapi.org/v2/top-headlines?country={code}&apiKey={NEWS_API_KEY}"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                print(f"[NEWS-{label}-ERR] Status: {response.status_code}")
                continue
                
            data = response.json()
            if data.get("status") == "ok":
                articles = data.get("articles", [])
                arts = [a["title"] for a in articles if a.get("title") and len(a["title"]) > 10][:3]
                if arts:
                    result.append(f"📰【{label}】\n" + "\n".join("• " + t for t in arts))
        except Exception as e:
            print(f"[NEWS-{label}-ERR]", e)
            
    return "\n\n".join(result) if result else "❌ 今日新聞查詢失敗"

# ========== 股票（修正版）==========
def stock(name: str) -> str:
    code = STOCK.get(name, name)
    
    # 台股處理
    if code.endswith(".TW"):
        sym = code.replace(".TW", "").zfill(4)
        
        # 先嘗試證交所API
        url = f"https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                for row in data:
                    if row.get('證券代號') == sym:
                        price = row.get('收盤價')
                        if price and price != '--':
                            return f"📈 {name}（台股）\n💰 {price}（收盤價）"
        except Exception as e:
            print("[STOCK-TWSE-ERR]", e)
        
        # 備用：使用yfinance
        try:
            import yfinance as yf
            tkr = yf.Ticker(code)
            hist = tkr.history(period="1d")
            if not hist.empty:
                price = hist['Close'].iloc[-1]
                prev = hist['Open'].iloc[-1]
                diff = price - prev
                pct = diff / prev * 100 if prev else 0
                emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                return f"{emo} {name}（台股）\n💰 {price:.2f}\n{diff:+.2f} ({pct:+.2f}%)"
            else:
                return f"❌ {name}（台股） 查無今日資料"
        except Exception as e:
            print("[STOCK-YF-TW-ERR]", e)
            return f"❌ {name}（台股） 查詢失敗"
    
    # 美股處理
    try:
        import yfinance as yf
        tkr = yf.Ticker(code)
        
        # 嘗試獲取即時資料
        try:
            info = tkr.fast_info
            price = info.last_price
            prev = info.previous_close
        except:
            # 備用方法
            hist = tkr.history(period="2d")
            if len(hist) >= 2:
                price = hist['Close'].iloc[-1]
                prev = hist['Close'].iloc[-2]
            else:
                return f"❌ {name}（美股） 查無資料"
        
        if price is not None and prev is not None:
            diff = price - prev
            pct = diff / prev * 100 if prev else 0
            emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            return f"{emo} {name}（美股）\n💰 {price:.2f}\n{diff:+.2f} ({pct:+.2f}%)"
        else:
            return f"❌ {name}（美股） 查無資料"
    except Exception as e:
        print("[YF-ERR]", code, e)
        return f"❌ {name}（美股） 查詢失敗"

def stock_all():
    return "\n".join(stock(name) for name in stock_list_tpex)

# ========== 行事曆（修正版）==========
def cal():
    if not GOOGLE_CREDS_JSON_B64: 
        return "❌ 行事曆查詢失敗（無憑證）"
        
    try:
        info = json.loads(base64.b64decode(GOOGLE_CREDS_JSON_B64))
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/calendar.readonly"]
        )
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        
        # 取得台北時間的今日範圍
        today = datetime.now(tz).date()
        start = tz.localize(datetime.combine(today, datetime.min.time())).isoformat()
        end = tz.localize(datetime.combine(today, datetime.max.time())).isoformat()
        
        events_result = svc.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
            maxResults=10
        ).execute()
        
        items = events_result.get("items", [])
        events = []
        
        for event in items:
            if event.get("summary"):
                start_time = event["start"].get("dateTime", event["start"].get("date"))
                if "T" in start_time:  # 有時間的事件
                    time_obj = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    time_str = time_obj.astimezone(tz).strftime("%H:%M")
                    events.append(f"🗓️ {time_str} {event['summary']}")
                else:  # 全天事件
                    events.append(f"🗓️ {event['summary']}")
                    
        return "\n".join(events) if events else "今日無行程"
        
    except Exception as e:
        print("[CAL-ERR]", e)
        return "❌ 行事曆查詢失敗"

# ========== 美股前一晚行情（修正版）==========
def us():
    idx = {
        "道瓊": "^DJI",
        "S&P500": "^GSPC",
        "NASDAQ": "^IXIC"
    }
    focus = {
        "NVDA": "輝達",
        "SMCI": "美超微",
        "GOOGL": "Google",
        "AAPL": "蘋果"
    }

    def q_yf(code, name):
        try:
            import yfinance as yf
            tkr = yf.Ticker(code)
            hist = tkr.history(period="2d")
            
            if len(hist) >= 2:
                price = hist['Close'].iloc[-1]
                prev = hist['Close'].iloc[-2]
                diff = price - prev
                pct = diff / prev * 100 if prev else 0
                emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                return f"{emo} {name}: {price:.2f} ({diff:+.2f},{pct:+.2f}%)"
            else:
                return f"❌ {name}: 查無資料"
        except Exception as e:
            print("[YF-ERR]", code, e)
            return f"❌ {name}: 查詢失敗"

    idx_lines = [q_yf(c, n) for n, c in idx.items()]
    focus_lines = [q_yf(c, n) for c, n in focus.items()]

    return "📊 前一晚美股行情\n" + "\n".join(idx_lines) + "\n" + "\n".join(focus_lines)

# ========== Google Maps 路況（修正版）==========
def traffic(label):
    if not GOOGLE_MAPS_API_KEY:
        return "❌ 路況查詢失敗（無API Key）"
        
    if label not in ROUTE_CONFIG:
        return f"❌ 不支援的路線：{label}"
        
    cfg = ROUTE_CONFIG[label]
    o, d = cfg['o'], cfg['d']
    waypoints = "|".join(cfg['waypoints'])
    
    o_encoded = quote_plus(o)
    d_encoded = quote_plus(d)
    waypoints_encoded = quote_plus(waypoints)
    
    url = (
        f"https://maps.googleapis.com/maps/api/directions/json?"
        f"origin={o_encoded}&destination={d_encoded}&waypoints={waypoints_encoded}"
        f"&key={GOOGLE_MAPS_API_KEY}&departure_time=now&language=zh-TW"
    )
    
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print(f"[TRAFFIC-ERR] Status: {r.status_code}, Response: {r.text}")
            return "❌ 路況查詢失敗（API錯誤）"
            
        js = r.json()
        
        if js.get("status") != "OK":
            print(f"[TRAFFIC-ERR] API Status: {js.get('status')}, Error: {js.get('error_message')}")
            return f"❌ 路況查詢失敗：{js.get('error_message', 'Unknown error')}"
            
        routes = js.get("routes", [])
        if not routes:
            return "❌ 路況查詢失敗（無路線）"
            
        legs = routes[0].get("legs", [])
        if not legs:
            return "❌ 路況查詢失敗（無路段）"
            
        steps = legs[0]["steps"]
        traffic_info = []
        
        for step in steps:
            road = step["html_instructions"].replace("<b>", "").replace("</b>", "")
            duration = step.get("duration", {}).get("value", 0)
            traffic_duration = step.get("duration_in_traffic", {}).get("value", duration)
            
            if traffic_duration > duration * 1.3:
                color = TRAFFIC_EMOJI["RED"]
            elif traffic_duration > duration * 1.1:
                color = TRAFFIC_EMOJI["YELLOW"]
            else:
                color = TRAFFIC_EMOJI["GREEN"]
                
            traffic_info.append(f"{color} {road}")
            
        summary = routes[0].get("summary", "")
        duration_text = legs[0].get("duration_in_traffic", {}).get("text", "未知")
        
        return f"🚗 路線: {summary}\n預估時間: {duration_text}\n" + "\n".join(traffic_info)
        
    except Exception as e:
        print("[TRAFFIC-ERR]", e)
        return "❌ 路況查詢失敗"

# ========== LINE 推播（修正版）==========
def push(message):
    current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[LineBot] {current_time} 推播給 {LINE_USER_ID}：{message[:50]}...")
    
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("[LineBot] 推播失敗：缺少必要參數")
        return
        
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
        print("[LineBot] 推播成功")
    except Exception as e:
        print(f"[LineBot] 推播失敗：{e}")

# ========== 定時排程內容（時區修正版）==========
def morning_briefing():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：morning_briefing")
    
    msg = [
        "【早安簡報】",
        weather_accu("新店區", *LOCATION_COORDS["新店區"]),
        news(),
        cal(),
        fx(),
        us()
    ]
    push("\n\n".join(msg))

def commute_to_work():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：commute_to_work")
    
    msg = [
        "【通勤提醒/中山區】",
        weather_accu("中山區", *LOCATION_COORDS["中山區"]),
        traffic("家到公司")
    ]
    push("\n\n".join(msg))

def market_open():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：market_open")
    
    msg = ["【台股開盤】"] + [stock(name) for name in stock_list_tpex]
    push("\n\n".join(msg))

def market_mid():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：market_mid")
    
    msg = ["【台股盤中快訊】"] + [stock(name) for name in stock_list_tpex]
    push("\n\n".join(msg))

def market_close():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：market_close")
    
    msg = ["【台股收盤】"] + [stock(name) for name in stock_list_tpex]
    push("\n\n".join(msg))

def evening_zhongzheng():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：evening_zhongzheng")
    
    msg = [
        "【下班打球提醒/中正區】",
        weather_accu("中正區", *LOCATION_COORDS["中正區"]),
        get_taiwan_oil_price(),
        traffic("公司到郵局")
    ]
    push("\n\n".join(msg))

def evening_xindian():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：evening_xindian")
    
    msg = [
        "【回家/新店區】",
        weather_accu("新店區", *LOCATION_COORDS["新店區"]),
        get_taiwan_oil_price(),
        traffic("公司到家")
    ]
    push("\n\n".join(msg))

def us_market_open1():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：us_market_open1")
    
    push("【美股開盤速報】\n" + us())

def us_market_open2():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 排程觸發：us_market_open2")
    
    push("【美股盤後行情】\n" + us())

def keep_alive():
    current_time = datetime.now(tz)
    print(f"[Scheduler] {current_time.strftime('%Y-%m-%d %H:%M:%S')} 定時喚醒維持運作")

# ========== Scheduler 啟動（重要修正：加入時區設定）==========
scheduler = BackgroundScheduler(timezone=tz)  # 重要：指定時區

# 添加所有排程工作
scheduler.add_job(
    keep_alive, 
    CronTrigger(minute='0,10,20,30,40,50', timezone=tz),
    id='keep_alive'
)

scheduler.add_job(
    morning_briefing, 
    CronTrigger(hour=7, minute=10, timezone=tz),
    id='morning_briefing'
)

scheduler.add_job(
    commute_to_work, 
    CronTrigger(day_of_week='mon-fri', hour=8, minute=0, timezone=tz),
    id='commute_to_work'
)

scheduler.add_job(
    market_open, 
    CronTrigger(day_of_week='mon-fri', hour=9, minute=30, timezone=tz),
    id='market_open'
)

scheduler.add_job(
    market_mid, 
    CronTrigger(day_of_week='mon-fri', hour=12, minute=0, timezone=tz),
    id='market_mid'
)

scheduler.add_job(
    market_close, 
    CronTrigger(day_of_week='mon-fri', hour=13, minute=45, timezone=tz),
    id='market_close'
)

scheduler.add_job(
    evening_zhongzheng, 
    CronTrigger(day_of_week='mon,wed,fri', hour=18, minute=0, timezone=tz),
    id='evening_zhongzheng'
)

scheduler.add_job(
    evening_xindian, 
    CronTrigger(day_of_week='tue,thu', hour=18, minute=0, timezone=tz),
    id='evening_xindian'
)

scheduler.add_job(
    us_market_open1, 
    CronTrigger(day_of_week='mon-fri', hour=21, minute=30, timezone=tz),
    id='us_market_open1'
)

scheduler.add_job(
    us_market_open2, 
    CronTrigger(day_of_week='mon-fri', hour=23, minute=0, timezone=tz),
    id='us_market_open2'
)

# 啟動排程器
try:
    scheduler.start()
    print(f"[Scheduler] 排程器已啟動，時區：{tz}")
    
    # 列出所有排程工作以供檢查
    for job in scheduler.get_jobs():
        print(f"[Scheduler] 工作：{job.id}，下次執行：{job.next_run_time}")
        
except Exception as e:
    print(f"[Scheduler] 排程器啟動失敗：{e}")

# ========== Flask Routes（增加除錯功能）==========
@app.route("/")
def home():
    current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"✅ LINE Bot 正常運作中<br>當前時間：{current_time}"

@app.route("/debug/time")
def debug_time():
    import time
    utc_time = datetime.utcnow()
    local_time = datetime.now(tz)
    server_time = datetime.now()
    
    return f"""
    系統時區除錯資訊：<br>
    UTC時間：{utc_time}<br>
    台北時間：{local_time}<br>
    伺服器本地時間：{server_time}<br>
    時區偏移：{local_time.utcoffset()}<br>
    """

@app.route("/debug/jobs")
def debug_jobs():
    jobs_info = []
    for job in scheduler.get_jobs():
        jobs_info.append(f"工作ID：{job.id}<br>下次執行：{job.next_run_time}<br>")
    return "<br>".join(jobs_info) if jobs_info else "無排程工作"

@app.route("/test_weather")
def test_weather():
    city = request.args.get("city", "新店區")
    coords = LOCATION_COORDS.get(city, LOCATION_COORDS["新店區"])
    return weather_accu(city, *coords)

@app.route("/test_fx")
def test_fx():
    return fx()

@app.route("/test_oil")
def test_oil():
    return get_taiwan_oil_price()

@app.route("/test_news")
def test_news():
    return news()

@app.route("/test_stock")
def test_stock():
    return stock_all()

@app.route("/test_traffic")
def test_traffic():
    label = request.args.get("label", "家到公司")
    return traffic(label)

@app.route("/test_cal")
def test_cal():
    return cal()

@app.route("/test_us")
def test_us():
    return us()

@app.route("/send_scheduled_test")
def send_scheduled_test():
    time_str = request.args.get("time", "").strip()
    current_time = datetime.now(tz)
    print(f"[TEST] 模擬排程時間：{time_str}，當前時間：{current_time}")
    
    if time_str == "07:10":
        morning_briefing()
    elif time_str == "08:00":
        commute_to_work()
    elif time_str == "09:30":
        market_open()
    elif time_str == "12:00":
        market_mid()
    elif time_str == "13:45":
        market_close()
    elif time_str == "18:00":
        # 判斷星期（台北時間）
        weekday = current_time.weekday()
        if weekday in [0,2,4]:  # Mon Wed Fri
            evening_zhongzheng()
        else:  # Tue Thu
            evening_xindian()
    elif time_str == "21:30":
        us_market_open1()
    elif time_str == "23:00":
        us_market_open2()
    else:
        return f"❌ 不支援時間 {time_str}<br>支援：07:10, 08:00, 09:30, 12:00, 13:45, 18:00, 21:30, 23:00"
        
    return f"✅ 模擬推播 {time_str} 完成，執行時間：{current_time.strftime('%H:%M:%S')}"

@app.route("/health")
def health():
    return "OK"

# ========== LINE BOT Webhook（修正版）==========
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("[LINE] Invalid signature")
        abort(400)
    except Exception as e:
        print(f"[LINE] Webhook error: {e}")
        abort(500)
        
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    txt = event.message.text.strip()
    current_time = datetime.now(tz).strftime("%H:%M:%S")
    
    print(f"[LINE] {current_time} 收到訊息：{txt}")
    
    try:
        if txt == "天氣":
            reply = weather_accu("新店區", *LOCATION_COORDS["新店區"])
        elif txt == "油價":
            reply = get_taiwan_oil_price()
        elif txt == "匯率":
            reply = fx()
        elif txt == "新聞":
            reply = news()
        elif txt == "美股":
            reply = us()
        elif txt == "股票":
            reply = stock_all()
        elif txt == "行程":
            reply = cal()
        elif txt == "路況":
            reply = traffic("家到公司")
        elif txt == "時間":
            reply = f"現在時間：{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        elif txt.startswith("股票 "):
            stock_name = txt[3:].strip()
            reply = stock(stock_name)
        elif txt.startswith("路況 "):
            route_name = txt[3:].strip()
            reply = traffic(route_name)
        else:
            reply = (
                "🤖 支援指令：\n"
                "• 天氣 - 新店區天氣\n"
                "• 油價 - 最新油價\n"
                "• 匯率 - 台銀匯率\n"
                "• 新聞 - 今日新聞\n"
                "• 美股 - 美股行情\n"
                "• 股票 - 台股清單\n"
                "• 行程 - 今日行程\n"
                "• 路況 - 家到公司\n"
                "• 時間 - 現在時間\n"
                "• 股票 [名稱] - 個股查詢\n"
                "• 路況 [路線] - 指定路線"
            )
            
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(text=reply)
        )
        print(f"[LINE] 回覆成功：{reply[:50]}...")
        
    except Exception as e:
        print(f"[LINE] 處理訊息失敗：{e}")
        error_reply = f"❌ 處理失敗，請稍後再試"
        try:
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text=error_reply)
            )
        except:
            pass

if __name__ == "__main__":
    print(f"[APP] 應用程式啟動，時區：{tz}")
    print(f"[APP] 當前時間：{datetime.now(tz)}")
    app.run(host="0.0.0.0", port=10000, debug=False)
