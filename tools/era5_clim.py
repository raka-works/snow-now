"""過去10シーズンの、ゲレンデ上部での11/1からの累計降雪（ERA5、アメダス実測で量を補正）。初回だけ使う。
出力: data/clim_upper.json {resort_id: {"scale": 倍率, "seasons": {"2016": [11/1からの日ごとの累計cm, ...]}}}"""
import csv, json, time
import datetime as dt
import requests

P = json.load(open("data/params.json", encoding="utf-8"))
FIRST, LAST = 2016, 2025


def snow(p, t):
    if p is None or t is None or p <= 0:
        return 0.0
    f = 1.0 if t <= P["t0"] else 0.0 if t >= P["t1"] else (P["t1"] - t) / (P["t1"] - P["t0"])
    return p * f * P["k"]


def era5(lat, lon, ele):
    j = requests.get("https://archive-api.open-meteo.com/v1/archive", params=dict(
        latitude=lat, longitude=lon, elevation=ele, start_date=f"{FIRST}-11-01", end_date=f"{LAST + 1}-04-30",
        hourly="precipitation,temperature_2m", models="era5", timezone="Asia/Tokyo"), timeout=180).json()["hourly"]
    daily = {}
    for ts, p, t in zip(j["time"], j["precipitation"], j["temperature_2m"]):
        daily[ts[:10]] = daily.get(ts[:10], 0.0) + snow(p, t)
    return daily


def station_scale(sid, st):
    lat, lon, alt = st[sid]
    fc = era5(lat, lon, alt)
    ob = 0.0
    a = 0.0
    for r in csv.DictReader(open(f"data/obs/{sid}.csv", encoding="utf-8")):
        if r["snowfall"] and r["date"] in fc:
            ob += float(r["snowfall"]); a += fc[r["date"]]
    return ob / a


def main():
    st = json.load(open("data/verify/stations.json"))
    resorts = json.load(open("resorts.json", encoding="utf-8"))
    scales = {}
    for sid in sorted({r["amedas"] for r in resorts if r["amedas"]}):
        scales[sid] = round(station_scale(sid, st), 3)
        print(sid, scales[sid], flush=True)
        time.sleep(1)
    fallback = round((scales["48031"] + scales["48216"]) / 2, 3)  # 焼額山：近い野沢温泉と菅平の平均
    out = {}
    for r in resorts:
        ele = round(r["base"] + (r["top"] - r["base"]) * 0.75)
        sc = scales.get(r["amedas"], fallback)
        daily = era5(r["lat"], r["lon"], ele)
        seasons = {}
        for y in range(FIRST, LAST + 1):
            d, cum, arr = dt.date(y, 11, 1), 0.0, []
            while d <= dt.date(y + 1, 4, 30):
                cum += daily.get(d.isoformat(), 0.0) * sc
                arr.append(round(cum))
                d += dt.timedelta(days=1)
            seasons[str(y)] = arr
        out[r["id"]] = {"scale": sc, "elevation": ele, "seasons": seasons}
        print(r["id"], sc, flush=True)
        time.sleep(1)
    json.dump(out, open("data/clim_upper.json", "w", encoding="utf-8"), separators=(",", ":"))


if __name__ == "__main__":
    main()
