"""過去10シーズンの「11/1からの累計降雪」を、ゲレンデ上部の標高で計算する（最寄りアメダスの観測値から）。

1. 観測点の日別の降水量・気温から、換算式（data/params.json）で降雪を出す。
   これを同じ観測点で実際に観測された降雪量と比べて、観測点ごとの倍率 c を決める（雨量計の雪の取りこぼしなどを吸収）。
2. 気温を標高差で直して（0.65℃/100m）、上部の標高での降雪 = c × 降水 × 雪の割合 × 換算 を出す。
3. 週ごとに10シーズン分の累計の中央値と少ない年（下から2割）を出す。ふもとの積雪の中央値も付ける。
観測点は1つに頼らず、近さと標高の近さで点を付けた上位3つの重み付き平均（平地の観測点1つで山の値が決まるのを避ける）。
出力: data/clim.json {resort_id: {"c": 倍率, "weeks": {"MM-DD": [上部累計の中央, 少ない年, ふもと積雪の中央]}}}"""
import csv, json, math, os
import datetime as dt
from statistics import median

LAPSE = 0.0065
P = json.load(open("data/params.json", encoding="utf-8"))
Q = P["snow"]


def frac(t):
    return min(max((Q["t1"] - t) / (Q["t1"] - Q["t0"]), 0.0), 1.0)


def day_snow(prec, tmin, tavg, tmax, shift=0.0):
    """日別値から降雪 cm。最低・平均・最高の3つの気温で雪の割合を平均する。"""
    if prec is None or tavg is None:
        return None
    ts = [x - shift for x in (tmin, tavg, tmax) if x is not None]
    f = sum(frac(t) for t in ts) / len(ts)
    t = tavg - shift
    return prec * f * Q["k"] * (1 + Q["a"] * min(max(-t, 0.0), 10.0))


def fl(x):
    return float(x) if x not in ("", None) else None


def load(sid):
    p = f"data/obs/{sid}.csv"
    if not os.path.exists(p):
        return None
    return [{k: (fl(v) if k != "date" else v) for k, v in r.items()} for r in csv.DictReader(open(p, encoding="utf-8"))]


def station_factor(rows):
    est = obs = 0.0
    for r in rows:
        e = day_snow(r["prec"], r["tmin"], r["tavg"], r["tmax"])
        if e is not None and r["snowfall"] is not None:
            est += e; obs += r["snowfall"]
    return obs / est if est > 200 else None


def dist(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def main():
    resorts = json.load(open("resorts.json", encoding="utf-8"))
    st = json.load(open("tools/cache/snow_stations.json", encoding="utf-8"))
    pos = {k: (v["lat"][0] + v["lat"][1] / 60, v["lon"][0] + v["lon"][1] / 60, v["alt"]) for k, v in st.items()}
    cache, out, factors = {}, {}, {}
    for r in resorts:
        upper = r["base"] + (r["top"] - r["base"]) * 0.75
        # 観測点の点数：距離 km ＋ ゲレンデのふもととの標高差 40m ごとに 1km 相当。60km 以内の上位3つ
        cands = []
        for k, p in pos.items():
            km = dist((r["lat"], r["lon"]), p[:2])
            if km > 60 or not os.path.exists(f"data/obs/{k}.csv"):
                continue
            if k not in cache:
                cache[k] = load(k)
                factors[k] = station_factor(cache[k]) if cache[k] else None
            if cache[k] and factors[k]:
                cands.append((km + abs(r["base"] - p[2]) / 40, km, k))
        cands = sorted(cands)[:3]
        if not cands:
            continue
        per = []
        for score, km, sid in cands:
            weeks = station_weeks(cache[sid], factors[sid], (upper - pos[sid][2]) * LAPSE)
            per.append((1 / (score + 5), km, sid, weeks))
        keys = set.intersection(*(set(w) for *_, w in per))
        tw = sum(p[0] for p in per)
        weeks = {}
        for md in sorted(keys):
            med = sum(p[0] * p[3][md][0] for p in per) / tw
            low = sum(p[0] * p[3][md][1] for p in per) / tw
            big = sum(p[0] * p[3][md][3] for p in per) / tw
            weeks[md] = [round(med), round(low), None, round(big, 1)]
        # 麓の積雪は一番点の良い観測点のもの（名前・標高を一緒に出す）
        best = per[0]
        for md in weeks:
            weeks[md][2] = best[3].get(md, [None, None, None])[2]
        out[r["id"]] = {"stations": [p[2] for p in per], "depth_station": {"name": st[best[2]].get("kjName", best[2]), "alt": pos[best[2]][2], "km": round(best[1], 1)},
                        "c": round(sum(p[0] * factors[p[2]] for p in per) / tw, 2), "weeks": weeks}
    json.dump(out, open("data/clim.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    fs = sorted(v for v in factors.values() if v)
    print(len(out), "か所", "観測点の倍率 中央", round(fs[len(fs) // 2], 2), "範囲", round(fs[0], 2), "〜", round(fs[-1], 2))


def station_weeks(rows, c, shift):
    """1つの観測点から、上部の標高に直した週ごとの [累計の中央, 少ない年, 積雪の中央]。"""
    seasons = {}
    for x in rows:
        d = dt.date.fromisoformat(x["date"])
        season = d.year if d.month >= 7 else d.year - 1
        e = day_snow(x["prec"], x["tmin"], x["tavg"], x["tmax"], shift)
        seasons.setdefault(season, []).append((d, c * e if e is not None else 0.0, x["snow_depth"]))
    weeks = {}
    start = dt.date(2001, 11, 7)  # 月日だけ使う
    for w in range(26):
        md = (start + dt.timedelta(days=7 * w)).strftime("%m-%d")
        m, dd = int(md[:2]), int(md[3:])
        cums, depths, big = [], [], []
        for season, days in seasons.items():
            target = dt.date(season if m >= 7 else season + 1, m, dd)
            upto = [(d, s, dep) for d, s, dep in days if dt.date(season, 11, 1) <= d <= target]
            if not upto or upto[-1][0] != target:
                continue
            cums.append(sum(s for _, s, _ in upto))
            big.append(sum(1 for d, s, _ in upto if (target - d).days < 7 and s >= 20))
            if upto[-1][2] is not None:
                depths.append(upto[-1][2])
        if len(cums) >= 5:
            cums.sort()
            weeks[md] = [round(median(cums)), round(cums[int(len(cums) * 0.2)]), round(median(depths)) if len(depths) >= 5 else None,
                         round(sum(big) / len(big), 2)]
    return weeks


if __name__ == "__main__":
    main()
