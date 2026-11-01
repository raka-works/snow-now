"""data/latest.json と過去の観測から、全国のページ（index.html）と地域ごとのページ（r/*.html）を作る。"""
import datetime as dt
import html
import json
from collections import defaultdict
import math
import os
import sys
from statistics import median

JST = dt.timezone(dt.timedelta(hours=9))
P = json.load(open("data/params.json", encoding="utf-8"))  # tools/verify2.py が全国の実測に合わせて決めた係数
Q = P["snow"]
WEEK = "月火水木金土日"
MODELS = list(P["tbias"])
ECM = "ecmwf_ifs025"
LEADS = sorted(int(k) for k in P["weight"])  # 確かめた先行日数（1・2・3・5・7日前）
# ページの地域（resorts.json の region）と、ファイル名
PAGES = [("北海道", "hokkaido"), ("東北", "tohoku"), ("関東甲信越", "kanto"), ("北陸", "hokuriku"),
         ("中京", "chukyo"), ("関西", "kansai"), ("中国・四国・九州", "west")]
# 係数の地域（verify2.py の REGIONS）
TOHOKU = ("青森", "岩手", "宮城", "秋田", "山形", "福島")
HOKURIKU = ("新潟", "富山", "石川", "福井")
KANTO = ("群馬", "栃木", "長野", "山梨", "埼玉", "東京", "神奈川", "茨城", "千葉")


def fit_region(r):
    p = r["pref"]
    if p.startswith("北海道"): return "北海道"
    if p.startswith(TOHOKU): return "東北"
    if p.startswith(HOKURIKU): return "北陸・新潟"
    if p.startswith(KANTO): return "関東甲信"
    return "西日本・東海"


def snow_cm(p, t):
    """1時間の降水 mm と気温 ℃ から降雪 cm。寒いほど軽い雪で量が増える（換算式は verify2.py で実測に合わせたもの）。"""
    if p is None or t is None or p <= 0:
        return 0.0
    f = min(max((Q["t1"] - t) / (Q["t1"] - Q["t0"]), 0.0), 1.0)
    return p * f * Q["k"] * (1 + Q["a"] * min(max(-t, 0.0), 10.0))


def is_rain(p, t):
    return p is not None and t is not None and p > 0 and t >= Q["t1"]


def per_model(h, scale):
    """モデルごとに、気温の偏りと地域の量の倍率を直した1時間ごとの値。"""
    out = {}
    for m in MODELS:
        ps, ts = h.get(f"precipitation_{m}"), h.get(f"temperature_2m_{m}")
        if not ps:
            continue
        gs = h.get(f"wind_gusts_10m_{m}") or [None] * len(ps)
        rows = []
        for i, tm in enumerate(h["time"]):
            p, t = ps[i], ts[i]
            if p is None or t is None:
                continue
            t -= P["tbias"][m]
            rows.append({"time": tm, "snow": scale[m] * snow_cm(p, t), "rain": p if is_rain(p, t) else 0.0,
                         "temp": t, "gust": gs[i] / 3.6 if gs[i] is not None else None})
        out[m] = rows
    return out


def weights(lead):
    """その先行日数の重み。確かめた日数の間はつなぐ。"""
    L = min(LEADS, key=lambda x: (abs(x - lead), x))
    return P["weight"][str(L)]


def prob20(b, lead):
    """補正後の平均 b cm から「実測20cm以上」の確率（verify2.py のロジスティック。7日先まで）。"""
    L = min(LEADS, key=lambda x: (abs(x - lead), x))
    c = P["prob"][str(L)]["20"]
    return 1 / (1 + math.exp(-(c[0] + c[1] * math.log1p(max(b, 0)))))


def windows(hours, day):
    """その日の朝までの新雪（前日9時〜当日9時の24時間。係数を決めた検証の日降雪と同じ長さ）と、日中（9〜16時）の様子。"""
    d0 = dt.datetime.combine(day, dt.time(9))
    night = [x for x in hours if d0 - dt.timedelta(hours=24) < dt.datetime.fromisoformat(x["time"]) <= d0]
    daytime = [x for x in hours if d0 < dt.datetime.fromisoformat(x["time"]) <= d0 + dt.timedelta(hours=7)]
    return night, daytime


def blend_day(models, d, lead):
    """モデルごとの夜の新雪・日中の雨・朝の気温・風を、先行日数ごとの重みで平均する。"""
    w = weights(lead)
    num = den = rain = 0.0
    temps, gusts = [], []
    for m, hrs in models.items():
        night, daytime = windows(hrs, d)
        if len(night) < 20 or not w.get(m):
            continue
        num += w[m] * sum(x["snow"] for x in night)
        rain += w[m] * sum(x["rain"] for x in daytime)
        den += w[m]
        temps.append(night[-1]["temp"])
        gusts += [x["gust"] for x in daytime if x["gust"] is not None]
    if not den:
        return None
    return {"new": num / den, "rain": rain / den, "temp": sum(temps) / len(temps), "gust": max(gusts) if gusts else None}


def ens_members(h, scale):
    ms = ["precipitation"] + sorted(k for k in h if k.startswith("precipitation_member"))
    out = []
    for key in ms:
        tkey = key.replace("precipitation", "temperature_2m")
        rows = []
        for i, ts in enumerate(h["time"]):
            p, t = h[key][i], h[tkey][i]
            if p is None or t is None:
                continue
            t -= P["tbias"][ECM]
            rows.append({"time": ts, "snow": scale[ECM] * snow_cm(p, t), "rain": p if is_rain(p, t) else 0.0, "temp": t})
        out.append(rows)
    return out


def judge(lead, rain, gust, temp, prob, opened):
    if opened is False:
        return "closed", "営業していない"
    if rain >= 1.0:
        return "bad", "雨"
    if gust is not None and gust >= 20 and lead <= 3:
        return "bad", "強風"
    if lead > 7:  # 8日目以降は確かめていないので、パウダーとは言わない（多い日は数字と確率で見る）
        return "ok", "滑れる"
    # verify2.py で決めた境目。4日目・6日目は前後の日の境目の平均
    if str(lead) in P["pthr"]:
        thr = P["pthr"][str(lead)]
    else:
        lo, hi = max(x for x in LEADS if x < lead), min(x for x in LEADS if x > lead)
        thr = (P["pthr"][str(lo)] + P["pthr"][str(hi)]) / 2
    if prob is not None and prob >= thr and (temp is None or temp <= -2):
        return "powder", "パウダー"
    return "ok", "滑れる"


def to_season(d, today):
    """日付を今シーズン（7月〜翌6月）の同じ月日に置き換える。"""
    season = today.year if today.month >= 7 else today.year - 1  # 今シーズンが始まった年
    y = season + (1 if d.month < 7 else 0)
    return dt.date(y, d.month, min(d.day, 28) if d.month == 2 else d.day)


def parse_ymd(s):
    yy, mm, dd = s.split("/")
    return dt.date(int(yy), int(mm), {"上旬": 5, "中旬": 15, "下旬": 25}.get(dd) or int(dd))


def close_date(r, today):
    """去年の営業終了日を今シーズンに置き換えた推定。分からなければ None。"""
    try:
        return to_season(parse_ymd(r["close_last"]), today) if r.get("close_last") else None
    except ValueError:
        return None


def open_date(r, today):
    """(オープン日, 推定の種類)。今シーズンの発表がなければ去年のオープン日を今シーズンに置き換える（"last"）。
    どちらもなければ (None, None)。main で同じ地域の推定日の中央を入れる（"area"）。"""
    start = to_season(dt.date(2000, 7, 1), today)
    if r.get("open"):
        d = dt.date.fromisoformat(r["open"])
        if d >= start:
            return d, None
        return to_season(d, today), "last"  # 去年のシーズンの発表が残っている
    if r.get("open_last"):
        return to_season(parse_ymd(r["open_last"]), today), "last"
    return None, None


def plan_stats(c, day):
    """過去10シーズンの、その日までの上部の累計降雪（11/1から）とふもとの積雪。clim.py の週ごとの値から一番近い週。"""
    if not c or not c.get("weeks"):
        return None

    def sday(m, d):  # 11/1 からの日数（年は関係ない）
        return (dt.date(2001 if m >= 7 else 2002, m, min(d, 28) if m == 2 else d) - dt.date(2001, 11, 1)).days
    t = sday(day.month, day.day)
    gap, v = min(((abs(sday(int(k[:2]), int(k[3:])) - t), v) for k, v in c["weeks"].items()), key=lambda x: x[0])
    if gap > 4:
        return None
    return {"median": v[0], "low": v[1], "depth": v[2]}


def main():
    resorts = sorted(json.load(open("resorts.json", encoding="utf-8")), key=lambda r: (r["region"], r["area"], r["name"]))
    data = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "data/latest.json", encoding="utf-8"))
    now = dt.datetime.fromisoformat(data["fetched"])
    today = now.date() if now.hour < 9 else now.date() + dt.timedelta(days=1)
    days = [today + dt.timedelta(days=i) for i in range(14)]
    ens_all = data.get("ensemble") or {}
    opens = {r["id"]: open_date(r, today) for r in resorts}
    by_area = defaultdict(list)
    for r in resorts:
        if opens[r["id"]][0]:
            by_area[r["area"]].append(opens[r["id"]][0])
    for r in resorts:  # オープン日がまったく分からなければ、同じ地域のゲレンデの真ん中の日
        if not opens[r["id"]][0] and by_area[r["area"]]:
            opens[r["id"]] = (sorted(by_area[r["area"]])[len(by_area[r["area"]]) // 2], "area")
    rows = []
    for r in resorts:
        f = data["forecast"].get(r["id"])
        if not f:
            continue
        scale = P["scale"][fit_region(r)]
        up, low = per_model(f["upper"], scale), per_model(f["lower"], scale)
        ens = ens_all.get(r["id"]) or {}  # fetch.py が日ごとにまとめた51通り
        opened_date, est = opens[r["id"]]
        closing = close_date(r, today)
        cells = []
        for d in days:
            lead = max((d - now.date()).days, 1)  # 予報を取った日から数える（検証の「L日前の予報」と同じ）
            b = blend_day(up, d, lead)
            e = ens.get(d.isoformat())
            sums = e["sums"] if e else []
            if lead <= 7 and b:
                new, rain, temp, gust = b["new"], b["rain"], b["temp"], b["gust"]
                prob, src = prob20(new, max(lead, 1)), "blend"
            elif len(sums) >= 20:
                new = median(sums)
                rain, temp = e["rain"], e["temp"]
                gust, prob, src = None, sum(s >= 20 for s in sums) / len(sums), "ens"
            else:
                cells.append(None)
                continue
            lo = hi = None
            if src == "ens":  # 幅は51通りの中央値を出す日だけ（7日先までは中心が別の計算なので出さない）
                lo, hi = sums[int(len(sums) * 0.1)], sums[int(len(sums) * 0.9)]
            bl = blend_day(low, d, lead)
            opened = False if opened_date is None or (closing and d > closing) else d >= opened_date
            lv, label = judge(lead, rain, gust, temp, prob, opened)
            cells.append({"date": d.isoformat(), "new": round(new), "lo": None if lo is None else round(lo),
                          "hi": None if hi is None else round(hi), "prob": round(prob, 2), "src": src,
                          "rain": round(rain, 1), "temp": round(temp, 1), "low_rain": round(bl["rain"], 1) if bl else None,
                          "lv": lv, "label": label, "gust": round(gust) if gust is not None else None, "lead": lead})
        am = (data.get("amedas") or {}).get(r["amedas"] or "", {})
        rows.append({"r": r, "cells": cells, "amedas": am, "open": opened_date, "est": est, "close": closing})

    start = today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=7)
    weeks = [start + dt.timedelta(days=7 * w) for w in range(6)]
    clim = json.load(open("data/clim.json", encoding="utf-8")) if os.path.exists("data/clim.json") else {}
    plan = {r["id"]: [plan_stats(clim.get(r["id"]), w + dt.timedelta(days=6)) for w in weeks] for r in resorts}

    onemonth = data.get("onemonth") or {}
    os.makedirs("r", exist_ok=True)
    for region, slug in PAGES:
        rr = [x for x in rows if x["r"]["region"] == region]
        om = {k: v for k, v in onemonth.items() if k in ONEMONTH_FOR.get(region, ())}
        open(f"r/{slug}.html", "w", encoding="utf-8").write(render(now, days, rr, weeks, plan, om, region, "../"))
    open("index.html", "w", encoding="utf-8").write(render_index(now, days, rows))
    json.dump({"made": now.isoformat(), "rows": [{"id": x["r"]["id"], "cells": x["cells"]} for x in rows]},
              open("data/summary.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("pages ok", len(rows))


# 地域のページに載せる気象庁の地方1か月予報（fetch.py の onemonth のキー）
ONEMONTH_FOR = {"北海道": ("北海道",), "東北": ("東北",), "関東甲信越": ("関東甲信", "北陸"), "北陸": ("北陸",),
                "中京": ("東海",), "関西": ("近畿",), "中国・四国・九州": ("中国", "四国", "九州北部")}


def weekend_picks(rows, days, n, prefix):
    first = next((d for d in days if d.weekday() >= 5), None)  # 次の土日（日曜なら日曜だけ）
    wk = [x.isoformat() for x in days if first and 0 <= (x - first).days <= (1 if first.weekday() == 5 else 0)]
    picks = []
    for row in rows:
        for c in row["cells"]:
            if c and c["date"] in wk and c["lv"] in ("powder", "ok") and c["new"] >= 5 and row["est"] != "area":
                picks.append((c["new"] + (30 if c["lv"] == "powder" else 0) - c["rain"] * 5, row, c))
    picks.sort(key=lambda x: -x[0])
    top, seen = [], set()
    for _, row, c in picks:
        if row["r"]["id"] in seen:
            continue
        seen.add(row["r"]["id"])
        d = dt.date.fromisoformat(c["date"])
        conf = "見込み" if c["lead"] <= 3 else "目安"
        slug = dict(PAGES)[row["r"]["region"]]
        top.append(f'<li><span class="lv lv-{c["lv"]}">{badge(c["lv"])}{esc(c["label"])}</span>'
                   f'<a href="{prefix}r/{slug}.html#{row["r"]["id"]}"><b>{esc(row["r"]["name"])}</b><span class="sub">{esc(row["r"]["pref"])}・{d.month}/{d.day}({WEEK[d.weekday()]})の朝 '
                   f'新雪{c["new"]}cm{conf}・20cm以上の確率 {round(c["prob"] * 100)}%</span></a></li>')
        if len(top) == n:
            break
    if top:
        return "".join(top)
    if any(c and c["date"] in wk and c["lv"] != "closed" for row in rows for c in row["cells"]):
        return '<li class="none">今週末に営業するゲレンデで、5cm以上の新雪の見込みはありません</li>'
    soon = sorted((row["open"], row["r"]["name"], row["est"]) for row in rows if row["open"] and row["open"] > dt.date.fromisoformat(wk[-1] if wk else days[0].isoformat()))[:5]
    return ('<li class="none">今週末に営業するゲレンデはまだありません。オープンが早いのは：'
            + "、".join(f'{esc(n)} {d.month}/{d.day}{"ごろ" if e else ""}' for d, n, e in soon) + '</li>')


def nav(prefix, current=None):
    return '<nav class="regions">' + "".join(
        f'<a href="{prefix}{"index.html" if s is None else "r/" + s + ".html"}"{" aria-current=\"page\"" if n == current else ""}>{n}</a>'
        for n, s in [("全国", None)] + PAGES) + "</nav>"


def accuracy():
    v = P["verify"]
    pct = lambda x: f"{round(x * 100)}%" if x is not None else "－"
    return (f'去年までの2冬・全国{P["stations"]}か所のアメダスで確かめた当たり具合（1冬目で係数を決めて2冬目で測り、その逆も平均）：'
            f'20cm以上降った日を拾えた割合は1日前 {pct(v["1"]["pod"])}・3日前 {pct(v["3"]["pod"])}・7日前 {pct(v["7"]["pod"])}、'
            f'20cm以上と出た日のうち外れた割合は1日前 {pct(v["1"]["far"])}・7日前 {pct(v["7"]["far"])}（降雪量だけで数えた値。表のパウダーはさらに朝の気温−2℃以下が条件）。4日前・6日前は近い日の係数を使っています。8日目以降は51通りの予報のうち20cm以上になった割合で、確かめていない目安です。')


def render_index(now, days, rows):
    tpl = open("template.html", encoding="utf-8").read()
    head, rest = tpl.split("<!--grid-->")
    _, tail = rest.split("<!--/grid-->")
    counts = []
    for n, s in PAGES:
        rr = [x for x in rows if x["r"]["region"] == n]
        pw = len({x["r"]["id"] for x in rr for c in x["cells"] if c and c["lv"] == "powder" and c["lead"] <= 7})
        counts.append(f'<li><a href="r/{s}.html"><b>{n}</b><span class="sub">{len(rr)}か所'
                      + (f'・7日以内にパウダーの見込み {pw}か所' if pw else "") + '</span></a></li>')
    body = ('<section><h2>地域を選ぶ</h2><ul class="picks">' + "".join(counts) + "</ul></section>"
            f'<section><p class="note">{esc(accuracy())}</p></section>')
    return fill(head + body + tail, now, "全国", "", weekend_picks(rows, days, 10, ""), "今週末のおすすめ（全国）")


def fill(s, now, title, prefix, weekend, wtitle):
    return (s.replace("{{title}}", esc(title)).replace("{{nav}}", nav(prefix, title)).replace("{{wtitle}}", wtitle)
            .replace("{{updated}}", f"{now.month}/{now.day} {now:%H:%M}").replace("{{weekend}}", weekend))


def esc(s):
    return html.escape(str(s))


def render(now, days, rows, weeks, plan, onemonth, region, prefix):
    tpl = open("template.html", encoding="utf-8").read().replace("<!--grid-->", "").replace("<!--/grid-->", "")
    weekend = weekend_picks(rows, days, 5, "../")

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
            far = " ens" if c["src"] == "ens" else " far" if c["lead"] >= 4 else ""
            tip = (f'{r["name"]}（上部{round(r["base"] + (r["top"] - r["base"]) * 0.75)}m） {d.month}/{d.day}({WEEK[d.weekday()]}) 朝までの新雪 {c["new"]}cm'
                   + (f'（幅 {c["lo"]}〜{c["hi"]}cm）' if c["lo"] is not None else "")
                   + f' / 20cm以上の確率 {round(c["prob"] * 100)}%' + ("（目安）" if c["src"] == "ens" else "")
                   + f' / 朝の気温 {c["temp"]}℃' + (f' / 日中の雨 {c["rain"]}mm' if c["rain"] >= 0.5 else "")
                   + (f' / ふもとの雨 {c["low_rain"]}mm' if c["low_rain"] and c["low_rain"] >= 1 else "")
                   + (f' / 最大瞬間風速 {c["gust"]}m/s' if c["gust"] is not None and c["lead"] <= 3 else "")
                   + f' / {c["label"]}')
            tds.append(f'<td class="c lv-{c["lv"]}{we}{far}" tabindex="0" data-tip="{esc(tip)}">'
                       f'<span class="v">{c["new"] if c["new"] >= 5 or c["lv"] == "powder" else ""}</span>'
                       f'<span class="bar"><i style="height:{h}px"></i></span>'
                       f'<span class="ic">{badge(c["lv"])}</span></td>')
        am = row["amedas"]
        now_snow = (f'{r.get("amedas_name") or ""}（{r.get("amedas_alt", "")}m）積雪 {am["snow"]}cm'
                    + (f'・24時間で+{am["snow24h"]}cm' if am.get("snow24h") else "")) if am.get("snow") is not None else ""
        od = row["open"]
        if od is None:
            opening = "オープン日不明"
        else:
            how = {"last": "（去年の日から推定）", "area": "（周りのゲレンデから推定）"}.get(row["est"], "")
            cl = row["close"]
            opening = (f'{od.month}/{od.day}{"ごろ" if how else ""}〜{how}' if od > now.date()
                       else "営業終了（去年の日から推定）" if cl and now.date() > cl
                       else "営業中" + ("（推定）" if how else "") + (f'・{cl.month}/{cl.day}ごろまで' if cl and (cl - now.date()).days <= 30 else ""))
        drive = f'・東京から車{r["drive_h"]}時間' if r.get("drive_h") and r["drive_h"] <= 8 else ""
        if ri == 0 or r["area"] != rows[ri - 1]["r"]["area"]:
            body.append(f'<tr class="area" data-area="{esc(r["area"])}"><th class="name">{esc(r["area"])}</th><td colspan="{len(days)}"></td></tr>')
        wk = [c["new"] for d, c in zip(days, row["cells"]) if c and d.weekday() >= 5 and c["lv"] in ("powder", "ok")][:2]
        pw = any(c and c["lv"] == "powder" and c["lead"] <= 7 for c in row["cells"])
        body.append(f'<tr id="{r["id"]}" data-area="{esc(r["area"])}" data-i="{ri}" data-we="{max(wk, default=-1)}" data-drive="{r.get("drive_h") or 99}" data-pow="{int(pw)}">'
                    f'<th class="name"><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["name"])}</a>'
                    f'<span class="sub">{opening}{drive}{"・" + esc(now_snow) if now_snow else ""}</span></th>{"".join(tds)}</tr>')

    # 1か月先
    whead = "".join(f'<th>〜{(w + dt.timedelta(days=6)).month}/{(w + dt.timedelta(days=6)).day}</th>' for w in weeks)
    pbody = []
    for i, row in enumerate(rows):
        r = row["r"]
        if i == 0 or r["area"] != rows[i - 1]["r"]["area"]:
            pbody.append(f'<tr class="area" data-area="{esc(r["area"])}"><th class="name">{esc(r["area"])}</th><td colspan="{len(weeks)}"></td></tr>')
        tds = []
        for s in plan[r["id"]]:
            if not s:
                tds.append('<td class="na">-</td>')
                continue
            dep = f'・麓 {fmt_cm(s["depth"])}' if s["depth"] is not None else ""
            tds.append(f'<td><b>{fmt_cm(s["median"])}</b><span class="sub">少 {fmt_cm(s["low"])}{dep}</span></td>')
        st = (f'<span class="sub">麓＝{esc(r["amedas_name"])}（{r["amedas_alt"]}m・{r["amedas_km"]}km先）の積雪</span>'
              if r.get("amedas_name") and any(s and s["depth"] is not None for s in plan[r["id"]]) else "")
        pbody.append(f'<tr data-area="{esc(r["area"])}"><th class="name">{esc(r["name"])}{st}</th>{"".join(tds)}</tr>')
    om = []
    for area, v in onemonth.items():
        t = v.get("text", [])
        issued = v.get("issued", "")[:10]
        om.append(f'<div class="om"><h3>{esc(area)}地方（{esc(issued)}発表）</h3><ul>'
                  + "".join(f"<li>{esc(x)}</li>" for x in t) + "</ul></div>")

    areas = "".join(f'<option>{esc(a)}</option>' for a in dict.fromkeys(row["r"]["area"] for row in rows))
    return (fill(tpl, now, region, prefix, weekend, f"今週末のおすすめ（{region}）").replace("{{areas}}", areas)
            .replace("{{head}}", head).replace("{{body}}", "".join(body))
            .replace("{{whead}}", whead).replace("{{pbody}}", "".join(pbody)).replace("{{onemonth}}", "".join(om))
            .replace("{{accuracy}}", esc(accuracy())))


def fmt_cm(x):
    return f"{x / 100:.1f}m" if x >= 100 else f"{round(x)}cm"


def badge(lv):
    return {"powder": "❄", "ok": "○", "bad": "△", "closed": "－"}[lv]


if __name__ == "__main__":
    main()
