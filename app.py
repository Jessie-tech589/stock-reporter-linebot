import os, base64, json, requests, yfinance as yf
from datetime import datetime, timedelta, date
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from google.oauth2 import service_account
from googleapiclient.discovery import build
from urllib.parse import quote

app = Flask(__name__)
tz = pytz.timezone("Asia/Taipei")

# ======== 環境變數 ========
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
LINE_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET", "dummy")
LINE_USER_ID             = os.getenv("LINE_USER_ID")
WEATHER_API_KEY          = os.getenv("WEATHER_API_KEY")
GOOGLE_MAPS_API_KEY      = os.getenv("GOOGLE_MAPS_API_KEY")
NEWS_API_KEY             = os.getenv("NEWS_API_KEY")
GOOGLE_CREDS_JSON_B64    = os.getenv("GOOGLE_CREDS_JSON")
GOOGLE_CALENDAR_ID       = os.getenv("GOOGLE_CALENDAR_ID", "primary")
FUGLE_API_KEY            = os.getenv("FUGLE_API_KEY")
FINNHUB_API_KEY          = os.getenv("FINNHUB_API_KEY")
CWA_API_KEY              = os.getenv("CWA_API_KEY", WEATHER_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler      = WebhookHandler(LINE_CHANNEL_SECRET)

# ====== 地名/台股定義 ======
DISTRICT_FULLNAME = {
    "新店": "新北市新店區", "新店區": "新北市新店區",
    "中山": "台北市中山區", "中山區": "台北市中山區",
    "中正": "台北市中正區", "中正區": "台北市中正區",
    "大安": "台北市大安區", "大安區": "台北市大安區",
    "新莊": "新北市新莊區", "新莊區": "新北市新莊區"
}

STOCK = {
    "台積電": "2330.TW", "聯電": "2303.TW", "鴻準": "2354.TW",
    "仁寶": "2324.TW", "陽明": "2609.TW", "華航": "2610.TW",
    "長榮航": "2618.TW", "00918": "00918.TW", "00878": "00878.TW",
    "元大美債20年": "00679B.TW", "群益25年美債": "00723B.TW",
    "大盤": "^TWII", "輝達": "NVDA", "美超微": "SMCI", "Google": "GOOGL", "蘋果": "AAPL"
}

# ====== 交通路線定義 ======
def traffic_config(label):
    cfg = {
        "家到公司": dict(
            o="新北市新店區建國路99巷",
            d="台北市中山區南京東路三段131號",
            sum="新北市新店區建國路|新北市新店區民族路|新北市新店區北新路|台北市羅斯福路|台北市基隆路|台北市辛亥路|台北市復興南路|台北市南京東路"
        ),
        "公司到中正區": dict(
            o="台北市中山區南京東路三段131號",
            d="台北市中正區愛國東路216號",
            sum="台北市南京東路|台北市林森北路|台北市信義路|台北市信義二段10巷|台北市愛國東21巷"
        ),
        "公司到新店區": dict(
            o="台北市中山區南京東路三段131號",
            d="新北市新店區建國路99巷",
            sum="台北市南京東路|台北市復興南路|台北市辛亥路|台北市基隆路|台北市羅斯福路|新北市新店區北新路|新北市新店區民族路|新北市新店區建國路"
        )
    }
    return cfg.get(label)

def traffic_route(label):
    # 取得即時路況，回傳文字描述
    conf = traffic_config(label)
    if not conf or not GOOGLE_MAPS_API_KEY:
        return "交通路況查詢失敗"
    url = (
        f"https://maps.googleapis.com/maps/api/directions/json?"
        f"origin={quote(conf['o'])}&destination={quote(conf['d'])}&key={GOOGLE_MAPS_API_KEY}&departure_time=now"
    )
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("status") != "OK":
            return "交通查詢失敗"
        route = data["routes"][0]["legs"][0]
        summary = conf["sum"].replace("|", " ➔ ")
        dur = route["duration_in_traffic"]["text"] if "duration_in_traffic" in route else route["duration"]["text"]
        return (f"🚗 預設路線：{summary}\n"
                f"🕒 預估行車時間：{dur}")
    except Exception as e:
        print("[TRAFFIC-ERR]", e)
        return "交通查詢失敗"

def safe_get(url, timeout=10):
    try:
        print("[REQ]", url)
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        print("[RESP]", r.status_code)
        return r if r.status_code==200 else None
    except Exception as e:
        print("[REQ-ERR]", url, e)
        return None

# ====== 天氣 ======
def weather(loc: str) -> str:
    search = DISTRICT_FULLNAME.get(loc.strip(), loc.strip())
    url = (
        f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093"
        f"?Authorization={CWA_API_KEY}&locationName={quote(search)}"
    )
    print(f"[CWA-DEBUG] url: {url}")
    try:
        r = requests.get(url, timeout=10)
        print(f"[CWA-DEBUG] status: {r.status_code}")
        data = r.json()
        print(f"[CWA-DEBUG] data keys: {list(data.keys())}")
        locations = data.get("records", {}).get("locations", [])
        if not locations or not locations[0].get("location"):
            return f"天氣查詢失敗（{search}）"
        info = locations[0]["location"][0]
        wx   = info["weatherElement"][6]["time"][0]["elementValue"][0]["value"]
        pop  = info["weatherElement"][7]["time"][0]["elementValue"][0]["value"]
        minT = info["weatherElement"][8]["time"][0]["elementValue"][0]["value"]
        maxT = info["weatherElement"][12]["time"][0]["elementValue"][0]["value"]
        return (f"🌦️ {search}\n"
                f"{wx}，降雨 {pop}%\n"
                f"🌡️ {minT}～{maxT}°C")
    except Exception as e:
        print("[CWA-WX-ERR]", e)
        return f"天氣查詢失敗（{search}）"

# ====== 匯率 ======
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

# ====== 油價 ======
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

# ====== 新聞 ======
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

# ====== 台/美股（多檔顯示） ======
def stock_all():
    msg = []
    # 台股
    for name in ["台積電","聯電","鴻準","仁寶","陽明","華航","長榮航","00918","00878","元大美債20年","群益25年美債","大盤"]:
        msg.append(stock(name))
    # 美股
    for name in ["輝達","美超微","Google","蘋果"]:
        msg.append(stock(name))
    return "\n".join(msg)

def stock(name: str) -> str:
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
    # 美股
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

# ====== 行事曆 ======
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

# ====== 美股前一晚摘要 ======
def us():
    idx = {"道瓊": ".DJI", "S&P500": ".INX", "NASDAQ": ".IXIC"}
    focus = {"NVDA": "輝達", "SMCI": "美超微", "GOOGL": "Google", "AAPL": "蘋果"}
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

# ====== LINE 推播 ======
def push(msg): 
    print("[LINE-PUSH]\n", msg)
    line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg.strip()))

def safe_run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return f"{fn.__name__} 查詢失敗"

# ====== 定時推播任務（時間皆為 Asia/Taipei） ======
def morning_briefing():
    msg = [
        "【早安】",
        weather("新店區"),
        news(),
        cal(),
        fx(),
        us(),
        # 不再顯示 stock_all()
    ]
    push("\n\n".join(msg))

def commute_to_work():
    msg = [
        "【通勤提醒/中山區】",
        weather("中山區"),
        traffic_route("家到公司")
    ]
    push("\n\n".join(msg))

def market_open():
    msg = [
        "【台股開盤】",
        stock_all()
    ]
    push("\n\n".join(msg))

def market_mid():
    msg = [
        "【台股盤中快訊】",
        stock_all()
    ]
    push("\n\n".join(msg))

def market_close():
    msg = [
        "【台股收盤】",
        stock_all()
    ]
    push("\n\n".join(msg))

def evening_zhongzheng():
    msg = [
        "【下班打球提醒/中正區】",
        weather("中正區"),
        get_taiwan_oil_price(),
        traffic_route("公司到中正區")
    ]
    push("\n\n".join(msg))

def evening_xindian():
    msg = [
        "【回家/新店區】",
        weather("新店區"),
        get_taiwan_oil_price(),
        traffic_route("公司到家")
    ]
    push("\n\n".join(msg))

def us_market_open1():
    msg = [
        "【美股開盤速報】",
        us()
    ]
    push("\n\n".join(msg))

def us_market_open2():
    msg = [
        "【美股行情更新】",
        us()
    ]
    push("\n\n".join(msg))

def keep_alive():
    print(f"[Scheduler] 定時喚醒維持運作 {datetime.now(tz)}")

# ====== Scheduler 設定 ======
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(keep_alive,         CronTrigger(minute="0,10,20,30,40,45,50"))
scheduler.add_job(morning_briefing,   CronTrigger(hour=7, minute=10))
scheduler.add_job(commute_to_work,    CronTrigger(hour=8, minute=0, day_of_week='mon-fri'))
scheduler.add_job(market_open,        CronTrigger(hour=9, minute=30, day_of_week='mon-fri'))
scheduler.add_job(market_mid,         CronTrigger(hour=12, minute=0, day_of_week='mon-fri'))
scheduler.add_job(market_close,       CronTrigger(hour=13, minute=45, day_of_week='mon-fri'))
scheduler.add_job(evening_zhongzheng, CronTrigger(hour=18, minute=0, day_of_week='mon,wed,fri'))
scheduler.add_job(evening_xindian,    CronTrigger(hour=18, minute=0, day_of_week='tue,thu'))
scheduler.add_job(us_market_open1,    CronTrigger(hour=21, minute=30, day_of_week='mon-fri'))
scheduler.add_job(us_market_open2,    CronTrigger(hour=23, minute=0, day_of_week='mon-fri'))
scheduler.start()

# ====== 測試/健康檢查/Webhook ======
@app.route("/callback", methods=["POST"])
def callback():
    try:
        handler.handle(request.get_data(as_text=True), request.headers.get("X-Line-Signature"))
    except Exception:
        abort(400)
    return "OK"

@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/test_weather")
def test_weather():
    loc = request.args.get("loc", "新北市新店區")
    return weather(loc)

@app.route("/test_oil")
def test_oil():
    return get_taiwan_oil_price()

@app.route("/test_fx")
def test_fx():
    return fx()

@app.route("/test_stock")
def test_stock():
    return stock_all()

@app.route("/test_us")
def test_us():
    return us()

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
