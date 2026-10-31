"""ゲレンデから20km以内の全国の積雪観測点のアメダス日別値（10シーズン分）を取る。tools/etrn.py の全国版。"""
import json, re, sys, csv, os
from concurrent.futures import ThreadPoolExecutor
import requests
sys.path.insert(0, "tools")
from etrn import month_rows, MONTHS


def block_numbers(stations):
    out = {}
    for pref in sorted({sid[:2] for sid in stations}):
        r = requests.get(f"https://www.data.jma.go.jp/obd/stats/etrn/select/prefecture.php?prec_no={pref}&block_no=&year=&month=&day=&view=", timeout=60)
        r.encoding = "utf-8"
        found = {name: (b, kind) for kind, b, name in re.findall(r"viewPoint\('(\w)','(\d+)','([^']+)'", r.text)}
        for sid, v in stations.items():
            if sid[:2] == pref and v["kjName"] in found:
                out[sid] = (int(pref), *found[v["kjName"]])
    return out


def one(args):
    sid, (p, b, k) = args
    path = f"data/obs/{sid}.csv"
    if os.path.exists(path):
        return sid, "skip"
    jobs = [(y if m >= 11 else y + 1, m) for y in range(2016, 2026) for m in MONTHS]
    rows = []
    for y, m in jobs:
        for _ in range(3):
            try:
                rows += month_rows(p, b, y, m, k); break
            except Exception:
                pass
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    return sid, len(rows)


def main():
    st = json.load(open("tools/cache/snow_stations.json", encoding="utf-8"))
    bn = block_numbers(st)
    print("対応が取れた観測点", len(bn), "/", len(st), flush=True)
    json.dump({k: list(v) for k, v in bn.items()}, open("tools/cache/etrn_blocks.json", "w"))
    with ThreadPoolExecutor(10) as ex:
        for sid, n in ex.map(one, bn.items()):
            print(sid, n, flush=True)


if __name__ == "__main__":
    main()
