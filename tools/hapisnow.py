"""ハピスノの各ゲレンデページから、2026-27の営業期間と公式サイトを取る。"""
import json, re, html
from concurrent.futures import ThreadPoolExecutor
import requests

H = {"User-Agent": "Mozilla/5.0 (snow-now personal)"}


def one(u):
    r = requests.get(u, headers=H, timeout=60); r.encoding = "utf-8"; t = r.text
    x = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S))))
    name = re.search(r"<title>(.*?)[【|｜]", t)
    period = re.search(r"営業期間 (\d{4})年(\d{1,2})月(\d{1,2})日\s*[〜～~]\s*(\d{4})年(\d{1,2})月(\d{1,2})日", x)
    off = re.search(r'href="(https?://(?!hapisnow)[^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*公式サイト', t)
    addr = re.search(r"住所 〒?[\d-]* ?(\S+)", x)
    return {"url": u, "name": html.unescape(name.group(1)).strip() if name else None,
            "open": f"{period[1]}-{int(period[2]):02d}-{int(period[3]):02d}" if period else None,
            "close": f"{period[4]}-{int(period[5]):02d}-{int(period[6]):02d}" if period else None,
            "official": off.group(1) if off else None, "address": addr.group(1) if addr else None}


def main():
    urls = open("tools/cache/hapi_urls.txt").read().split()
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, urls))
    json.dump(res, open("tools/cache/hapisnow.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    for r in res:
        print(r["name"], r["open"], r["official"], r["address"])


if __name__ == "__main__":
    main()
