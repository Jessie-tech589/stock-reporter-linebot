import os
import base64
import json
import requests
import yfinance as yf
from datetime import datetime, date
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import time

# ========== 環境變數 ==========
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
NEWSDATA_API_KEY = os.getenv("NEWSDATA_API_KEY")
GOOGLE_CREDS_JSON_B64 = os.getenv("GOOGLE_CREDS_JSON")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
ACCUWEATHER_API_KEY = os.getenv("ACCUWEATHER_API_KEY")

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

LOCATION_COORDS = {
    "新店區": (24.972, 121.539),
    "中山區": (25.063, 121.526),
    "中正區": (25.033, 121.519),
    "大安區": (25.033, 121.543)
}

STOCK = {
    "台積電": "2330.TW", "聯電": "2303.TW", "鴻準": "2354.TW", "仁寶": "2324.TW",
    "陽明": "2609.TW", "華航": "2610.TW", "長榮航": "2618.TW",
    "大盤": "^TWII",
    "輝達": "NVDA", "美超微": "SMCI", "GOOGL": "GOOGL", "Google": "GOOGL",
    "微軟": "MSFT"
}

stock_list_tpex = [
    "台積電", "聯電", "鴻準", "仁寶", "陽明", "華航", "長榮航", "大盤"
]

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

WEATHER_ICON = {
    "Sunny": "☀️", "Clear": "🌕", "Cloudy": "☁️", "Partly cloudy": "⛅",
    "Rain": "🌧️", "Thunderstorm": "⛈️", "Fog": "🌫️", "Snow": "🌨️"
}
TRAFFIC_EMOJI = { "RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢" }

# ========== 天氣查詢 ==========
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
    url = "https://tw.rter.info/capi.php"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        result = []
        for code, flag in [("USD", "🇺🇸"), ("JPY", "🇯🇵"), ("CNY", "🇨🇳"), ("HKD", "🇭🇰")]:
            key = f"USD{code}" if code != "USD" else "USDTWD"
            rate = data.get(key, {}).get("Exrate")
            if rate:
                result.append(f"{flag} {code}: {rate}")
        return "💱 今日匯率\n" + "\n".join(result) if result else "查無匯率資料"
    except Exception as e:
        print("[FX-ERR]", e)
        return "匯率查詢失敗"

# ========== 油價 ==========
def get_taiwan_oil_price():
    url = "https://vipmbr.cpc.com.tw/mbwebs/mbwebs/ShowHistoryPrice"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", class_="tablePrice")
        if not table:
            print("[GAS-HTML]", r.text[:200])
            return "⛽️ 油價查詢失敗（找不到表格）"
        rows = table.find_all("tr")
        if len(rows) < 2:
            return "⛽️ 油價查詢失敗"
        cols = rows[1].find_all("td")
        if len(cols) < 5:
            return "⛽️ 油價查詢失敗"
        gas_92 = cols[1].text.strip()
        gas_95 = cols[2].text.strip()
        gas_98 = cols[3].text.strip()
        diesel = cols[4].text.strip()
        return (
            f"⛽️ 最新油價：\n"
            f"92無鉛: {gas_92} 元\n"
            f"95無鉛: {gas_95} 元\n"
            f"98無鉛: {gas_98} 元\n"
            f"柴油: {diesel} 元"
        )
    except Exception as e:
        print("[GAS-ERR]", e)
        return "⛽️ 油價查詢失敗"

# ========== 新聞（NewsData.io，含台灣/大陸/國際） ==========
def news():
    api_key = NEWSDATA_API_KEY or ""
    url = f"https://newsdata.io/api/1/news?apikey={api_key}&country=tw,cn,us&language=zh&category=top"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        tw_news = []
        cn_news = []
        intl_news = []
        for item in data.get("results", []):
            country = item.get("country", "")
            title = item.get("title", "")
            link = item.get("link", "")
            if country == "tw":
                tw_news.append(f"• {title}\n{link}")
            elif country == "cn":
                cn_news.append(f"• {title}\n{link}")
            else:
                intl_news.append(f"• {title}\n{link}")
        msg = []
        if tw_news:
            msg.append("【台灣】\n" + "\n".join(tw_news[:3]))
        if cn_news:
            msg.append("【大陸】\n" + "\n".join(cn_news[:3]))
        if intl_news:
            msg.append("【國際】\n" + "\n".join(intl_news[:3]))
        return "\n\n".join(msg) if msg else "今日無新聞"
    except Exception as e:
        print("[NEWSDATA-ERR]", e)
        return "新聞查詢失敗"

# ========== 股票 ==========
def stock(name: str) -> str:
    code = STOCK.get(name, name)
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
    try:
        tkr = yf.Ticker(code)
        info = getattr(tkr, "fast_info", {}) or tkr.info
        price = info.get("regularMarketPrice")
        prev = info.get("previousClose")
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
    result = []
    for name in stock_list_tpex:
        result.append(stock(name))
        time.sleep(1)  # 降低被封鎖風險
    return "\n".join(result)

# ========== 行事曆 ==========
def cal():
    if not GOOGLE_CREDS_JSON_B64: return "行事曆查詢失敗"
    try:
        info = json.loads(base64.b64decode(GOOGLE_CREDS_JSON_B64))
        creds = service_account.Credentials.from_service_account_info(info,scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc = build("calendar","v3",credentials=creds,cache_discovery=False)
        today = date.today()
        start = datetime.combine(today,datetime.min.time()).isoformat()
        end = datetime.combine(today,datetime.max.time()).isoformat()
        items = svc.events().list(calendarId=GOOGLE_CALENDAR_ID,timeMin=start,timeMax=end,singleEvents=True,orderBy="startTime",maxResults=10).execute().get("items",[])
        return "\n".join("🗓️ "+e["summary"] for e in items if e.get("summary")) or "今日無行程"
    except Exception as e:
        print("[CAL-ERR]", e)
        return "行事曆查詢失敗"

# ========== 美股前一晚行情 ==========
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
            tkr = yf.Ticker(code)
            price = tkr.fast_info.get("last_price")
            prev = tkr.fast_info.get("previous_close")
            if price is None or prev is None:
                info = tkr.info
                price = info.get("regularMarketPrice")
                prev = info.get("previousClose")
            if price and prev:
                diff = price - prev
                pct = diff / prev * 100 if prev else 0
                emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                return f"{emo} {name}: {price:.2f} ({diff:+.2f},{pct:+.2f}%)"
        except Exception as e:
            print("[YF-ERR]", code, e)
        return f"❌ {name}: 查無資料"
    idx_lines = [q_yf(c, n) for n, c in idx.items()]
    focus_lines = [q_yf(c, n) for c, n in focus.items()]
    return "📊 前一晚美股行情\n" + "\n".join(idx_lines) + "\n" + "\n".join(focus_lines)

# ========== Google Maps 路況 ==========
def traffic(label):
    if label not in ROUTE_CONFIG:
        return f"🚗 找不到路線 {label}"
    cfg = ROUTE_CONFIG[label]
    o, d = cfg['o'], cfg['d']
    waypoints = "|".join(f"via:{w}" for w in cfg['waypoints']) if cfg.get('waypoints') else ""
    o_encoded = quote_plus(o)
    d_encoded = quote_plus(d)
    waypoints_encoded = quote_plus(waypoints)
    url = (
        f"https://maps.googleapis.com/maps/api/directions/json?"
        f"origin={o_encoded}&destination={d_encoded}"
        f"{'&waypoints=' + waypoints_encoded if waypoints else ''}"
        f"&key={GOOGLE_MAPS_API_KEY}&departure_time=now&language=zh-TW"
    )
    try:
        r = requests.get(url, timeout=10)
        js = r.json()
        routes = js.get("routes", [])
        if not routes:
            return "🚗 路況查詢失敗（無有效路線）"
        legs = routes[0].get("legs", [])
        if not legs:
            return "🚗 路況查詢失敗（無有效路段）"
        duration = legs[0].get('duration_in_traffic', legs[0].get('duration', {}))
        duration_text = duration.get('text', 'N/A')
        summary = routes[0].get("summary", "")
        return f"🚗 路線: {summary}\n預估時間: {duration_text}"
    except Exception as e:
        print("[TRAFFIC-ERR]", e)
        return "🚗 路況查詢失敗"

# ========== LINE 推播 ==========
def push(message):
    print(f"[LineBot] 推播給 {LINE_USER_ID}：{message}")
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
    except Exception as e:
        print(f"[LineBot] 推播失敗：{e}")

# ========== 定時任務內容 ==========
def morning_briefing():
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
    msg = [
        "【通勤提醒/中山區】",
        weather_accu("中山區", *LOCATION_COORDS["中山區"]),
        traffic("家到公司")
    ]
    push("\n\n".join(msg))

def market_open():
    msg = ["【台股開盤】"] + [stock(name) for name in stock_list_tpex]
    push("\n\n".join(msg))

def market_mid():
    msg = ["【台股盤中快訊】"] + [stock(name) for name in stock_list_tpex]
    push("\n\n".join(msg))

def market_close():
    msg = ["【台股收盤】"] + [stock(name) for name in stock_list_tpex]
    push("\n\n".join(msg))

def evening_zhongzheng():
    msg = [
        "【下班打球提醒/中正區】",
        weather_accu("中正區", *LOCATION_COORDS["中正區"]),
        get_taiwan_oil_price(),
        traffic("公司到郵局")
    ]
    push("\n\n".join(msg))

def evening_xindian():
    msg = [
        "【回家/新店區】",
        weather_accu("新店區", *LOCATION_COORDS["新店區"]),
        get_taiwan_oil_price(),
        traffic("公司到家")
    ]
    push("\n\n".join(msg))

def us_market_open1():
    push("【美股開盤速報】\n" + us())

def us_market_open2():
    push("【美股盤後行情】\n" + us())

# ========== Flask Routes ==========
@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

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
        weekday = datetime.now().weekday()
        if weekday in [0,2,4]: # Mon Wed Fri
            evening_zhongzheng()
        else: # Tue Thu
            evening_xindian()
    elif time_str == "21:30":
        us_market_open1()
    elif time_str == "23:00":
        us_market_open2()
    else:
        return f"❌ 不支援時間 {time_str}"
    return f"✅ 模擬推播 {time_str} 完成"

@app.route("/health")
def health():
    return "OK"

# ========== LINE BOT Webhook ==========
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
    else:
        reply = "指令未支援"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
