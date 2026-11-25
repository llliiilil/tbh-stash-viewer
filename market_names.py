"""新物品的市场名解析：ItemKey -> Steam market hash_name。

价格表里的 hash 是早期用全量匹配器建出来的；游戏更新后新加的物品没有条目，
查看器就既没最低价也没中位价。这里按需补：
  1) 从游戏本地化 bundle 读英文物品名（items_zh.csv 只有中文，市场名是英文）；
  2) 去市场搜索页 q=<英文名>，把返回的 hash 用同一套「英文名 + 品阶 + 品质尾缀」
     规则反查回 ItemKey，能对上就是这一件的市场名；
  3) 命中与最近尝试时间缓存在 market_names.json，失败的 3 天内不重复试。

只服务「仓库/背包里新出现、且可交易」的物品，一次少量（一张页面约 645KB）。
"""
import csv
import json
import os
import re
import struct
import time

BASE = os.path.dirname(os.path.abspath(__file__))
# 游戏安装目录（可用环境变量 TBH_GAME_DIR 覆盖）：只用来读本地化 bundle 里的英文物品名
GAME_DIR = os.environ.get("TBH_GAME_DIR") or \
    r"D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"
AA = os.path.join(GAME_DIR, "StreamingAssets", "aa", "StandaloneWindows64")
CACHE_PATH = os.path.join(BASE, "market_names.json")
EN_CACHE = os.path.join(BASE, "en_names_cache.json")
GRADE_TOKENS = {"common": "COMMON", "uncommon": "UNCOMMON", "rare": "RARE",
                "arcana": "ARCANA", "divine": "DIVINE", "legendary": "LEGENDARY",
                "immortal": "IMMORTAL", "beyond": "BEYOND", "celestial": "CELESTIAL",
                "cosmic": "COSMIC"}
RETRY_SECONDS = 3 * 86400         # 解析失败后 3 天内不再重复请求
_CACHE = None


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower()) if s else ""


def _parse_table(raw):
    n = len(raw)
    for start in range(0, min(n, 4096)):
        (cnt,) = struct.unpack_from("<i", raw, start)
        if not (100 <= cnt <= 20000):
            continue
        off = start + 4
        entries = []
        try:
            for _ in range(cnt):
                if off + 12 > n:
                    raise ValueError
                (eid,) = struct.unpack_from("<q", raw, off)
                off += 8
                (ln,) = struct.unpack_from("<i", raw, off)
                if ln < 0 or off + 4 + ln > n:
                    raise ValueError
                s = raw[off + 4:off + 4 + ln].decode("utf-8")
                off += 4 + ln + (-ln) % 4
                (mcount,) = struct.unpack_from("<i", raw, off)
                if mcount != 0:
                    raise ValueError
                off += 4
                entries.append((eid, s))
        except Exception:
            continue
        if entries and off <= n:
            return entries
    return []


def _get_raw(path, name):
    import UnityPy
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            d = obj.read(check_read=False)
            if getattr(d, "m_Name", "") == name:
                return bytes(obj.get_raw_data())
        except Exception:
            pass
    return None


def en_names(log=print):
    """NameKey -> 英文物品名（首次读 bundle 后缓存成 json）。"""
    if os.path.exists(EN_CACHE):
        try:
            return json.load(open(EN_CACHE, encoding="utf-8"))
        except Exception:
            pass
    bundle = os.path.join(AA, "localization-string-tables-english(unitedstates)(en-us)_assets_all.bundle")
    shared_bundle = os.path.join(AA, "localization-assets-shared_assets_all.bundle")
    en_entries = dict(_parse_table(_get_raw(bundle, "ItemTable_en-US")))
    shared = dict(_parse_table(_get_raw(shared_bundle, "ItemTable Shared Data")))
    names = {shared[i]: v for i, v in en_entries.items() if i in shared}
    try:
        json.dump(names, open(EN_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    log(f"  [names] 本地化英文名 {len(names)} 条（已缓存）")
    return names


def item_rows():
    """ItemKey -> {NameKey, GRADE, marketable}"""
    rows = {}
    for r in csv.DictReader(open(os.path.join(BASE, "csv", "ItemInfoData.csv"),
                                encoding="utf-8-sig")):
        rows[r["ItemKey"]] = {"NameKey": r["NameKey"].strip(), "GRADE": r["GRADE"],
                              "marketable": r.get("IsCanExchangeMarketable") == "True"}
    return rows


_INDEX = None


def _index(log=print):
    """(英文名, 品阶) -> ItemKey，以及英文名 -> ItemKey。"""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    names = en_names(log)
    rows = item_rows()
    by_name_grade, by_name = {}, {}
    for k, r in rows.items():
        en = names.get(r["NameKey"])
        if not en:
            continue
        key = norm(en)
        by_name_grade.setdefault((key, r["GRADE"]), []).append(k)
        by_name.setdefault(key, []).append(k)
    _INDEX = (by_name_grade, by_name)
    return _INDEX


def _split_hash(hash_name):
    """市场名 -> (规整后的英文基名, 品阶或 None)。"Eternal Bow (Legendary) C" -> ("eternalbow", "LEGENDARY")"""
    s = re.sub(r"\s+[A-Za-z]\s*$", "", hash_name).strip()
    grade = None
    for m in re.finditer(r"\((\w+)\)", s):
        g = GRADE_TOKENS.get(m.group(1).lower())
        if g:
            grade = g
            s = s[:m.start()] + s[m.end():]
    return norm(s), grade


def _match(hash_name, log=print):
    """市场 hash_name -> ItemKey（与旧匹配器同一套规则）。"""
    by_name_grade, by_name = _index(log)
    base, grade = _split_hash(hash_name)
    cands = by_name_grade.get((base, grade)) if grade else None
    if not cands:
        cands = by_name.get(base)
    return cands[0] if cands else None


def _load_cache():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        _CACHE = json.load(open(CACHE_PATH, encoding="utf-8"))
    except Exception:
        _CACHE = {}
    _CACHE.setdefault("names", {})
    _CACHE.setdefault("tried", {})
    return _CACHE


def _save_cache():
    try:
        json.dump(_load_cache(), open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def known_hash(item_key):
    return _load_cache()["names"].get(str(item_key))


def resolve_one(item_key, log=print):
    """单件解析：返回市场 hash_name 或 None（成功/失败都会写缓存）。"""
    import update_prices as up                     # 延迟导入，避免循环依赖
    c = _load_cache()
    rows = item_rows()
    r = rows.get(str(item_key)) or {}
    if not r:
        return None                      # csv 里没这条：先不记时间，等数据更新后再说
    if not r.get("marketable"):
        # 不可交易物品本来就没有市场名：记一次时间，否则每轮都会占掉 5 个名额里的一格
        c["tried"][str(item_key)] = time.time()
        _save_cache()
        return None
    en = en_names(log).get(r.get("NameKey"))
    if not en:
        c["tried"][str(item_key)] = time.time()
        _save_cache()
        return None
    ck = up.get_auth(log).cookies()
    if not ck:
        return None
    proxy = os.environ.get("STEAM_PROXY", up.PROXY)
    try:
        import httpx
        with httpx.Client(timeout=40, proxy=proxy or None,
                          headers=getattr(up, "MARKET_HEADERS", up.HEADERS),
                          cookies=ck) as cl:
            resp = cl.get(up.MARKET_SEARCH_URL,
                          params={"appid": up.APPID, "q": en, "count": up.MARKET_PAGE_SIZE, "start": 0})
    except Exception as e:
        log(f"  [names] {en} 搜索失败（{type(e).__name__}）")
        return None
    c["tried"][str(item_key)] = time.time()
    if resp.status_code != 200:
        _save_cache()
        return None
    _, items = up.parse_market_page(resp.text)
    want_base = norm(en)
    want_grade = r.get("GRADE")
    cands = []
    for x in items or []:
        base, grade = _split_hash(x["hash"])
        if base != want_base:
            continue
        if want_grade and grade and grade != want_grade:
            continue                        # 同名不同品阶：不是这件
        cands.append((x["cents"] or 10 ** 9, x["hash"]))
    hits = [h for _c, h in sorted(cands)]   # 同名多版本（A/B/C）时取最便宜的那个
    if hits:
        c["names"][str(item_key)] = hits[0]
        log(f"  [names] {item_key} → {hits[0]}")
    _save_cache()
    return hits[0] if hits else None


def resolve_missing(item_keys, limit=8, log=print):
    """给一批 ItemKey 解析市场名，返回 {ItemKey: hash}。带上缓存与重试节流。"""
    c = _load_cache()
    got = {}
    done = 0
    for k in item_keys:
        k = str(k)
        if k in c["names"]:
            got[k] = c["names"][k]
            continue
        if time.time() - (c["tried"].get(k) or 0) < RETRY_SECONDS:
            continue
        if done >= limit:
            break
        done += 1
        h = resolve_one(k, log)
        if h:
            got[k] = h
        if done:
            time.sleep(2)
    return got
