import os
import base64
import json
import requests
from datetime import datetime, timedelta, date
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ========== 環境變數 ==========
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID             = os.getenv("LINE_USER_ID")      # 推播用 UserID
WEATHER_API_KEY          = os.getenv("WEATHER_API_KEY")   # 備用（如CWA）
GOOGLE_MAPS_API_KEY      = os.getenv("GOOGLE_MAPS_API_KEY")  # Google Maps (車流、地理編碼)
NEWS_API_KEY             = os.getenv("NEWS_API_KEY")      # NewsAPI
GOOGLE_CREDS_JSON_B64    = os.getenv("GOOGLE_CREDS_JSON") # Google Calendar 憑證(b64)
GOOGLE_CALENDAR_ID       = os.getenv("GOOGLE_CALENDAR_ID","primary")
FUGLE_API_KEY            = os.getenv("FUGLE_API_KEY")     # Fugle 台股API
FINNHUB_API_KEY          = os.getenv("FINNHUB_API_KEY")   # Finnhub 美股API
CWA_API_KEY              = os.getenv("CWA_API_KEY", WEATHER_API_KEY)  # 中央氣象局API
ACCUWEATHER_API_KEY      = os.getenv("ACCUWEATHER_API_KEY") # AccuWeather API

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

# ========== STOCK MAPPING（台股/美股完整） ==========
STOCK = {
    "台積電":"2330.TW","聯電":"2303.TW","鴻準":"2354.TW","仁寶":"2324.TW",
    "陽明":"2609.TW","華航":"2610.TW","長榮航":"2618.TW",
    "00918":"00918.TW","00878":"00878.TW",
    "元大美債20年":"00679B.TW","群益25年美債":"00723B.TW",
    "大盤":"^TWII",
    "輝達":"NVDA","美超微":"SMCI","GOOGL":"GOOGL","Google":"GOOGL",
    "蘋果":"AAPL","特斯拉":"TSLA","微軟":"MSFT"
}

# ========== 路線對照 ==========
ROUTE_CONFIG = {
    "家到公司": dict(
            o="新北市新店區建國路99巷", d="台北市中山區南京東路三段131號",
            sum="新北市新店區建國路|新北市新店區民族路|新北市新店區北新路|台北市羅斯福路|台北市基隆路|台北市辛亥路|台北市復興南路|台北市南京東路"),
        "公司到郵局": dict(
            o="台北市中山區南京東路三段131號", d="台北市中正區愛國東路216號",
            sum="台北市南京東路|台北市林森北路|台北市信義路|台北市信義二段10巷|台北市愛國東21巷"),
        "公司到家": dict(
            o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
            sum="台北市南京東路|台北市復興南路|台北市辛亥路|台北市基隆路|台北市羅斯福路|新北市新店區北新路|新北市新店區民族路|新北市新店區建國路")
}

# ========== Emoji 小圖示 ==========
WEATHER_ICON = {
    "Sunny": "☀️", "Clear": "🌕", "Cloudy": "☁️", "Partly cloudy": "⛅",
    "Rain": "🌧️", "Thunderstorm": "⛈️", "Fog": "🌫️", "Snow": "🌨️",
}
TRAFFIC_EMOJI = { "RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢" }

# =====================[API安全封裝&重試]=====================
def safe_get(url, timeout=10):
    print(f"[REQ] {url}")
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        print(f"[RESP] {r.status_code}")
        return r if r.status_code==200 else None
    except Exception as e:
        print("[REQ-ERR]", url, e)
        return None
		

# ========== 天氣查詢（AccuWeather） ==========
def weather_accu(city, lat, lon):
    try:
        url_loc = f"https://dataservice.accuweather.com/locations/v1/cities/geoposition/search?apikey={ACCUWEATHER_API_KEY}&q={lat},{lon}&language=zh-tw"
        loc_res = requests.get(url_loc, timeout=10)
        loc_data = loc_res.json()
        key = loc_data["Key"]
        loc_name = loc_data["LocalizedName"]
        url_wx = f"https://dataservice.accuweather.com/currentconditions/v1/{key}?apikey={ACCUWEATHER_API_KEY}&details=true&language=zh-tw"
        wx_res = requests.get(url_wx, timeout=10)
        wx = wx_res.json()[0]
        temp = wx['Temperature']['Metric']['Value']
        realfeel = wx['RealFeelTemperature']['Metric']['Value']
        wxtext = wx['WeatherText']
        icon = WEATHER_ICON.get(wxtext, "🌦️")
        return (f"{icon} {loc_name} ({city})\n"
                f"{wxtext}，溫度 {temp}°C，體感 {realfeel}°C")
    except Exception as e:
        print("[WX-ERR]", e)
        return f"天氣查詢失敗（{city}）"

# ========== 匯率 ==========
def fx():
    url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(requests.get(url).text, "lxml")
        table = soup.find("table")
        rows = table.find_all("tr")
        mapping = {
            "美元 (USD)": ("USD","🇺🇸"), "日圓 (JPY)": ("JPY","🇯🇵"),
            "人民幣 (CNY)": ("CNY","🇨🇳"), "港幣 (HKD)": ("HKD","🇭🇰"),
        }
        result = []
        for row in rows:
            cells = row.find_all("td")
            if cells and cells[0].text.strip() in mapping:
                code, flag = mapping[cells[0].text.strip()]
                rate = cells[2].text.strip()
                result.append(f"{flag} {code}: {rate}")
        return "💱 今日匯率（現金賣出）\n" + "\n".join(result) if result else "查無匯率資料"
    except Exception as e:
        print("[FX-ERR]", e)
        return "匯率查詢失敗"

# ========== 油價 ==========
def get_taiwan_oil_price():
    url = "https://www2.moeaea.gov.tw/oil111/Gasoline/NationwideAvg"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        today = data['nationwideAvgList'][0]
        return (f"⛽ 油價（{today['announceDate']}）\n"
                f"92無鉛: {today['gasoline92']} 元\n"
                f"95無鉛: {today['gasoline95']} 元\n"
                f"98無鉛: {today['gasoline98']} 元\n"
                f"超級柴油: {today['diesel']} 元")
    except Exception as e:
        print("[OIL-ERR]", e)
        return "油價查詢失敗"

# ========== 新聞 ==========
def news():
    sources = [
        ("台灣", "tw"),
        ("大陸", "cn"),
        ("國際", "us"),
    ]
    result = []
    for label, code in sources:
        url = f"https://newsapi.org/v2/top-headlines?country={code}&apiKey={NEWS_API_KEY}"
        try:
            data = requests.get(url).json()
            if data.get("status") == "ok":
                arts = [a["title"] for a in data.get("articles", []) if a.get("title")] [:3]
                if arts:
                    result.append(f"📰【{label}】" + "\n" + "\n".join("• " + t for t in arts))
        except Exception as e:
            print(f"[NEWS-{label}-ERR]", e)
    return "\n\n".join(result) if result else "今日無新聞"

# ========== 股票 ==========
def stock_all():
    result = []
    for name, code in STOCK.items():
        if code.endswith(".TW"):
            result.append(stock(name))
    return "\n".join(result)

def stock(name: str) -> str:
    code = STOCK.get(name, name)
    # 台股
    if code.endswith(".TW"):
        sym = code.replace(".TW", "").zfill(4)
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
        try:
            r = requests.get(url, timeout=10)
            data = r.json()
            for row in data:
                if row.get('證券代號') == sym:
                    price = row.get('收盤價')
                    if price and price != '--':
                        return f"📈 {name}（台股）\n💰 {price}（收盤價）"
                    else:
                        return f"❌ {name}（台股） 查無今日收盤價"
            return f"❌ {name}（台股） 查無代號"
        except Exception as e:
            print("[STOCK-TW-ERR]", e)
            return f"❌ {name}（台股） 查詢失敗"
    # 美股
    try:
        import yfinance as yf
        tkr = yf.Ticker(code)
        info = getattr(tkr, "fast_info", {}) or tkr.info
        price = info.get("regularMarketPrice")
        prev  = info.get("previousClose")
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
    return "\n".join(stock(name) for name in [
        "台積電","聯電","鴻準","仁寶","陽明","華航","長榮航","00918","00878","元大美債20年","群益25年美債","大盤"
    ])

# ========== 行事曆（Google Calendar） ==========
def cal():
    if not GOOGLE_CREDS_JSON_B64: return "行事曆查詢失敗"
    try:
        info=json.loads(base64.b64decode(GOOGLE_CREDS_JSON_B64))
        creds=service_account.Credentials.from_service_account_info(info,scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc=build("calendar","v3",credentials=creds,cache_discovery=False)
        today=date.today()
        start=tz.localize(datetime.combine(today,datetime.min.time())).isoformat()
        end  =tz.localize(datetime.combine(today,datetime.max.time())).isoformat()
        items=svc.events().list(calendarId=GOOGLE_CALENDAR_ID,timeMin=start,timeMax=end,singleEvents=True,orderBy="startTime",maxResults=10).execute().get("items",[])
        return "\n".join("🗓️ "+e["summary"] for e in items if e.get("summary")) or "今日無行程"
    except Exception as e:
        print("[CAL-ERR]", e)
        return "行事曆查詢失敗"

# ========== 美股前一晚行情 ==========
def us():
    idx = {"道瓊": ".DJI", "S&P500": ".INX", "NASDAQ": ".IXIC"}
    focus = {"NVDA":"輝達", "SMCI":"美超微", "GOOGL":"Google", "AAPL":"蘋果"}
    lines = []
    def q(code, name):
        try:
            url = f"https://finnhub.io/api/v1/quote?symbol={code}&token={FINNHUB_API_KEY}"
            r = requests.get(url, timeout=10)
            data = r.json()
            c = data.get("c"); pc = data.get("pc")
            if c and pc:
                diff = c - pc
                pct = diff / pc * 100 if pc else 0
                emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                return f"{emo} {name}: {c:.2f} ({diff:+.2f},{pct:+.2f}%)"
        except Exception as e:
            print("[FINNHUB-ERR]", code, e)
        return f"❌ {name}: 查無資料"
    idx_lines = [q(c, n) for n, c in idx.items()]
    focus_lines = [q(c, n) for c, n in focus.items()]
    return "📊 前一晚美股行情\n" + "\n".join(idx_lines) + "\n" + "\n".join(focus_lines)

# ========== Google Maps 路況（圓形 emoji） ==========
def traffic(label):
    cfg = ROUTE_CONFIG[label]
    o, d = cfg['o'], cfg['d']
    waypoints = "|".join(cfg['waypoints'])
    url = f"https://maps.googleapis.com/maps/api/directions/json?origin={o}&destination={d}&waypoints={waypoints}&key={GOOGLE_MAPS_API_KEY}&departure_time=now&language=zh-TW"
    try:
        r = requests.get(url, timeout=10)
        js = r.json()
        routes = js.get("routes", [])
        if not routes:
            return "🚗 路況查詢失敗"
        steps = routes[0]["legs"][0]["steps"]
        traffic_info = []
        for step in steps:
            road = step["html_instructions"].replace("<b>","").replace("</b>","")
            # Google Directions API 沒直接給 traffic，這裡簡單判斷：
            duration = step.get("duration",{}).get("value",0)
            traffic_duration = step.get("duration_in_traffic",{}).get("value",duration)
            if traffic_duration > duration * 1.3:
                color = TRAFFIC_EMOJI["RED"]
            elif traffic_duration > duration * 1.1:
                color = TRAFFIC_EMOJI["YELLOW"]
            else:
                color = TRAFFIC_EMOJI["GREEN"]
            traffic_info.append(f"{color} {road}")
        summary = js['routes'][0].get("summary","")
        duration = js['routes'][0]["legs"][0]["duration_in_traffic"]["text"]
        return f"🚗 路線: {summary}\n預估時間: {duration}\n" + "\n".join(traffic_info)
    except Exception as e:
        print("[TRAFFIC-ERR]", e)
        return "路況查詢失敗"

# ========== LINE 推播 ==========
def push(message):
    """推播訊息給指定用戶（含LOG）"""
    print(f"[LineBot] 推播給 {LINE_USER_ID}：{message}")
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
    except Exception as e:
        print(f"[LineBot] 推播失敗：{e}")
		
# ========== 定時排程內容 ==========
def morning_briefing():
    """07:10 早安推播"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：morning_briefing")
    msg = [
        "【早安】",
        weather_accu("新店區", *LOCATION_COORDS["新店區"]),
        news(),
        cal(),
        fx(),
        us()
    ]
    push("\n\n".join(msg))

def commute_to_work():
    """08:00 通勤提醒（中山區天氣＋家到公司車流）"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：commute_to_work")
    msg = [
        "【通勤提醒/中山區】",
        weather_accu("中山區", *LOCATION_COORDS["中山區"]),
        traffic("家到公司")
    ]
    push("\n\n".join(msg))

def market_open():
    """09:30 台股開盤通知"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：market_open")
    msg = ["【台股開盤】"] + [stock(k) for k in STOCK if k != "大盤"]
    push("\n\n".join(msg))

def market_close():
    """12:00 台股盤中快訊"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：market_mid")
    msg = ["【台股收盤】"] + [stock(k) for k in STOCK if k != "大盤"]
    push("\n\n".join(msg))

def market_close():
    """13:45 台股收盤資訊"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：market_close")
    msg = ["【台股收盤】"]
    msg += [stock(x) for x in ["台積電","聯電","鴻準","仁寶","陽明","大盤"]]
    push("\n".join(msg))

def evening_zhongzheng():
    msg = [
        "【下班打球提醒/中正區】",
        weather_accu("中正區", *LOCATION_COORDS["中正區"]),
        get_taiwan_oil_price(),
        traffic("公司到中正區")
    ]
    push("\n\n".join(msg))

def evening_xindian():
    msg = [
        "【回家/新店區】",
        weather_accu("新店區", *LOCATION_COORDS["新店區"]),
        get_taiwan_oil_price(),
        traffic("公司到新店區")
    ]
    push("\n\n".join(msg))

def us_market_open1():
    """21:30 美股開盤速報"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：us_market_open1")
    push("【美股開盤速報】\n" + us())

def us_market_open2():
    """23:00 美股盤後行情"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：us_market_open2")
    push("【美股盤後行情】\n" + us())

def keep_alive():
    """10分鐘喚醒排程（防止休眠）"""
    print(f"[Scheduler] 定時喚醒維持運作 {datetime.now()}")
	
# ========== Scheduler 啟動 ==========
scheduler = BackgroundScheduler()
scheduler.add_job(keep_alive,      CronTrigger(minute='0,10,20,30,40,50'))
scheduler.add_job(morning_briefing,   CronTrigger(hour=7,  minute=10))
scheduler.add_job(commute_to_work,    CronTrigger(day_of_week='0-4', hour=8,  minute=0))
scheduler.add_job(market_open,        CronTrigger(day_of_week='0-4', hour=9,  minute=30))
scheduler.add_job(market_mid,         CronTrigger(day_of_week='0-4', hour=12, minute=0))
scheduler.add_job(market_close,       CronTrigger(day_of_week='0-4', hour=13, minute=45))
scheduler.add_job(evening_zhongzheng, CronTrigger(day_of_week='0,2,4', hour=18, minute=0))
scheduler.add_job(evening_xindian,    CronTrigger(day_of_week='1,3', hour=18, minute=0))
scheduler.add_job(us_market_open1,    CronTrigger(day_of_week='0-4', hour=21, minute=30))
scheduler.add_job(us_market_open2,    CronTrigger(day_of_week='0-4', hour=23, minute=0))
scheduler.start())

# ========== Flask Routes ==========
@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/test_weather")
def test_weather():
    loc = request.args.get("loc") or "新北市新店區建國路99巷"
    return weather(loc)

@app.route("/test_traffic")
def test_traffic():
    lbl = request.args.get("label") or "家到公司"
    return traffic(lbl)

@app.route("/test_stock")
def test_stock():
    return "<br>".join(stock(x) for x in ["台積電","聯電","鴻準","仁寶","陽明","華航","長榮航","00918","00878","元大美債20年","群益25年美債","大盤"])

@app.route("/test_fx")
def test_fx():
    return fx()

@app.route("/test_oil")
def test_oil():
    return get_taiwan_oil_price()

@app.route("/test_news")
def test_news():
    return news()

@app.route("/test_us")
def test_us():
    return us()

@app.route("/health")
def health():
    return "OK"
	
# =====================[LINE BOT Webhook & 指令回應]=====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(TextSendMessage)
def handle_message(event):
    """依照用戶指令回應特定資訊"""
    txt = event.message.text.strip()
    if txt == "天氣":
        reply = weather("新北市新店區建國路99巷")
    elif txt == "油價":
        reply = get_taiwan_oil_price()
    elif txt == "匯率":
        reply = fx()
    elif txt == "新聞":
        reply = news()
    elif txt == "美股":
        reply = us()
    else:
        reply = "指令未支援"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# =====================[主程式入口]=====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
