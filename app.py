import os
import base64
import json
import time
import logging
import requests
import yfinance as yf
import pytz
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
TZ = pytz.timezone('Asia/Taipei')
app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

def get_google_creds_json_b64():
    raw = os.getenv("GOOGLE_CREDS_JSON")
    if not raw:
        logging.warning("GOOGLE_CREDS_JSON 環境變數未設定。行事曆功能將無法使用。")
        return None
    try:
        decoded_bytes = base64.b64decode(raw)
        json.loads(decoded_bytes.decode("utf-8"))
        return raw
    except Exception:
        try:
            json.loads(raw)
            encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
            logging.warning("GOOGLE_CREDS_JSON 已自動轉換為 base64 格式。請考慮直接設定 base64 編碼的字串。")
            return encoded
        except Exception as e:
            logging.error(f"GOOGLE_CREDS_JSON 格式錯誤，無法解析: {e}")
            return None

GOOGLE_CREDS_JSON_B64 = get_google_creds_json_b64()

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

LOCATION_COORDS = {
    "新店區": (24.972, 121.539),
    "中山區": (25.063, 121.526),
    "中正區": (25.033, 121.519),
}

STOCK = {
    "台積電": "2330.TW", "聯電": "2303.TW", "鴻準": "2354.TW",
    "陽明": "2609.TW", "華航": "2610.TW", "長榮航": "2618.TW",
    "大盤": "^TWII", "美股大盤指數": "^IXIC", "輝達": "NVDA", "美超微": "SMCI",
}
stock_list_tpex = ["大盤", "台積電", "聯電", "鴻準", "陽明", "華航", "長榮航"]
stock_list_us = ["美股大盤指數", "輝達", "美超微"]

ROUTE_CONFIG = {
    "家到公司": dict(
        o="新北市新店區建國路99巷", d="台北市中山區南京東路三段131號",
        waypoints=[
            "新北市新店區民族路", "新北市新店區北新路", "台北市羅斯福路", "台北市基隆路",
            "台北市辛亥路", "台北市復興南路", "台北市南京東路"
        ]
    ),
    "公司到郵局": dict(
        o="台北市中山區南京東路三段131號", d="台北市中正區愛國東路216號",
        waypoints=["台北市林森北路", "台北市信義路", "台北市信義二段10巷", "台北市愛國東21巷"]
    ),
    "公司到家": dict(
        o="台北市中山區南京東路三段131號", d="新北市新店區建國路99巷",
        waypoints=[
            "台北市復興南路", "台北市辛亥路", "台北市基隆路", "台北市羅斯福路",
            "新北市新店區北新路", "新北市新店區民族路", "新北市新店區建國路"
        ]
    ),
}

def now_tw():
    return datetime.now(TZ)

def fx():
    try:
        url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        mapping = {
            "美元 (USD)": ("USD","🇺🇸"),
            "日圓 (JPY)": ("JPY","🇯🇵"),
            "人民幣 (CNY)": ("CNY","🇨🇳"),
            "港幣 (HKD)": ("HKD","🇭🇰")
        }
        result = []
        if table:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if cells and cells[0].text.strip() in mapping:
                    code, flag = mapping[cells[0].text.strip()]
                    if len(cells) > 2:
                        rate = cells[2].text.strip()
                        result.append(f"{flag} {code}: {rate}")
        if result:
            return "💱 今日匯率（現金賣出，台銀）\n" + "\n".join(result)
        else:
            logging.warning("[FX-TWBANK-PARSE-ERR] 台銀匯率解析失敗或無資料")
            return "匯率查詢失敗（台銀）"
    except Exception as e:
        logging.error(f"[FX-TWBANK-ERR] 台銀匯率查詢失敗: {e}")
    return "匯率查詢失敗"

def get_taiwan_oil_price():
    try:
        url = "https://www2.moeaea.gov.tw/oil111/"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, "lxml")
        price_table = soup.find("table", class_="tab_style_1")
        prices = {}
        if price_table:
            rows = price_table.find_all("tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) >= 3:
                    oil_type = cols[1].get_text(strip=True)
                    price = cols[2].get_text(strip=True)
                    prices[oil_type] = price
            p92 = prices.get("92無鉛汽油", "N/A")
            return f"⛽️ 最新油價（能源局）\n92無鉛汽油: {p92} 元/公升"
        else:
            logging.warning("[OIL-ENB-PARSE-ERR] 未找到油價表格或解析失敗")
            return "⛽️ 油價查詢失敗（能源局）"
    except Exception as e:
        logging.error(f"[OIL-ENB-ERR] 油價查詢失敗: {e}")
        return "⛽️ 油價查詢失敗（能源局）"

def cal():
    try:
        if not GOOGLE_CREDS_JSON_B64:
            return "行事曆查詢失敗：未設定 Google 憑證"
        info = json.loads(base64.b64decode(GOOGLE_CREDS_JSON_B64))
        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        today = now_tw().date()
        start = datetime.combine(today, datetime.min.time(), TZ).isoformat()
        end = datetime.combine(today, datetime.max.time(), TZ).isoformat()
        items = svc.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start, timeMax=end,
            singleEvents=True, orderBy="startTime", maxResults=10
        ).execute().get("items", [])
        if not items:
            return "今日無行程"
        events_str = []
        for event in items:
            event_summary = event.get("summary", "無標題事件")
            start_time = ""
            if "dateTime" in event["start"]:
                dt = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(TZ)
                start_time = dt.strftime("%H:%M") + " "
            events_str.append(f"🗓️ {start_time}{event_summary}")
        return "\n".join(events_str)
    except Exception as e:
        logging.error(f"[CAL-ERR] 行事曆查詢失敗: {e} (請檢查憑證和日曆 ID)")
        return "行事曆查詢失敗"

def traffic(route_name):
    try:
        if not GOOGLE_MAPS_API_KEY:
            return "交通資訊查詢失敗：未設定 Google Maps API 金鑰"
        route = ROUTE_CONFIG.get(route_name)
        if not route:
            return f"找不到 {route_name} 的路線配置。"
        origin = quote_plus(route["o"])
        destination = quote_plus(route["d"])
        waypoints_str = ""
        if route.get("waypoints"):
            waypoints_str = "|".join([quote_plus(wp) for wp in route["waypoints"]])
            waypoints_str = f"&waypoints={waypoints_str}"
        departure_time = int(time.time())
        url = (f"https://maps.googleapis.com/maps/api/directions/json?"
               f"origin={origin}&destination={destination}"
               f"&key={GOOGLE_MAPS_API_KEY}&mode=driving&language=zh-TW"
               f"&units=metric{waypoints_str}"
               f"&departure_time={departure_time}")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        response = response.json()
        if response["status"] == "OK" and response["routes"]:
            leg = response["routes"][0]["legs"][0]
            duration_text = leg["duration"]["text"]
            distance_text = leg["distance"]["text"]
            summary = response["routes"][0]["summary"]
            duration_in_traffic_seconds = leg.get("duration_in_traffic", {}).get("value")
            duration_seconds = leg["duration"]["value"]
            traffic_emoji = "🟢"
            if duration_in_traffic_seconds is not None and duration_seconds is not None and duration_seconds > 0:
                traffic_increase_pct = ((duration_in_traffic_seconds - duration_seconds) / duration_seconds) * 100
                if traffic_increase_pct > 30:
                    traffic_emoji = "🔴"
                elif traffic_increase_pct > 10:
                    traffic_emoji = "🟠"
            return (f"🚗 {route_name} 路況 {traffic_emoji}：\n"
                    f"摘要: {summary}\n"
                    f"距離: {distance_text}\n"
                    f"預計時間: {duration_text}")
        else:
            status = response.get("status", "未知狀態")
            error_message = response.get("error_message", "無詳細錯誤訊息")
            logging.warning(f"[TRAFFIC-ERR] 交通資訊 API 回應錯誤: Status: {status}, Message: {error_message}")
            return f"交通資訊查詢失敗 ({route_name})"
    except Exception as e:
        logging.error(f"[TRAFFIC-EXCEPTION] 交通資訊查詢發生例外: {e}")
        return f"交通資訊查詢失敗 ({route_name})"

def us_stocks_info():
    result = []
    us_stock_map = {name: STOCK[name] for name in stock_list_us}
    tickers_str = " ".join(us_stock_map.values())
    try:
        data = yf.Tickers(tickers_str)
        for name, code in us_stock_map.items():
            try:
                info = data.tickers[code].info
                price = info.get("regularMarketPrice")
                prev = info.get("previousClose")
                if price is not None and prev is not None:
                    diff = price - prev
                    pct = (diff / prev * 100) if prev != 0 else 0
                    emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                    result.append(f"{emo} {name}：{price:.2f} ({diff:+.2f}, {pct:+.2f}%)")
                else:
                    result.append(f"❌ {name}：查無價格資料")
            except Exception as e:
                logging.warning(f"❌ {name} ({code}) 美股資料查詢失敗: {e}")
                result.append(f"❌ {name}：部分資料查詢失敗")
        return "【美股資訊】\n" + "\n".join(result)
    except Exception as e:
        logging.error(f"[US-STOCK-BATCH-ERR] 美股批次查詢失敗: {e}")
        return "美股資訊批次查詢失敗。"

def tw_stocks_info():
    result = []
    tw_stock_map = {name: STOCK[name] for name in stock_list_tpex}
    tickers_str = " ".join(tw_stock_map.values())
    try:
        data = yf.Tickers(tickers_str)
        for name, code in tw_stock_map.items():
            try:
                info = data.tickers[code].info
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                prev = info.get("previousClose")
                if price is not None and prev is not None:
                    diff = price - prev
                    pct = (diff / prev * 100) if prev != 0 else 0
                    emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                    result.append(f"{emo} {name}：{price:.2f} ({diff:+.2f}, {pct:+.2f}%)")
                else:
                    result.append(f"❌ {name}：查無價格資料")
            except Exception as e:
                logging.warning(f"❌ {name} ({code}) 台股資料查詢失敗: {e}")
                result.append(f"❌ {name}：部分資料查詢失敗")
        return "【台股資訊】\n" + "\n".join(result)
    except Exception as e:
        logging.error(f"[TW-STOCK-BATCH-ERR] 台股批次查詢失敗: {e}")
        return "台股資訊批次查詢失敗。"

def push(message):
    if not LINE_USER_ID or not line_bot_api:
        logging.error("[LineBot] 推播失敗：未設定 USER_ID 或 line_bot_api")
        return
    logging.info(f"[LineBot] 推播給 {LINE_USER_ID}：{message[:50]}...")
    try:
        line_bot_api.push_message(LINE_USER_ID, TextSendMessage(text=message))
    except LineBotApiError as e:
        logging.error(f"[LineBot] 推播失敗 (Line API Error): {e.status_code}, {e.error.message}")
    except Exception as e:
        logging.error(f"[LineBot] 推播失敗 (General Error): {e}")

scheduler = BackgroundScheduler(timezone=TZ)

def keep_alive():
    logging.info(f"[Scheduler] 定時喚醒維持運作 {now_tw()}")
    try:
        requests.get(f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/health", timeout=5)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Keep-alive health check failed: {e}")

def register_jobs():
    scheduler.add_job(keep_alive, CronTrigger(minute="0,10,20,30,40,50"))
    scheduler.add_job(send_8am_update, CronTrigger(day_of_week="mon-fri", hour=8, minute=0))
    scheduler.add_job(send_930am_update, CronTrigger(day_of_week="mon-fri", hour=9, minute=30))
    scheduler.add_job(send_1345pm_update, CronTrigger(day_of_week="mon-fri", hour=13, minute=45))
    scheduler.add_job(send_18pm_update, CronTrigger(day_of_week="mon-fri", hour=18, minute=0))
    scheduler.add_job(send_23pm_update, CronTrigger(day_of_week="mon-fri", hour=23, minute=0))
    logging.info("所有排程任務已註冊。")

def send_8am_update():
    logging.info("[Push] 08:00 早上更新推播開始")
    messages = []
    messages.append(us_stocks_info())
    messages.append(fx())
    messages.append(traffic("家到公司"))
    full_message = "\n\n----------\n\n".join(messages)
    push(f"【早安資訊】\n\n{full_message}")
    logging.info("[Push] 08:00 早上更新推播完成")

def send_930am_update():
    logging.info("[Push] 09:30 台股開盤推播開始")
    messages = []
    messages.append("【台股開盤】")
    messages.append(tw_stocks_info())
    messages.append(fx())
    full_message = "\n\n".join(messages)
    push(full_message)
    logging.info("[Push] 09:30 台股開盤推播完成")

def send_1345pm_update():
    logging.info("[Push] 13:45 台股收盤推播開始")
    messages = []
    messages.append("【台股收盤】")
    messages.append(tw_stocks_info())
    messages.append(fx())
    full_message = "\n\n".join(messages)
    push(full_message)
    logging.info("[Push] 13:45 台股收盤推播完成")

def send_18pm_update():
    logging.info("[Push] 18:00 傍晚更新推播開始")
    messages = []
    messages.append(fx())
    messages.append(get_taiwan_oil_price())
    today_day = now_tw().day
    if today_day % 2 != 0:
        messages.append(traffic("公司到郵局"))
    else:
        messages.append(traffic("公司到家"))
    full_message = "\n\n----------\n\n".join(messages)
    push(f"【傍晚資訊】\n\n{full_message}")
    logging.info("[Push] 18:00 傍晚更新推播完成")

def send_23pm_update():
    logging.info("[Push] 23:00 美股盤中/收盤推播開始")
    messages = []
    messages.append("【美股盤中/收盤】")
    messages.append(us_stocks_info())
    full_message = "\n\n".join(messages)
    push(full_message)
    logging.info("[Push] 23:00 美股盤中/收盤推播完成")

register_jobs()
scheduler.start()

@app.route("/")
def home():
    return "✅ LINE Bot 正常運作中"

@app.route("/health")
def health():
    return "OK"

@app.route("/send_scheduled_test")
def send_scheduled_test():
    time_str = request.args.get("time", "").strip()
    job_map = {
        "08:00": send_8am_update,
        "09:30": send_930am_update,
        "13:45": send_1345pm_update,
        "18:00": send_18pm_update,
        "23:00": send_23pm_update,
    }
    try:
        if time_str in job_map:
            job_map[time_str]()
        else:
            return f"❌ 不支援時間 {time_str} 或該時間無對應排程任務"
    except Exception as e:
        logging.error(f"[TestTrigger] 模擬推播 {time_str} 時發生錯誤: {e}")
        return f"❌ 發送時發生錯誤: {e}"
    return f"✅ 模擬推播 {time_str} 完成"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logging.error("Webhook 簽名驗證失敗，請檢查 LINE Channel Secret。")
        abort(400)
    except Exception as e:
        logging.error(f"Webhook 處理時發生錯誤: {e}")
        abort(500)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    txt = event.message.text.strip()
    reply = ""
    if txt == "油價":
        reply = get_taiwan_oil_price()
    elif txt == "匯率":
        reply = fx()
    elif txt == "美股":
        reply = us_stocks_info()
    elif txt == "行事曆":
        reply = cal()
    elif txt.startswith("股票"):
        parts = txt.split(" ", 1)
        if len(parts) > 1:
            stock_name = parts[1]
            try:
                code = STOCK.get(stock_name) or stock_name.upper()
                if not code:
                    reply = f"❌ 找不到股票: {stock_name}"
                else:
                    tkr = yf.Ticker(code)
                    info = tkr.info
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                    prev = info.get("previousClose")
                    if price is not None and prev is not None:
                        diff = price - prev
                        pct = (diff / prev * 100) if prev != 0 else 0
                        emo = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                        reply = f"{emo} {stock_name}（yfinance）\n💰 {price:.2f}（{diff:+.2f}, {pct:+.2f}%)"
                    else:
                        reply = f"❌ {stock_name}（yfinance） 查無資料"
            except Exception as e:
                logging.warning(f"[STOCK-YF-MANUAL-ERR] {stock_name} {e}")
                reply = f"❌ {stock_name}（yfinance） 查詢失敗"
        else:
            reply = "請輸入股票名稱或代碼，例如：股票 台積電"
    elif txt == "台股":
        reply = tw_stocks_info()
    elif txt.startswith("路況"):
        parts = txt.split(" ", 1)
        if len(parts) > 1:
            route_name = parts[1]
            reply = traffic(route_name)
        else:
            reply = "請輸入路線名稱，例如：路況 家到公司"
    if not reply:
        reply = (
            "您好！我可以提供以下資訊：\n"
            "油價 / 匯率 / 美股 / 行事曆 / 台股\n"
            "路況 [路線名稱]\n"
            "股票 [股票名稱或代碼]"
        )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
# ***不要加 if __name__=="__main__": ...***   <<--- 這一行留空，結尾只保留 Flask app 宣告！

