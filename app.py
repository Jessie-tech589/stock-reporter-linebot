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
from urllib.parse import quote

app = Flask(__name__)
tz = pytz.timezone("Asia/Taipei")

# ========== ENV ===============
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
LINE_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET", "dummy")
LINE_USER_ID             = os.getenv("LINE_USER_ID")
WEATHER_API_KEY          = os.getenv("WEATHER_API_KEY")
GOOGLE_MAPS_API_KEY      = os.getenv("GOOGLE_MAPS_API_KEY")
NEWS_API_KEY             = os.getenv("NEWS_API_KEY")
GOOGLE_CREDS_JSON_B64    = os.getenv("GOOGLE_CREDS_JSON")
GOOGLE_CALENDAR_ID       = os.getenv("GOOGLE_CALENDAR_ID","primary")
FUGLE_API_KEY            = os.getenv("FUGLE_API_KEY")
FINNHUB_API_KEY          = os.getenv("FINNHUB_API_KEY")
CWA_API_KEY              = os.getenv("CWA_API_KEY")


line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler      = WebhookHandler(LINE_CHANNEL_SECRET)

# ========== STOCK MAPPING =============
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

def safe_get(url, timeout=10):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        return r if r.status_code==200 else None
    except Exception as e:
        print("[REQ-ERR]", url, e)
        return None

# ========== 天氣 ==========

def weather(loc: str) -> str:
    """
    以「區名」查詢中央氣象署 F-D0047-089 API 天氣（未來24小時預報）
    例：新店區、中山區、大安區
    """
    if not CWA_API_KEY:
        return "【系統未設定CWA_API_KEY】"
    loc = loc.strip()
    # 自動補全
    city = DISTRICT_CITY.get(loc)
    if city:
        search = f"{city}{loc}"
    else:
        search = loc
    url = (
        f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089"
        f"?Authorization={CWA_API_KEY}&locationName={quote(search)}"
    )
    print(f"[CWA-DEBUG] 查詢地名: {search}  API_KEY: {'有' if CWA_API_KEY else '無'}")
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return f"天氣查詢失敗（{loc}）"
        data = r.json()
        locations = data.get("records", {}).get("locations", [])
        if not locations or not locations[0].get("location"):
            return f"天氣查詢失敗（{loc}）"
        info = locations[0]["location"][0]
        wx   = info["weatherElement"][6]["time"][0]["elementValue"][0]["value"]
        pop  = info["weatherElement"][7]["time"][0]["elementValue"][0]["value"]
        minT = info["weatherElement"][8]["time"][0]["elementValue"][0]["value"]
        maxT = info["weatherElement"][12]["time"][0]["elementValue"][0]["value"]
        return (f"🌦️ {loc}\n"
                f"{wx}，降雨 {pop}%\n"
                f"🌡️ {minT}～{maxT}°C")
    except Exception as e:
        print("[CWA-WX-ERR]", e)
        return f"天氣查詢失敗（{loc}）"   
   

# ========== 匯率 ==========
def fx():
    url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
    r = safe_get(url)
    if not r:
        return "匯率查詢失敗"
    try:
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


# ========== 油價 ==========
def get_taiwan_oil_price():
    url = "https://www2.moeaea.gov.tw/oil111/Gasoline/NationwideAvg"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        # 解析 JSON 內容
        lst = data.get('nationwideAvgList', [])
        if not lst:
            return "油價查詢失敗（無資料）"
        # 取最新一筆
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

# ========== 新聞 ==========
def news():
    """
    NewsAPI 抓 台灣、大陸、國際重大新聞 各三則，合併成一串
    """
    sources = [
        ("台灣", "tw"),
        ("中國", "cn"),
        ("國際", "us"),  # 你也可改 gb、jp、fr...
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



# ========== 股票 ==========
def stock(name: str) -> str:
    code = STOCK.get(name, name)
    # 台股
    if code.endswith(".TW"):
        sym = code.replace(".TW", "").zfill(4)   # 這樣 sym="2330"
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
        r = safe_get(url)
        data = r.json() if r else []
        for row in data:
            if row.get('證券代號') == sym:      # 這裡一定是 4 碼數字比對
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

# ========== 行事曆 ==========
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

# ========== 路況 ==========
def traffic(label):
    cfg={
      "家到公司":dict(
        o="新北市新店區建國路99巷",d="台北市中山區南京東路三段131號",
        wp=["新北市新店區民族路","新北市北新路","台北市羅斯福路","台北市基隆路",
            "台北市辛亥路","台北市復興南路","台北市南京東路"],
        sum="建國路→民族路→北新路→羅斯福→基隆→辛亥→復興南→南京東"),
      "公司到中正區":dict(
        o="台北市中山區南京東路三段131號",d="台北市中正區愛國東路216號",
        wp=["台北市林森北路","台北市信義路","台北市信義路二段10巷","台北市愛國東路21巷"],
        sum="南京東→林森北→信義路→信義10巷→愛國東21巷"),
      "公司到新店區":dict(
        o="台北市中山區南京東路三段131號",d="新北市新店區建國路99巷",
        wp=["台北市復興南路","台北市辛亥路","台北市基隆路","台北市羅斯福路",
            "新北市北新路","新北市民族路"],
        sum="南京東→復興南→辛亥→基隆→羅斯福→北新→民族→建國路")
    }.get(label)
    if not cfg: return "路況查詢失敗"
    wp="|".join(cfg['wp'])
    url=(f"https://maps.googleapis.com/maps/api/directions/json?origin={cfg['o']}&destination={cfg['d']}"
         f"&waypoints={wp}&departure_time=now&mode=driving&key={GOOGLE_MAPS_API_KEY}")
    r=safe_get(url)
    if not r or not r.json().get("routes"): return "路況查詢失敗"
    leg=r.json()["routes"][0]["legs"][0]; dur=leg.get("duration_in_traffic",leg["duration"])
    sec,base=dur['value'],leg['duration']['value']
    lamp="🔴" if sec/base>1.25 else "🟡" if sec/base>1.05 else "🟢"
    return f"🚗 {cfg['o']} → {cfg['d']}\n🛵 {cfg['sum']}\n{lamp} {dur['text']}"

# ========== 美股前一晚摘要 ==========
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
    # 如果三大指數全都查無資料
    if idx_miss == len(idx):
        return "📈 前一晚美股行情\n今日美股休市（或暫無行情）\n" + "\n".join(focus_lines)
    return "📈 前一晚美股行情\n" + "\n".join(idx_lines) + "\n" + "\n".join(focus_lines)
# ========== 即時美股開盤行情 ==========
def us_open():
    tickers = {
        "道瓊": ".DJI",
        "S&P500": ".INX",
        "NASDAQ": ".IXIC",
        "NVDA": "NVDA",
        "SMCI": "SMCI",
        "GOOGL": "GOOGL",
        "AAPL": "AAPL"
    }
    lines = []
    for name, code in tickers.items():
        try:
            url = f"https://finnhub.io/api/v1/quote?symbol={code}&token={FINNHUB_API_KEY}"
            r = safe_get(url)
            data = r.json() if r else {}
            c = data.get("c"); pc = data.get("pc")
            if c and pc:
                diff = c - pc
                pct = diff / pc * 100 if pc else 0
                emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                lines.append(f"{emo} {name}: {c:.2f} ({diff:+.2f},{pct:+.2f}%)")
            else:
                lines.append(f"❌ {name}: 查無資料")
        except Exception as e:
            print("[FINNHUB-ERR]", code, e)
            lines.append(f"❌ {name}: 查詢失敗")
    return "🇺🇸 美股開盤速報\n\n" + "\n".join(lines) if lines else "美股查詢失敗"

# ========== LINE 推播 ==========
def push(msg): line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg.strip()))

# ========== 排程任務 ==========
def safe_run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return f"{fn.__name__} 查詢失敗"

def j0710():
    now = datetime.now(tz)
    msg = (
        f"🌅 早安 {now:%Y-%m-%d (%a)}\n\n"
        f"{safe_run(weather, '新北市新店區')}\n\n"
        f"{safe_run(news)}\n\n"
        f"{safe_run(cal)}\n\n"
        f"{safe_run(fx)}\n\n"
        f"{safe_run(us)}"
    )
    push(msg)
    
def j0800():
    push("🚌 通勤提醒\n\n"+traffic("家到公司")+"\n\n"+weather("台北市中山區"))

def _tai(head):
    lst = ["大盤","台積電","聯電","鴻準","00918","00878","元大美債20年","群益25年美債","仁寶","陽明","華航","長榮航"]
    push(head+"\n\n"+"\n".join(stock(s) for s in lst))

def j0930(): _tai("📈 台股開盤")
def j1200(): _tai("📊 台股盤中")
def j1345(): _tai("🔚 台股收盤")

def j1800():
    wd = datetime.now(tz).weekday()
    if wd in (0,2,4):   # 一三五
        push("🏸 下班打球提醒（中正區）\n\n"+traffic("公司到中正區")+"\n\n"+weather("台北市中正區")+"\n\n"+get_taiwan_oil_price())
    else:               # 二四
        push("🏠 下班回家提醒（新店區）\n\n"+traffic("公司到新店區")+"\n\n"+weather("新北市新店區")+"\n\n"+get_taiwan_oil_price())

def j2130(): push(us_open())
def j2300(): push("📊 美股行情更新\n\n"+us())
def keep():  safe_get("https://example.com")

# ========== APScheduler ==========
sch=BackgroundScheduler(timezone="Asia/Taipei")
sch.add_job(j0710 ,'cron',hour=7 ,minute=10)
sch.add_job(j0800 ,'cron',hour=8 ,minute=0 ,day_of_week='mon-fri')
sch.add_job(j0930 ,'cron',hour=9 ,minute=30,day_of_week='mon-fri')
sch.add_job(j1200 ,'cron',hour=12,minute=0 ,day_of_week='mon-fri')
sch.add_job(j1345 ,'cron',hour=13,minute=45,day_of_week='mon-fri')
sch.add_job(j1800 ,'cron',hour=18,minute=0 ,day_of_week='mon-fri')
sch.add_job(j2130 ,'cron',hour=21,minute=30,day_of_week='mon-fri')
sch.add_job(j2300 ,'cron',hour=23,minute=0 ,day_of_week='mon-fri')
sch.add_job(keep  ,'cron',minute='0,10,20,30,40,50')
sch.start()

# ========== Webhook / Health ==========
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

@app.route("/test_fx")
def test_fx():
    return fx()
@app.route("/test_us")
def test_us():
    return us()

@app.route("/test_weather", methods=["GET"])
def test_weather():
    loc = request.args.get("loc", "新北市新店區")  # 可帶「新店」、「新店區」、「中山」等
    return weather(loc)

@app.route("/test_oil")
def test_oil():
    return get_taiwan_oil_price()

@app.route("/test_stock")
def test_stock():
    return stock("聯電")

@app.route("/health")
def health():
    return "OK"

# ========== 主程式 ==========
if __name__ == "__main__":
    print("[TEST] 台積電 =", stock("台積電"))
    print("[TEST] NVDA  =", stock("NVDA"))
    print(get_taiwan_oil_price())
    app.run(host="0.0.0.0", port=10000)
