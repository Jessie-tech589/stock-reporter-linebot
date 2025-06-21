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
tz = pytz.timezone("Asia/Taipei")

# ========== [環境變數載入] ==========
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

# LINE SDK 初始化
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler      = WebhookHandler(LINE_CHANNEL_SECRET)

# ========== [地名自動補全字典] ==========
DISTRICT_FULLNAME = {
    "新店": "新北市新店區", "新店區": "新北市新店區",
    "中山": "台北市中山區", "中山區": "台北市中山區",
    "中正": "台北市中正區", "中正區": "台北市中正區",
    "大安": "台北市大安區", "大安區": "台北市大安區",
    "新莊": "新北市新莊區", "新莊區": "新北市新莊區",
}

# ========== [股票對照表] ==========
STOCK = {
    "輝達":"NVDA","美超微":"SMCI","GOOGL":"GOOGL","Google":"GOOGL",
    "蘋果":"AAPL","特斯拉":"TSLA","微軟":"MSFT",
    "台積電":"2330.TW","聯電":"2303.TW",
    "鴻準":"2354.TW","仁寶":"2324.TW",
    "陽明":"2609.TW","華航":"2610.TW","長榮航":"2618.TW",
    "00918":"00918.TW","00878":"00878.TW",
    "元大美債20年":"00679B.TW","群益25年美債":"00723B.TW",
    "大盤":"^TWII"
}

# ========== [工具：安全的 requests get] ==========
def safe_get(url, timeout=10):
    try:
        print(f"[REQ] {url}")
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        print(f"[RESP] {r.status_code}")
        return r if r.status_code==200 else None
    except Exception as e:
        print("[REQ-ERR]", url, e)
        return None

# ========== [查詢天氣（自動補全地名）] ==========
def weather(loc: str) -> str:
    search = DISTRICT_FULLNAME.get(loc.strip(), loc.strip())
    url = (
        f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089"
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

# ========== [查詢匯率] ==========
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
                    rate = cells[2].text.strip()  # 本行現金賣出
                    result.append(f"{flag[mapping[name]]} {mapping[name]}：{rate}")
        return "💱 今日匯率（現金賣出）\n" + "\n".join(result) if result else "查無匯率資料"
    except Exception as e:
        print("[FX-ERR]", e)
        return "匯率查詢失敗"

# ========== [查詢油價] ==========
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

# ========== [查詢新聞] ==========
def news():
    sources = [
        ("台灣", "tw"),
        ("中國", "cn"),
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

# ========== [查詢所有台股資訊（全名對照表內台股）] ==========
def stock_all_tw():
    results = []
    for name, code in STOCK.items():
        if code.endswith(".TW") or code=="^TWII":
            res = stock(name)
            results.append(res)
    return "\n".join(results)

# ========== [查詢單一股票] ==========
def stock(name: str) -> str:
    code = STOCK.get(name, name)
    # 台股
    if code.endswith(".TW") or code=="^TWII":
        sym = code.replace(".TW", "").zfill(4) if code!="^TWII" else code
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
        r = safe_get(url)
        data = r.json() if r else []
        for row in data:
            if (row.get('證券代號') == sym) or (sym=="^TWII" and row.get('證券代號')==sym):
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

# ========== [查詢行事曆] ==========
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

# ========== [查詢美股行情] ==========
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

# ========== [主動推播 LINE 訊息] ==========
def push(msg):
    print("[PUSH]", msg)  # log
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg.strip()))
    except Exception as e:
        print("[PUSH-ERR]", e)

def keep_alive():
    print("[KEEP-ALIVE] 活著")

# ========== [排程工作，每個都加註解] ==========

def job_morning():
    """07:10 早安推播：天氣、新聞、行事曆、匯率、前晚美股行情"""
    print("[SCHEDULE] 07:10 morning_briefing")
    msg = "\n".join([
        weather("中山區"),
        news(),
        cal(),
        fx(),
        us()
    ])
    push("【早安推播】\n"+msg)

def job_commute():
    """08:00 上班通勤推播（中山區天氣）"""
    print("[SCHEDULE] 08:00 commute_to_work")
    msg = weather("中山區")
    push("【上班通勤推播】\n"+msg)

def job_market_open():
    """09:30 台股開盤（所有台股）"""
    print("[SCHEDULE] 09:30 market_open")
    msg = stock_all_tw()
    push("【台股開盤】\n"+msg)

def job_market_mid():
    """12:00 台股盤中快訊（所有台股）"""
    print("[SCHEDULE] 12:00 market_mid")
    msg = stock_all_tw()
    push("【台股盤中快訊】\n"+msg)

def job_market_close():
    """13:45 台股收盤（所有台股）"""
    print("[SCHEDULE] 13:45 market_close")
    msg = stock_all_tw()
    push("【台股收盤】\n"+msg)

def job_evening_zhongzheng():
    """17:30 下班推播（週一三五：中正區天氣+油價+打球提醒）"""
    print("[SCHEDULE] 17:30 evening_zhongzheng")
    msg = "\n".join([
        "下班提醒（中正區打球日）！",
        weather("中正區"),
        get_taiwan_oil_price()
    ])
    push("【下班推播】\n"+msg)

def job_evening_xindian():
    """17:30 下班推播（週二四：新店區天氣+油價+打球提醒）"""
    print("[SCHEDULE] 17:30 evening_xindian")
    msg = "\n".join([
        "下班提醒（新店區打球日）！",
        weather("新店區"),
        get_taiwan_oil_price()
    ])
    push("【下班推播】\n"+msg)

def job_us_market_open1():
    """21:30 美股開盤速報"""
    print("[SCHEDULE] 21:30 us_market_open1")
    msg = us()
    push("【美股開盤速報】\n"+msg)

def job_us_market_open2():
    """23:00 美股行情"""
    print("[SCHEDULE] 23:00 us_market_open2")
    msg = us()
    push("【美股行情】\n"+msg)

# ========== [排程設定] ==========
scheduler = BackgroundScheduler(timezone=tz)
scheduler.add_job(job_morning,           CronTrigger(hour=7, minute=10))
scheduler.add_job(job_commute,           CronTrigger(day_of_week='0-4', hour=8, minute=0))
scheduler.add_job(job_market_open,       CronTrigger(day_of_week='0-4', hour=9, minute=30))
scheduler.add_job(job_market_mid,        CronTrigger(day_of_week='0-4', hour=12, minute=0))
scheduler.add_job(job_market_close,      CronTrigger(day_of_week='0-4', hour=13, minute=45))
scheduler.add_job(job_evening_zhongzheng,CronTrigger(day_of_week='0,2,4', hour=17, minute=30))
scheduler.add_job(job_evening_xindian,   CronTrigger(day_of_week='1,3', hour=17, minute=30))
scheduler.add_job(job_us_market_open1,   CronTrigger(day_of_week='0-4', hour=21, minute=30))
scheduler.add_job(job_us_market_open2,   CronTrigger(day_of_week='0-4', hour=23, minute=0))
# 保活
scheduler.add_job(keep_alive,            CronTrigger(minute='0,10,20,30,40,45,50'))
scheduler.start()

# ========== [API 測試路徑] ==========
@app.route("/callback", methods=["POST"])
def callback():
    try:
        handler.handle(request.get_data(as_text=True), request.headers.get("X-Line-Signature"))
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/test_weather", methods=["GET"])
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
    return stock_all_tw()

@app.route("/test_us")
def test_us():
    return us()

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    print("[TEST] 台股資訊", stock_all_tw())
    print("[TEST] 美股行情", us())
    print(get_taiwan_oil_price())
    app.run(host="0.0.0.0", port=10000)
