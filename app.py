import os, base64, json, re, requests, yfinance as yf
from datetime import datetime, timedelta, date
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from google.oauth2 import service_account
from googleapiclient.discovery import build
from urllib.parse import quote

app = Flask(__name__)
tz = pytz.timezone("Asia/Taipei")  # 設定時區

# =====================[環境變數設定]=====================
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

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler      = WebhookHandler(LINE_CHANNEL_SECRET)

# =====================[台股股票對應表]=====================
STOCK = {
    "台積電":"2330.TW","聯電":"2303.TW","鴻準":"2354.TW","仁寶":"2324.TW",
    "陽明":"2609.TW","華航":"2610.TW","長榮航":"2618.TW",
    "00918":"00918.TW","00878":"00878.TW",
    "元大美債20年":"00679B.TW","群益25年美債":"00723B.TW",
    "大盤":"^TWII","輝達":"NVDA","美超微":"SMCI","GOOGL":"GOOGL","Google":"GOOGL",
    "蘋果":"AAPL","特斯拉":"TSLA","微軟":"MSFT"
}

# =====================[常用地址經緯度表]=====================
ADDR_COORDS = {
    "新北市新店區建國路99巷": (24.9659, 121.5412),
    "台北市中山區南京東路三段131號": (25.0524, 121.5382),
    "台北市中正區愛國東路216號": (25.0349, 121.5265),
    # 可擴充
}
def get_latlng(address):
    """用本地表或Google Maps Geocode 取得經緯度"""
    if address in ADDR_COORDS:
        return ADDR_COORDS[address]
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={quote(address)}&key={GOOGLE_MAPS_API_KEY}"
    r = requests.get(url)
    data = r.json()
    if data.get("status") == "OK" and data["results"]:
        loc = data["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    return None, None

# =====================[AccuWeather 經緯度查天氣]=====================
def weather(address):
    """
    依據地址取得即時天氣
    1. 用經緯度查LocationKey
    2. 用LocationKey查目前天氣
    """
    lat, lng = get_latlng(address)
    if not lat or not lng:
        print(f"[Weather] 查無座標 {address}")
        return f"天氣查無座標（{address}）"
    url1 = f"https://dataservice.accuweather.com/locations/v1/cities/geoposition/search?apikey={ACCUWEATHER_API_KEY}&q={lat},{lng}&language=zh-tw"
    r = requests.get(url1)
    if r.status_code != 200 or not r.json().get("Key"):
        print(f"[Weather] 查無LocationKey {address} res={r.text}")
        return f"天氣查無LocationKey（{address}）"
    key = r.json()["Key"]
    url2 = f"https://dataservice.accuweather.com/currentconditions/v1/{key}?apikey={ACCUWEATHER_API_KEY}&language=zh-tw"
    r2 = requests.get(url2)
    try:
        arr = r2.json()
        if isinstance(arr, list) and arr:
            info = arr[0]
            txt = info["WeatherText"]
            temp = info["Temperature"]["Metric"]["Value"]
            return f"🌤️ {address}\n{txt}\n🌡️ {temp}°C"
    except Exception as e:
        print(f"[Weather] 天氣失敗 {address} {e} {r2.text}")
    return f"天氣查詢失敗（{address}）"

# =====================[Google Maps 車流路線查詢]=====================
def traffic(label):
    """
    根據指定路線label（家到公司/公司到中正區/公司到新店區）查詢Google Maps車流
    """
    cfg = {
        "家到公司": dict(
            o="新北市新店區建國路99巷", d="台北市中山區南京東路三段131號",
            sum="新北市新店區建國路|新北市新店區民族路|新北市新店區北新路|台北市羅斯福路|台北市基隆路|台北市辛亥路|台北市復興南路|台北市南京東路"),
        "公司到郵局": dict(
            o="台北市中山區南京東路三段131號", d="台北市中正區愛國東路216號",
            sum="台北市南京東路|台北市林森北路|台北市信義路|台北市信義二段10巷|台北市愛國東21巷"),
        "公司到家": dict(
            o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
            sum="台北市南京東路|台北市復興南路|台北市辛亥路|台北市基隆路|台北市羅斯福路|新北市新店區北新路|新北市新店區民族路|新北市新店區建國路")
    }.get(label)
    if not cfg: return f"查無路線 {label}"
    waypoints = [s for s in cfg["sum"].split("|") if s]
    url = (
        f"https://maps.googleapis.com/maps/api/directions/json?origin={quote(cfg['o'])}"
        f"&destination={quote(cfg['d'])}&waypoints={'|'.join(quote(w) for w in waypoints[1:-1])}"
        f"&departure_time=now&key={GOOGLE_MAPS_API_KEY}&language=zh-TW"
    )
    r = requests.get(url)
    data = r.json()
    print(f"[Traffic] {label} {url}")
    if data.get("status") != "OK":
        print(f"[Traffic] 查詢失敗 {label}: {data}")
        return f"🚗 路況查詢失敗（{label}）"
    try:
        route = data["routes"][0]["legs"][0]
        duration = route["duration_in_traffic"]["text"] if "duration_in_traffic" in route else route["duration"]["text"]
        summary = cfg["sum"].replace("|", " → ")
        return f"🚗 {label}\n預估車程：{duration}\n路線：{summary}"
    except Exception as e:
        print(f"[Traffic] 路況解析失敗 {e}")
        return f"🚗 路況解析失敗（{label}）"

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

# =====================[匯率查詢：台銀即時]=====================
def fx():
    url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
    r = safe_get(url)
    if not r:
        return "匯率查詢失敗"
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        rows = table.find_all("tr")
        result = []
        mapping = {
            "美元 (USD)": "USD",
            "日圓 (JPY)": "JPY",
            "人民幣 (CNY)": "CNY",
            "港幣 (HKD)": "HKD",
        }
        flag = {
            "USD": "🇺🇸",
            "JPY": "🇯🇵",
            "CNY": "🇨🇳",
            "HKD": "🇭🇰"
        }
        for row in rows:
            cells = row.find_all("td")
            if len(cells) > 0:
                name = cells[0].text.strip()
                if name in mapping:
                    rate = cells[2].text.strip()
                    result.append(f"{flag[mapping[name]]} {mapping[name]}：{rate}")
        return "💱 今日匯率（現金賣出）\n" + "\n".join(result) if result else "查無匯率資料"
    except Exception as e:
        print("[FX-ERR]", e)
        return "匯率查詢失敗"

# =====================[油價查詢：經濟部能源局API]=====================
def get_taiwan_oil_price():
    url = "https://www2.moeaea.gov.tw/oil111/Gasoline/NationwideAvg"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        lst = data.get('nationwideAvgList', [])
        if not lst:
            return "油價查詢失敗（無資料）"
        today = lst[0]
        return (
            f"⛽ 本週油價（{today['announceDate']}）\n"
            f"92無鉛: {today['gasoline92']} 元\n"
            f"95無鉛: {today['gasoline95']} 元\n"
            f"98無鉛: {today['gasoline98']} 元\n"
            f"超級柴油: {today['diesel']} 元"
        )
    except Exception as e:
        print("[OIL-ERR]", e)
        return "油價查詢失敗"

# =====================[新聞查詢：NewsAPI]=====================
def news():
    sources = [
        ("台灣", "tw"),
        ("大陸", "cn"),
        ("國際", "us"),
    ]
    result = []
    for label, code in sources:
        url = f"https://newsapi.org/v2/top-headlines?country={code}&apiKey={NEWS_API_KEY}"
        r = safe_get(url)
        try:
            data = r.json() if r else {}
            if data.get("status") == "ok":
                arts = [a["title"] for a in data.get("articles", []) if a.get("title")] [:3]
                if arts:
                    result.append(f"【{label}】" + "\n" + "\n".join("• " + t for t in arts))
        except Exception as e:
            print(f"[NEWS-{label}-ERR]", e)
    return "\n\n".join(result) if result else "今日無新聞"

# =====================[台股/美股查詢：yfinance+twse]=====================
def stock(name: str) -> str:
    """
    台股走 twse json, 美股走yfinance, 計算漲跌
    """
    code = STOCK.get(name, name)
    if code.endswith(".TW"):
        sym = code.replace(".TW", "").zfill(4)
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
        r = safe_get(url)
        data = r.json() if r else []
        for row in data:
            if row.get('證券代號') == sym:
                price = row.get('收盤價')
                if price and price != '--':
                    return f"📈 {name}（台股）\n💰 {price}（收盤價）"
                else:
                    return f"❌ {name}（台股） 查無今日收盤價"
        return f"❌ {name}（台股） 查無代號"
    try:
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

# =====================[行事曆查詢：Google Calendar]=====================
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

# =====================[美股/指數查詢：Finnhub API]=====================
def us():
    idx = {"道瓊": ".DJI", "S&P500": ".INX", "NASDAQ": ".IXIC"}
    focus = {"NVDA":"輝達", "SMCI":"美超微", "GOOGL":"Google", "AAPL":"蘋果"}
    lines = []
    idx_miss = 0
    def q(code, name):
        nonlocal idx_miss
        try:
            url = f"https://finnhub.io/api/v1/quote?symbol={code}&token={FINNHUB_API_KEY}"
            r = safe_get(url)
            data = r.json() if r else {}
            c = data.get("c"); pc = data.get("pc")
            if c and pc:
                diff = c - pc
                pct = diff / pc * 100 if pc else 0
                emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                return f"{emo} {name}: {c:.2f} ({diff:+.2f},{pct:+.2f}%)"
        except Exception as e:
            print("[FINNHUB-ERR]", code, e)
        idx_miss += 1
        return f"❌ {name}: 查無資料"
    idx_lines = [q(c, n) for n, c in idx.items()]
    focus_lines = [q(c, n) for c, n in focus.items()]
    if idx_miss == len(idx):
        return "📈 前一晚美股行情\n今日美股休市（或暫無行情）\n" + "\n".join(focus_lines)
    return "📈 前一晚美股行情\n" + "\n".join(idx_lines) + "\n" + "\n".join(focus_lines)

# =====================[LineBot推播]=====================
def push(message):
    """推播訊息給指定用戶（含LOG）"""
    print(f"[LineBot] 推播給 {LINE_USER_ID}：{message}")
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
    except Exception as e:
        print(f"[LineBot] 推播失敗：{e}")

# =====================[排程推播定義]=====================
scheduler = BackgroundScheduler(timezone="Asia/Taipei")

def morning_briefing():
    """07:10 早安推播"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：morning_briefing")
    msg = [
        "【早安】",
        weather("新北市新店區建國路99巷"),
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
        "【通勤提醒】",
        weather("台北市中山區南京東路三段131號"),
        traffic("家到公司")
    ]
    push("\n\n".join(msg))

def market_open():
    """09:30 台股開盤通知"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：market_open")
    msg = ["【台股開盤】"]
    msg += [stock(x) for x in ["台積電","聯電","鴻準","仁寶","陽明"]]
    push("\n".join(msg))

def market_mid():
    """12:00 台股盤中快訊"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：market_mid")
    msg = ["【台股盤中】"]
    msg += [stock(x) for x in ["台積電","聯電","鴻準","仁寶","陽明"]]
    push("\n".join(msg))

def market_close():
    """13:45 台股收盤資訊"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：market_close")
    msg = ["【台股收盤】"]
    msg += [stock(x) for x in ["台積電","聯電","鴻準","仁寶","陽明","大盤"]]
    push("\n".join(msg))

def evening_zhongzheng():
    """18:00 下班/打球提醒（中正區天氣、車流、油價）"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：evening_zhongzheng")
    msg = [
        "【下班打球提醒/中正區】",
        weather("台北市中正區愛國東路216號"),
        traffic("公司到郵局"),
        get_taiwan_oil_price()
    ]
    push("\n\n".join(msg))

def evening_xindian():
    """18:00 下班/回家提醒（新店區天氣、車流、油價）"""
    print(f"[Scheduler] 排程觸發時間：{datetime.now()}，任務：evening_xindian")
    msg = [
        "【回家/新店區】",
        weather("新北市新店區建國路99巷"),
        traffic("公司到家"),
        get_taiwan_oil_price()
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

# =====================[排程註冊]=====================
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
scheduler.start()

# =====================[測試用API路由/健康檢查]=====================
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

@app.route("/")
def home():
    return "LineBot is running."

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
