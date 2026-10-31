"""過去2シーズンの予報をアメダス実測と突き合わせ、モデルと雪の換算式を選ぶ。"""
import csv, json, itertools, math, sys
from collections import defaultdict
from statistics import mean

MODELS = ["jma_msm", "jma_gsm", "ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
LEADS = [1, 2, 3, 5, 7]


def load_obs(sid):
    out = {}
    for r in csv.DictReader(open(f"data/obs/{sid}.csv", encoding="utf-8")):
        out[r["date"]] = {k: (float(v) if v not in ("", None) else None) for k, v in r.items() if k != "date"}
    return out


def snow_cm(p, t, k, t0, t1):
    """1時間の降水 p mm、気温 t ℃ → 降雪 cm。t0以下は全部雪、t1以上は全部雨、間は直線。"""
    if p is None or t is None or p <= 0:
        return 0.0
    f = 1.0 if t <= t0 else 0.0 if t >= t1 else (t1 - t) / (t1 - t0)
    return p * f * k


def daily(h, key_p, key_t):
    d = defaultdict(lambda: {"p": [], "t": []})
    for i, ts in enumerate(h["time"]):
        day = ts[:10]
        d[day]["p"].append(h[key_p][i])
        d[day]["t"].append(h[key_t][i])
    return d


def load_fc():
    st = json.load(open("data/verify/stations.json"))
    fc = {}
    for sid in st:
        for i in (0, 1):
            try:
                h = json.load(open(f"data/verify/{sid}_{i}.json"))
            except FileNotFoundError:
                continue
            for m in MODELS:
                for L in LEADS:
                    kp, kt = f"precipitation_previous_day{L}_{m}", f"temperature_2m_previous_day{L}_{m}"
                    if kp not in h:
                        continue
                    for day, v in daily(h, kp, kt).items():
                        if any(x is None for x in v["p"]) or len(v["p"]) < 24:
                            continue
                        fc[(sid, m, L, day)] = v
    return fc


def score(pairs, thr_obs=20, thr_fc=None):
    """pairs: [(予報cm, 実測cm)]"""
    thr_fc = thr_obs if thr_fc is None else thr_fc
    n = len(pairs)
    if n == 0:
        return {}
    f = [a for a, _ in pairs]; o = [b for _, b in pairs]
    mf, mo = mean(f), mean(o)
    cov = sum((a - mf) * (b - mo) for a, b in pairs)
    sf = math.sqrt(sum((a - mf) ** 2 for a in f)); so = math.sqrt(sum((b - mo) ** 2 for b in o))
    hit = sum(a >= thr_fc and b >= thr_obs for a, b in pairs)
    miss = sum(a < thr_fc and b >= thr_obs for a, b in pairs)
    fa = sum(a >= thr_fc and b < thr_obs for a, b in pairs)
    return {"n": n, "bias": round(sum(f) / max(sum(o), 1e-9), 2), "mae": round(mean(abs(a - b) for a, b in pairs), 2),
            "r": round(cov / (sf * so), 3) if sf and so else None,
            "pod": round(hit / (hit + miss), 2) if hit + miss else None,
            "far": round(fa / (hit + fa), 2) if hit + fa else None,
            "csi": round(hit / (hit + miss + fa), 2) if hit + miss + fa else None, "events": hit + miss}


def build_pairs(fc, obs, m, L, k, t0, t1, tbias=0.0):
    pairs = []
    for (sid, mm, LL, day), v in fc.items():
        if mm != m or LL != L:
            continue
        o = obs[sid].get(day)
        if not o or o["snowfall"] is None:
            continue
        s = sum(snow_cm(p, t + tbias, k, t0, t1) for p, t in zip(v["p"], v["t"]))
        pairs.append((s, o["snowfall"]))
    return pairs


def main():
    fc = load_fc()
    import os
    fc = {k: v for k, v in fc.items() if os.path.exists(f"data/obs/{k[0]}.csv")}
    sids = sorted({k[0] for k in fc})
    obs = {s: load_obs(s) for s in sids}
    print("stations", sids, "fc days", len(fc))

    # 1) 気温の偏り（予報の日平均 − 実測の日平均）
    print("\n[気温の偏り ℃]  lead1")
    tb = {}
    for m in MODELS:
        d = [mean(v["t"]) - obs[s][day]["tavg"] for (s, mm, L, day), v in fc.items()
             if mm == m and L == 1 and obs[s].get(day) and obs[s][day]["tavg"] is not None]
        if d:
            tb[m] = mean(d); print(f"  {m:14s} {tb[m]:+.2f}  n={len(d)}")

    # 2) 換算式の当てはめ（気温の偏りを直したうえで、4モデルの1日前予報で最良のものを探す）
    best = None
    for k, t0, t1 in itertools.product([0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5], [-1.5, -1.0, -0.5, 0.0, 0.5], [1.0, 1.5, 2.0, 2.5, 3.0]):
        if t1 <= t0:
            continue
        sc = [score(build_pairs(fc, obs, m, 1, k, t0, t1, -tb[m])) for m in MODELS if m != "icon_seamless"]
        val = mean(s["mae"] for s in sc) - 5 * mean((s["csi"] or 0) for s in sc)
        if best is None or val < best[0]:
            best = (val, k, t0, t1)
    _, k, t0, t1 = best
    print(f"[換算式]  k={k} cm/mm  全部雪≤{t0}℃  全部雨≥{t1}℃")

    # 3) モデルごとの補正倍率（3日前までの合計を実測に合わせる）と重み
    use = ["jma_msm", "jma_gsm", "ecmwf_ifs025", "gfs_seamless"]
    scale, wt = {}, {}
    for m in use:
        pr = [p for L in (1, 2, 3) for p in build_pairs(fc, obs, m, L, k, t0, t1, -tb[m])]
        scale[m] = round(sum(b for _, b in pr) / sum(a for a, _ in pr), 3)
        pr = [(a * scale[m], b) for a, b in pr]
        wt[m] = round(1 / score(pr)["mae"] ** 2, 4)
        print(f"  {m:14s} 倍率 {scale[m]}  重み {wt[m]}  {score(pr)}")

    # 4) 補正後の重み付き平均の成績と、パウダー判定の基準値（実測20cm以上を一番よく当てる予報値）
    def blend_pairs(L):
        by = defaultdict(dict)
        for (s_, m, LL, day), v in fc.items():
            if m in use and LL == L and not (m == "jma_msm" and L > 2):
                by[(s_, day)][m] = scale[m] * sum(snow_cm(p, t - tb[m], k, t0, t1) for p, t in zip(v["p"], v["t"]))
        out = []
        for (s_, day), d in by.items():
            o = obs[s_].get(day)
            if len(d) >= 3 and o and o["snowfall"] is not None:
                out.append((sum(wt[m] * x for m, x in d.items()) / sum(wt[m] for m in d), o["snowfall"]))
        return out
    print("[補正後の平均]")
    res, thr = {}, {}
    for L in LEADS:
        pr = blend_pairs(L)
        cand = [(score(pr, 20, tf)["csi"] or 0, tf) for tf in range(10, 26)]
        c, tf = max(cand)
        thr[L] = tf
        res[L] = score(pr, 20, tf)
        print(f"  {L}日前  基準 {tf}cm  {res[L]}")
    json.dump({"k": k, "t0": t0, "t1": t1, "tbias": {m: round(tb[m], 2) for m in use}, "scale": scale, "weight": wt,
               "powder_thr": thr, "scores": res, "stations": sids},
              open("data/params.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
