"""過去の予報（Open-Meteo Previous Runs API）を観測点の位置・標高で取る。検証用。"""
import json, time
import requests

STATIONS = {  # 番号: (緯度, 経度, 標高)  ※アメダス地点表の値
}
MODELS = "jma_msm,jma_gsm,ecmwf_ifs025,gfs_seamless,icon_seamless"
LEADS = [1, 2, 3, 5, 7]
SEASONS = [("2024-12-01", "2025-03-31"), ("2025-12-01", "2026-03-31")]


def load_stations():
    t = requests.get("https://www.jma.go.jp/bosai/amedas/const/amedastable.json", timeout=30).json()
    ids = ["48141", "48031", "48061", "48066", "48191", "48216", "54841", "54816", "54836", "54676", "54616", "54661"]
    return {i: (t[i]["lat"][0] + t[i]["lat"][1] / 60, t[i]["lon"][0] + t[i]["lon"][1] / 60, t[i]["alt"]) for i in ids}


def main():
    st = load_stations()
    json.dump(st, open("data/verify/stations.json", "w"))
    vars_ = []
    for d in LEADS:
        vars_ += [f"precipitation_previous_day{d}", f"temperature_2m_previous_day{d}"]
    for sid, (lat, lon, alt) in st.items():
        for i, (s, e) in enumerate(SEASONS):
            r = requests.get("https://previous-runs-api.open-meteo.com/v1/forecast", params=dict(
                latitude=lat, longitude=lon, elevation=alt, start_date=s, end_date=e,
                hourly=",".join(vars_), models=MODELS, timezone="Asia/Tokyo"), timeout=120)
            r.raise_for_status()
            json.dump(r.json()["hourly"], open(f"data/verify/{sid}_{i}.json", "w"))
            print(sid, i, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
