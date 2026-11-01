"""予報・観測・1か月予報を取って data/latest.json に保存する。GitHub Actions で1日3回動かす。"""
import datetime as dt
import json
import os
import re
import sys
import time
from statistics import median

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


def chunked(url, locs, params, size, pause):
    """地点をまとめて取る。Open-Meteo の無料枠（1分600回・1時間5000回相当）に収まるよう分けて、間を空ける。"""
    out = []
    for i in range(0, len(locs), size):
        part = locs[i:i + size]
        q = dict(params, latitude=",".join(str(x[0]) for x in part), longitude=",".join(str(x[1]) for x in part),
                 elevation=",".join(str(x[2]) for x in part))
        for _ in range(12):
            try:
                r = requests.get(url, params=q, headers=UA, timeout=240)
            except (requests.Timeout, requests.ConnectionError) as e:  # Actions からは時間切れになることがある
                print("時間切れ・接続エラー、取り直す", e, flush=True); time.sleep(30); r = None; continue
            if r.status_code in (429, 502, 503, 504):
                # 1時間の枠を使い切ったときは長めに待つ（Actions の時間内に収まる範囲で）
                wait = 300 if "Hourly" in r.text else 65
                print(r.status_code, f"{wait}秒待機", r.text[:200], flush=True); time.sleep(wait); continue
            break
        if r is None or r.status_code != 200:
            raise RuntimeError(f"{url} {i}地点目から: {'時間切れ' if r is None else str(r.status_code) + ' ' + r.text[:300]}")
        j = r.json()
        out += j if isinstance(j, list) else [j]
        if i + size < len(locs):
            time.sleep(pause)
    return out


def forecast(resorts):
    """決定論の予報。上部とふもと。"""
    locs = [(r["lat"], r["lon"], e) for r in resorts for e in elevations(r).values()]
    j = chunked("https://api.open-meteo.com/v1/forecast", locs, dict(
        hourly="precipitation,temperature_2m,wind_gusts_10m", models=",".join(DET_MODELS),
        forecast_days=9, past_days=1, timezone="Asia/Tokyo"), 50, 10)  # past_days：朝の回でもその日の朝までの24時間がそろうように  # 8日目以降はアンサンブルを使うので9日分で足りる（枠の節約）
    return {r["id"]: {"upper": j[2 * i]["hourly"], "lower": j[2 * i + 1]["hourly"]} for i, r in enumerate(resorts)}


ENS_PATH = "data/ens_daily.json"


def ensemble(resorts, force=False):
    """ECMWF のアンサンブル（51通り）を上部の標高で取り、日ごとの値だけ残す。
    重いので毎朝4時に別の回で取り（python fetch.py ens）、ほかの回は前の分を使い回す。取れなければ3日前までの分を使う。
    {id: {date: {"sums": 51通りの夜の新雪 cm, "rain": 日中の雨の中央, "temp": 朝の気温の中央}}}"""
    old = None
    if os.path.exists(ENS_PATH):
        old = json.load(open(ENS_PATH, encoding="utf-8"))
        if old.get("window") != 24:  # 夜の区間を変える前の分は使わない
            old = None
    age = dt.datetime.now(JST) - dt.datetime.fromisoformat(old["made"]) if old else None
    if old and not force and age < dt.timedelta(hours=30):
        return old["data"]
    try:
        return fetch_ensemble(resorts)
    except Exception as e:
        if old and age < dt.timedelta(days=3):
            print(f"アンサンブルの取得に失敗。{old['made']} の分を使う: {e}", flush=True)
            return old["data"]
        raise


def fetch_ensemble(resorts):
    import build
    j = chunked("https://ensemble-api.open-meteo.com/v1/ensemble",
                [(r["lat"], r["lon"], elevations(r)["upper"]) for r in resorts],
                dict(hourly="precipitation,temperature_2m", models="ecmwf_ifs025", forecast_days=15,
                     timezone="Asia/Tokyo"), 25, 65)
    out = {}
    for r, x in zip(resorts, j):
        ms = build.ens_members(x["hourly"], build.P["scale"][build.fit_region(r)])
        days = sorted({t[:10] for t in x["hourly"]["time"]})[1:]
        res = {}
        for ds in days:
            d = dt.date.fromisoformat(ds)
            w = [build.windows(m, d) for m in ms]
            w = [(n, dy) for n, dy in w if len(n) >= 20]
            if len(w) < 20:
                continue
            res[ds] = {"sums": sorted(round(sum(h["snow"] for h in n), 1) for n, _ in w),
                       "rain": round(median(sum(h["rain"] for h in dy) for _, dy in w), 1),
                       "temp": round(median(n[-1]["temp"] for n, _ in w), 1)}
        out[r["id"]] = res
    json.dump({"made": dt.datetime.now(JST).isoformat(timespec="minutes"), "window": 24, "data": out},
              open(ENS_PATH, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return out


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


# 地方1か月予報のファイル名の番号（発表する気象台）→ 地方
ONEMONTH_AREAS = {"016000": "北海道", "040000": "東北", "130000": "関東甲信", "150000": "北陸", "230000": "東海",
                  "270000": "近畿", "340000": "中国", "370000": "四国", "400000": "九州北部"}


def onemonth():
    """気象庁の地方1か月予報（全地方）。発表文をそのまま使う。キーは「北陸」「関東甲信」など。"""
    feed = get("https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml").text
    latest = {}
    for u in sorted(set(re.findall(r"https://[^<\"]+_VPCK50_\d+\.xml", feed))):
        latest[u.rsplit("_", 1)[1]] = u  # 同じ地方は新しい方（ファイル名が日時順）
    out = {}
    for code, u in latest.items():
        name = ONEMONTH_AREAS.get(code.split(".")[0])
        if not name:
            continue
        x = get(u).content.decode("utf-8")
        text = []
        g = [t.strip() for t in re.findall(r"<jmx_eb:GeneralSituationText[^>]*>([^<]+)<", x)]
        if g:
            text.append("天気：" + " ".join(g))
        for kind, body in re.findall(r'<jmx_eb:SignificantClimateElement kind="([^"]+)">(.*?)</jmx_eb:SignificantClimateElement>', x, re.S):
            ts = [t.strip() for t in re.findall(r"<jmx_eb:Text[^>]*>([^<]+)<", body)]
            if ts:
                text.append(f"{kind}（{'1か月' if len(ts) == 1 else '週ごと'}）：" + " ".join(ts))
        out[name] = {"url": u, "issued": re.search(r"<ReportDateTime>([^<]+)", x).group(1), "text": text}
    return out


def main():
    resorts = json.load(open("resorts.json", encoding="utf-8"))
    if sys.argv[1:] == ["ens"]:  # 毎朝4時の回：アンサンブルだけ取る（決定論の予報と同じ1時間に重ねないため）
        os.makedirs("data", exist_ok=True)
        ensemble(resorts, force=True)
        print("ens ok")
        return
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
    # 1か月予報は週1回の発表で、フィードから消えることがあるので、取れた最新を残しておく
    if data["onemonth"]:
        json.dump(data["onemonth"], open("data/onemonth.json", "w", encoding="utf-8"), ensure_ascii=False)
    elif os.path.exists("data/onemonth.json"):
        data["onemonth"] = json.load(open("data/onemonth.json", encoding="utf-8"))
    json.dump(data, open("data/latest.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("ok", now)


if __name__ == "__main__":
    main()
