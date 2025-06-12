def get_traffic(from_place="home", to_place="office"):
    # 固定地址表請照你的原程式 ADDRESSES 設定
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
        url = f"https://maps.googleapis.com/maps/api/directions/json?origin={from_addr}&destination={to_addr}&key={api_key}"
        print(f"[Traffic] Request URL: {url}")  # 新增，幫你看實際 query
        res = requests.get(url, timeout=10)
        data = res.json()
        print(f"[Traffic] Google Maps API Response: {data}")  # 關鍵log，讓你抓問題
        if data.get('status') != 'OK':
            error_msg = data.get('error_message', '')
            return (f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n"
                    f"無法取得路線\n"
                    f"狀態: {data.get('status')}\n"
                    f"訊息: {error_msg}\n"
                    f"預估時間: 約25分鐘")
        route = data['routes'][0]['legs'][0]
        duration = route['duration']['text']
        distance = route['distance']['text']
        return (f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n"
                f"預計時間: {duration}\n"
                f"距離: {distance}\n\n"
                f"資料來源: Google Maps")
    except Exception as e:
        print(f"車流API錯誤: {str(e)}")
        return f"🚗 車流資訊\n\n{from_place} → {to_place}\n\n取得資料失敗\n預估時間: 約25分鐘"
