"""data/resorts_raw.json（surfsnow＋リフト標高）とハピスノから、本番用の resorts.json を作る。"""
import difflib, json, math, re, unicodedata
import requests

EXCLUDE = re.compile(r"ウォータージャンプ|グラススキー|KINGS|Quest|BRUSH|BAGPARK|サマーゲレンデ|SAIR|S-air|ジャンプスキー場|セラ")
JMA_AREA = {  # 気象庁の地方1か月予報（ファイル名の末尾）
    "北海道": "016000", "青森": "040000", "岩手": "040000", "宮城": "040000", "秋田": "040000", "山形": "040000", "福島": "040000",
    "新潟": "150000", "富山": "150000", "石川": "150000", "福井": "150000",
    "茨城": "130000", "栃木": "130000", "群馬": "130000", "埼玉": "130000", "千葉": "130000", "東京": "130000", "神奈川": "130000", "山梨": "130000", "長野": "130000",
    "岐阜": "230000", "静岡": "230000", "愛知": "230000", "三重": "230000",
    "滋賀": "270000", "京都": "270000", "大阪": "270000", "兵庫": "270000", "奈良": "270000", "和歌山": "270000",
    "鳥取": "340000", "島根": "340000", "岡山": "340000", "広島": "340000", "山口": "400000",
    "徳島": "370000", "香川": "370000", "愛媛": "370000", "高知": "370000",
    "福岡": "400000", "佐賀": "400000", "長崎": "400000", "大分": "400000", "熊本": "400000", "宮崎": "460100", "鹿児島": "460100"}
TOKYO = (35.6812, 139.7671)


def dist(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"（.*?）|\(.*?\)|スキー場|スノーリゾート|マウンテンリゾート|スキーリゾート|リゾート|スノーパーク|スキーパーク|\s|・|　", "", s)
    return s.lower()


def town(addr):
    """住所から市区町村まで（郡は飛ばす）。"""
    a = re.sub(r"〒?[\d-]+\s*", "", unicodedata.normalize("NFKC", addr or ""))
    m = re.search(r"(?:北海道|東京都|京都府|大阪府|.{2,3}県)(?:.{1,5}?郡)?(.{1,6}?[市町村])", a)
    return m.group(1) if m else None


def match_hapisnow(raw, hapi):
    """ハピスノの各ゲレンデを、市町村が同じで名前が一番似ている surfsnow のゲレンデ1つに対応させる。"""
    out = {}
    for h in hapi:
        cands = [(difflib.SequenceMatcher(None, norm(h["name"]), norm(r["name"])).ratio(), r["sid"])
                 for r in raw if town(r["address"]) and town(r["address"]) == town(h["address"])]
        if cands:
            score, sid = max(cands)
            if score >= 0.5 and (sid not in out or out[sid][0] < score):
                out[sid] = (score, h)
    return {k: v[1] for k, v in out.items()}


def drive_hours(points):
    out = []
    for i in range(0, len(points), 90):
        ch = points[i:i + 90]
        coords = ";".join(f"{lon},{lat}" for lat, lon in [TOKYO] + ch)
        j = requests.get(f"https://router.project-osrm.org/table/v1/driving/{coords}", params={"sources": "0"}, timeout=120).json()
        out += [None if d is None else round(d / 3600, 1) for d in j["durations"][0][1:]]
    return out


def main():
    raw = json.load(open("data/resorts_raw.json", encoding="utf-8"))
    hapi = json.load(open("tools/cache/hapisnow.json", encoding="utf-8"))
    st = json.load(open("tools/cache/snow_stations.json", encoding="utf-8"))
    stpos = {k: (v["lat"][0] + v["lat"][1] / 60, v["lon"][0] + v["lon"][1] / 60, v["alt"], v["kjName"]) for k, v in st.items()}
    hmatch = match_hapisnow(raw, hapi)
    out = []
    for r in raw:
        if EXCLUDE.search(r["name"]) or not r["lat"]:
            continue
        base, top, est = r["base"], r["top"], False
        if not top and r.get("top_lift"):
            base, top, est = r["base_lift"], r["top_lift"], True
        if not top or not base or top < base:
            continue
        pref = re.search(r"(北海道|東京|京都|大阪|[^\s\d〒-]{2,3}(?=県))", r["address"] or "")
        pref = pref.group(1) if pref else None
        if pref not in JMA_AREA:
            continue
        near = min(((dist((r["lat"], r["lon"]), p[:2]), k) for k, p in stpos.items()), default=(99, None))
        h = hmatch.get(r["sid"])
        opened = h["open"] if h and h["open"] else None
        src = "ハピスノ" if opened else None
        if not opened and r["open_surf"] and r["open_surf"].startswith("2026/") and r["open_surf"][8:].isdigit():
            opened, src = r["open_surf"].replace("/", "-"), "SURF&SNOW"
        out.append({
            "id": r["sid"], "name": r["name"], "pref": pref, "region": r["crumbs"][1] if len(r["crumbs"]) > 1 else "",
            "area": r["crumbs"][2] if len(r["crumbs"]) > 2 else "", "jma": JMA_AREA[pref],
            "lat": round(r["lat"], 5), "lon": round(r["lon"], 5), "base": base, "top": top, "elev_est": est,
            "amedas": near[1] if near[0] <= 20 else None, "amedas_km": round(near[0], 1),
            "amedas_name": stpos[near[1]][3] if near[0] <= 20 else None, "amedas_alt": stpos[near[1]][2] if near[0] <= 20 else None,
            "open": opened, "open_src": src, "open_last": r["open_surf"] if not opened else None,
            "url": (h or {}).get("official") or f"https://surfsnow.jp/guide/htm/{r['sid']}s.htm",
            "url_kind": "公式" if h and h.get("official") else "SURF&SNOW",
            "courses": r["courses"], "max_slope": r["max_slope"], "longest": r["longest"]})
    for r, hrs in zip(out, drive_hours([(r["lat"], r["lon"]) for r in out])):
        r["drive_h"] = hrs
    json.dump(out, open("resorts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(len(out), "か所", "観測点あり", sum(1 for r in out if r["amedas"]), "標高推定", sum(r["elev_est"] for r in out),
          "オープン日あり", sum(1 for r in out if r["open"]), "公式URL", sum(r["url_kind"] == "公式" for r in out),
          "ハピスノ一致", sum(r["open_src"] == "ハピスノ" for r in out))


if __name__ == "__main__":
    main()
