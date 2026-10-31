"""data/latest.json と過去の観測から index.html を作る。"""
import csv
import datetime as dt
import html
import json
import os
import sys
from collections import defaultdict
from statistics import median

JST = dt.timezone(dt.timedelta(hours=9))
P = json.load(open("data/params.json", encoding="utf-8"))
WEEK = "月火水木金土日"
STATION_NAME = {  # 2週間の表で「いまの積雪」に使う観測点
   "48141": "白馬", "48031": "野沢温泉", "48061": "信濃町", "48066": "飯山", "48216": "菅平",
                "54841": "湯沢", "54816": "関山"}


def snow_cm(p, t):
    """1時間の降水 mm と気温 ℃ から降雪 cm（換算式は tools/verify.py で実測に合わせたもの）。"""
    if p is None or t is None or p <= 0:
        return 0.0
    t0, t1 = P["t0"], P["t1"]
    f = 1.0 if t <= t0 else 0.0 if t >= t1 else (t1 - t) / (t1 - t0)
    return p * f * P["k"]


def is_rain(p, t):
    return p is not None and t is not None and p > 0 and t >= P["t1"]


def hourly_blend(h):
    """モデルごとに気温の偏りと量の倍率を直して、重み付きで平均する。"""
    out = []
    for i, ts in enumerate(h["time"]):
        num = den = 0.0
        temps, rain, gust = [], 0.0, []
        for m, w in P["weight"].items():
            p, t = h.get(f"precipitation_{m}", [None])[i], h.get(f"temperature_2m_{m}", [None])[i]
            if p is None or t is None:
                continue
            t -= P["tbias"][m]
            num += w * P["scale"][m] * snow_cm(p, t)
            rain += w * (p if is_rain(p, t) else 0)
            den += w
            temps.append(t)
            g = h.get(f"wind_gusts_10m_{m}", [None])[i]
            if g is not None:
                gust.append(g)
        if den:
            out.append({"time": ts, "snow": num / den, "rain": rain / den, "temp": sum(temps) / len(temps),
                        "gust": median(gust) / 3.6 if gust else None, "n": len(temps)})
    return out


def windows(hours, day):
    """その日の朝までの新雪（前日15時〜当日9時）と、日中（9〜16時）の様子。"""
    d0 = dt.datetime.combine(day, dt.time(9))
    night = [x for x in hours if d0 - dt.timedelta(hours=18) < dt.datetime.fromisoformat(x["time"]) <= d0]
    daytime = [x for x in hours if d0 < dt.datetime.fromisoformat(x["time"]) <= d0 + dt.timedelta(hours=7)]
    return night, daytime


def ens_members(h):
    ms = sorted(k[len("precipitation_"):] for k in h if k.startswith("precipitation_member"))
    ms = ["precipitation"] + [f"precipitation_{m}" for m in ms]
    m_ecm = "ecmwf_ifs025"
    out = []
    for key in ms:
        tkey = key.replace("precipitation", "temperature_2m")
        out.append([{"time": ts, "snow": P["scale"][m_ecm] * snow_cm(h[key][i], (h[tkey][i] or 0) - P["tbias"][m_ecm]),
                     "rain": h[key][i] if is_rain(h[key][i], (h[tkey][i] or 0) - P["tbias"][m_ecm]) else 0,
                     "temp": (h[tkey][i] or 0) - P["tbias"][m_ecm]}
                    for i, ts in enumerate(h["time"]) if h[key][i] is not None])
    return out


def judge(day, new, rain, gust, temp, prob, opened):
    """3段階の判定。prob は20cm以上の確率（アンサンブル）、new は見込みの新雪 cm。"""
    if opened is False:
        return "closed", "営業前"
    if rain >= 1.0:
        return "bad", "雨"
    if gust is not None and gust >= 20:
        return "bad", "強風"
    thr = P["powder_thr"].get(str(min(max((day - TODAY).days, 1), 3)), 15)
    if (prob is not None and prob >= 0.5 and (day - TODAY).days > 3) or (new >= thr and (day - TODAY).days <= 3):
        if temp is None or temp <= -2:
            return "powder", "パウダー"
    return "ok", "滑れる"


def upper_stats(c, day):
    """過去10シーズンの、その日までの上部の累計降雪（11/1から）。中央値と少ない年（下から2割）。"""
    vals = []
    for y, arr in c["seasons"].items():
        y = int(y)
        try:
            d = dt.date(y + (1 if day.month < 7 else 0), day.month, day.day)
        except ValueError:  # 2/29
            d = dt.date(y + 1, 2, 28)
        i = (d - dt.date(y, 11, 1)).days
        if 0 <= i < len(arr):
            vals.append(arr[i])
    if len(vals) < 5:
        return None
    vals.sort()
    return {"median": median(vals), "low": vals[int(len(vals) * 0.2)]}


def main():
    global TODAY
    resorts = json.load(open("resorts.json", encoding="utf-8"))
    data = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "data/latest.json", encoding="utf-8"))
    now = dt.datetime.fromisoformat(data["fetched"])
    TODAY = now.date() if now.hour < 9 else now.date() + dt.timedelta(days=1)
    days = [TODAY + dt.timedelta(days=i) for i in range(14)]
    rows = []
    for r in resorts:
        f = data["forecast"][r["id"]]
        up, low = hourly_blend(f["upper"]), hourly_blend(f["lower"])
        ens = ens_members(data["ensemble"][r["id"]]) if data.get("ensemble") else []
        opened_date = dt.date.fromisoformat(r["open"]) if r.get("open") else None
        cells = []
        for d in days:
            night, daytime = windows(up, d)
            if not night:
                cells.append(None)
                continue
            lead = (d - TODAY).days
            new = sum(x["snow"] for x in night)
            rain = sum(x["rain"] for x in daytime)
            gusts = [x["gust"] for x in daytime if x["gust"] is not None]
            temp = night[-1]["temp"]
            prob = lo = hi = None
            if ens:
                sums = sorted(sum(x["snow"] for x in windows(m, d)[0]) for m in ens)
                if sums:
                    prob = sum(s >= 20 for s in sums) / len(sums)
                    lo, hi = sums[int(len(sums) * 0.1)], sums[int(len(sums) * 0.9)]
                    if lead > 3:
                        new = median(sums)
                        rain = median(sum(x["rain"] for x in windows(m, d)[1]) for m in ens)
            ln, _ = windows(low, d)
            low_rain = sum(x["rain"] for x in ln)
            opened = False if opened_date is None else d >= opened_date
            lv, label = judge(d, new, rain, max(gusts) if gusts and lead <= 3 else None, temp, prob, opened)
            cells.append({"date": d.isoformat(), "new": round(new), "lo": None if lo is None else round(lo),
                          "hi": None if hi is None else round(hi), "prob": prob, "rain": round(rain, 1),
                          "temp": round(temp, 1), "low_rain": round(low_rain, 1), "lv": lv, "label": label,
                          "gust": round(max(gusts)) if gusts else None, "lead": lead})
        am = (data.get("amedas") or {}).get(r["amedas"] or "", {})
        rows.append({"r": r, "cells": cells, "amedas": am})

    weeks = []
    start = TODAY - dt.timedelta(days=TODAY.weekday()) + dt.timedelta(days=7)
    for w in range(6):
        weeks.append(start + dt.timedelta(days=7 * w))
    clim = json.load(open("data/clim_upper.json", encoding="utf-8"))
    plan = [{"r": r, "weeks": [upper_stats(clim[r["id"]], w + dt.timedelta(days=6)) for w in weeks]} for r in resorts]

    html_out = render(now, days, rows, weeks, plan, data.get("onemonth") or {})
    open("index.html", "w", encoding="utf-8").write(html_out)
    json.dump({"made": now.isoformat(), "rows": [{"id": x["r"]["id"], "cells": x["cells"]} for x in rows]},
              open("data/summary.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("index.html ok")


def esc(s):
    return html.escape(str(s))


def render(now, days, rows, weeks, plan, onemonth):
    tpl = open("template.html", encoding="utf-8").read()
    # 今週末のおすすめ（次の土日）
    wk = [d for d in days if d.weekday() >= 5][:2]
    picks = []
    for row in rows:
        for c in row["cells"]:
            if c and c["date"] in [d.isoformat() for d in wk] and c["lv"] in ("powder", "ok"):
                score = c["new"] + (30 if c["lv"] == "powder" else 0) - c["rain"] * 5
                picks.append((score, row, c))
    picks.sort(key=lambda x: -x[0])
    top = []
    seen = set()
    for score, row, c in picks:
        if row["r"]["id"] in seen or c["new"] < 3:
            continue
        seen.add(row["r"]["id"])
        d = dt.date.fromisoformat(c["date"])
        conf = "見込み" if c["lead"] <= 3 else "目安"
        extra = f"・20cm以上の確率 {round(c['prob'] * 100)}%" if c["prob"] is not None else ""
        top.append(f'<li><span class="lv lv-{c["lv"]}">{badge(c["lv"])}{esc(c["label"])}</span>'
                   f'<b>{esc(row["r"]["name"])}</b><span class="sub">{d.month}/{d.day}({WEEK[d.weekday()]})の朝 '
                   f'新雪{c["new"]}cm{conf}{extra}</span></li>')
        if len(top) == 3:
            break
    weekend = "".join(top) or '<li class="none">今週末は新雪の見込みがあるゲレンデがありません</li>'

    # 2週間の表
    head = "".join(f'<th class="{"we" if d.weekday() >= 5 else ""}">{d.month}/{d.day}<br>{WEEK[d.weekday()]}</th>' for d in days)
    maxv = max([c["new"] for row in rows for c in row["cells"] if c] + [20])
    body = []
    for ri, row in enumerate(rows):
        r = row["r"]
        tds = []
        for d, c in zip(days, row["cells"]):
            we = " we" if d.weekday() >= 5 else ""
            if not c:
                tds.append(f'<td class="na{we}"></td>')
                continue
            h = max(2, round(c["new"] / maxv * 30)) if c["new"] > 0 else 0
            far = " far" if c["lead"] > 3 else ""
            tip = (f'{r["name"]}（上部{round(r["base"] + (r["top"] - r["base"]) * 0.75)}m） {d.month}/{d.day}({WEEK[d.weekday()]}) 朝までの新雪 {c["new"]}cm'
                   + (f'（幅 {c["lo"]}〜{c["hi"]}cm）' if c["lo"] is not None else "")
                   + (f' / 20cm以上の確率 {round(c["prob"] * 100)}%' if c["prob"] is not None else "")
                   + f' / 朝の気温 {c["temp"]}℃' + (f' / 日中の雨 {c["rain"]}mm' if c["rain"] >= 0.5 else "")
                   + (f' / 最大瞬間風速 {c["gust"]}m/s' if c["gust"] is not None and c["lead"] <= 3 else "")
                   + f' / {c["label"]}')
            tds.append(f'<td class="c lv-{c["lv"]}{we}{far}" tabindex="0" data-tip="{esc(tip)}">'
                       f'<span class="v">{c["new"] if c["new"] >= 5 or c["lv"] == "powder" else ""}</span>'
                       f'<span class="bar"><i style="height:{h}px"></i></span>'
                       f'<span class="ic">{badge(c["lv"])}</span></td>')
        am = row["amedas"]
        now_snow = (f'{STATION_NAME.get(r["amedas"], "")}（ふもと）積雪 {am["snow"]}cm'
                    + (f'・24時間で+{am["snow24h"]}cm' if am.get("snow24h") else "")) if am.get("snow") is not None else ""
        if r.get("open"):
            od = dt.date.fromisoformat(r["open"])
            opening = f'{od.month}/{od.day}〜' if od > now.date() else "営業中"
        else:
            opening = "オープン日未発表"
        if ri == 0 or r["area"] != rows[ri - 1]["r"]["area"]:
            body.append(f'<tr class="area"><th class="name">{esc(r["area"])}</th><td colspan="{len(days)}"></td></tr>')
        body.append(f'<tr><th class="name"><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["name"])}</a>'
                    f'<span class="sub">{opening}{"・" + esc(now_snow) if now_snow else ""}</span></th>{"".join(tds)}</tr>')

    # 1か月先
    whead = "".join(f'<th>〜{(w + dt.timedelta(days=6)).month}/{(w + dt.timedelta(days=6)).day}</th>' for w in weeks)
    pbody = []
    for i, p in enumerate(plan):
        r = p["r"]
        if i == 0 or r["area"] != plan[i - 1]["r"]["area"]:
            pbody.append(f'<tr class="area"><th class="name">{esc(r["area"])}</th><td colspan="{len(weeks)}"></td></tr>')
        tds = []
        for s in p["weeks"]:
            if not s:
                tds.append('<td class="na">-</td>')
                continue
            tds.append(f'<td><b>{fmt_cm(s["median"])}</b><span class="sub">少 {fmt_cm(s["low"])}</span></td>')
        pbody.append(f'<tr><th class="name">{esc(r["name"])}</th>{"".join(tds)}</tr>')
    om = []
    for area, v in onemonth.items():
        t = v.get("text", [])
        issued = v.get("issued", "")[:10]
        om.append(f'<div class="om"><h3>{esc(area)}地方（{esc(issued)}発表）</h3><ul>'
                  + "".join(f"<li>{esc(x)}</li>" for x in t) + "</ul></div>")

    sc = P["scores"]
    acc = (f'去年までの2冬で確かめた当たり具合（20cm以上降った日を、前日の予報で拾えた割合）：'
           f'1日前 {round(sc["1"]["pod"] * 100)}%・3日前 {round(sc["3"]["pod"] * 100)}%・7日前 {round(sc["7"]["pod"] * 100)}%。'
           f'パウダーと出た日のうち外れた割合は1日前で {round(sc["1"]["far"] * 100)}%。')
    return (tpl.replace("{{updated}}", f"{now.month}/{now.day} {now:%H:%M}")
            .replace("{{weekend}}", weekend).replace("{{head}}", head).replace("{{body}}", "".join(body))
            .replace("{{whead}}", whead).replace("{{pbody}}", "".join(pbody)).replace("{{onemonth}}", "".join(om))
            .replace("{{accuracy}}", esc(acc)))


def fmt_cm(x):
    return f"{x / 100:.1f}m" if x >= 100 else f"{round(x)}cm"


def badge(lv):
    return {"powder": "❄", "ok": "○", "bad": "△", "closed": "－"}[lv]


if __name__ == "__main__":
    main()
