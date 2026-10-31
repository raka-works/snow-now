"""全国のスキー場の一覧を作る（surfsnow のゲレンデページ＋ハピスノの営業期間）。シーズン前に1回使う。
出力: data/resorts_raw.json"""
import json, re, html
from concurrent.futures import ThreadPoolExecutor
import requests

H = {"User-Agent": "Mozilla/5.0 (snow-now personal)"}


def text_of(t):
    t = re.sub(r"<script(?![^>]*ld\+json).*?</script>|<style.*?</style>", "", t, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def num(s):
    return int(s.replace(",", "")) if s else None


def surf(rid):
    r = requests.get(f"https://surfsnow.jp/guide/htm/{rid}s.htm", headers=H, timeout=60)
    r.encoding = "utf-8"
    t = r.text
    if r.status_code != 200 or "404 Not Found" in t[:3000]:
        return None
    name = re.search(r"<title>(.*?) ‐", t)
    geo = re.search(r'"latitude":\s*"([\d.]+)",\s*"longitude":\s*"([\d.]+)"', t)
    x = text_of(t)
    g = lambda p: (re.search(p, x) or [None, None])[1]
    crumbs = re.findall(r'"name":"([^"]+)","item"', t)
    return {
        "sid": rid, "name": html.unescape(name.group(1)) if name else rid,
        "lat": float(geo.group(1)) if geo else None, "lon": float(geo.group(2)) if geo else None,
        "top": num(g(r"標高 ([\d,]+)m PEAK")), "base": num(g(r"PEAK ([\d,]+)m BASE")),
        "address": g(r"住所 (〒?[\d-]* ?\S+?[都道府県]\S*)"),
        "open_surf": g(r"オープン予定日 (\d{4}/\d{2}/(?:\d{2}|上旬|中旬|下旬))"),
        "close_surf": g(r"クローズ予定日 (\d{4}/\d{2}/(?:\d{2}|上旬|中旬|下旬))"),
        "courses": num(g(r"コース数 (\d+)")), "max_slope": num(g(r"最大斜度 (\d+)度")),
        "longest": num(g(r"最長滑走距離 ([\d,]+)m")), "crumbs": crumbs[:6],
        "closed_note": "営業を休止" in x or "閉鎖" in x[:3000],
    }


def main():
    ids = open("tools/cache/guide_ids.txt").read().split()
    with ThreadPoolExecutor(6) as ex:
        res = [r for r in ex.map(surf, ids) if r]
    json.dump(res, open("data/resorts_raw.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(len(res), "件", sum(1 for r in res if r["lat"]), "座標あり", sum(1 for r in res if r["top"]), "標高あり")


if __name__ == "__main__":
    main()
