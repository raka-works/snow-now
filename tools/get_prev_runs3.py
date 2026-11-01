"""全国100観測点の過去の予報（4・6日前を足す）。Open-Meteo の上限（1時間5000回相当）に収まるよう1分おきに取る。"""
import json, os, time
import requests

MODELS = "jma_msm,jma_gsm,ecmwf_ifs025,gfs_seamless"
SEASONS = [("2024-12-01", "2025-03-31"), ("2025-12-01", "2026-03-31")]


def main():
    st = json.load(open("tools/cache/verify_stations.json", encoding="utf-8"))
    vars_ = [f"{v}_previous_day{d}" for d in (4, 6) for v in ("precipitation", "temperature_2m")]
    meta = {}
    for sid, v in st.items():
        lat, lon = v["lat"][0] + v["lat"][1] / 60, v["lon"][0] + v["lon"][1] / 60
        meta[sid] = (lat, lon, v["alt"], v["kjName"])
        for i, (s, e) in enumerate(SEASONS):
            path = f"data/verify2/{sid}_{i}.json"
            if not os.path.exists(path):
                continue
            old = json.load(open(path))
            if "precipitation_previous_day4_jma_gsm" in old:
                continue
            for _ in range(10):
                r = requests.get("https://previous-runs-api.open-meteo.com/v1/forecast", params=dict(
                    latitude=lat, longitude=lon, elevation=v["alt"], start_date=s, end_date=e,
                    hourly=",".join(vars_), models=MODELS, timezone="Asia/Tokyo"), timeout=180)
                if r.status_code == 429:
                    print("429 待機", r.text[:120], flush=True); time.sleep(300); continue
                r.raise_for_status(); break
            new = r.json()["hourly"]
            assert new["time"] == old["time"]
            old.update({k: x for k, x in new.items() if k != "time"})
            json.dump(old, open(path, "w"))
            print(sid, i, flush=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
