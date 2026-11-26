"""一键准备：查依赖 → 提取 ui/ → 生成数据表 → 提取图标 → 自检。

正常用法是双击仓库根目录的 setup.bat（本脚本负责干活、写日志、弹窗报错）。
也可以手动跑：
    python tools\\setup_all.py
    python tools\\setup_all.py --game-dir "D:\\SteamLibrary\\steamapps\\common\\TaskbarHero\\TaskBarHero_Data"
    python tools\\setup_all.py --no-popup --skip-pip      # 脚本/自动化场景

结果写在仓库根目录 setup_log.txt（每次运行覆盖，出问题就看它），失败会弹窗提示。
"""
import argparse
import ctypes
import glob
import os
import re
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT, "setup_log.txt")

IMPORTS = [("tkinter", "tkinter（官方 Python 自带，精简版没有）"),
           ("PIL", "Pillow"),
           ("Crypto", "pycryptodome"),
           ("httpx", "httpx"),
           ("UnityPy", "UnityPy")]

# 和 stash_viewer.py 的 _missing_assets() 保持一致
REQUIRED = ["ui/Ui_Stash_Bg.png", "ui/Ui_Stash_Bg_Decor.png", "ui/TextImage_Stash_SC.png",
            "ui/TabButton_Stash_Active.png", "ui/TabButton_Stash_DeActive_Active.png",
            "ui/Btn_Large_Active.png", "ui/Btn_Large_Plain.png", "ui/Button_Close_Active.png",
            "ui/ItemSlot_Bg.png", "ui/ItemSlot_GradeBg_NORMAL.png", "ui/Btn_Small_Active.png",
            "items_zh.csv", "localization_zh.json"]


def _log(f=None, msg=""):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}" if f else msg
    print(line, flush=True)
    if f:
        f.write(line + "\n")
        f.flush()


def _popup(title, text, error=False):
    try:
        flags = 0x10 if error else 0x40            # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        pass


def _steam_roots():
    roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        v = os.environ.get(env)
        if v:
            roots.append(os.path.join(v, "Steam"))
    for drive in "CDEFGH":
        roots.append(os.path.join(f"{drive}:\\", "SteamLibrary"))
        roots.append(os.path.join(f"{drive}:\\", "Steam"))
        roots.append(os.path.join(f"{drive}:\\", "Program Files (x86)", "Steam"))
    return roots


def _library_folders(root):
    out = []
    for vdf in (os.path.join(root, "steamapps", "libraryfolders.vdf"),
                os.path.join(root, "config", "libraryfolders.vdf")):
        if not os.path.exists(vdf):
            continue
        try:
            text = open(vdf, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        out += [m.group(1).replace("\\\\", "\\")
                for m in re.finditer(r'"path"\s+"([^"]+)"', text)]
    return out


def _valid_data_dir(base):
    if not base:
        return ""
    p = os.path.abspath(base)
    cand = p if os.path.basename(p) == "TaskBarHero_Data" else \
        os.path.join(p, "steamapps", "common", "TaskbarHero", "TaskBarHero_Data")
    if os.path.exists(os.path.join(cand, "sharedassets0.assets")) and \
            os.path.isdir(os.path.join(cand, "StreamingAssets", "aa", "StandaloneWindows64")):
        return cand
    return ""


def find_game_dir(explicit, f=None):
    bases = [explicit, os.environ.get("TBH_GAME_DIR")]
    for root in _steam_roots():
        bases.append(root)
        bases += _library_folders(root)
    seen = set()
    for base in bases:
        cand = _valid_data_dir(base)
        if not cand or cand.lower() in seen:
            continue
        seen.add(cand.lower())
        _log(f, f"游戏目录：{cand}")
        return cand
    return ""


def missing_imports():
    import importlib
    bad = []
    for mod, label in IMPORTS:
        try:
            importlib.invalidate_caches()
            importlib.import_module(mod)
        except Exception:
            bad.append(label)
    return bad


def pip_install(f, extra=()):
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "-r", os.path.join(ROOT, "requirements.txt"), *extra]
    _log(f, "运行：" + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()[-25:]
    for line in tail:
        _log(f, "  " + line)
    return r.returncode == 0


def run_tool(f, script, extra=()):
    path = os.path.join(ROOT, "tools", script)
    if not os.path.exists(path):
        raise RuntimeError(f"缺少脚本：{path}")
    cmd = [sys.executable, "-X", "utf8", path, *extra]
    _log(f, "运行：" + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if out:
        for line in out.splitlines()[-8:]:
            _log(f, "  " + line)
    if r.returncode != 0:
        err = (r.stderr or out or "").strip().splitlines()[-12:]
        for line in err:
            _log(f, "  ! " + line)
        raise RuntimeError(f"{script} 失败（exit {r.returncode}）")


def self_check(f):
    missing = [p for p in REQUIRED if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        raise RuntimeError("素材仍不完整，缺：" + "、".join(missing))
    icons = glob.glob(os.path.join(ROOT, "icons", "*.png"))
    rows = 0
    with open(os.path.join(ROOT, "items_zh.csv"), encoding="utf-8-sig") as fh:
        rows = max(0, sum(1 for _ in fh) - 1)
    if not icons:
        _log(f, "提醒：icons/ 里没有图标，格子会是空的")
    _log(f, f"自检通过：ui {len(glob.glob(os.path.join(ROOT, 'ui', '*.png')))} 张、"
            f"图标 {len(icons)} 张、物品表 {rows} 行")
    return rows, len(icons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default="")
    ap.add_argument("--skip-pip", action="store_true")
    ap.add_argument("--no-popup", action="store_true")
    args = ap.parse_args()

    popup = (lambda *a, **k: None) if args.no_popup else _popup
    os.chdir(ROOT)
    try:
        f = open(LOG_PATH, "w", encoding="utf-8")
    except Exception:
        f = None
    _log(f, "=" * 60)
    _log(f, f"一键准备开始 · Python {sys.version.split()[0]} · {ROOT}")

    try:
        if sys.version_info < (3, 10):
            raise RuntimeError(f"Python 版本过低（{sys.version.split()[0]}），需要 3.10 或更高")

        # 1. 依赖
        bad = missing_imports()
        if bad:
            if args.skip_pip:
                raise RuntimeError("依赖缺失且指定了 --skip-pip：" + "、".join(bad))
            _log(f, "依赖缺失：" + "、".join(bad) + "，开始安装")
            if not pip_install(f):
                raise RuntimeError("pip install 失败，看一眼 setup_log.txt 里的报错")
            bad = missing_imports()
            if bad:
                _log(f, "改用 --user 再装一次")
                pip_install(f, ("--user",))
                bad = missing_imports()
                if bad:
                    raise RuntimeError("依赖仍缺失：" + "、".join(bad) + "（试着手动 pip install -r requirements.txt）")
        _log(f, "依赖就绪")

        # 2. 游戏目录
        if args.game_dir:
            game_dir = _valid_data_dir(args.game_dir)
            if not game_dir:
                raise RuntimeError(f"指定的游戏目录不可用：{args.game_dir}\n"
                                   "应该指向 TaskbarHero_Data（里面有 sharedassets0.assets）。")
            _log(f, f"游戏目录：{game_dir}")
        else:
            game_dir = find_game_dir("", f)
        if not game_dir:
            raise RuntimeError("没找到《TaskBar Hero》的安装目录。\n"
                               "请确认游戏已安装；或用 TBH_GAME_DIR 指定游戏目录；\n"
                               "也可以把目录作为参数传给 setup.bat。")

        # 3. ui / 数据表 / 图标
        run_tool(f, "extract_sprites.py", ("--game-dir", game_dir))
        run_tool(f, "build_item_tables.py", ("--game-dir", game_dir))
        run_tool(f, "extract_icons.py", ("--game-dir", game_dir))

        rows, icons = self_check(f)
        _log(f, "全部完成")
        if f:
            f.close()
        popup("准备完成", f"素材和数据表已生成到：\n{ROOT}\n\n"
                          f"ui 素材、物品表 {rows} 行、图标 {icons} 张。\n\n"
                          f"现在双击 start.bat 就能打开查看器。\n日志：setup_log.txt")
        return 0
    except Exception as e:
        _log(f, "失败：" + str(e))
        detail = traceback.format_exc().strip().splitlines()[-6:]
        for line in detail:
            _log(f, "  " + line)
        if f:
            f.close()
        popup("准备失败", f"{e}\n\n日志已写到：\n{LOG_PATH}\n\n"
                          "双击 setup.bat 可以重试；也可以在命令行里看完整报错。", error=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
