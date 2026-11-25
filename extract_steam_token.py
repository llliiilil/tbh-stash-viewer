"""从本机 Steam 客户端 CEF 缓存 / Chrome / Edge 的 cookie 库提取 Steam 登录令牌。

Chromium cookie 加密:
  v10/v11: AES-256-GCM, key = DPAPI(os_crypt.encrypted_key from Local State)
  v20:     Chrome 127+ App-Bound（无法离线解，报告跳过）
  其他:    旧式整体 DPAPI
目标 cookie: steamRefresh_steam(login.steampowered.com, ~200 天长效令牌)
             steamLoginSecure / sessionid
输出: 直接写入 D:\\Programs\\TaskbarHeroData\\steam_cookies.json（值不打印，只打掩码）
"""
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from ctypes import wintypes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.stdout.reconfigure(encoding="utf-8")
LOCAL = os.environ["LOCALAPPDATA"]

TARGETS = [
    ("Steam客户端", os.path.join(LOCAL, r"Steam\htmlcache\Default\Network\Cookies"),
     os.path.join(LOCAL, r"Steam\htmlcache\Local State")),
    ("Steam客户端(旧路径)", os.path.join(LOCAL, r"Steam\htmlcache\Cookies"),
     os.path.join(LOCAL, r"Steam\htmlcache\Local State")),
    ("Chrome-Default", os.path.join(LOCAL, r"Google\Chrome\User Data\Default\Network\Cookies"),
     os.path.join(LOCAL, r"Google\Chrome\User Data\Local State")),
] + [(f"Edge-Profile {i}",
      os.path.join(LOCAL, rf"Microsoft\Edge\User Data\Profile {i}\Network\Cookies"),
      os.path.join(LOCAL, rf"Microsoft\Edge\User Data\Local State")) for i in range(1, 5)]

WANT = {"steamLoginSecure", "steamRefresh_steam", "sessionid"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "steam_cookies.json")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi(data):
    bin_ = ctypes.create_string_buffer(data)
    bin_blob = DATA_BLOB(len(data), ctypes.cast(bin_, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(bin_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("CryptUnprotectData 失败")
    raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    return raw


def load_key(ls_path):
    j = json.load(open(ls_path, encoding="utf-8"))
    enc = base64.b64decode(j["os_crypt"]["encrypted_key"])
    return dpapi(enc[5:])            # 去掉 "DPAPI" 前缀


def chromium_exp(e):
    return 0 if not e else e / 1e6 - 11644473600


def dec_value(ev, key):
    if not ev:
        return "", "空"
    tag = bytes(ev[:3])
    if tag in (b"v10", b"v11"):
        pt = AESGCM(key).decrypt(ev[3:15], ev[15:], None)
        s = pt.decode("utf-8", "replace")
        # 部分版本尾部带 32 字节校验，两种都按格式校验取可用者
        if not _looks_ok(s) and len(pt) > 32:
            s2 = pt[:-32].decode("utf-8", "replace")
            if _looks_ok(s2):
                s = s2
        return s, tag.decode()
    if tag == b"v20":
        return "", "v20(ABE)"
    try:
        return dpapi(bytes(ev)).decode("utf-8", "replace"), "dpapi"
    except Exception:
        return "", "未知"


def _looks_ok(s):
    return s.startswith("7656") or s.startswith("eyJ") or s.isalnum()


def copy_locked(src, dst):
    """esentutl 备份语义复制，可绕过运行中进程的独占锁；失败再试普通复制。"""
    os.system(f'esentutl /y "{src}" /d "{dst}" /o >nul 2>&1')
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return True
    try:
        shutil.copy2(src, dst)
        return os.path.getsize(dst) > 0
    except Exception:
        return False


def scan(label, cookies_path, ls_path, found):
    if not os.path.exists(cookies_path):
        print(f"[{label}] 无 cookie 库（跳过）")
        return
    tmp = tempfile.mkdtemp(prefix="steamck_")
    try:
        db = os.path.join(tmp, "Cookies")
        if not copy_locked(cookies_path, db):
            print(f"[{label}] cookie 库复制失败（被独占锁）")
            return
        for suffix in ("-wal", "-journal"):
            src = cookies_path + suffix
            if os.path.exists(src):
                copy_locked(src, db + suffix)
        try:
            key = load_key(ls_path)
        except Exception as e:
            print(f"[{label}] key 提取失败: {e}")
            return
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT host_key,name,encrypted_value,expires_utc FROM cookies "
            "WHERE host_key LIKE '%steampowered%' OR host_key LIKE '%steamcommunity%'"
        ).fetchall()
        con.close()
        for host, name, ev, exp in rows:
            if name not in WANT:
                continue
            val, tag = dec_value(ev, key)
            unix_exp = chromium_exp(exp)
            if val:
                found[name] = (val, host, unix_exp)
                masked = val[:14] + "…" + f"(len{len(val)})"
                valid = "有效" if unix_exp == 0 or unix_exp > __import__("time").time() else "已过期"
                print(f"[{label}] {name} @{host} {tag} {valid} 至"
                      f"{__import__('time').strftime('%m-%d %H:%M', __import__('time').localtime(unix_exp)) if unix_exp else '会话'}"
                      f" | {masked}")
            else:
                print(f"[{label}] {name} @{host} 无法解密({tag})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    found = {}
    for label, cp, ls in TARGETS:
        try:
            scan(label, cp, ls, found)
        except Exception as e:
            print(f"[{label}] 异常: {type(e).__name__}: {e}")

    if not found:
        print("未找到任何 Steam 令牌 cookie")
        return

    payload = {}
    if "steamRefresh_steam" in found:
        payload["refresh_token"] = found["steamRefresh_steam"][0]
    if "sessionid" in found:
        payload["sessionid"] = found["sessionid"][0]
    if "steamLoginSecure" in found:
        payload["steamLoginSecure"] = found["steamLoginSecure"][0]

    existed = {}
    if os.path.exists(OUT):
        try:
            existed = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            existed = {}
    existed.update(payload)
    json.dump(existed, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("\n已写入", OUT, "键:", list(existed.keys()),
          "| refresh:", "有" if payload.get("refresh_token") else "无")


if __name__ == "__main__":
    main()
