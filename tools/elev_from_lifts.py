"""OSMのリフトの端点＋国土地理院の標高で、ゲレンデのふもと・山頂の標高を求める。"""
import json, math, time
from concurrent.futures import ThreadPoolExecutor
import requests


def dist(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def gsi(p):
    for _ in range(5):
        try:
            r = requests.get("https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php",
                             params={"lon": f"{p[1]:.6f}", "lat": f"{p[0]:.6f}", "outtype": "JSON"}, timeout=30)
            e = r.json()["elevation"]
            return float(e) if e not in ("-----", None) else None
        except Exception:
            time.sleep(2)
    return None


def dem(points):
    """国土地理院の標高API（日本国内、1〜10mメッシュ）。"""
    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(gsi, points))


def main():
    resorts = json.load(open("data/resorts_raw.json", encoding="utf-8"))
    lifts = [w for w in json.load(open("tools/cache/lifts.json", encoding="utf-8"))["elements"] if w.get("geometry")]
    ends = []
    for w in lifts:
        g = w["geometry"]
        ends.append(((g[0]["lat"], g[0]["lon"]), (g[-1]["lat"], g[-1]["lon"])))
    pts = [p for e in ends for p in e]
    el = dem(pts)
    lift_el = [(ends[i], el[2 * i], el[2 * i + 1]) for i in range(len(ends)) if el[2 * i] is not None and el[2 * i + 1] is not None]
    json.dump(lift_el, open("tools/cache/lift_el.json", "w"))
    # 各リフトを一番近いゲレンデ（4km以内）に割り当てる
    assign = {}
    for (a, b), ea, eb in lift_el:
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        best = min(((dist(mid, (r["lat"], r["lon"])), r["sid"]) for r in resorts if r["lat"]), default=None)
        if best and best[0] <= 4:
            assign.setdefault(best[1], []).append((ea, eb))
    for r in resorts:
        ls = assign.get(r["sid"], [])
        r["lifts"] = len(ls)
        r["base_lift"] = round(min(min(x) for x in ls)) if ls else None
        r["top_lift"] = round(max(max(x) for x in ls)) if ls else None
    json.dump(resorts, open("data/resorts_raw.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    both = [r for r in resorts if r["top"] and r["top_lift"]]
    dt = sorted(abs(r["top"] - r["top_lift"]) for r in both); db = sorted(abs(r["base"] - r["base_lift"]) for r in both)
    print("比較", len(both), "山頂の差 中央", dt[len(dt) // 2], "9割", dt[int(len(dt) * .9)], "| ふもとの差 中央", db[len(db) // 2], "9割", db[int(len(db) * .9)])
    print("標高なし→リフトで補える", sum(1 for r in resorts if not r["top"] and r["top_lift"]), "/", sum(1 for r in resorts if not r["top"]))


if __name__ == "__main__":
    main()
