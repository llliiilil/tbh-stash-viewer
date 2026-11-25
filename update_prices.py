"""Steam 市场价格维护（TaskbarHero 3678970）。

单一节奏（由 stash_viewer 调用）:
  仓库/背包物品：每 2 小时一轮（fast_refresh），只刷仓库/背包/材料库里现存的物品；
  新进仓库的物品因为「最久没更新优先」会在下一轮自然被补上。
  逐件 priceoverview(currency=23)；被 429 限流且已登录时，改用市场搜索页按 q= 关键字补。
  全量扫描已删除：Steam 的列表 JSON 接口整站下线，页面又只给固定前 100 件，无法枚举全市场。

币种说明（2026-11-25）:
  priceoverview 支持 currency 参数，匿名即可拿到人民币价（currency=23），不依赖登录。
  被 429 限流时改用「市场搜索页」按 q= 关键字取价，解析页面内嵌状态里的 eCurrency/strMinSellSubtotal。
  已登录（steam_login.py 登录一次，长效令牌自动续期）时市场页取价走登录态，币种同样是人民币。

CLI: python update_prices.py   （跑一轮：只刷仓库/背包物品）

登录态（steam_cookies.json，可选）:
  {"refresh_token": "<steamRefresh_steam cookie 值>"}  ← 长效令牌（~200 天）
  或 {"steamLoginSecure": "...", "sessionid": "..."}   ← 旧格式（~24h 手拷）
  登录不会给额外额度：priceoverview 带登录同样 429，只影响市场页兜底是否可用。
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

if sys.stdout is not None:  # pythonw 下 stdout 为 None，直接 reconfigure 会崩
    sys.stdout.reconfigure(encoding="utf-8")
import httpx

APPID = 3678970
BASE = os.path.dirname(os.path.abspath(__file__))
PROXY = "http://127.0.0.1:7890"
OUT_PATH = os.path.join(BASE, "item_prices.json")
FAST_INTERVAL = 2 * 3600      # 仓库/背包的人民币价：每 2 小时一轮
PAGE_SLEEP = 2                # 市场页之间的间隔
RATELIMIT_SLEEP = 90          # 429 冷却 90/120/150s 递增
CURRENCY = 23                 # 人民币（priceoverview 支持 currency 参数，匿名可用）
# 旧注释判断为「出口 IP + 游戏(appid)」限流，已被 11-25 的对照实验推翻（见 OVERVIEW_HEADERS）。
# 6 秒间隔保留：1001 件一轮约 100 分钟，仓库/背包物品排最前；更快的节奏未验证。
OVERVIEW_MIN_GAP = 6.0
# 2026-11-25 实测：换出口节点后 429 不变；同一出口、同一时刻交替发请求，
# 「浏览器 UA + 无登录 Cookie」12 件全部 429，「curl/8.9.0」UA 8 件全部 200。
# 即被限的是请求头特征而非 IP。priceoverview 专用 OVERVIEW_HEADERS（curl UA）；
# HEADERS 仍供列表接口使用。
OVERVIEW_HEADERS = {"User-Agent": "curl/8.9.0"}
# 11-25 实测：同一出口连续成功约 500 件后被 429，52 分钟后仍未恢复（周期长于 1 小时，未能测出）。
# 因此连续撞 429 时逐级拉长等待：5 → 15 → 30 → 60 分钟，一旦成功复位。
OVERVIEW_COOLDOWNS = (300, 900, 1800, 3600, 3600)
OVERVIEW_COOLDOWN = OVERVIEW_COOLDOWNS[0]   # 首次 429 的冷却秒数（悬停与后台刷新共用）
_ov_streak = [0]               # 连续 429 次数（成功即清零）
SAVE_EVERY = 40               # 人民币刷新每更新 N 件落一次盘，查看器随落盘重载

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://steamcommunity.com/market/",
}
# 11-25 实测：市场搜索页与 priceoverview 是同一条规则——浏览器 UA 直接 429 / ReadTimeout，
# curl UA 200（一页约 3.7MB，热身后 1.5 秒）。页面兜底与按名解析一律用这套头。
MARKET_HEADERS = {"User-Agent": "curl/8.9.0", "Accept-Language": "en-US,en;q=0.9"}


# ---------- 人民币行情（priceoverview，匿名，进程内全局限速） ----------
_ov_lock = threading.Lock()
_ov_next = [0.0]              # 下一次允许发请求的 monotonic 时刻
_ov_cooldown = [0.0]          # 429 冷却截止（monotonic）


def overview_wait():
    """priceoverview 串行限速。持锁等待，所有调用方（后台刷新/悬停）自然排队；
    处于 429 冷却时立即返回 False。"""
    with _ov_lock:
        now = time.monotonic()
        if now < _ov_cooldown[0]:
            return False
        wait = _ov_next[0] - now
        if wait > 0:
            time.sleep(wait)
        _ov_next[0] = time.monotonic() + OVERVIEW_MIN_GAP
        return True


def overview_in_cooldown():
    """priceoverview 是否处于 429 冷却期。"""
    with _ov_lock:
        return time.monotonic() < _ov_cooldown[0]


def overview_cooldown_left():
    """429 冷却剩余秒数（不在冷却中返回 0）。"""
    with _ov_lock:
        return max(0.0, _ov_cooldown[0] - time.monotonic())


def _cents(txt):
    """'¥ 0.57' / '$1,179.95' → 57 / 117995（该币种的最小单位）；解析不了返回 None。"""
    m = re.search(r"[\d][\d,]*(?:\.\d+)?", txt or "")
    return int(round(float(m.group(0).replace(",", "")) * 100)) if m else None


def cny_overview(hname, log=None):
    """匿名拉单件人民币行情（不依赖登录态）。
    返回 dict{cny_cents 最低挂牌, cny_median_cents 24h成交中位, cny_volume 24h成交量}；
    无挂牌/接口无数据返回 None；限流冷却中返回 "cooldown"。"""
    if not overview_wait():
        return "cooldown"
    with httpx.Client(timeout=15, headers=OVERVIEW_HEADERS,
                      proxy=os.environ.get("STEAM_PROXY", PROXY) or None) as cl:
        r = cl.get("https://steamcommunity.com/market/priceoverview/",
                   params={"appid": APPID, "currency": CURRENCY, "market_hash_name": hname})
    if r.status_code == 429:
        with _ov_lock:
            _ov_streak[0] += 1
            cd = OVERVIEW_COOLDOWNS[min(_ov_streak[0], len(OVERVIEW_COOLDOWNS)) - 1]
            _ov_cooldown[0] = max(_ov_cooldown[0], time.monotonic() + cd)
        if log:
            log(f"  priceoverview 429（连续第 {_ov_streak[0]} 次），冷却 {cd // 60} 分钟")
        return "cooldown"
    with _ov_lock:
        _ov_streak[0] = 0
    if r.status_code == 404:
        return None                  # 该市场名在市场上已不存在（改名/下架），不是故障
    if r.status_code != 200:
        log and log(f"  priceoverview HTTP {r.status_code}")
        return None
    d = r.json()
    if not isinstance(d, dict) or not d.get("success"):
        return None
    low, med = _cents(d.get("lowest_price")), _cents(d.get("median_price"))
    if low is None and med is None:
        return None
    return {"cny_cents": low if low is not None else med,
            "cny_median_cents": med,
            "cny_volume": int(re.sub(r"\D", "", d.get("volume", "") or "") or 0),
            "cny_ts": time.time()}


def _jwt_claims(token):
    """解码 JWT payload（不验签），取 exp/sub 等声明。"""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


class SteamAuth:
    """登录态管理：steamRefresh_steam 长效令牌（~200 天）自动换发
    steamLoginSecure 访问令牌（~24h JWT）。也兼容手拷 steamLoginSecure 的旧格式。
    换发走浏览器同款 GET login.steampowered.com/jwt/refresh（302 + Set-Cookie，
    跨域 cookie 需从 Set-Cookie 头直接提取，不能用 cookie jar）。"""

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    TOKEN_CACHE = os.path.join(BASE, "steam_token_cache.json")

    def __init__(self, log=print):
        self.log = log
        self._refresh = ""      # 长效令牌 JWT（steamRefresh_steam）
        self._steamid = ""
        self._sessionid = ""
        self._access = ""       # steamLoginSecure 的值（steamid||accessJWT）
        self._exp = 0.0
        self._load_config()
        self._load_cache()

    def _load_config(self):
        cpath = os.path.join(BASE, "steam_cookies.json")
        if not os.path.exists(cpath):
            return
        try:
            c = json.load(open(cpath, encoding="utf-8"))
        except Exception as e:
            self.log(f"  [auth] steam_cookies.json 解析失败: {e}")
            return
        self._sessionid = c.get("sessionid", "") or ""
        rt = c.get("refresh_token") or c.get("steamRefresh_steam") or ""
        if rt:
            self._refresh = rt.strip()       # 原样保存：有的带「steamid||」前缀，有的不带，发请求时不要改写
            self._steamid = _jwt_claims(self._refresh).get("sub", "")
            exp = _jwt_claims(self._refresh).get("exp")
            self.log("  [auth] 检测到长效令牌（refresh）"
                     + (f"，有效至 {time.strftime('%Y-%m-%d', time.localtime(exp))}"
                        if exp else ""))
            # steam_login.py 登录时一并保存的访问令牌：仍有效就直接用，不必每次启动都换发
            self._access = c.get("steamLoginSecure", "") or ""
            self._exp = _jwt_claims(self._access.split("||")[-1]).get("exp", 0) if self._access else 0
        else:
            self._access = c.get("steamLoginSecure", "") or ""
            self._exp = _jwt_claims(self._access.split("||")[-1]).get("exp", 0)
            if self._access:
                self.log("  [auth] 使用客户端提取的 steamLoginSecure，有效至 "
                         + time.strftime("%m-%d %H:%M", time.localtime(self._exp)))

    def _load_cache(self):
        try:
            j = json.load(open(self.TOKEN_CACHE, encoding="utf-8"))
            if time.time() < j.get("exp", 0) - 600:
                self._access = j.get("access", "")
                self._exp = j.get("exp", 0)
        except Exception:
            pass

    def _save_cache(self):
        try:
            json.dump({"access": self._access, "exp": self._exp},
                      open(self.TOKEN_CACHE, "w", encoding="utf-8"))
        except Exception:
            pass

    def cookies(self):
        """返回可用登录 cookie；长效令牌走 /jwt/refresh 换发，客户端提取的令牌
        临期则重启 steamwebhelper 让 Steam 重发。无配置返回 {}。"""
        if not (self._refresh or self._access):
            return {}
        if self._refresh and time.time() > self._exp - 600:
            try:
                self._mint()
            except Exception as e:
                self.log(f"  [auth] 换发失败（沿用旧令牌）: {e}")
        elif self._access and time.time() > self._exp - 600:
            self._renew_client()
        return {"steamLoginSecure": self._access} if self._access else {}

    def _renew_client(self):
        """steamwebhelper 被 Steam 杀掉后会自动重启并重发 steamLoginSecure。
        杀 webhelper → 轮询重跑提取脚本，直到拿到临期 10 分钟以上的新令牌。"""
        extractor = os.path.join(BASE, "extract_steam_token.py")
        if not os.path.exists(extractor):
            self.log("  [auth] 续期脚本缺失（extract_steam_token.py），沿用旧令牌")
            return
        self.log("  [auth] 会话临期 → 重启 steamwebhelper 以重发登录态")
        os.system("taskkill /F /IM steamwebhelper.exe >nul 2>&1")
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(4)
            subprocess.run([sys.executable, "-X", "utf8", extractor], timeout=90)
            self._load_config()
            if self._access and time.time() < self._exp - 600:
                self.log("  [auth] 已拿到新会话，有效至 "
                         + time.strftime("%m-%d %H:%M", time.localtime(self._exp)))
                return
        self.log("  [auth] 续期超时，沿用旧令牌")

    @staticmethod
    def _get(cl, url, params=None, cookies=None):
        """带重试的 GET（经代理偶发 TLS 中断）。"""
        for i in range(3):
            try:
                return cl.get(url, params=params, cookies=cookies)
            except Exception as e:
                if i == 2:
                    raise
                time.sleep(2)

    def _mint(self):
        """用长效令牌换新 access token；长效令牌若轮换则写回配置文件续命。"""
        proxy = os.environ.get("STEAM_PROXY", PROXY)
        headers = {"User-Agent": self.UA,
                   "Referer": "https://steamcommunity.com/",
                   "Origin": "https://steamcommunity.com"}
        rt_cookie = self._refresh
        with httpx.Client(proxy=proxy or None, timeout=20, headers=headers,
                          follow_redirects=False) as cl:
            r = self._get(cl, "https://login.steampowered.com/jwt/refresh",
                          {"redir": "https://steamcommunity.com/market/"},
                          {"steamRefresh_steam": rt_cookie})
            access = refresh_new = ""

            def take(resp):                  # 跨域 cookie 从 Set-Cookie 头直接提取（不能用 cookie jar）
                nonlocal access, refresh_new
                for sc in resp.headers.get_list("set-cookie"):
                    k, _, v = sc.partition("=")
                    v = urllib.parse.unquote(v.split(";", 1)[0])
                    if k == "steamLoginSecure":
                        access = v
                    elif k == "steamRefresh_steam":
                        refresh_new = v

            take(r)
            # 2026-11-25 实测：第一跳只回 302 → steamcommunity.com/login/settoken?steamID=…&nonce=…，
            # 新的 steamLoginSecure 在第二跳的 Set-Cookie 里。只看第一跳永远拿不到（旧 bug）。
            if not access and r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                take(self._get(cl, r.headers["location"]))
        if not access:
            raise RuntimeError(f"HTTP {r.status_code}，未从 Set-Cookie 拿到 steamLoginSecure")
        self._access = access
        self._exp = _jwt_claims(access.split("||")[-1]).get("exp", time.time() + 3600)
        self._save_cache()
        self.log("  [auth] steamLoginSecure 已自动换发，有效至 "
                 + time.strftime("%m-%d %H:%M", time.localtime(self._exp)))
        if refresh_new and refresh_new != rt_cookie:
            self._rotate_refresh(refresh_new)

    def _rotate_refresh(self, new_rt):
        """Steam 轮换长效令牌时写回 steam_cookies.json，保持 200 天链不断。"""
        cpath = os.path.join(BASE, "steam_cookies.json")
        try:
            c = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
            for k in ("refresh_token", "steamRefresh_steam"):
                if k in c:
                    c[k] = new_rt
            json.dump(c, open(cpath, "w", encoding="utf-8"))
            self._refresh = new_rt.strip()
            self.log("  [auth] 长效令牌已轮换并写回 steam_cookies.json")
        except Exception as e:
            self.log(f"  [auth] 长效令牌写回失败: {e}")


_AUTH = None


def get_auth(log=print):
    global _AUTH
    if _AUTH is None:
        _AUTH = SteamAuth(log=log)
    return _AUTH


def load_state():
    """整份 item_prices.json（ prices + 元信息）；读不到给空壳。"""
    try:
        return json.load(open(OUT_PATH, encoding="utf-8"))
    except Exception:
        return {"prices": {}, "market_total": 0, "unknown_count": 0}


def save_prices(prices, market_total, unknown_count, mode):
    """落盘。full_ts 记录最近一次成功全量扫描的时刻（人民币刷新不改它），
    查看器用它判断何时该做下一次全量。"""
    full_ts = time.time()
    if mode != "full":
        try:
            full_ts = json.load(open(OUT_PATH, encoding="utf-8")).get("full_ts") or 0
        except Exception:
            full_ts = 0
    payload = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ts": time.time(),
        "full_ts": full_ts,
        "currency": "CNY",
        "market_total": market_total,
        "matched": len(prices),
        "mode": mode,
        "unknown_count": unknown_count,
        "prices": prices,
    }
    tmp = OUT_PATH + ".tmp"
    json.dump(payload, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, OUT_PATH)          # 原子替换：查看器重载时不会读到半个文件


LAST_RUN = {"result": "idle", "updated": 0}   # 最近一轮刷新的结果：done/cooldown/stopped/failed
MARKET_STATS = {"tried": 0}                   # 最近一次市场页兜底实际发起了几件


MARKET_SEARCH_URL = "https://steamcommunity.com/market/search"
MARKET_PAGE_SIZE = 100          # 市场搜索页每页条数（实测 100 可用；全市场约 12 次请求）


def parse_market_page(text):
    r'''解析市场搜索页里内嵌的清单状态（2026-11-25 实测；旧的 search/render JSON 接口已 404）。
    页面脚本里有一段转义过的 JSON：
      {\"pages\":[{\"start\":0,\"total_count\":1104,…,\"results\":[{\"strHash\":\"…\",
       \"cSellOrders\":1245,\"eCurrency\":23,\"strMinSellSubtotal\":\"CN¥3.83\",
       \"asset_description\":{…}}, …]}]}
    返回 (total_count, [item])，item = {hash, listings, currency, cents, price_text}；
    页面结构变了或被拦时返回 (None, [])。'''
    m = re.search(r'\\"pages\\":\[\{\\"start\\":\d+,\\"total_count\\":(\d+)', text)
    if not m:
        return None, []
    items = []
    head = '{\\"strHash\\":\\"'
    for mo in re.finditer(re.escape(head), text):
        chunk = text[mo.end(): mo.end() + 4000]
        cut = chunk.find('\\"asset_description\\"')
        if cut < 0:
            continue
        chunk = chunk[:cut]
        mh = re.match(r'(?P<hash>.*?)\\",\\"cSellOrders\\":(?P<so>\d+)', chunk)
        if not mh:
            continue
        cur = re.search(r'\\"eCurrency\\":(\d+)', chunk)
        mp = re.search(r'\\"strMinSellSubtotal\\":\\"([^\\]*)\\"', chunk)
        price_text = mp.group(1) if mp else ""
        items.append({
            "hash": mh.group("hash").replace("\\\\", "\\").replace("\\'", "'"),
            "listings": int(mh.group("so")),
            "currency": int(cur.group(1)) if cur else 0,
            "cents": _cents(price_text),
            "price_text": price_text,
        })
    return int(m.group(1)), items


STALE_CENTS_SECONDS = 105 * 60  # 逐件接口被限流时，市场页回退只补这么久没更新的物品（略小于 2 小时）


def market_q_refresh(priority, log=print, stop=None, on_save=None, stale_only=True):
    """市场搜索页按关键字取价——只在逐件接口被限流时用。
    每件物品用 q=<市场名> 搜它自己，从返回结果里挑出同名条目（q 是子串匹配，会带回同族物品）。
    一张页面约 645KB，所以默认只补 cny_ts 超过 STALE_CENTS_SECONDS 的物品。
    返回更新件数。"""
    state = load_state()
    prices = state.get("prices", {})
    now = time.time()
    todo = [k for k in priority if k in prices and prices[k].get("hash")
            and (not stale_only or now - (prices[k].get("cny_ts") or 0) > STALE_CENTS_SECONDS)]
    MARKET_STATS["tried"] = len(todo)
    if not todo:
        return 0
    ck = get_auth(log).cookies()
    log(f"  [market] 逐件接口不可用：改用市场搜索页补 {len(todo)} 件（每件一张页面）")
    done = updated = 0
    fails = 0
    stopped = lambda: stop is not None and stop.is_set()
    proxy = os.environ.get("STEAM_PROXY", PROXY)
    with httpx.Client(timeout=40, proxy=proxy or None, headers=MARKET_HEADERS,
                      cookies=ck or {}) as cl:
        for k in todo:
            if stopped():
                break
            h = prices[k]["hash"]
            try:
                r = cl.get(MARKET_SEARCH_URL,
                           params={"appid": APPID, "q": h, "count": MARKET_PAGE_SIZE, "start": 0})
            except Exception as e:
                fails += 1
                log(f"  市场页请求失败（{type(e).__name__}），跳过 {h[:30]}")
                if fails >= 3:
                    log("  市场页连续 3 次失败，停止回退（剩下的下轮再补）")
                    break
                continue
            if r.status_code != 200:
                log(f"  市场页 HTTP {r.status_code}，停止回退")
                break
            fails = 0
            _, items = parse_market_page(r.text)
            done += 1
            hit = next((x for x in (items or []) if x["hash"] == h), None)
            if hit is not None:
                prices[k]["cny_ts"] = time.time()
                if hit["currency"] == CURRENCY and hit["cents"]:
                    prices[k].update({"cny_cents": hit["cents"], "price_text": hit["price_text"],
                                      "price_cents": hit["cents"], "listings": hit["listings"]})
                    updated += 1
            if time.time() - (prices[k].get("cny_median_ts") or 0) > MEDIAN_STALE:
                st = price_history_stats(h, log)
                if st:
                    prices[k].update({"cny_median_cents": st["median_cents"],
                                      "cny_volume": st["volume"],
                                      "cny_median_ts": time.time()})
            log(f"  回退进度 {done}/{len(todo)}")
            if done % 10 == 0:
                save_prices(prices, state.get("market_total", 0),
                            state.get("unknown_count", 0), "fast")
                if on_save:
                    try:
                        on_save()
                    except Exception as e:
                        log(f"  on_save 回调异常: {type(e).__name__}: {e}")
            if stop is not None:
                stop.wait(2)
            else:
                time.sleep(2)
    save_prices(prices, state.get("market_total", 0), state.get("unknown_count", 0), "fast")
    if on_save:
        try:
            on_save()
        except Exception as e:
            log(f"  on_save 回调异常: {type(e).__name__}: {e}")
    log(f"  回退结束：处理 {done}/{len(todo)} 件，更新 {updated} 件")
    return updated


PRICEHISTORY_URL = "https://steamcommunity.com/market/pricehistory/"
MEDIAN_STALE = 6 * 3600        # 中位/成交量只在超过 6 小时没更新时才去补（多一次小请求）
_HIST_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_HIST_RE = re.compile(r"([A-Za-z]{3}) (\d{1,2}) (\d{4}) (\d{1,2}):")


def _hist_ts(s):
    """把 pricehistory 的时间字段（"Nov 25 2026 01: +0"，UTC）转成 epoch。"""
    m = _HIST_RE.match(s or "")
    if not m:
        return None
    import calendar
    mon, day, year, hour = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    if mon not in _HIST_MONTHS:
        return None
    return calendar.timegm((year, _HIST_MONTHS[mon], day, hour, 0, 0))


def price_history_stats(hname, log=None):
    """登录态下用 market/pricehistory 算「近 24 小时成交量加权中位价」和成交量。
    匿名请求会被 400 拒绝，所以只在有 steamLoginSecure 时可用。返回
    {median_cents, volume}；拿不到返回 None。一件约 30KB。"""
    ck = get_auth(log or print).cookies()
    if not ck:
        return None
    proxy = os.environ.get("STEAM_PROXY", PROXY)
    try:
        with httpx.Client(timeout=40, proxy=proxy or None, headers=HEADERS, cookies=ck) as cl:
            r = cl.get(PRICEHISTORY_URL,
                       params={"appid": APPID, "market_hash_name": hname, "currency": CURRENCY})
    except Exception as e:
        log and log(f"  [hist] 请求失败（{type(e).__name__}）")
        return None
    if r.status_code != 200:
        log and log(f"  [hist] HTTP {r.status_code}（中位价跳过）")
        return None
    try:
        rows = (r.json() or {}).get("prices") or []
    except Exception:
        return None
    cutoff = time.time() - 86400
    pts = []
    for row in rows:
        try:
            ts = _hist_ts(row[0])
            if ts and ts >= cutoff:
                pts.append((float(row[1]), int(row[2] or 0)))
        except Exception:
            continue
    if not pts:
        return None
    pts.sort()
    total = sum(v for _p, v in pts)
    if total <= 0:
        med = pts[len(pts) // 2][0]
    else:
        acc = 0
        med = pts[-1][0]
        for p, v in pts:
            acc += v
            if acc >= total / 2.0:
                med = p
                break
    return {"median_cents": int(round(med * 100)), "volume": total}


def price_one_via_page(hname, log=None):
    """市场搜索页取单件价（q= 关键字）。逐件接口被 429 时给悬停用。
    返回 {cny_cents, cny_median_cents, cny_volume}（中位/成交量来自 pricehistory）或 None。"""
    ck = get_auth(log or print).cookies()
    proxy = os.environ.get("STEAM_PROXY", PROXY)
    try:
        with httpx.Client(timeout=40, proxy=proxy or None, headers=MARKET_HEADERS,
                          cookies=ck or {}) as cl:
            r = cl.get(MARKET_SEARCH_URL,
                       params={"appid": APPID, "q": hname, "count": MARKET_PAGE_SIZE, "start": 0})
    except Exception as e:
        log and log(f"  [market] 单件取价失败（{type(e).__name__}）")
        return None
    if r.status_code != 200:
        return None
    _, items = parse_market_page(r.text)
    hit = next((x for x in (items or []) if x["hash"] == hname), None)
    if not hit or hit["currency"] != CURRENCY or not hit["cents"]:
        return None
    st = price_history_stats(hname, log) or {}
    return {"cny_cents": hit["cents"],
            "cny_median_cents": st.get("median_cents"),
            "cny_volume": st.get("volume")}


def sync_new_items(owned, new_keys=None, log=print, limit=5, backlog=1):
    """仓库里新出现、价格表里还没有条目的物品：解析出市场名，补一条空记录，
    这样本轮的 fast_refresh 就能把它当普通物品取价。
      new_keys  本次新出现的 ItemKey（优先解析，最多 limit 件）
      backlog   历史积压（以前就没解析出来、也不是这次新增的）每轮最多试几件，
                默认 1 件，避免加一件新物品时被一堆旧账拖着转圈
    失败的在 market_names.json 里按 3 天节流（网络失败 1 小时后重试），不会反复请求。"""
    state = load_state()
    prices = state.get("prices", {})
    missing = [str(k) for k in (owned or ()) if str(k) not in prices]
    if not missing:
        return 0
    fresh = set(str(k) for k in (new_keys or ()))
    front = [k for k in missing if k in fresh]
    rest = [k for k in missing if k not in fresh]
    picks = front[:limit] + rest[:max(0, backlog)]
    if not picks:
        log("  [names] 没有需要解析的新物品（历史积压留到之后的轮次）")
        return 0
    try:
        import market_names
    except Exception as e:
        log(f"  [names] 模块不可用（{type(e).__name__}: {e}）")
        return 0
    try:
        got = market_names.resolve_missing(picks, limit=len(picks), log=log)
    except Exception as e:
        log(f"  [names] 解析异常（{type(e).__name__}: {e}）")
        return 0
    added = 0
    for k, h in got.items():
        prices.setdefault(k, {}).update({"hash": h, "cny_cents": None})
        added += 1
    if added:
        save_prices(prices, state.get("market_total", 0), state.get("unknown_count", 0), "fast")
        log(f"  [names] 新物品补了 {added} 件市场名，本轮一并取价")
    return added


def fast_refresh(priority=None, log=print, only_priority=False, on_save=None, stop=None, force=False):
    """人民币价维护：先走最省流量的逐件 priceoverview（币种固定 currency=23）。
    一旦被限流（429）且已登录，本轮改用市场搜索页按关键字补那些 105 分钟以上没更新的物品。
      priority     ItemKey 集合（仓库/背包/材料库）
      only_priority 只刷 priority 里的物品
      on_save      每次落盘后的回调（查看器用它重载价格表）
      stop         threading.Event，置位后本轮停止（查看器的总开关用）
      force        跳过「多久没更新」门槛，全部现拉（目前查看器不用，留给调试）
    结果写入 LAST_RUN["result"]：done / cooldown / stopped / failed。"""
    if only_priority and not priority:
        return load_state().get("prices", {})        # 没有仓库物品：什么都不请求
    res = _overview_refresh(priority, log, only_priority, on_save, stop)
    if LAST_RUN.get("result") not in ("cooldown", "failed"):
        return res
    owned = set(priority or ())
    if not owned or not get_auth(log).cookies():
        return res
    MARKET_STATS["tried"] = 0
    updated = market_q_refresh(owned, log=log, stop=stop, on_save=on_save, stale_only=not force)
    if updated:
        LAST_RUN.update(result="done", updated=updated)
    elif MARKET_STATS["tried"] == 0:
        # 没有过期物品需要回退：说明仓库里的价都还新鲜，本轮视为已完成
        LAST_RUN.update(result="done", updated=0)
    return res


def _overview_refresh(priority=None, log=print, only_priority=False, on_save=None, stop=None):
    """人民币价维护（未登录时用）：对清单逐件拉 priceoverview（currency=23，匿名），更新
    cny_cents / cny_median_cents / cny_volume。
      priority     ItemKey 集合：only_priority 时限定刷新范围；否则排到最前
      only_priority 只刷 priority 里的物品（查看器自动刷新与手动刷新都用它：Steam 对
                   该接口约有 500 件/周期的匿名额度，只够刷仓库/背包里的物品）
      on_save      每次落盘后的回调（查看器用它重载价格表）
      stop         threading.Event，置位后本轮停止（查看器的总开关用）
    遇到限流冷却即提前结束，已更新的部分已落盘。
    结果写入 LAST_RUN["result"]：done / cooldown / stopped / failed。"""
    state = load_state()
    prices = state.get("prices", {})
    keys = list(prices)
    now = time.time()
    if priority and only_priority:
        # 只刷「该刷的」：从没取过价的新物品，或超过 105 分钟没更新的。
        # 数量变化（同一个 ItemKey 又多了一个）不产生新键，也不会被这里命中。
        keys = [k for k in keys if k in priority
                and (not prices[k].get("cny_ts")
                     or now - (prices[k].get("cny_ts") or 0) > STALE_CENTS_SECONDS)]
        # 新物品（没有 cny_ts）排最前，其次是越久没更新的越靠前
        keys.sort(key=lambda k: prices[k].get("cny_ts") or 0)
    else:
        # 组内按 cny_ts 从旧到新：从没拿到过价的排最前。这样一轮被限流打断后，
        # 下一轮先补没轮到的物品，而不是把清单头部的再拉一遍。
        keys.sort(key=lambda k: (k not in (priority or ()), prices[k].get("cny_ts") or 0))
    total = len(keys)
    updated = empty = skipped = failed = 0
    consec_fail = 0
    result = "done"
    LAST_RUN.update(result="running", updated=0)
    stopped = lambda: stop is not None and stop.is_set()
    log(f"  人民币刷新开始：{total} 件"
        + ("（仅仓库/背包物品）" if only_priority else "（仓库/背包物品优先）"))

    def finish(res):
        LAST_RUN.update(result=res, updated=updated)
        return prices

    def flush():
        save_prices(prices, state.get("market_total", 0),
                    state.get("unknown_count", 0), "fast")
        if on_save:
            try:
                on_save()
            except Exception as e:
                log(f"  on_save 回调异常: {type(e).__name__}: {e}")

    for n, k in enumerate(keys, 1):
        if stopped():
            log(f"  已手动停止（已更新 {updated} 件）")
            result = "stopped"
            break
        hn = prices[k].get("hash")
        if not hn:
            skipped += 1
            continue
        try:
            res = cny_overview(hn, log=log)
        except Exception as e:                     # 网络抖动/代理故障：跳过这件，不中断整轮
            failed += 1
            consec_fail += 1
            if consec_fail >= 5:
                log(f"  连续 {consec_fail} 件请求失败（{type(e).__name__}），本轮提前结束")
                result = "failed"
                break
            stop.wait(3) if stop is not None else time.sleep(3)
            continue
        consec_fail = 0
        if res == "cooldown":
            log(f"  限流冷却中，本轮提前结束（已更新 {updated} 件并保存）")
            result = "cooldown"
            break
        if res is None:
            empty += 1
            prices[k]["cny_ts"] = time.time()     # 无挂牌也要记时间，否则每轮都白查一遍
        else:
            prices[k].update(res)
            prices[k]["cny_median_ts"] = time.time()
            updated += 1
        if updated and updated % SAVE_EVERY == 0:
            flush()
        log(f"  进度 {n}/{total}")
    flush()
    if result == "done":
        log(f"  人民币刷新完成：更新 {updated} 件，无挂牌/无数据 {empty} 件，"
            f"请求失败 {failed} 件，跳过 {skipped} 件")
    return finish(result)


def main():
    """CLI 只做单件取价；批量刷新交给 stash_viewer（只有它知道仓库/背包里有什么）。"""
    for a in sys.argv:
        if a.startswith("--proxy="):
            os.environ["STEAM_PROXY"] = a.split("=", 1)[1]
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not names:
        print("用法: python update_prices.py \"<市场 hash_name>\" [...]\n"
              "      批量刷新仓库/背包物品请用查看器的开关与底部按钮。")
        return
    for nm in names:
        print(nm, "->", price_one_via_page(nm))


if __name__ == "__main__":
    main()
