"""从游戏本体提取 UI 按钮 / 图标 Sprite（仓库里不附带游戏素材，需要自己提取一份）。

用法：
    python tools/extract_sprites.py --list --filter Button        # 先看看有哪些 Sprite
    python tools/extract_sprites.py                               # 提取默认按钮组到 ./ui
    python tools/extract_sprites.py --names "StashTitle:TextImage_Stash_SC" --out ui

参数：
    --game-dir   游戏安装目录（默认取环境变量 TBH_GAME_DIR，否则用 Steam 默认路径）
    --assets     要扫描的资源文件（默认 sharedassets0.assets）
    --out        输出目录（默认仓库根下的 ui/）
    --names      "Sprite名:输出名" 列表，逗号分隔；不给就用下面的默认按钮组
    --list       只列出 Sprite 名，不写文件（配合 --filter 缩小范围）
    --filter     --list 时的名称过滤串（不区分大小写）
"""
import argparse
import os
import sys

DEFAULT_GAME_DIR = os.environ.get("TBH_GAME_DIR") or \
    r"D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_MAP = {
    "Button_Large_DeActive": "Btn_Large_DeActive",
    "Button_Small_Active": "Btn_Small_Active",
    "Button_Small_DeActive": "Btn_Small_DeActive",
    "Button_BG": "Btn_BG",
    "StashSorting_Active": "Btn_Sorting_Active",
    "Button_Close_Active": "Button_Close_Active",
    "Button_Close_Hover": "Button_Close_Hover",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=DEFAULT_GAME_DIR)
    ap.add_argument("--assets", default="sharedassets0.assets")
    ap.add_argument("--out", default=os.path.join(ROOT, "ui"))
    ap.add_argument("--names", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--filter", default="")
    args = ap.parse_args()

    try:
        import UnityPy
    except ImportError:
        sys.exit("缺少依赖：pip install UnityPy")

    src = os.path.join(args.game_dir, args.assets)
    if not os.path.exists(src):
        sys.exit(f"找不到资源文件：{src}\n用 --game-dir 指定游戏安装目录，或 --assets 指定别的 .assets")

    want = DEFAULT_MAP
    if args.names:
        want = {}
        for pair in args.names.split(","):
            if not pair.strip():
                continue
            k, _, v = pair.partition(":")
            want[k.strip()] = (v or k).strip()

    os.makedirs(args.out, exist_ok=True)
    env = UnityPy.load(src)
    names = []
    saved = 0
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
        if n in want:
            try:
                img = d.image
                out = os.path.join(args.out, want[n] + ".png")
                img.save(out)
                saved += 1
                print(f"saved {n} -> {out} size={img.size}")
            except Exception as e:
                print(f"fail {n}: {e}")

    if args.list:
        print("\n".join(sorted(names)) or "（没有匹配的 Sprite）")
    else:
        print(f"完成：{saved}/{len(want)} 个 Sprite 已写入 {args.out}")
        missing = [k for k in want if k not in names and not saved]
        if saved == 0:
            print("提示：先用 --list --filter Button 查看真实 Sprite 名")


if __name__ == "__main__":
    main()
