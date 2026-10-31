"""気象庁「過去の気象データ」からアメダスの日別値を取る（初回だけ使う）。
出力: data/obs/<観測点番号>.csv（date,prec,tavg,tmax,tmin,wind_gust,snowfall,snow_depth）"""
import csv, html, os, re, sys, time
import requests

STATIONS = {  # アメダス番号: (府県番号, etrnの番号)
    "48141": (48, "0396"), "48031": (48, "0393"), "48061": (48, "0394"),
    "48066": (48, "0395"), "48191": (48, "0399"), "48216": (48, "0992"),
    "54841": (54, "0544"), "54816": (54, "0998"), "54836": (54, "0543"),
    "54676": (54, "0537"), "54616": (54, "0534"), "54661": (54, "0536"),
}
MONTHS = [11, 12, 1, 2, 3, 4]
URL = "https://www.data.jma.go.jp/obd/stats/etrn/view/daily_{k}1.php?prec_no={p}&block_no={b}&year={y}&month={m}&day=&view="
# 列の位置（a1＝アメダス、s1＝気象台）
COLS = {"a": dict(n=18, prec=1, tavg=4, tmax=5, tmin=6, gust=12, snowfall=16, depth=17),
        "s": dict(n=21, prec=3, tavg=6, tmax=7, tmin=8, gust=14, snowfall=17, depth=18)}


def num(s):
    if s.strip() == "--":  # 気象台の表で「現象なし」
        return 0.0
    s = re.sub(r"[)\]\s×#]", "", s)
    try:
        return float(s)
    except ValueError:
        return ""


def month_rows(p, b, y, m, k="a"):
    r = requests.get(URL.format(p=p, b=b, y=y, m=m, k=k), timeout=30)
    r.encoding = "utf-8"
    t = r.text[max(r.text.find('id="tablefix1"'), r.text.find("id='tablefix1'")):]
    t = t[: t.find("</table>")]
    out = []
    for row in re.findall(r"<tr.*?</tr>", t, re.S):
        c = [html.unescape(re.sub("<.*?>", "", x)).strip() for x in re.findall(r"<t[hd].*?</t[hd]>", row, re.S)]
        q = COLS[k]
        if len(c) == q["n"] and c[0].isdigit():
            out.append({"date": f"{y}-{m:02d}-{int(c[0]):02d}", "prec": num(c[q["prec"]]), "tavg": num(c[q["tavg"]]),
                        "tmax": num(c[q["tmax"]]), "tmin": num(c[q["tmin"]]), "wind_gust": num(c[q["gust"]]),
                        "snowfall": num(c[q["snowfall"]]), "snow_depth": num(c[q["depth"]])})
    return out


def main(first=2016, last=2025):
    for st, (p, b) in STATIONS.items():
        path = f"data/obs/{st}.csv"
        rows = []
        for season in range(first, last + 1):
            for m in MONTHS:
                y = season if m >= 11 else season + 1
                rows += month_rows(p, b, y, m)
                pass
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(st, len(rows), flush=True)


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
