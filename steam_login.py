"""Steam 登录态：登录一次，之后自动保持。

做法：弹出一个独立的 Edge 窗口（专用配置目录、无地址栏，看起来像个登录框），用户正常
登录一次；登录成功后通过浏览器的本机调试接口取出 Cookie，写进 steam_cookies.json
（update_prices.SteamAuth 认得的格式），随后关闭窗口。之后的请求都由脚本自己发。

有了长效令牌 steamRefresh_steam 之后，SteamAuth.cookies() 会在访问令牌临期时自动换发
新的 steamLoginSecure，不需要再手动导出 Cookie。

CLI: pythonw steam_login.py             弹出登录窗口，等待登录（最长 10 分钟），结果写 steam_login.log
     python  steam_login.py --selftest  不访问 Steam，只验证「启动 Edge + 调试通道 + 取 Cookie + 关闭」可用
     python  steam_login.py --status    报告当前 steam_cookies.json 里令牌的有效期
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE, "edge_login_profile")      # 这个窗口专用的浏览器配置目录
COOKIES_PATH = os.path.join(BASE, "steam_cookies.json")
LOG_PATH = os.path.join(BASE, "steam_login.log")
def _default_proxy():
    """STEAM_PROXY 优先；没设置时看本机 7890 有没有在监听，没开就不走代理。"""
    if "STEAM_PROXY" in os.environ:
        return os.environ["STEAM_PROXY"] or None
    import socket
    try:
        socket.create_connection(("127.0.0.1", 7890), timeout=0.3).close()
        return "http://127.0.0.1:7890"
    except Exception:
        return None


PROXY = _default_proxy()                                     # 与 update_prices.PROXY 一致
LOGIN_URL = "https://steamcommunity.com/login/home/?goto=market%2F"
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
WAIT_LOGIN = 10 * 60          # 等用户登录的最长时间
# 要保存的 Cookie：访问令牌 / 长效令牌 / 会话 ID / 地区（决定页面文案与部分接口行为）
WANT = ("steamLoginSecure", "steamRefresh_steam", "sessionid", "steamCountry")


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass


def _jwt_exp(token):
    """'steamid||JWT' 或 'JWT' → 到期时间戳；解析不了返回 0。"""
    try:
        part = token.split("||")[-1].split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part)).get("exp", 0))
    except Exception:
        return 0.0


# ---------- 最小 WebSocket 客户端（只用标准库；只连本机 127.0.0.1 的调试端口） ----------

class WS:
    def __init__(self, url, timeout=10):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("调试端口在握手阶段断开")
            buf += chunk
        head = buf.split(b"\r\n\r\n", 1)[0]
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError("调试端口握手失败: " + head.split(b"\r\n")[0].decode(errors="replace"))
        self.buf = buf.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise ConnectionError("调试端口已关闭")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        payload = b""
        while True:
            b1, b2 = self._read(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                (n,) = struct.unpack(">H", self._read(2))
            elif n == 127:
                (n,) = struct.unpack(">Q", self._read(8))
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:                     # ping → pong
                self.sock.sendall(bytes([0x8A, 0x80]) + b"\x00\x00\x00\x00")
                continue
            if op == 0x8:
                raise ConnectionError("调试端口关闭了连接")
            if op in (0x1, 0x0):
                payload += data
                if fin:
                    return payload.decode(errors="replace")

    def call(self, _id, method, params=None):
        self.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
        while True:                          # 事件通知（id 缺失）直接跳过，只等本次调用的回应
            msg = json.loads(self.recv())
            if msg.get("id") == _id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ---------- 浏览器调试通道（只连本机，不走系统代理） ----------

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_json(port, path, timeout=5):
    with _opener.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def _find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("找不到 msedge.exe")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def launch(url):
    """启动独立配置目录的 Edge 窗口。返回 (Popen, 调试端口)。"""
    edge = _find_edge()
    port = _free_port()
    args = [edge,
            f"--user-data-dir={PROFILE_DIR}",
            f"--remote-debugging-port={port}"]
    if PROXY:
        args.append(f"--proxy-server={PROXY}")
    args += [
            "--no-first-run", "--no-default-browser-check",
            "--disable-features=msEdgeSignIn,msImplicitSignin,msSeamlessWebToBrowserSignIn",
            "--window-size=600,800",
            f"--app={url}"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 25
    while time.time() < deadline:            # 等调试端口就绪
        try:
            _http_json(port, "/json/version", timeout=2)
            return proc, port
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("Edge 已启动但 25 秒内调试端口没有就绪（可能被组策略禁用了远程调试）")


def close_browser(port, proc):
    """先请浏览器自己退出，不行再按 PID 结束这一个实例（不影响用户平时开的 Edge）。"""
    try:
        ver = _http_json(port, "/json/version", timeout=3)
        ws = WS(ver["webSocketDebuggerUrl"])
        try:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            time.sleep(0.5)
        finally:
            ws.close()
    except Exception:
        pass
    for _ in range(20):
        if proc.poll() is not None:
            return
        time.sleep(0.5)
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get_cookies(port):
    """取出 Steam 相关域名的 Cookie → {名字: 值}。窗口还没打开页面时返回 {}。"""
    try:
        targets = [t for t in _http_json(port, "/json/list", timeout=4) if t.get("type") == "page"]
    except Exception:
        return {}
    for t in targets:
        u = t.get("url", "")
        if not (u.startswith("https://steamcommunity.com") or u.startswith("https://login.steampowered.com")
                or u.startswith("https://steamcommunity.com/login")):
            continue
        ws = WS(t["webSocketDebuggerUrl"])
        try:
            res = ws.call(1, "Network.getCookies",
                          {"urls": ["https://steamcommunity.com/", "https://login.steampowered.com/"]})
        finally:
            ws.close()
        out = {}
        for c in res.get("cookies", []):
            if c.get("name") in WANT and c.get("value"):
                out[c["name"]] = c["value"]
        return out
    return {}


def logged_in(cookies):
    """已登录 = 有 steamLoginSecure 且访问令牌还有 2 分钟以上有效期。"""
    tok = cookies.get("steamLoginSecure", "")
    exp = _jwt_exp(tok)
    return bool(tok) and exp > time.time() + 120, exp


def save_cookies(cookies):
    """写入 steam_cookies.json（SteamAuth 的格式：refresh_token + steamLoginSecure + sessionid）。
    写之前留一份旧文件。"""
    data = {}
    if os.path.exists(COOKIES_PATH):
        try:
            data = json.load(open(COOKIES_PATH, encoding="utf-8"))
        except Exception:
            data = {}
    if "steamRefresh_steam" in cookies:
        data["refresh_token"] = cookies["steamRefresh_steam"]
        data.pop("steamRefresh_steam", None)
    for k in ("steamLoginSecure", "sessionid", "steamCountry"):
        if k in cookies:
            data[k] = cookies[k]
    tmp = COOKIES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, COOKIES_PATH)


def status():
    """报告 steam_cookies.json 里令牌的有效期（不输出令牌内容）。"""
    try:
        c = json.load(open(COOKIES_PATH, encoding="utf-8"))
    except Exception as e:
        return f"steam_cookies.json 读不到: {type(e).__name__}"
    lines = []
    rt = c.get("refresh_token") or ""
    if rt:
        e = _jwt_exp(rt)
        lines.append("长效令牌: " + (time.strftime("%Y-%m-%d %H:%M", time.localtime(e)) if e else "有（无法解析有效期）"))
    else:
        lines.append("长效令牌: 无（登录后才能自动续期）")
    at = c.get("steamLoginSecure") or ""
    e = _jwt_exp(at)
    lines.append("访问令牌: " + (time.strftime("%Y-%m-%d %H:%M", time.localtime(e)) + ("（已过期）" if e < time.time() else "")
                                   if e else "无"))
    return " | ".join(lines)


def run_login():
    proc = port = None
    try:
        proc, port = launch(LOGIN_URL)
        log(f"登录窗口已打开，等待登录（最长 {WAIT_LOGIN // 60} 分钟）")
        deadline = time.time() + WAIT_LOGIN
        reminded = False
        while time.time() < deadline:
            ok, exp = logged_in(get_cookies(port))
            if ok:
                time.sleep(2.5)               # 等重定向把其余 Cookie（长效令牌等）写完
                cookies = get_cookies(port)
                save_cookies(cookies)
                missing = [k for k in ("steamRefresh_steam", "sessionid") if k not in cookies]
                log("登录成功，已保存: " + ", ".join(sorted(cookies))
                    + (f"；缺少: {', '.join(missing)}" if missing else ""))
                log("现在: " + status())
                return 0
            if not reminded and time.time() > deadline - WAIT_LOGIN / 2:
                reminded = True
                log("仍在等待登录…")
            time.sleep(2)
        log("等待超时：没有检测到登录，未改动 steam_cookies.json")
        return 2
    except Exception as e:
        log(f"登录流程失败: {type(e).__name__}: {e}")
        return 1
    finally:
        if port:
            close_browser(port, proc)


def selftest():
    """不访问 Steam：验证 启动 → 调试端口 → WebSocket 调用 → 关闭 这条链路。"""
    proc = port = None
    try:
        proc, port = launch("about:blank")
        ver = _http_json(port, "/json/version")
        log(f"自检: 调试端口就绪，浏览器 {ver.get('Browser')}")
        targets = _http_json(port, "/json/list")
        page = next(t for t in targets if t.get("type") == "page")
        ws = WS(page["webSocketDebuggerUrl"])
        try:
            res = ws.call(1, "Network.getAllCookies")
        finally:
            ws.close()
        log(f"自检: WebSocket 调用成功，当前 Cookie 数 {len(res.get('cookies', []))}")
        log("自检: 通过")
        return 0
    except Exception as e:
        log(f"自检失败: {type(e).__name__}: {e}")
        return 1
    finally:
        if port:
            close_browser(port, proc)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--status" in sys.argv:
        log(status())
        sys.exit(0)
    sys.exit(run_login())
