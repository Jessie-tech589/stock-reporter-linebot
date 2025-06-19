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
    def query(q):
        url = f"http://api.openweathermap.org/geo/1.0/direct?q={quote(q+',台灣')}&limit=1&appid={WEATHER_API_KEY}"
        r = safe_get(url)
        try:
            return r.json()[0] if r and r.json() else None
        except: return None
    geo = query(loc) or query(loc.replace("區","")) or query("台北市")
    if not geo:
        return f"天氣查詢失敗（{loc}）"
    lat, lon = geo["lat"], geo["lon"]
    w = safe_get(f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&lang=zh_tw&units=metric")
    if not w:
        return "天氣查詢失敗"
    try:
        d = w.json()
        t, desc = d["main"]["temp"], d["weather"][0]["description"]
        hum, ws = d["main"]["humidity"], d["wind"]["speed"]
        return f"🌤️ {loc} {desc}\n🌡️{t}°C 💧{hum}% 💨{ws}m/s"
    except Exception as e:
        print("[WX-ERR]", e)
        return "天氣查詢失敗"

# ========== 匯率 ==========
def fx():
    # 台銀匯率表網址
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
        for row in rows:
            cells = row.find_all("td")
            if len(cells) > 0:
                name = cells[0].text.strip()
                if name in mapping:
                    rate = cells[2].text.strip()  # 本行現金賣出
                    flag = {"USD":"🇺🇸", "JPY":"🇯🇵", "CNY":"🇨🇳", "HKD":"🇭🇰"}[mapping[name]]
                    result.append(f"{flag} {mapping[name]}：{rate}")
        return "💱 今日匯率（現金賣出）\n" + "\n".join(result) if result else "查無匯率資料"
    except Exception as e:
        print("[FX-ERR]", e)
        return "匯率查詢失敗"


# ========== 油價 ==========
def oil():
    # 官方公開網頁
    url = "https://vipmbr.cpc.com.tw/mbwebs/mbprice_oil.aspx"
    r = safe_get(url)
    try:
        if r:
            soup = BeautifulSoup(r.text, "lxml")
            table = soup.find("table", {"id":"gvOilPrice"})
            rows = table.find_all("tr") if table else []
            if len(rows) > 2:
                cells = rows[1].find_all("td")
                price_92 = cells[1].text.strip()
                price_95 = cells[2].text.strip()
                price_98 = cells[3].text.strip()
                price_ds = cells[4].text.strip()
                return (f"⛽ 今日油價：\n"
                        f"92: {price_92} 元\n"
                        f"95: {price_95} 元\n"
                        f"98: {price_98} 元\n"
                        f"超柴: {price_ds} 元")
    except Exception as e:
        print("[OIL-ERR]", e)
    return "油價查詢失敗"

# ========== 新聞 ==========
def news():
    r = safe_get(f"https://newsapi.org/v2/top-headlines?country=tw&apiKey={NEWS_API_KEY}")
    try:
        arts = [a["title"] for a in (r.json().get("articles",[]) if r else []) if a.get("title")] [:3]
        return "\n".join("• "+t for t in arts) if arts else "今日無新聞"
    except Exception as e:
        print("[NEWS-ERR]", e)
        return "新聞查詢失敗"

# ========== 股票 ==========
def stock(name: str) -> str:
    code = STOCK.get(name, name)
    # 台股 .TW 先用 Fugle, fallback TWSE, 最後 Yahoo
    if code.endswith(".TW") and FUGLE_API_KEY:
        sym = code.replace(".TW", "")
        try:
            url = f"https://api.fugle.tw/marketdata/v1.0/intraday/quote/{sym}?apiToken={FUGLE_API_KEY}"
            r = safe_get(url)
            dq = r.json().get("data", {}).get("quote") if r else None
            price = dq.get("tradePrice") if dq else None
            prev  = dq.get("prevClose")  if dq else None
            if price and prev:
                diff = price - prev
                pct  = diff / prev * 100 if prev else 0
                emo  = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                return f"{emo} {name}\n💰 {price:.2f}\n{diff:+.2f} ({pct:+.2f}%)"
        except Exception as e:
            print("[FUGLE-ERR]", code, e)
        # fallback TWSE openapi
        try:
            url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
            r = safe_get(url)
            data = r.json() if r else []
            for row in data:
                if row.get('證券代號') == sym.zfill(4):
                    price = float(row['收盤價'])
                    return f"➡️ {name}\n💰 {price:.2f}\n(僅收盤價)"
        except Exception as e:
            print("[TWSE-ERR]", code, e)
    # 其它用 Yahoo (如美股)
    try:
        tkr = yf.Ticker(code)
        info = getattr(tkr, "fast_info", {}) or tkr.info
        price = info.get("regularMarketPrice")
        prev  = info.get("previousClose")
        if price and prev:
            diff = price - prev
            pct  = diff / prev * 100
            emo  = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            return f"{emo} {name}\n💰 {price:.2f}\n{diff:+.2f} ({pct:+.2f}%)"
    except Exception as e:
        print("[YF-ERR]", code, e)
    return f"❌ {name} 查無股價"

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
    us_tz = pytz.timezone("US/Eastern")
    today = datetime.now(us_tz).date()
    ref = today - timedelta(days=3 if today.weekday()==0 else 1)
    idx={"道瓊":"^DJI","S&P500":"^GSPC","NASDAQ":"^IXIC"}
    focus={"NVDA":"輝達","SMCI":"美超微","GOOGL":"Google","AAPL":"蘋果"}
    def line(code,name):
        try:
            h=yf.Ticker(code).history(start=str(ref),end=str(ref+timedelta(days=1)))
            if h.empty: return ""
            o,c=h.iloc[0]['Open'],h.iloc[0]['Close']; diff,p=(c-o),(c-o)/o*100
            e="📈" if diff>0 else "📉" if diff<0 else "➡️"
            return f"{e} {name}: {c:.2f} ({diff:+.2f},{p:+.2f}%)"
        except Exception as e:
            print("[US-ERR]", code, e)
            return ""
    return ("📈 前一晚美股行情\n\n"
            + "\n".join(line(c,n) for n,c in idx.items()) + "\n"
            + "\n".join(line(c,n) for c,n in focus.items()))

# ========== 即時美股開盤行情 ==========
def us_open():
    tickers = {
        "道瓊": "^DJI",
        "S&P500": "^GSPC",
        "NASDAQ": "^IXIC",
        "NVDA": "NVDA",
        "SMCI": "SMCI",
        "GOOGL": "GOOGL",
        "AAPL": "AAPL"
    }
    lines = []
    for name, code in tickers.items():
        price = prev = None
        try:
            tkr   = yf.Ticker(code)
            info  = tkr.fast_info or tkr.info
            price = info.get("regularMarketPrice")
            prev  = info.get("previousClose")
            if price is None:
                hist = tkr.history(period="1d", interval="1m")
                if not hist.empty:
                    price = hist["Close"].iloc[-1]
                    prev  = hist["Close"].iloc[0]
        except Exception as e:
            print("[USOPEN-ERR]", code, e)
        if price and prev:
            diff = price - prev
            pct  = diff / prev * 100
            emo  = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            lines.append(f"{emo} {name}: {price:.2f} ({diff:+.2f},{pct:+.2f}%)")
    return "🇺🇸 美股開盤速報\n\n" + "\n".join(lines) if lines else "美股查詢失敗"

# ========== LINE 推播 ==========
def push(msg): line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=msg.strip()))

# ========== 排程任務 ==========
def j0710():
    now = datetime.now(tz)
    push(f"🌅 早安 {now:%Y-%m-%d (%a)}\n\n{weather('新北市新店區')}\n\n{news()}\n\n{cal()}\n\n{fx()}\n\n{us()}")

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
        push("🏸 下班打球提醒（中正區）\n\n"+traffic("公司到中正區")+"\n\n"+weather("台北市中正區")+"\n\n"+oil())
    else:               # 二四
        push("🏠 下班回家提醒（新店區）\n\n"+traffic("公司到新店區")+"\n\n"+weather("新北市新店區")+"\n\n"+oil())

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

@app.route("/test_stock")
def test_stock():
    return stock("台積電")

@app.route("/health")
def health():
    return "OK"

# ========== 主程式 ==========
if __name__ == "__main__":
    print("[TEST] 台積電 =", stock("台積電"))
    print("[TEST] NVDA  =", stock("NVDA"))
    app.run(host="0.0.0.0", port=10000)
