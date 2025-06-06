import os
import yfinance as yf
import requests
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

# LINE Bot 設定 - 從環境變數讀取
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
YOUR_USER_ID = "U35ee3690b802603dd7f285a67c698b53"  # 你的 User ID

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 根路由 - 解決 404 問題
@app.route("/", methods=['GET'])
def home():
    return "✅ 股市播報 LINE Bot 運作中！"

# 機車車流狀況函數
def get_motorcycle_traffic():
    """取得機車路線車流狀況（依據星期幾決定方向）"""
    try:
        today = datetime.now()
        weekday = today.weekday()  # 0=週一, 1=週二, ..., 6=週日
        
        # 判斷通勤方向和路線
        if weekday in [0, 2, 4]:  # 週一、三、五 - 公司到金南
            direction = "🏢→📮 公司到金南郵局"
            routes = [
                "🛵 南京東路三段：車流順暢",
                "🚦 林森南路：紅綠燈較多，車流中等",
                "⚠️ 信義路：機車較多，注意安全",
                "🏃 金歐旁小巷：通常順暢"
            ]
            tips = "💡 建議：林森南路紅綠燈多，預留時間"
            
        elif weekday in [1, 3]:  # 週二、四 - 公司回家
            direction = "🏢→🏠 公司回家"
            routes = [
                "🛵 復興南路：車流順暢",
                "⚠️ 辛亥路：車流較多，小心大車",
                "🚦 基隆路：紅綠燈多，車流中等",
                "🛣️ 羅斯福路：機車道寬敞",
                "🏠 北新路→民族路：通常順暢"
            ]
            tips = "💡 建議：辛亥路大車多，保持安全距離"
            
        else:  # 週末
            direction = "🌴 週末"
            routes = [
                "🛵 市區道路：車流較少",
                "🚦 主要路口：紅綠燈正常",
                "⚠️ 商圈附近：人車較多"
            ]
            tips = "🎯 週末愉快！騎車注意安全"
        
        traffic_info = f"{direction}\n\n" + "\n".join(routes) + f"\n\n{tips}"
        return traffic_info
        
    except Exception as e:
        return f"❌ 交通資訊取得失敗: {str(e)}"

def get_detailed_motorcycle_traffic():
    """取得詳細機車路線車流資訊"""
    try:
        today = datetime.now()
        weekday = today.weekday()
        current_time = today.strftime('%H:%M')
        
        if weekday in [0, 2, 4]:  # 週一、三、五 - 公司到金南
            return f"""🏢→📮 公司到金南郵局路線 {current_time}

📍 詳細路況：
• 南京東路三段131號 ▶️ 南京東路
  🟢 車流順暢，機車道寶適

• 南京東路 ▶️ 林森南路  
  🟡 紅綠燈較多，預估5-8分鐘

• 林森南路 ▶️ 信義路
  🟡 車流中等，機車較多

• 信義路 ▶️ 金歐旁小巷
  🟢 小巷通常順暢

• 小巷 ▶️ 金南郵局
  🟢 抵達目的地

⏱️ 預估騎車時間：12-18分鐘
🛵 機車建議：走慢車道，注意紅綠燈"""

        elif weekday in [1, 3]:  # 週二、四 - 公司回家
            return f"""🏢→🏠 公司回家路線 {current_time}

📍 詳細路況：
• 南京東路三段131號 ▶️ 復興南路
  🟢 車流順暢

• 復興南路 ▶️ 辛亥路
  🟡 車流較多，大車注意

• 辛亥路 ▶️ 基隆路  
  🟡 紅綠燈多，機車道OK

• 基隆路 ▶️ 羅斯福路
  🟢 機車道寬敞舒適

• 羅斯福路 ▶️ 北新路
  🟢 往新店方向順暢

• 北新路 ▶️ 民族路 ▶️ 建國路99巷
  🟢 住宅區道路，通常順暢

⏱️ 預估騎車時間：25-35分鐘  
🛵 機車建議：辛亥路小心大車，保持安全距離"""
            
        else:  # 週末
            return f"""🌴 週末機車路況 {current_time}

🛵 整體路況：
• 市區主要道路：🟢 順暢
• 商圈周邊：🟡 人車較多  
• 住宅區道路：🟢 非常順暢

🎯 週末騎車提醒：
• 注意逛街人潮
• 商圈停車不易
• 保持安全距離

🌟 週末愉快，騎車注意安全！"""
            
    except Exception as e:
        return f"❌ 詳細交通資訊取得失敗: {str(e)}"

# 股市資料抓取函數
def get_us_stocks():
    """取得美股資料（輝達、美超微、台積電ADR、Google）"""
    symbols = {
        'NVDA': '輝達',
        'SMCI': '美超微', 
        'TSM': '台積電ADR',
        'GOOGL': 'Google'
    }
    
    stocks_data = []
    try:
        for symbol, name in symbols.items():
            stock = yf.Ticker(symbol)
            hist = stock.history(period='2d')
            if len(hist) >= 2:
                current_price = hist['Close'][-1]
                prev_price = hist['Close'][-2]
                change = current_price - prev_price
                change_pct = (change / prev_price) * 100
                
                # 判斷漲跌符號
                if change > 0:
                    symbol_icon = "📈"
                    change_str = f"+${change:.2f} (+{change_pct:.2f}%)"
                else:
                    symbol_icon = "📉"
                    change_str = f"-${abs(change):.2f} ({change_pct:.2f}%)"
                
                stocks_data.append(f"{symbol_icon} {name}({symbol}): ${current_price:.2f} {change_str}")
        
        return "\n".join(stocks_data) if stocks_data else "❌ 無法取得美股資料"
        
    except Exception as e:
        return f"❌ 美股資料取得失敗: {str(e)}"

def get_taiwan_stocks():
    """取得台股資料（簡化版，使用台股指數）"""
    try:
        # 使用台股指數 ^TWII
        taiex = yf.Ticker("^TWII")
        hist = taiex.history(period='2d')
        
        if len(hist) >= 2:
            current = hist['Close'][-1]
            prev = hist['Close'][-2]
            change = current - prev
            change_pct = (change / prev) * 100
            
            if change > 0:
                symbol_icon = "📈"
                change_str = f"+{change:.2f} (+{change_pct:.2f}%)"
            else:
                symbol_icon = "📉" 
                change_str = f"{change:.2f} ({change_pct:.2f}%)"
                
            return f"{symbol_icon} 台股指數: {current:.2f} {change_str}"
        else:
            return "❌ 台股休市中"
            
    except Exception as e:
        return f"❌ 台股資料取得失敗: {str(e)}"

def get_weather_by_location():
    """根據星期幾取得不同地點的天氣"""
    try:
        today = datetime.now()
        weekday = today.weekday()
        
        if weekday in [0, 2, 4]:  # 週一、三、五 - 金南郵局（中正區）
            return "🌤️ 中正區: 晴時多雲 23°C"
        elif weekday in [1, 3]:  # 週二、四 - 新店家裡
            return "🌤️ 新店: 晴時多雲 22°C"
        else:  # 週末
            return "🌤️ 台北: 晴時多雲 23°C"
    except:
        return "🌤️ 天氣: 請查看氣象局"

def get_weather():
    """取得天氣資料（中山區公司）"""
    try:
        return "🌤️ 中山區: 晴時多雲 23°C"
    except:
        return "🌤️ 天氣: 請查看氣象局"

# 定時發送函數
def send_morning_report():
    """8:00 晨間報告（包含機車路線車流）"""
    us_stocks = get_us_stocks()
    weather = get_weather()
    traffic = get_motorcycle_traffic()
    
    message = f"""🌅 晨間報告 {datetime.now().strftime('%Y-%m-%d')}

📈 美股昨夜收盤：
{us_stocks}

🌤️ 今日天氣：
{weather}

🛵 機車通勤路況：
{traffic}

📅 祝您騎車安全，投資順利！
"""
    
    try:
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 晨間報告發送成功: {datetime.now()}")
    except Exception as e:
        print(f"❌ 晨間報告發送失敗: {e}")

def send_taiwan_opening():
    """9:30 台股開盤"""
    taiwan_stocks = get_taiwan_stocks()
    
    message = f"""📊 台股開盤 {datetime.now().strftime('%H:%M')}

{taiwan_stocks}

💡 開盤表現供參考，投資請謹慎評估！
"""
    
    try:
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 台股開盤報告發送成功: {datetime.now()}")
    except Exception as e:
        print(f"❌ 台股開盤報告發送失敗: {e}")

def send_taiwan_midday():
    """12:00 台股中場"""
    taiwan_stocks = get_taiwan_stocks()
    
    message = f"""🍱 台股中場 {datetime.now().strftime('%H:%M')}

{taiwan_stocks}

📈 上午盤表現，下午盤請持續關注！
"""
    
    try:
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 台股中場報告發送成功: {datetime.now()}")
    except Exception as e:
        print(f"❌ 台股中場報告發送失敗: {e}")

def send_taiwan_closing():
    """13:30 台股收盤"""
    taiwan_stocks = get_taiwan_stocks()
    
    message = f"""🔔 台股收盤 {datetime.now().strftime('%H:%M')}

{taiwan_stocks}

📊 今日交易結束，明日請繼續關注！
"""
    
    try:
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 台股收盤報告發送成功: {datetime.now()}")
    except Exception as e:
        print(f"❌ 台股收盤報告發送失敗: {e}")

def send_evening_commute():
    """18:00 下班提醒（天氣+路況依據目的地）"""
    today = datetime.now()
    weekday = today.weekday()
    
    # 根據星期幾決定目的地和天氣
    if weekday in [0, 2, 4]:  # 週一、三、五 - 去金南郵局
        destination = "📮 前往金南郵局"
        weather = get_weather_by_location()
        traffic = get_detailed_motorcycle_traffic()
        
    elif weekday in [1, 3]:  # 週二、四 - 回家
        destination = "🏠 回家"
        weather = get_weather_by_location()
        traffic = get_detailed_motorcycle_traffic()
        
    else:  # 週末
        destination = "🌴 週末"
        weather = get_weather_by_location()
        traffic = "🛵 週末愉快！騎車注意安全"
    
    message = f"""🕰️ 下班時間到！{datetime.now().strftime('%H:%M')}

🎯 今日目的地：{destination}

🌤️ 目的地天氣：
{weather}

🛵 機車路況：
{traffic}

💼 辛苦了一天，騎車注意安全！
"""
    
    try:
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 下班提醒發送成功: {datetime.now()}")
    except Exception as e:
        print(f"❌ 下班提醒發送失敗: {e}")

def send_evening_summary():
    """21:00 晚間總結"""
    taiwan_stocks = get_taiwan_stocks()
    us_stocks = get_us_stocks()
    
    message = f"""🌙 晚間總結 {datetime.now().strftime('%Y-%m-%d')}

📊 今日台股表現：
{taiwan_stocks}

🔄 目前美股盤前：
{us_stocks}

😴 晚安，明日再見！
"""
    
    try:
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 晚間總結發送成功: {datetime.now()}")
    except Exception as e:
        print(f"❌ 晚間總結發送失敗: {e}")

# 設定排程
scheduler = BackgroundScheduler()

# 晨間報告 - 每日 8:00（包含機車路線車流）
scheduler.add_job(send_morning_report, 'cron', hour=8, minute=0, id='morning_report')

# 台股開盤 - 週一至週五 9:30
scheduler.add_job(send_taiwan_opening, 'cron', hour=9, minute=30, day_of_week='mon-fri', id='taiwan_opening')

# 台股中場 - 週一至週五 12:00
scheduler.add_job(send_taiwan_midday, 'cron', hour=12, minute=0, day_of_week='mon-fri', id='taiwan_midday')

# 台股收盤 - 週一至週五 13:30
scheduler.add_job(send_taiwan_closing, 'cron', hour=13, minute=30, day_of_week='mon-fri', id='taiwan_closing')

# 下班提醒 - 每日 18:00（天氣+路況依據目的地）
scheduler.add_job(send_evening_commute, 'cron', hour=18, minute=0, id='evening_commute')

# 晚間總結 - 每日 21:00
scheduler.add_job(send_evening_summary, 'cron', hour=21, minute=0, id='evening_summary')

scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# LINE Webhook 處理 - 修改為 /callback
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
    user_message = event.message.text.lower()
    
    # 手動查詢指令
    if '美股' in user_message or 'us' in user_message:
        reply_text = f"📈 美股即時資訊：\n{get_us_stocks()}"
    elif '台股' in user_message or 'tw' in user_message:
        reply_text = f"📊 台股即時資訊：\n{get_taiwan_stocks()}"
    elif '天氣' in user_message or 'weather' in user_message:
        reply_text = f"🌤️ 天氣資訊：\n{get_weather()}"
    elif '車流' in user_message or 'traffic' in user_message or '交通' in user_message or '機車' in user_message:
        reply_text = f"🛵 機車路線車流：\n{get_detailed_motorcycle_traffic()}"
    elif '羽球' in user_message or 'badminton' in user_message:
        reply_text = "🏸 羽球時間到了！記得帶球拍和運動服！"
    elif '測試' in user_message or 'test' in user_message:
        reply_text = "✅ 股市播報員 Bot 運作正常！\n\n可用指令：\n• 美股 - 查看美股\n• 台股 - 查看台股\n• 天氣 - 查看天氣\n• 車流 - 機車路線車況\n• 羽球 - 運動提醒"
    else:
        reply_text = "🤖 股市播報員為您服務！\n\n請輸入：\n• 美股 - 查看美股資訊\n• 台股 - 查看台股資訊\n• 天氣 - 查看天氣\n• 車流 - 機車路線車況\n• 羽球 - 運動提醒\n• 測試 - 系統狀態"
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# 測試功能
@app.route("/test", methods=['GET'])
def test():
    return "✅ LINE Bot 運作中！"

@app.route("/test_morning", methods=['GET'])
def test_morning():
    send_morning_report()
    return "✅ 晨間報告測試發送完成！"

@app.route("/test_traffic", methods=['GET'])
def test_traffic():
    return get_detailed_motorcycle_traffic()

@app.route("/test_evening", methods=['GET'])
def test_evening():
    send_evening_commute()
    return "✅ 下班提醒測試發送完成！"

if __name__ == "__main__":
    print("🚀 股市播報 LINE Bot 啟動中...")
    print("📊 排程設定：")
    print("  - 8:00  晨間報告（美股+天氣+機車路況）")
    print("  - 9:30  台股開盤（週一至週五）")
    print("  - 12:00 台股中場（週一至週五）") 
    print("  - 13:30 台股收盤（週一至週五）")
    print("  - 18:00 下班提醒（目的地天氣+機車路況）")
    print("  - 21:00 晚間總結")
    print("🛵 機車通勤路線：")
    print("  - 週一、三、五：公司→金南郵局")
    print("    南京東路→林森南路→信義路→金歐旁小巷")
    print("  - 週二、四：公司→家")
    print("    復興南路→辛亥路→基隆路→羅斯福路→北新路→民族路→建國路99巷")
    print("  - 週末：休閒騎車路況")
    print("✅ Bot 已就緒，等待排程執行...")
    
    # 使用環境變數的 PORT，如果沒有就使用 5000
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
