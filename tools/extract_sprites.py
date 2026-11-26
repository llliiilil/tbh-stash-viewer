"""从游戏本体提取查看器要用的全部 UI Sprite（仓库不附带游戏素材，需要自己提取一份）。

用法：
    python tools/extract_sprites.py                          # 提取整套 UI（面板/页签/格底/按钮）+ 生成彩色按钮
    python tools/extract_sprites.py --list --filter Button    # 先看看有哪些 Sprite
    python tools/extract_sprites.py --names "Ui_Stash_Bg,TextImage_Stash_SC" --out ui
    python tools/extract_sprites.py --no-derived              # 只要原版 Sprite，不生成棕色/灰色按钮

参数：
    --game-dir   游戏安装目录（默认 TBH_GAME_DIR，其次 Steam 常见路径）
    --assets     要扫描的资源文件（默认 sharedassets0.assets）
    --out        输出目录（默认仓库根下的 ui/）
    --names      "Sprite名:输出名" 列表，逗号分隔；不给就用下面的整套 UI 清单
    --list       只列出 Sprite 名，不写文件（配合 --filter 缩小范围）
    --filter     --list 时的名称过滤串（不区分大小写）
    --no-derived 跳过由 Btn_Large_DeActive 派生的 Btn_Large_Active / Btn_Large_Plain
"""
import argparse
import os
import sys

DEFAULT_GAME_DIR = os.environ.get("TBH_GAME_DIR") or \
    r"D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 查看器实际会用到的整套 UI；同名补成多条时保留面积最大的那张（按钮的 12/28/32 三档就是这样）
DEFAULT_MAP = {
    "Ui_Stash_Bg": "Ui_Stash_Bg",
    "Ui_Stash_Bg_Decor": "Ui_Stash_Bg_Decor",
    "TextImage_Stash_SC": "TextImage_Stash_SC",
    "TabButton_Stash_Active": "TabButton_Stash_Active",
    "TabButton_Stash_DeActive_Active": "TabButton_Stash_DeActive_Active",
    "TabButton_Stash_DeActive_Hover": "TabButton_Stash_DeActive_Hover",
    "TabButton_Stash_DLC_Active": "TabButton_Stash_DLC_Active",
    "TabButton_Stash_DLC_Hover": "TabButton_Stash_DLC_Hover",
    "ItemSlot_Bg": "ItemSlot_Bg",
    "ItemSlot_MoveOver": "ItemSlot_MoveOver",
    "ItemSlot_Selected": "ItemSlot_Selected",
    "Button_BG": "Btn_BG",
    "Button_Small_Active": "Btn_Small_Active",
    "Button_Small_DeActive": "Btn_Small_DeActive",
    "StashSorting_Active": "Btn_Sorting_Active",
    "Button_Close_Active": "Button_Close_Active",
    "Button_Close_Hover": "Button_Close_Hover",
    "Button_Large_DeActive": "Btn_Large_DeActive",
    "Ui_Popup_Bg": "Ui_Popup_Bg",
    "Ui_Popup_Bg_Mid": "Ui_Popup_Bg_Mid",
}
for _g in ("NORMAL", "UNCOMMON", "RARE", "ARCANA", "DIVINE", "LEGENDARY",
           "IMMORTAL", "BEYOND", "CELESTIAL", "COSMIC"):
    DEFAULT_MAP[f"ItemSlot_GradeBg_{_g}"] = f"ItemSlot_GradeBg_{_g}"

# 原版只有灰底大按钮；棕色 Active / 中性灰 Plain 是查看器自己按小按钮配色派生的
BROWN_ANCHORS = [(33, (36, 11, 5)), (48, (54, 13, 0)), (53, (62, 15, 0)),
                 (55, (70, 17, 0)), (72, (100, 40, 15)), (113, (103, 58, 36))]
LIGHT_CUT = 95                    # 亮度 >= 95 的银色描边/高光压成暗色，避免浅底上衬出亮边
# 同名 Sprite 有多档尺寸时，取和旧版素材一致的那张（关闭按钮在游戏里就有 12/28/32 三档）；
# 其余同名情况按面积取最大
PREFER_SIDE = {"Button_Close_Active": 28, "Button_Close_Hover": 32}


def _brown(g):
    if g <= BROWN_ANCHORS[0][0]:
        return BROWN_ANCHORS[0][1]
    if g >= BROWN_ANCHORS[-1][0]:
        return BROWN_ANCHORS[-1][1]
    for i in range(len(BROWN_ANCHORS) - 1):
        g0, c0 = BROWN_ANCHORS[i]
        g1, c1 = BROWN_ANCHORS[i + 1]
        if g0 <= g <= g1:
            t = (g - g0) / (g1 - g0)
            return tuple(int(round(c0[j] + (c1[j] - c0[j]) * t)) for j in range(3))
    return BROWN_ANCHORS[-1][1]


def _derive_buttons(out):
    """Btn_Large_DeActive → 棕色 Active + 深灰 Plain（只改纯灰像素，边框/阴影原样保留）。"""
    from PIL import Image
    src_path = os.path.join(out, "Btn_Large_DeActive.png")
    if not os.path.exists(src_path):
        print("跳过派生按钮：没有 Btn_Large_DeActive.png")
        return
    base = Image.open(src_path).convert("RGBA")

    brown = base.copy()
    bp = brown.load()
    for y in range(base.height):
        for x in range(base.width):
            r, g, b, a = bp[x, y]
            if a == 255 and r == g == b:
                bp[x, y] = _brown(r) + (a,)

    def strip_light(img, dark):
        out_img = img.copy()
        p = out_img.load()
        n = 0
        for y in range(out_img.height):
            for x in range(out_img.width):
                r, g, b, a = p[x, y]
                lum = 0.299 * r + 0.114 * g + 0.587 * b
                if a and lum >= LIGHT_CUT:
                    p[x, y] = dark
                    n += 1
        return out_img, n

    brown_final, nb = strip_light(brown, (48, 41, 33, 255))
    gray_final, ng = strip_light(base, (40, 40, 40, 255))
    brown_final.save(os.path.join(out, "Btn_Large_Active.png"))
    gray_final.save(os.path.join(out, "Btn_Large_Plain.png"))
    print(f"派生按钮：Btn_Large_Active.png / Btn_Large_Plain.png（压暗 {nb}/{ng} 像素）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=DEFAULT_GAME_DIR)
    ap.add_argument("--assets", default="sharedassets0.assets")
    ap.add_argument("--out", default=os.path.join(ROOT, "ui"))
    ap.add_argument("--names", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--filter", default="")
    ap.add_argument("--no-derived", action="store_true")
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
    best = {}
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
        pref = PREFER_SIDE.get(n)
        if pref:                       # 同名多档：挑边长最接近查看器实际显示尺寸的那张
            score = abs(img.size[0] - pref) * 1000 + area
            better = n not in best or score < best[n][0]
        else:
            score = area
            better = n not in best or area > best[n][0]
        if better:
            best[n] = (score, img)

    if args.list:
        print("\n".join(sorted(names)) or "（没有匹配的 Sprite）")
        return

    saved = 0
    for n, (_a, img) in best.items():
        out = os.path.join(args.out, want[n] + ".png")
        img.save(out)
        saved += 1
        print(f"saved {n} -> {out} size={img.size}")

    missing = [k for k in want if k not in best]
    print(f"完成：{saved}/{len(want)} 个 Sprite 已写入 {args.out}")
    if missing:
        print("没找到：" + "、".join(missing))
        print("提示：先用 --list --filter 关键字 查看真实 Sprite 名")
    if not args.names and not args.no_derived:
        _derive_buttons(args.out)


if __name__ == "__main__":
    main()
