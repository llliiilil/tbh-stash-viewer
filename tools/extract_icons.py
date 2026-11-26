"""从游戏本体提取物品图标（仓库不附带游戏素材，版权归游戏方）。

图标在 sharedassets0.assets 里，名字就是物品表 `ItemInfoData` 的 `IconPath`（如 `AMULET_600001`），
原生 16×16。清单优先读 `items_zh.csv` 的「图标」列，没有就从游戏里的 `ItemInfoData` TextAsset 现取。

用法：
    python tools/extract_icons.py --game-dir "C:\\Program Files (x86)\\Steam\\steamapps\\common\\TaskbarHero\\TaskBarHero_Data"
    python tools/extract_icons.py --items items_zh.csv --out icons
    python tools/extract_icons.py --list --filter AMULET      # 先看资源里有哪些图标名

参数：
    --game-dir  游戏安装目录（默认 TBH_GAME_DIR，其次 Steam 常见路径）
    --assets    图标所在的资源文件（默认 sharedassets0.assets）
    --items     图标清单来源（默认仓库根下的 items_zh.csv）
    --out       输出目录（默认仓库根下的 icons/）
"""
import argparse
import csv
import io
import os
import sys

DEFAULT_GAME_DIR = os.environ.get("TBH_GAME_DIR") or \
    r"D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _icon_names_from_items(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        col = "图标" if "图标" in (rows.fieldnames or []) else "IconPath"
        return {r[col].strip() for r in rows if r.get(col, "").strip()}


def _icon_names_from_assets(UnityPy, path):
    if not os.path.exists(path):
        return set()
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            d = obj.read(check_read=False)
        except Exception:
            continue
        if getattr(d, "m_Name", "") != "ItemInfoData":
            continue
        rows = csv.DictReader(io.StringIO(d.m_Script.lstrip("\ufeff")))
        return {r["IconPath"].strip() for r in rows if r.get("IconPath", "").strip()}
    return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=DEFAULT_GAME_DIR)
    ap.add_argument("--assets", default="sharedassets0.assets")
    ap.add_argument("--items", default=os.path.join(ROOT, "items_zh.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "icons"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--filter", default="")
    args = ap.parse_args()

    try:
        import UnityPy
    except ImportError:
        sys.exit("缺少依赖：pip install UnityPy")

    src = os.path.join(args.game_dir, args.assets)
    if not os.path.exists(src):
        sys.exit(f"找不到资源文件：{src}\n用 --game-dir 指定游戏安装目录")

    want = _icon_names_from_items(args.items) or _icon_names_from_assets(UnityPy, src)
    if not want:
        sys.exit("拿不到图标清单：items_zh.csv 和游戏里的 ItemInfoData 都没读到")

    os.makedirs(args.out, exist_ok=True)
    env = UnityPy.load(src)
    names, saved, best = [], 0, {}
    for obj in env.objects:
        if obj.type.name != "Sprite":
            continue
        try:
            d = obj.read(check_read=False)
        except Exception:
            continue
        n = getattr(d, "m_Name", "")
        if args.list:
            if args.filter.lower() in n.lower():
                names.append(n)
            continue
        if n not in want:
            continue
        try:
            img = d.image
        except Exception as e:
            print(f"fail {n}: {e}")
            continue
        area = img.size[0] * img.size[1]
        if n not in best or area > best[n][0]:
            best[n] = (area, img)

    if args.list:
        print("\n".join(sorted(names)) or "（没有匹配的图标名）")
        return

    for n, (_a, img) in best.items():
        img.save(os.path.join(args.out, n + ".png"))
        saved += 1
    missing = sorted(want - set(best))
    print(f"完成：{saved}/{len(want)} 个图标写入 {args.out}")
    if missing:
        print("资源里没找到：" + "、".join(missing[:10]) + ("…" if len(missing) > 10 else ""))
        print("提示：--list --filter 关键字 可以列出资源里的真实图标名")


if __name__ == "__main__":
    main()
