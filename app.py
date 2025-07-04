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
from linebot.exceptions import InvalidSignatureError
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

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
ACCUWEATHER_API_KEY = os.getenv("ACCUWEATHER_API_KEY")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
GOOGLE_CREDS_JSON_B64 = os.getenv("GOOGLE_CREDS_JSON")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

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
    "輝達": "NVDA", "美超微": "SMCI", "GOOGL": "GOOGL", "Google": "GOOGL",
    "蘋果": "AAPL"
}
stock_list_tpex = ["台積電", "聯電", "鴻準", "仁寶", "陽明", "華航", "長榮航", "大盤"]

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
            "台北市林森北路",
            "台北市信義路",
            "台北市信義二段10巷",
            "台北市愛國東21巷"
        ]
    ),
    "公司到家": dict(
        o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
        waypoints=[
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
    "Rain": "🌧️", "Thunderstorm": "⛈️", "Fog": "🌫️", "Snow": "🌨️",
}

def now_tw():
    return datetime.now(TZ)

# 天氣（AccuWeather → OWM 備援）
def weather(city, lat, lon):
    try:
        url_loc = f"https://dataservice.accuweather.com/locations/v1/cities/geoposition/search?apikey={ACCUWEATHER_API_KEY}&q={lat},{lon}&language=zh-tw"
        loc_res = requests.get(url_loc, timeout=8)
        key = loc_res.json()["Key"]
        loc_name = loc_res.json()["LocalizedName"]
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
        icon = "🌤️"
        return f"{icon} {cityname}（{city}）\n{desc}，溫度 {temp}°C，體感 {feels}°C\n來源: OWM"
    except Exception as e:
        logging.warning(f"[WX-OWM-ERR] {e}")
    return f"天氣查詢失敗（{city}）"

# 匯率（台銀 → AlphaVantage 備援）
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
    except Exception as e:
        logging.warning(f"[FX-TWBANK-ERR] {e}")
    try:
        url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=TWD&apikey={ALPHA_VANTAGE_API_KEY}"
        js = requests.get(url, timeout=8).json()
        rate = js["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
        return f"💱 USD/TWD: {rate}\n來源: AlphaVantage"
    except Exception as e:
        logging.warning(f"[FX-AV-ERR] {e}")
    return "匯率查詢失敗"

# 油價（中油 → 能源局 備援）
def get_taiwan_oil_price():
    try:
        url = "https://www2.moeaea.gov.tw/oil111/"
        r = requests.get(url, timeout=10)
        r.encoding = 'utf-8'  # 明確設定編碼
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text()

        def find_price(label):
            m = re.search(rf"{label}.*?([\d.]+)\s*元", text)
            return m.group(1) if m else "N/A"

        p92 = find_price("92 無鉛汽油")
        p95 = find_price("95 無鉛汽油")
        p98 = find_price("98 無鉛汽油")
        pd = find_price("超級柴油")

        return (f"⛽️ 最新油價（能源局）\n"
                f"92: {p92} 元/公升\n"
                f"95: {p95} 元/公升\n"
                f"98: {p98} 元/公升\n"
                f"柴油: {pd} 元/公升")

    except Exception as e:
        logging.warning(f"[OIL-ENB-TEXT-ERR] {e}")
        return "⛽️ 油價查詢失敗（能源局）"



# 行事曆（Google）
def cal():
    try:
        if not GOOGLE_CREDS_JSON_B64:
            return "行事曆查詢失敗"
        info=json.loads(base64.b64decode(GOOGLE_CREDS_JSON_B64))
        creds=service_account.Credentials.from_service_account_info(info,scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc=build("calendar","v3",credentials=creds,cache_discovery=False)
        today = now_tw().date()
        start=datetime.combine(today,datetime.min.time(),TZ).isoformat()
        end =datetime.combine(today,datetime.max.time(),TZ).isoformat()
        items=svc.events().list(calendarId=GOOGLE_CALENDAR_ID,timeMin=start,timeMax=end,singleEvents=True,orderBy="startTime",maxResults=10).execute().get("items",[])
        return "\n".join("🗓️ "+e["summary"] for e in items if e.get("summary")) or "今日無行程"
    except Exception as e:
        logging.warning(f"[CAL-ERR] {e}")
        return "行事曆查詢失敗"

# 台股（證交所API→yfinance備援+sleep）
def stock(name: str) -> str:
    code = STOCK.get(name, name)
    # 主：TWSE API
    if code.endswith(".TW"):
        sym = code.replace(".TW", "").zfill(4)
        try:
            url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
            r = requests.get(url, timeout=8)
            data = r.json()
            for row in data:
                if row.get('證券代號') == sym:
                    price = row.get('收盤價')
                    if price and price != '--':
                        return f"📈 {name}（台股，TWSE）\n💰 {price}（收盤價）"
            return f"❌ {name}（台股，TWSE） 查無今日收盤價"
        except Exception as e:
            logging.warning(f"[STOCK-TWSE-ERR] {name} {e}")
    # 備援 yfinance
    try:
        time.sleep(2)
        tkr = yf.Ticker(code)
        price = tkr.info.get("regularMarketPrice")
        prev = tkr.info.get("previousClose")
        if price and prev:
            diff = price - prev
            pct = diff / prev * 100 if prev else 0
            emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
            return f"{emo} {name}（台股 yfinance）\n💰 {price:.2f}（{diff:+.2f}, {pct:+.2f}%)"
        else:
            return f"❌ {name}（台股 yfinance） 查無資料"
    except Exception as e:
        if "429" in str(e):
            return f"❌ {name}（台股 yfinance）: 來源被限制流量，請稍後再查"
        return f"❌ {name}（台股 yfinance） 查詢失敗"
    return f"❌ {name}（台股） 查詢失敗"

def stock_all():
    result = []
    for name in stock_list_tpex:
        result.append(stock(name))
        time.sleep(2)
    return "\n".join(result)

# 美股（yfinance）
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
            time.sleep(2)
            tkr = yf.Ticker(code)
            price = tkr.info.get("regularMarketPrice")
            prev = tkr.info.get("previousClose")
            if price and prev:
                diff = price - prev
                pct = diff / prev * 100 if prev else 0
                emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                return f"{emo} {name}: {price:.2f} ({diff:+.2f},{pct:+.2f}%)"
            else:
                return f"❌ {name}: 查無資料"
        except Exception as e:
            if "429" in str(e):
                return f"❌ {name}: 來源流量過大，暫時無法查詢"
            else:
                return f"❌ {name}: 查詢失敗"
    idx_lines = [q_yf(c, n) for n, c in idx.items()]
    focus_lines = [q_yf(c, n) for c, n in focus.items()]
    return "🇺🇸 前一晚美股行情\n" + "\n".join(idx_lines + focus_lines)

# 路況
def traffic(label):
    if label not in ROUTE_CONFIG:
        return f"🚗 找不到路線 {label}"
    cfg = ROUTE_CONFIG[label]
    o, d = cfg['o'], cfg['d']
    waypoints = cfg.get('waypoints', [])
    o_encoded = quote_plus(o)
    d_encoded = quote_plus(d)
    waypoints_encoded = "optimize:false|" + "|".join(quote_plus(w) for w in waypoints) if waypoints else ""
    url = (
        f"https://maps.googleapis.com/maps/api/directions/json?"
        f"origin={o_encoded}&destination={d_encoded}"
        f"{'&waypoints=' + waypoints_encoded if waypoints_encoded else ''}"
        f"&key={GOOGLE_MAPS_API_KEY}&departure_time=now&language=zh-TW"
    )
    try:
        r = requests.get(url, timeout=8)
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
        return f"🚗 路線: {summary}\n預估時間: {duration_text}\n來源: Google Maps"
    except Exception as e:
        logging.warning(f"[TRAFFIC-ERR] {e}")
    return "🚗 路況查詢失敗"


# LINE推播
def push(message):
    logging.info(f"[LineBot] 推播給 {LINE_USER_ID}：{message[:50]}...")
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
    except Exception as e:
        logging.error(f"[LineBot] 推播失敗：{e}")

# ========== 定時推播任務 ==========
def morning_briefing():
    logging.info("[Push] 07:10 Morning briefing 推播開始")

    try:
        weather = get_weather("中山區")  # 公司所在地
        news = get_news()
        calendar = get_today_events()
        fx = get_exchange_rate()
        us_market_summary = get_us_market_summary()  # 前一晚美股行情（含大盤＋個股）
        us_open_briefing = get_us_opening_summary()  # 今晨開盤摘要

        messages = [
            f"【早安天氣】\n{weather}",
            f"【今日新聞】\n{news}",
            f"【行事曆提醒】\n{calendar}",
            f"【匯率快訊】\n{fx}",
            f"【昨晚美股行情】\n{us_market_summary}",
            f"【美股今晨開盤】\n{us_open_briefing}"
        ]

        for msg in messages:
            try:
                line_bot_api.push_message(USER_ID, TextSendMessage(text=msg))
                time.sleep(5)  # 每則間隔 5 秒，避免觸發 LINE API 速率限制
            except LineBotApiError as e:
                logging.error(f"[LinePushError] {e}")

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
    scheduler.add_job(evening_zhongzheng, CronTrigger(day_of_week="mon,wed,fri", hour=18, minute=00))
    scheduler.add_job(evening_xindian, CronTrigger(day_of_week="tue,thu", hour=18, minute=00))
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
    try:
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
            now_wd = now_tw().weekday()
            if now_wd in [0,2,4]:
                evening_zhongzheng()
            else:
                evening_xindian()
        elif time_str == "21:30":
            us_market_open1()
        elif time_str == "23:00":
            us_market_open2()
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
    if txt == "天氣":
        reply = weather("新店區", *LOCATION_COORDS["新店區"])
    elif txt == "油價":
        reply = get_taiwan_oil_price()
    elif txt == "匯率":
        reply = fx()
    elif txt == "美股":
        reply = us()
    else:
        reply = "指令未支援"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@app.route("/send_traffic_test")
def send_traffic_test():
    try:
        msg = traffic("家到公司")
        push(f"🚗 測試路況：\n{msg}")
        return "✅ 測試路況訊息已送出"
    except Exception as e:
        logging.error(f"[TrafficTest] {e}")
        return f"❌ 發送失敗: {e}"




if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

