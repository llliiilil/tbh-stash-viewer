"""从游戏本体生成查看器要用的两个数据文件（仓库不附带它们，版权归游戏方）：

    localization_zh.json   中文文本表（ItemTable / StringTable）
    items_zh.csv           物品表（ItemKey → 中文名 / 品阶 / 类型 / 图标 …）

用法：
    python tools/build_item_tables.py --game-dir "C:\\Program Files (x86)\\Steam\\steamapps\\common\\TaskbarHero\\TaskBarHero_Data"
    python tools/build_item_tables.py --csv-dir csv --out .        # 表已导出到 csv/ 时

参数：
    --game-dir  游戏安装目录（默认 TBH_GAME_DIR，其次 Steam 常见路径）
    --csv-dir   ItemInfoData.csv 等表所在目录（默认仓库根下的 csv/）
    --out       输出目录（默认仓库根）
"""
import argparse
import csv
import glob
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GAME_DIR = os.environ.get("TBH_GAME_DIR") or \
    r"D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"

TABLE_COLUMNS = ["ItemKey", "名称", "NameKey", "类型", "品阶", "部位", "装备类型", "等级",
                 "堆叠上限", "图标", "基础属性1值", "基础属性2值", "材料类型", "仓库槽位"]


def _parse_table(raw):
    """本地化表：连续 (int64 id, utf8 字符串, int32 0) 记录；与查看器用的格式一致。"""
    n = len(raw)
    for start in range(0, min(n, 4096)):
        (cnt,) = struct.unpack_from("<i", raw, start)
        if not (100 <= cnt <= 200000):
            continue
        off = start + 4
        entries = {}
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
                entries[eid] = s
        except Exception:
            continue
        if entries and off <= n:
            return entries
    return {}


def _read_bundle_table(UnityPy, path, name):
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            d = obj.read(check_read=False)
            if getattr(d, "m_Name", "") == name:
                return _parse_table(bytes(obj.get_raw_data()))
        except Exception:
            continue
    return {}


def _bundle(game_dir, pattern):
    aa = os.path.join(game_dir, "StreamingAssets", "aa", "StandaloneWindows64")
    hits = sorted(glob.glob(os.path.join(aa, pattern)))
    return hits[0] if hits else None


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=DEFAULT_GAME_DIR)
    ap.add_argument("--csv-dir", default=os.path.join(ROOT, "csv"))
    ap.add_argument("--out", default=ROOT)
    args = ap.parse_args()

    try:
        import UnityPy
    except ImportError:
        sys.exit("缺少依赖：pip install UnityPy")

    zh_bundle = _bundle(args.game_dir, "localization-string-tables-chinese(simplified)*.bundle")
    shared_bundle = _bundle(args.game_dir, "localization-assets-shared*.bundle")
    if not zh_bundle:
        sys.exit(f"找不到中文本地化 bundle，检查 --game-dir：{args.game_dir}")

    item_zh = {}
    shared = {}
    shared_str = {}
    zh_str = {}
    if shared_bundle:
        shared = _read_bundle_table(UnityPy, shared_bundle, "ItemTable Shared Data")
        shared_str = _read_bundle_table(UnityPy, shared_bundle, "StringTable Shared Data")
    item_zh = _read_bundle_table(UnityPy, zh_bundle, "ItemTable_zh-Hans")
    zh_str = _read_bundle_table(UnityPy, zh_bundle, "StringTable_zh-Hans")
    # 本地化 bundle 是「自增 id → 文本」，共享 bundle 是「自增 id → 键名」，两表拼一次
    item_table = {shared[i]: v for i, v in item_zh.items() if i in shared} if shared else {}
    # StringTable 有一部分 id 不在共享表里（运行时动态串），沿用查看器旧口径：键名回落成 id:<id>
    string_table = {(shared_str[i] if i in shared_str else f"id:{i}"): v
                    for i, v in zh_str.items()} if zh_str else {}
    loc = {"ItemTable": dict(sorted(item_table.items())),
           "StringTable": dict(sorted(string_table.items()))}
    os.makedirs(args.out, exist_ok=True)
    loc_path = os.path.join(args.out, "localization_zh.json")
    with open(loc_path, "w", encoding="utf-8") as f:
        json.dump(loc, f, ensure_ascii=False)
    print(f"localization_zh.json：ItemTable {len(item_table)} 条，StringTable {len(string_table)} 条")

    items = _rows(os.path.join(args.csv_dir, "ItemInfoData.csv"))
    if not items:
        sys.exit(f"找不到 ItemInfoData.csv：{args.csv_dir}")
    materials = {r["ItemKey"]: r for r in _rows(os.path.join(args.csv_dir, "MaterialInfoData.csv"))}
    gears = {r["GearKey"]: r for r in _rows(os.path.join(args.csv_dir, "GearInfoData.csv"))}

    def cn(k):
        return item_table.get(k) or k

    out_path = os.path.join(args.out, "items_zh.csv")
    written = 0
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TABLE_COLUMNS)
        w.writeheader()
        for r in items:
            name_key = (r.get("NameKey") or "").strip()
            mat = materials.get(r["ItemKey"], {})
            gear = gears.get((r.get("GearKey") or "").strip(), {})
            w.writerow({
                "ItemKey": r["ItemKey"],
                "名称": cn(name_key) if name_key else "",
                "NameKey": name_key,
                "类型": r.get("ITEMTYPE", ""),
                "品阶": r.get("GRADE", ""),
                "部位": r.get("PARTS", ""),
                "装备类型": r.get("GEARTYPE", ""),
                "等级": r.get("Level", ""),
                "堆叠上限": r.get("MaxStack", ""),
                "图标": r.get("IconPath", ""),
                "基础属性1值": gear.get("BaseStat1_Value", ""),
                "基础属性2值": gear.get("BaseStat2_Value", ""),
                "材料类型": mat.get("MATERIALTYPE", ""),
                "仓库槽位": mat.get("IsMaterialStashSlot", ""),
            })
            written += 1
    print(f"items_zh.csv：{written} 条 → {out_path}")


if __name__ == "__main__":
    main()
