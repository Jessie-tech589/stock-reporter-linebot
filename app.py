import os
import requests
import json
from flask import Flask, request

# 基本 Flask app
app = Flask(__name__)

# 你可以直接在這裡改你的出發地/目的地
ADDRESSES = {
    "home": "新店區建國路99巷",
    "office": "台北市南京東路三段131號",
    "post_office": "台北市愛國東路216號"
}

def get_traffic(from_place="home", to_place="office"):
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        print("[Traffic] Google Maps API 金鑰未設定")
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n(Google Maps API金鑰未設定)\n預估時間: 約25分鐘"
    from_addr = ADDRESSES.get(from_place, from_place)
    to_addr = ADDRESSES.get(to_place, to_place)
    try:
        url = f"https://maps.googleapis.com/maps/api/directions/json?origin={from_addr}&destination={to_addr}&key={api_key}"
        print(f"[Traffic] Request URL: {url}")
        res = requests.get(url, timeout=10)
        data = res.json()
        print(f"[Traffic] Google Maps API Response: {json.dumps(data, ensure_ascii=False)}")
        if data.get('status') != 'OK':
            error_msg = data.get('error_message', '')
            return (f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n"
                    f"❌ 無法取得路線\n"
                    f"【Google Maps Status】{data.get('status')}\n"
                    f"【訊息】{error_msg or '無'}\n"
                    f"預估時間: 約25分鐘")
        route = data['routes'][0]['legs'][0]
        duration = route['duration']['text']
        distance = route['distance']['text']
        return (f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n"
                f"預計時間: {duration}\n"
                f"距離: {distance}\n\n"
                f"資料來源: Google Maps")
    except Exception as e:
        print(f"[Traffic] Exception: {str(e)}")
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n取得資料失敗\n預估時間: 約25分鐘"

# 測試網址 http://localhost:5000/traffic
@app.route("/traffic", methods=["GET"])
def traffic_test():
    # 可修改 from_place, to_place 來測試
    result = get_traffic("home", "office")
    return result

@app.route("/")
def index():
    return "Flask App 正常運行中！"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
