import os
import requests
import json
from flask import Flask, request

# 基本 Flask app
app = Flask(__name__)

# 你可以直接在這裡改你的出發地/目的地
def get_traffic_status(from_place="home", to_place="office"):
    ADDRESSES = {
        "home": "新店區建國路99巷",
        "office": "台北市南京東路三段131號",
        "post_office": "台北市愛國東路216號"
    }
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n(Google Maps API金鑰未設定)\n預估時間: 約25分鐘"
    from_addr = ADDRESSES.get(from_place, from_place)
    to_addr = ADDRESSES.get(to_place, to_place)
    try:
        url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            "origin": from_addr,
            "destination": to_addr,
            "key": api_key,
            "departure_time": "now"    # 必須有這個參數才有交通時間
        }
        res = requests.get(url, params=params, timeout=10)
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
        duration = route['duration']['value'] / 60    # 正常分鐘
        # 注意：有些地點組合沒有即時交通資料
        if 'duration_in_traffic' in route:
            duration_in_traffic = route['duration_in_traffic']['value'] / 60
        else:
            duration_in_traffic = duration
        ratio = duration_in_traffic / duration if duration > 0 else 1
        # 顯示測試用 raw 數據
        status = ""
        if ratio < 1.1:
            status = "🟢 順暢"
        elif ratio < 1.3:
            status = "🟡 稍慢"
        else:
            status = "🔴 塞車"
        # 調整為測試細節顯示
        return (f"🚗 車流資訊 {from_place} → {to_place}\n"
                f"{status}\n"
                f"預計時間: {duration_in_traffic:.1f} 分鐘\n"
                f"（正常:{duration:.1f} 分鐘, 交通比:{ratio:.2f}）\n"
                f"距離: {route['distance']['text']}\n"
                f"資料來源: Google Maps")
    except Exception as e:
        print(f"[Traffic] Exception: {str(e)}")
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n取得資料失敗\n預估時間: 約25分鐘"

# Flask 路由測試
@app.route("/traffic", methods=["GET"])
def traffic_test():
    # 你可以自由改 from_place, to_place
    return get_traffic_status("home", "office")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
