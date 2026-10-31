"""パウダーの見込みが出たら Discord に送る。同じ日・同じゲレンデは確度が上がったときだけ送り直す。"""
import datetime as dt
import json
import os

import requests

URL = "https://raka-works.github.io/snow-now/"
WEEK = "月火水木金土日"
# 全国だと冬の北海道で毎日鳴るので、通知は東京から行く地域に絞る（ページは全国）
NOTIFY_REGIONS = ("関東甲信越", "北陸", "東北")
PER_DAY = 6


def main():
    hook = os.environ.get("DISCORD_WEBHOOK")
    s = json.load(open("data/summary.json", encoding="utf-8"))
    resorts = {r["id"]: r for r in json.load(open("resorts.json", encoding="utf-8"))}
    names = {k: r["name"] for k, r in resorts.items()}
    sent = json.load(open("data/notified.json", encoding="utf-8")) if os.path.exists("data/notified.json") else {}
    today = dt.date.fromisoformat(s["made"][:10])
    sent = {k: v for k, v in sent.items() if dt.date.fromisoformat(k.split("|")[1]) >= today}
    by_day = {}
    for row in s["rows"]:
        for c in row["cells"]:
            if not c or c["lv"] != "powder" or resorts[row["id"]]["region"] not in NOTIFY_REGIONS:
                continue
            d = dt.date.fromisoformat(c["date"])
            # 3日先までは確定扱い。4〜7日先は土日だけ「予告」として送る
            if c["lead"] <= 3:
                kind = "見込み"
            elif c["lead"] <= 7 and d.weekday() >= 5:
                kind = "予告"
            else:
                continue
            key = f'{row["id"]}|{c["date"]}'
            if sent.get(key) == "見込み" or sent.get(key) == kind:
                continue
            sent[key] = kind
            head = f'{d.month}/{d.day}({WEEK[d.weekday()]})の朝・{kind}'
            by_day.setdefault((c["date"], head), []).append((c["new"], f'{names[row["id"]]} {c["new"]}cm'))
    if by_day:
        lines = [f"{h}：" + "、".join(x for _, x in sorted(v, reverse=True)[:PER_DAY]) for (_, h), v in sorted(by_day.items())]
        msg = "❄ パウダーの見込み（新雪）\n" + "\n".join(lines) + f"\n{URL}"
        if hook:
            requests.post(hook, json={"content": msg}, timeout=30).raise_for_status()
        print(msg)
    json.dump(sent, open("data/notified.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
