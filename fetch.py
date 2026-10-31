"""予報・観測・1か月予報を取って data/latest.json に保存する。GitHub Actions で1日3回動かす。"""
import datetime as dt
import json
import os
import re

import requests

JST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "snow-now (personal; github.com/raka-works/snow-now)"}
DET_MODELS = ["jma_msm", "jma_gsm", "ecmwf_ifs025", "gfs_seamless"]


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=90, **kw)
    r.raise_for_status()
    return r


def elevations(r):
    return {"upper": round(r["base"] + (r["top"] - r["base"]) * 0.75), "lower": r["base"]}


def forecast(resorts):
    """決定論の予報。上部とふもとを1回でまとめて取る。"""
    lat, lon, ele = [], [], []
    for r in resorts:
        for e in elevations(r).values():
            lat.append(r["lat"]); lon.append(r["lon"]); ele.append(e)
    j = get("https://api.open-meteo.com/v1/forecast", params=dict(
        latitude=",".join(map(str, lat)), longitude=",".join(map(str, lon)), elevation=",".join(map(str, ele)),
        hourly="precipitation,temperature_2m,wind_gusts_10m", models=",".join(DET_MODELS),
        forecast_days=16, timezone="Asia/Tokyo")).json()
    out = {}
    for i, r in enumerate(resorts):
        out[r["id"]] = {"upper": j[2 * i]["hourly"], "lower": j[2 * i + 1]["hourly"]}
    return out


def ensemble(resorts):
    """ECMWF のアンサンブル（51通り）。上部だけ。"""
    j = get("https://ensemble-api.open-meteo.com/v1/ensemble", params=dict(
        latitude=",".join(str(r["lat"]) for r in resorts), longitude=",".join(str(r["lon"]) for r in resorts),
        elevation=",".join(str(elevations(r)["upper"]) for r in resorts),
        hourly="precipitation,temperature_2m", models="ecmwf_ifs025", forecast_days=15,
        timezone="Asia/Tokyo")).json()
    return {r["id"]: j[i]["hourly"] for i, r in enumerate(resorts)}


def amedas(ids):
    """アメダスの最新値（積雪・気温）。積雪は冬の期間だけ出る。"""
    t = dt.datetime.fromisoformat(get("https://www.jma.go.jp/bosai/amedas/data/latest_time.txt").text.strip())
    out = {}
    for sid in ids:
        d = {}
        for back in (0, 3):  # 3時間ごとのファイル。日をまたいだ直後に備えて1つ前も読む
            u = t - dt.timedelta(hours=back)
            try:
                d.update(get(f"https://www.jma.go.jp/bosai/amedas/data/point/{sid}/{u:%Y%m%d}_{u.hour // 3 * 3:02d}.json").json())
            except requests.HTTPError:
                pass
        if not d:
            continue
        last = d[max(d)]

        def v(k):
            x = last.get(k)
            return x[0] if x and x[1] == 0 else None
        out[sid] = {"time": max(d), "temp": v("temp"), "snow": v("snow"), "snow24h": v("snow24h"),
                    "snow6h": v("snow6h"), "prec24h": v("precipitation24h")}
    return out


def onemonth():
    """気象庁の地方1か月予報（北陸・関東甲信）。発表文をそのまま使う。"""
    feed = get("https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml").text
    out = {}
    for area, code in (("北陸", "150000"), ("関東甲信", "130000")):
        urls = sorted(set(re.findall(rf"https://[^<\"]+_VPCK50_{code}\.xml", feed)))
        if not urls:
            continue
        x = get(urls[-1]).content.decode("utf-8")
        res = {"url": urls[-1], "issued": re.search(r"<ReportDateTime>([^<]+)", x).group(1),
               "text": re.findall(r"<jmx_eb:Text[^>]*>([^<]+)</jmx_eb:Text>", x)}
        out[area] = res
    return out


def main():
    resorts = json.load(open("resorts.json", encoding="utf-8"))
    now = dt.datetime.now(JST)
    data = {"fetched": now.isoformat(timespec="minutes"), "forecast": forecast(resorts)}
    for name, fn in (("ensemble", lambda: ensemble(resorts)),
                     ("amedas", lambda: amedas(sorted({r["amedas"] for r in resorts if r["amedas"]}))),
                     ("onemonth", onemonth)):
        try:
            data[name] = fn()
        except Exception as e:  # 一部が取れなくてもページは作る
            print(f"{name} の取得に失敗: {e}")
            data[name] = None
    os.makedirs("data", exist_ok=True)
    json.dump(data, open("data/latest.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("ok", now)


if __name__ == "__main__":
    main()
