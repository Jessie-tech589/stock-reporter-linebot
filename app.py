# ───────────────────────────────────────────
#   LINE Bot 生活播報員 – 完整真實資料版
# ───────────────────────────────────────────
import os, base64, json, re, requests, yfinance as yf
from datetime import datetime, timedelta, date
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# ──────────────── 環境變數 ────────────────
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
LINE_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET", "dummy")
LINE_USER_ID             = os.getenv("LINE_USER_ID")
WEATHER_API_KEY          = os.getenv("WEATHER_API_KEY")            # OpenWeather
GOOGLE_MAPS_API_KEY      = os.getenv("GOOGLE_MAPS_API_KEY")        # Directions API
NEWS_API_KEY             = os.getenv("NEWS_API_KEY")               # NewsAPI
GOOGLE_CREDS_JSON_B64    = os.getenv("GOOGLE_CREDS_JSON")          # 服務帳戶 json Base64
GOOGLE_CALENDAR_ID       = os.getenv("GOOGLE_CALENDAR_ID", "primary")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler       = WebhookHandler(LINE_CHANNEL_SECRET)
tz_taipei     = pytz.timezone("Asia/Taipei")

# ──────────────── 股票對照表 ────────────────
STOCK_MAPPING = {
    # 美股
    "輝達":"NVDA","美超微":"SMCI","GOOGL":"GOOGL","Google":"GOOGL",
    "蘋果":"AAPL","特斯拉":"TSLA","微軟":"MSFT",
    # 台股
    "台積電":"2330.TW","2330":"2330.TW","聯電":"2303.TW","2303":"2303.TW",
    "鴻準":"2354.TW","2354":"2354.TW","仁寶":"2324.TW","2324":"2324.TW",
    "陽明":"2609.TW","2609":"2609.TW","華航":"2610.TW","2610":"2610.TW",
    "長榮航":"2618.TW","2618":"2618.TW","00918":"00918.TW","00878":"00878.TW",
    "元大美債20年":"00679B.TW","群益25年美債":"00723B.TW","大盤":"^TWII"
}

# ──────────────── 共用工具 ────────────────
def safe_get(url, timeout=10):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        return r if r.status_code == 200 else None
    except Exception:
        return None

# ──────────────── 天氣 (Geocoding → Weather) ────────────────
def get_weather(full_loc: str) -> str:
    geo = safe_get(f"http://api.openweathermap.org/geo/1.0/direct?q={full_loc},TW&limit=1&appid={WEATHER_API_KEY}")
    if not geo or not geo.json(): return "天氣查詢失敗"
    lat, lon = geo.json()[0]["lat"], geo.json()[0]["lon"]
    w = safe_get(f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&lang=zh_tw&units=metric")
    if not w: return "天氣查詢失敗"
    try:
        d = w.json()
        t, desc = d["main"]["temp"], d["weather"][0]["description"]
        hum, ws = d["main"]["humidity"], d["wind"]["speed"]
        return f"🌤️ {full_loc} {desc}\n🌡️{t}°C 💧{hum}% 💨{ws}m/s"
    except Exception:
        return "天氣查詢失敗"

# ──────────────── 匯率 (爬台銀) ────────────────
def get_fx():
    r = safe_get("https://rate.bot.com.tw/xrt?Lang=zh-TW")
    if not r: return "匯率查詢失敗"
    try:
        usd_row = BeautifulSoup(r.text, "lxml").find("td", text="USD").parent
        sell = usd_row.select_one("td[data-table='本行現金賣出']").text.strip()
        return f"💵 美元匯率：1 USD ≒ {sell} TWD"
    except Exception:
        return "匯率查詢失敗"

# ──────────────── 油價 (爬中油) ────────────────
def get_oil():
    base = "https://www.cpc.com.tw"
    lst  = safe_get(f"{base}/NewsContent.aspx?type=3")
    if not lst: return "油價查詢失敗"
    try:
        first = BeautifulSoup(lst.text, "lxml").select_one(".news-list a")
        det   = safe_get(base + first["href"])
        if not det: return "油價查詢失敗"
        txt   = BeautifulSoup(det.text, "lxml").get_text(" ", strip=True)
        price = {k: re.search(k+r'.*?([\d.]+)元', txt).group(1)
                 for k in ["92","95","98","超柴"] if re.search(k+r'.*?([\d.]+)元', txt)}
        return "⛽ 今日油價：\n" + "\n".join(f"{k}: {v} 元" for k,v in price.items()) if price else "油價查詢失敗"
    except Exception:
        return "油價查詢失敗"

# ──────────────── 新聞 (NewsAPI) ────────────────
def get_news():
    r = safe_get(f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}")
    arts = [a["title"] for a in (r.json().get("articles",[]) if r else []) if a.get("title")][:3]
    return "\n".join("• "+t for t in arts) if arts else "今日無新聞"

# ──────────────── 股票 (yfinance) ────────────────
def stock_price(name):
    def _q(code): 
        try: h=yf.Ticker(code).history(period="2d"); return h if not h.empty else None
        except: return None
    h = _q(STOCK_MAPPING.get(name,name)) or _q(name)
    if h is None: return f"❌ {name} 查無股價"
    td, yd = h.iloc[-1], h.iloc[-2] if len(h)>1 else h.iloc[-1]
    p, diff = td['Close'], td['Close']-yd['Close']
    pct, emo = (diff/yd['Close']*100 if yd['Close'] else 0), "📈" if diff>0 else "📉" if diff<0 else "➡️"
    return f"{emo} {name}\n💰 {p:.2f}\n{diff:+.2f} ({pct:+.2f}%)"

# ──────────────── Google Calendar ────────────────
def gc_service():
    if not GOOGLE_CREDS_JSON_B64: return None
    info  = json.loads(base64.b64decode(GOOGLE_CREDS_JSON_B64))
    creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/calendar.readonly"])
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

def get_calendar():
    svc = gc_service()
    if not svc: return "行事曆查詢失敗"
    today = date.today()
    start = tz_taipei.localize(datetime.combine(today, datetime.min.time())).isoformat()
    end   = tz_taipei.localize(datetime.combine(today, datetime.max.time())).isoformat()
    try:
        items = svc.events().list(calendarId=GOOGLE_CALENDAR_ID, timeMin=start, timeMax=end,
                                  singleEvents=True, orderBy="startTime", maxResults=10).execute().get("items", [])
        return "\n".join("🗓️ "+e["summary"] for e in items if e.get("summary")) or "今日無行程"
    except Exception:
        return "行事曆查詢失敗"

# ──────────────── Google Maps 路況 ────────────────
def traffic(label):
    cfg = {
        "家到公司": dict(
            o="新北市新店區建國路99巷", d="台北市中山區南京東路三段131號",
            wp=["新北市新店區民族路","新北市北新路","台北市羅斯福路","台北市基隆路",
                "台北市辛亥路","台北市復興南路","台北市南京東路"],
            sum="建國路→民族路→北新路→羅斯福→基隆→辛亥→復興南→南京東"),
        "公司到中正區": dict(
            o="台北市中山區南京東路三段131號", d="台北市中正區愛國東路216號",
            wp=["台北市林森北路","台北市信義路","台北市信義路二段10巷","台北市愛國東路21巷"],
            sum="南京東→林森北→信義路→信義10巷→愛國東21巷"),
        "公司到新店區": dict(
            o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
            wp=["台北市復興南路","台北市辛亥路","台北市基隆路","台北市羅斯福路",
                "新北市北新路","新北市民族路"],
            sum="南京東→復興南→辛亥→基隆→羅斯福→北新→民族→建國路")
    }.get(label)
    if not cfg: return "路況查詢失敗"
    wp = "|".join(cfg['wp'])
    url=(f"https://maps.googleapis.com/maps/api/directions/json?origin={cfg['o']}&destination={cfg['d']}"
         f"&waypoints={wp}&departure_time=now&mode=driving&key={GOOGLE_MAPS_API_KEY}")
    r=safe_get(url)
    if not r or not r.json().get("routes"): return "路況查詢失敗"
    leg=r.json()["routes"][0]["legs"][0]
    dur=leg.get("duration_in_traffic",leg["duration"])
    sec,base=dur['value'],leg['duration']['value']
    lamp="🔴" if sec/base>1.25 else "🟡" if sec/base>1.05 else "🟢"
    return f"🚗 {cfg['o']} → {cfg['d']}\n🛵 {cfg['sum']}\n{lamp} {dur['text']}"

# ──────────────── 美股摘要 ────────────────
def us_summary():
    tz_us=pytz.timezone("US/Eastern")
    d=(datetime.now(tz_us)-timedelta(days=3 if datetime.now(tz_us).weekday()==0 else 1)).date()
    idx={"道瓊":"^DJI","S&P500":"^GSPC","NASDAQ":"^IXIC"}
    focus={"NVDA":"輝達","SMCI":"美超微","GOOGL":"Google","AAPL":"蘋果"}
    def line(code,name):
        h=yf.Ticker(code).history(start=str(d),end=str(d+timedelta(days=1)))
        if h.empty: return ""
        o,c=h.iloc[0]['Open'],h.iloc[0]['Close']
        diff,pct=c-o,(c-o)/o*100; e="📈" if diff>0 else "📉" if diff<0 else "➡️"
        return f"{e} {name}: {c:.2f} ({diff:+.2f},{pct:+.2f}%)"
    return ("📈 前一晚美股行情\n\n"
            + "\n".join(line(c,n) for n,c in idx.items()) + "\n"
            + "\n".join(line(c,n) for c,n in focus.items()))

# ──────────────── LINE 推播封裝 ────────────────
def push(msg): line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg.strip()))

# ──────────────── 各排程任務 ────────────────
def job_0710():
    now=datetime.now(tz_taipei)
    msg=(f"🌅 早安 {now:%Y-%m-%d (%a)}\n\n{get_weather('新北市新店區')}\n\n"
         f"{get_news()}\n\n{get_calendar()}\n\n{get_fx()}\n\n{us_summary()}")
    push(msg)

def job_0800(): push("🚌 通勤提醒\n\n"+traffic("家到公司")+"\n\n"+get_weather("台北市中山區"))

def _tai(txt): 
    lst=["大盤","台積電","聯電","鴻準","00918","00878","元大美債20年",
         "群益25年美債","仁寶","陽明","華航","長榮航"]
    push(txt+"\n\n"+"\n".join(stock_price(s) for s in lst))

def job_0930(): _tai("📈 台股開盤")
def job_1200(): _tai("📊 台股盤中")
def job_1345(): _tai("🔚 台股收盤")

def job_1730_MWF(): push("🏸 下班打球提醒（中正區）\n\n"+traffic("公司到中正區")+"\n\n"+get_weather("台北市中正區")+"\n\n"+get_oil())
def job_1730_TT():   push("🏠 下班回家提醒（新店區）\n\n"+traffic("公司到新店區")+"\n\n"+get_weather("新北市新店區")+"\n\n"+get_oil())

def job_2130(): push("🇺🇸 美股開盤速報\n\n"+us_summary())
def job_2300(): push("📊 美股行情更新\n\n"+us_summary())

def keep_alive(): safe_get("https://example.com")

# ──────────────── 10 條排程 ────────────────
sched=BackgroundScheduler(timezone="Asia/Taipei")
sched.add_job(job_0710     ,'cron',hour=7 ,minute=10)
sched.add_job(job_0800     ,'cron',hour=8 ,minute=0 ,day_of_week='mon-fri')
sched.add_job(job_0930     ,'cron',hour=9 ,minute=30,day_of_week='mon-fri')
sched.add_job(job_1200     ,'cron',hour=12,minute=0 ,day_of_week='mon-fri')
sched.add_job(job_1345     ,'cron',hour=13,minute=45,day_of_week='mon-fri')
sched.add_job(job_1730_MWF ,'cron',hour=17,minute=30,day_of_week='mon,wed,fri')
sched.add_job(job_1730_TT  ,'cron',hour=17,minute=30,day_of_week='tue,thu')
sched.add_job(job_2130     ,'cron',hour=21,minute=30,day_of_week='mon-fri')
sched.add_job(job_2300     ,'cron',hour=23,minute=0 ,day_of_week='mon-fri')
sched.add_job(keep_alive   ,'cron',minute='0,10,20,30,40,50')
sched.start()

# ──────────────── LINE webhook & 健康檢查 ────────────────
@app.route("/callback", methods=["POST"])
def callback():
    try:
        handler.handle(request.get_data(as_text=True), request.headers.get("X-Line-Signature"))
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/")       ; def home():   return "✅ LINE Bot 正常運作中"
@app.route("/health") ; def health(): return "OK"

# 手動模擬端點
@app.route("/send_scheduled_test")
def test():
    t=request.args.get("time")
    if not t: return "請指定 ?time=HH:MM"
    class Fake(datetime):
        @classmethod
        def now(cls, tz=None):
            today=datetime.now(tz_taipei)
            hh,mm=map(int,t.split(":"))
            return tz.localize(datetime(today.year,today.month,today.day,hh,mm))
    import builtins, types, importlib
    builtins.datetime=types.ModuleType("datetime"); builtins.datetime.datetime=Fake
    try:
        job_0710(); job_0800(); job_0930(); job_1200(); job_1345(); job_1730_MWF(); job_1730_TT(); job_2130(); job_2300()
        return f"已模擬 {t}"
    finally:
        builtins.datetime=importlib.import_module("datetime")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=10000)
