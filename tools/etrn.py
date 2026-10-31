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
URL = "https://www.data.jma.go.jp/obd/stats/etrn/view/daily_a1.php?prec_no={p}&block_no={b}&year={y}&month={m}&day=&view="


def num(s):
    s = re.sub(r"[)\]\s×#]", "", s)
    try:
        return float(s)
    except ValueError:
        return ""


def month_rows(p, b, y, m):
    r = requests.get(URL.format(p=p, b=b, y=y, m=m), timeout=30)
    r.encoding = "utf-8"
    t = r.text[r.text.find('id="tablefix1"'):]
    t = t[: t.find("</table>")]
    out = []
    for row in re.findall(r"<tr.*?</tr>", t, re.S):
        c = [html.unescape(re.sub("<.*?>", "", x)).strip() for x in re.findall(r"<t[hd].*?</t[hd]>", row, re.S)]
        if len(c) == 18 and c[0].isdigit():
            out.append({"date": f"{y}-{m:02d}-{int(c[0]):02d}", "prec": num(c[1]), "tavg": num(c[4]),
                        "tmax": num(c[5]), "tmin": num(c[6]), "wind_gust": num(c[12]),
                        "snowfall": num(c[16]), "snow_depth": num(c[17])})
    return out


def main(first=2016, last=2025):
    for st, (p, b) in STATIONS.items():
        path = f"data/obs/{st}.csv"
        rows = []
        for season in range(first, last + 1):
            for m in MONTHS:
                y = season if m >= 11 else season + 1
                rows += month_rows(p, b, y, m)
                time.sleep(0.6)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(st, len(rows), flush=True)


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
