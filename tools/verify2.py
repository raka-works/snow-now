"""全国の観測点で、予報（Open-Meteo Previous Runs）をアメダスの日降雪量と突き合わせ、補正の係数を決める。

やること
- モデルごとの気温の偏り
- 雪の換算式（気温で雪/雨を分け、寒いほど軽い雪＝量が増える）
- 地域ごと・モデルごとの量の倍率と、先行日数ごとの重み
- 補正後の平均から「20cm以上」「10cm以上」の確率を出す式（ロジスティック）
成績は「1冬目で決めて2冬目で測る」と「その逆」の平均（決めたデータでは測らない）。
最後に両方の冬を使って係数を決め、data/params.json に書く。"""
import csv, itertools, json, os
from collections import defaultdict
import numpy as np

MODELS = ["jma_msm", "jma_gsm", "ecmwf_ifs025", "gfs_seamless"]
LEADS = [1, 2, 3, 5, 7]
MAXLEAD = {"jma_msm": 2}  # MSM は3日先まで出ないので2日前まで
REGIONS = ["北海道", "東北", "北陸・新潟", "関東甲信", "西日本・東海"]


def region(sid):
    p = int(sid[:2])
    if p <= 24: return "北海道"
    if p <= 36: return "東北"
    if p in (54, 55, 56, 57): return "北陸・新潟"
    if p <= 49: return "関東甲信"
    return "西日本・東海"


def num(x):
    return float(x) if x not in ("", None) else np.nan


def load():
    """(lead) ごとに、行＝観測点×日、列＝モデル の配列を作る。"""
    st = json.load(open("tools/cache/verify_stations.json", encoding="utf-8"))
    rows = defaultdict(lambda: {"P": {}, "T": {}})
    meta = {}
    for sid in st:
        p = f"data/obs/{sid}.csv"
        if not os.path.exists(p):
            continue
        ob = {r["date"]: r for r in csv.DictReader(open(p, encoding="utf-8"))}
        for season in (0, 1):
            f = f"data/verify2/{sid}_{season}.json"
            if not os.path.exists(f):
                continue
            h = json.load(open(f))
            idx = defaultdict(list)
            for i, ts in enumerate(h["time"]):
                idx[ts[:10]].append(i)
            for day, ii in idx.items():
                o = ob.get(day)
                if len(ii) != 24 or not o or o["snowfall"] in ("", None):
                    continue
                meta[(sid, day)] = (season, region(sid), num(o["snowfall"]), num(o["tavg"]))
                for m in MODELS:
                    for L in LEADS:
                        kp, kt = f"precipitation_previous_day{L}_{m}", f"temperature_2m_previous_day{L}_{m}"
                        if kp not in h or L > MAXLEAD.get(m, 7):
                            continue
                        pv = [h[kp][i] for i in ii]; tv = [h[kt][i] for i in ii]
                        if None in pv or None in tv:
                            continue
                        rows[(L, sid, day)]["P"][m] = pv
                        rows[(L, sid, day)]["T"][m] = tv
    data = {}
    for L in LEADS:
        keys = sorted(k[1:] for k in rows if k[0] == L)
        n = len(keys)
        P = np.full((n, len(MODELS), 24), np.nan); T = np.full((n, len(MODELS), 24), np.nan)
        for i, k in enumerate(keys):
            r = rows[(L, *k)]
            for j, m in enumerate(MODELS):
                if m in r["P"]:
                    P[i, j] = r["P"][m]; T[i, j] = r["T"][m]
        data[L] = {"keys": keys, "P": P, "T": T,
                   "season": np.array([meta[k][0] for k in keys]), "region": np.array([REGIONS.index(meta[k][1]) for k in keys]),
                   "obs": np.array([meta[k][2] for k in keys]), "tavg": np.array([meta[k][3] for k in keys])}
    return data


def snow(P, T, q, tb):
    """P,T: (..., 24)。tb: モデルごとの気温の偏り (models,) → 日降雪 cm (..., models)"""
    t = T - tb[:, None]
    f = np.clip((q["t1"] - t) / (q["t1"] - q["t0"]), 0, 1)
    ratio = q["k"] * (1 + q["a"] * np.clip(-t, 0, 10))
    return np.nansum(np.where(P > 0, P, 0) * f * ratio, axis=-1) + np.where(np.isnan(P).all(-1), np.nan, 0)


def scores(f, o, thr_f=20, thr_o=20):
    ok = ~np.isnan(f) & ~np.isnan(o)
    f, o = f[ok], o[ok]
    if len(f) == 0:
        return None
    hit = np.sum((f >= thr_f) & (o >= thr_o)); miss = np.sum((f < thr_f) & (o >= thr_o)); fa = np.sum((f >= thr_f) & (o < thr_o))
    r = np.corrcoef(f, o)[0, 1] if f.std() and o.std() else np.nan
    q = lambda a, b: round(float(a / b), 3) if b else None
    return {"n": int(len(f)), "events": int(hit + miss), "bias": round(float(f.sum() / max(o.sum(), 1e-9)), 2),
            "mae": round(float(np.abs(f - o).mean()), 2), "r": round(float(r), 3),
            "pod": q(hit, hit + miss), "far": q(fa, hit + fa), "csi": q(hit, hit + miss + fa)}


def fit(data, seasons):
    tb = np.zeros(len(MODELS))
    d1 = data[1]
    sel = np.isin(d1["season"], seasons)
    for j in range(len(MODELS)):
        tm = np.nanmean(d1["T"][sel, j], axis=-1) - d1["tavg"][sel]
        tb[j] = np.nanmean(tm)
    # 換算式：1日前の各モデルの誤差の平均が小さく、20cm以上をよく当てるもの
    best = None
    S1 = None
    for k, a, t0, t1 in itertools.product([0.8, 0.9, 1.0, 1.1, 1.2, 1.3], [0.0, 0.03, 0.06, 0.1, 0.15], [-1.5, -1.0, -0.5, 0.0], [1.0, 1.5, 2.0, 2.5]):
        if t1 <= t0:
            continue
        q = {"k": k, "a": a, "t0": t0, "t1": t1}
        S = snow(d1["P"][sel], d1["T"][sel], q, tb)
        v = []
        for j in range(len(MODELS)):
            s = scores(S[:, j], d1["obs"][sel])
            v.append(s["mae"] - 5 * (s["csi"] or 0))
        v = float(np.mean(v))
        if best is None or v < best[0]:
            best = (v, q)
    q = best[1]
    # 地域×モデルの倍率（1〜3日前）
    scale = np.ones((len(REGIONS), len(MODELS)))
    for ri in range(len(REGIONS)):
        fs = np.zeros(len(MODELS)); os_ = np.zeros(len(MODELS))
        for L in (1, 2, 3):
            d = data[L]; s_ = np.isin(d["season"], seasons) & (d["region"] == ri)
            S = snow(d["P"][s_], d["T"][s_], q, tb)
            for j in range(len(MODELS)):
                ok = ~np.isnan(S[:, j])
                fs[j] += S[ok, j].sum(); os_[j] += d["obs"][s_][ok].sum()
        for j in range(len(MODELS)):
            scale[ri, j] = os_[j] / fs[j] if fs[j] > 100 and os_[j] > 100 else 1.0
    # 先行日数ごとの重み
    weight = {}
    for L in LEADS:
        d = data[L]; s_ = np.isin(d["season"], seasons)
        S = snow(d["P"][s_], d["T"][s_], q, tb) * scale[d["region"][s_]]
        w = []
        for j in range(len(MODELS)):
            ok = ~np.isnan(S[:, j])
            w.append(float(1 / np.mean((S[ok, j] - d["obs"][s_][ok]) ** 2)) if ok.sum() > 100 else 0.0)
        weight[L] = np.array(w)
    P = {"tbias": tb, "snow": q, "scale": scale, "weight": weight, "prob": {}}
    for L in LEADS:
        b, o = blend_arr(data, P, L, seasons)
        P["prob"][L] = {th: logistic(b, o >= th) for th in (10, 20)}
    return P


def blend_arr(data, P, L, seasons):
    d = data[L]; s_ = np.isin(d["season"], seasons)
    S = snow(d["P"][s_], d["T"][s_], P["snow"], P["tbias"]) * P["scale"][d["region"][s_]]
    W = np.where(np.isnan(S), 0, P["weight"][L])
    b = np.nansum(np.nan_to_num(S) * W, axis=1) / np.where(W.sum(1) > 0, W.sum(1), np.nan)
    ok = (~np.isnan(S)).sum(1) >= 2
    return np.where(ok, b, np.nan), d["obs"][s_]


def logistic(b, y, iters=4000, lr=0.5):
    ok = ~np.isnan(b)
    x = np.log1p(np.clip(b[ok], 0, None)); y = y[ok].astype(float)
    w = np.array([-3.0, 1.0])
    X = np.stack([np.ones_like(x), x], 1)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        w -= lr * X.T @ (p - y) / len(y)
    return [round(float(w[0]), 4), round(float(w[1]), 4)]


def prob(b, c):
    return 1 / (1 + np.exp(-(c[0] + c[1] * np.log1p(np.clip(b, 0, None)))))


def evaluate(data, P, season):
    res = {}
    raw_q = {"k": 1.0, "a": 0.0, "t0": 0.0, "t1": 2.0}
    for L in LEADS:
        b, o = blend_arr(data, P, L, (season,))
        ok = ~np.isnan(b) & ~np.isnan(o)
        b, o = b[ok], o[ok]
        p = prob(b, P["prob"][L][20]); y = (o >= 20).astype(float)
        brier = float(np.mean((p - y) ** 2)); clim = y.mean()
        rel = []
        for lo, hi in ((0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, 1.01)):
            g = (p >= lo) & (p < hi)
            if g.sum() >= 10:
                rel.append([round(float(p[g].mean()), 2), round(float(y[g].mean()), 2), int(g.sum())])
        d = data[L]; s_ = d["season"] == season
        base = {}
        for j, m in enumerate(MODELS[:2]):
            S = snow(d["P"][s_][:, j:j + 1], d["T"][s_][:, j:j + 1], raw_q, np.zeros(1))[:, 0]
            s = scores(S, d["obs"][s_])
            if s:
                base[m] = s
        res[L] = {"blend": scores(b, o), "powder": scores(np.where(p >= .5, 20, 0), o),
                  "brier_skill": round(1 - brier / float(np.mean((clim - y) ** 2)), 3), "reliability": rel, "baseline": base}
    return res


def jsonable(P):
    return {"tbias": dict(zip(MODELS, map(lambda x: round(float(x), 2), P["tbias"]))), "snow": P["snow"],
            "scale": {r: dict(zip(MODELS, map(lambda x: round(float(x), 3), P["scale"][i]))) for i, r in enumerate(REGIONS)},
            "weight": {str(L): dict(zip(MODELS, map(lambda x: round(float(x), 5), w))) for L, w in P["weight"].items()},
            "prob": {str(L): {str(t): c for t, c in v.items()} for L, v in P["prob"].items()}}


def main():
    data = load()
    print("観測点", len({k[0] for k in data[1]["keys"]}), "日数(1日前)", len(data[1]["keys"]))
    cv = {test: evaluate(data, fit(data, (1 - test,)), test) for test in (0, 1)}
    summary = {}
    for L in LEADS:
        a, b = cv[0][L], cv[1][L]
        def avg(f):
            try:
                x, y = f(a), f(b)
                return round((x + y) / 2, 3) if x is not None and y is not None else None
            except (KeyError, TypeError):
                return None
        summary[str(L)] = {"mae": avg(lambda x: x["blend"]["mae"]), "r": avg(lambda x: x["blend"]["r"]),
                           "pod": avg(lambda x: x["powder"]["pod"]), "far": avg(lambda x: x["powder"]["far"]),
                           "csi": avg(lambda x: x["powder"]["csi"]), "bss": avg(lambda x: x["brier_skill"]),
                           "msm_mae": avg(lambda x: x["baseline"]["jma_msm"]["mae"]), "msm_r": avg(lambda x: x["baseline"]["jma_msm"]["r"]),
                           "msm_csi": avg(lambda x: x["baseline"]["jma_msm"]["csi"]),
                           "gsm_mae": avg(lambda x: x["baseline"]["jma_gsm"]["mae"]), "gsm_r": avg(lambda x: x["baseline"]["jma_gsm"]["r"]),
                           "gsm_csi": avg(lambda x: x["baseline"]["jma_gsm"]["csi"]),
                           "events": a["blend"]["events"] + b["blend"]["events"], "n": a["blend"]["n"] + b["blend"]["n"],
                           "reliability": [a["reliability"], b["reliability"]]}
        print(L, "日前", summary[str(L)], flush=True)
    P = jsonable(fit(data, (0, 1)))
    P["verify"] = summary
    P["stations"] = len({k[0] for k in data[1]["keys"]})
    json.dump(P, open("data/params.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: P[k] for k in ("tbias", "snow", "scale")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
