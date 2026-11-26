"""TaskbarHero 仓库实时查看器 · v11 PIL 全图合成版。

- 整个界面在 PIL 里合成（面板花纹 + 品阶格底 + 图标 + 数量 + 标题 + 页签 + 关闭钮），
  花纹/装饰完整露出；tkinter 只显示一张图 + 透明热区（点击页签/关闭、悬停 tooltip）
- 格子容器位置取自预制体: x 8..310, y 128..394 (asset)，格 36x36，列距 43、行距 38
- 存档变化自动重合成
"""
import csv
import ctypes
import gzip
import hashlib
import math
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse

if sys.stdout is not None:  # pythonw 下 stdout 为 None,直接 reconfigure 会崩
    sys.stdout.reconfigure(encoding="utf-8")
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from PIL import Image, ImageDraw, ImageFont, ImageTk

__version__ = "1.0.8"

SAVE = os.environ.get("TBH_SAVE") or os.path.expandvars(
    r"%USERPROFILE%\AppData\LocalLow\TesseractStudio\TaskbarHero\SaveFile_Live.es3")
PASSWORD = os.environ.get("TBH_SAVE_PASSWORD", "emuMqG3bLYJ938ZDCfieWJ")
BASE = os.environ.get("TBH_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
ITEMS = os.path.join(BASE, "items_zh.csv")
ICONS = os.path.join(BASE, "icons")
UI = os.path.join(BASE, "ui")

GRADE_TO_BG = {"COMMON": "NORMAL", "UNCOMMON": "UNCOMMON", "RARE": "RARE",
               "ARCANA": "ARCANA", "DIVINE": "DIVINE", "LEGENDARY": "LEGENDARY",
               "IMMORTAL": "IMMORTAL", "BEYOND": "BEYOND", "CELESTIAL": "CELESTIAL",
               "COSMIC": "COSMIC"}
STEAM_APPID = 3678970
STEAM_PROXY = os.environ.get("STEAM_PROXY", "http://127.0.0.1:7890")
CNY_TTL = 300                 # 悬停拉取的人民币价缓存时长（与快刷节奏一致）
GAME_EXE = "taskbarhero.exe"  # 游戏进程名：运行中时查看器自动置顶（联动启动场景）
GAME_EXIT_GRACE = 2.0         # 游戏进程消失后宽限几秒再关查看器（容许游戏重启的空档）

ZMIN, ZMAX = 1.0, 4.0                    # 四角拖拽缩放范围（asset 像素 × zoom）
PREFS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "viewer_prefs.json")
COLS, ROWS = 7, 7
TRANSPARENT_KEY = "#ff00ff"              # 窗口透明键色（顶部/边缘全透明区）


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.SetWindowPos.restype = ctypes.c_bool
_user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_user32.GetAncestor.restype = ctypes.c_void_p
HWND_TOPMOST = -1      # 插入位柄必须按 64 位传（argtypes=c_void_p）：32 位截断后
HWND_NOTOPMOST = -2    # -1 变 0xFFFFFFFF，Windows 视为非法句柄导致 SetWindowPos 失败


def _fg_log(msg):
    """手势诊断日志（排查任务栏点击找回无效用，随时可删）。"""
    try:
        with open(os.path.join(BASE, "fg_debug.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def _proc_name_of_pid(pid):
    """pid 的进程可执行文件名（小写）；失败返回 ''。"""
    if not pid:
        return ""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    except Exception:
        pass
    finally:
        k32.CloseHandle(h)
    return ""


def _game_pids():
    """当前 GAME_EXE 的 PID 列表（ToolHelp 快照：比逐个 OpenProcess 查名字快很多）。"""
    k32 = ctypes.windll.kernel32
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong),
                    ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    k32.Process32FirstW.restype = ctypes.c_bool
    k32.Process32NextW.restype = ctypes.c_bool
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        pids = []
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if (e.szExeFile or "").lower() == GAME_EXE:
                pids.append(int(e.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        return pids
    finally:
        k32.CloseHandle(snap)


def steam_recv_cents(buyer_cents, pub_pct=0.10):
    """Steam 官方收入价：费用按卖家所得计算（Steam 5% + 厂商 10%，各自 floor），
    且 CNY 下两项费用各有人性化最低值 7 分（小额物品实测：
    26+7+7=40、52+7+7=66、110+7+11=128，与 Steam 挂单页/出售弹窗一致）。
    返回满足 R + fees(R) <= buyer 的最大整数 R（精确分）。"""
    if not isinstance(buyer_cents, int) or buyer_cents <= 0:
        return None

    def fees(r):
        return max(int(r * 0.05), 7) + max(int(r * pub_pct), 7)

    r = max(0, buyer_cents - 14)                # 费用下限 14 分，收到额不可能超过它
    while r > 0 and r + fees(r) > buyer_cents:
        r -= 1
    return r


def fmt_cny(cents):
    """分 → '¥ 0.57'（查看器里所有价格统一人民币）。"""
    return f"¥ {cents // 100}.{cents % 100:02d}"


def nearest(path, size=None):
    img = Image.open(path).convert("RGBA")
    if size:
        img = img.resize((size, size), Image.NEAREST)
    return img


def decrypt_save(path):
    raw = open(path, "rb").read()
    salt, body = raw[:16], raw[16:]
    key = hashlib.pbkdf2_hmac("sha1", PASSWORD.encode("utf-8"), salt, 100, dklen=16)
    pt = unpad(AES.new(key, AES.MODE_CBC, salt).decrypt(body), 16)
    if pt[:2] == b"\x1f\x8b":
        pt = gzip.decompress(pt)
    return json.loads(pt.decode("utf-8", "replace"))


def unwrap(o):
    if isinstance(o, dict) and {"__type", "value"} <= o.keys() and isinstance(o["value"], str):
        try:
            return unwrap(json.loads(o["value"]))
        except Exception:
            return o
    if isinstance(o, dict):
        return {k: unwrap(v) for k, v in o.items()}
    if isinstance(o, list):
        return [unwrap(v) for v in o]
    return o


def parse_save():
    root = unwrap(decrypt_save(SAVE))
    p = root["PlayerSaveData"]
    inst = {e["UniqueId"]: e["ItemKey"] for e in p["itemSaveDatas"]}
    stash, inventory, materials = {}, {}, []
    for e in p["stashSaveDatas"]:
        if e.get("ItemUniqueId"):
            stash[e["Index"]] = (str(inst.get(e["ItemUniqueId"], 0)), e.get("Quantity", 1))
    for e in p["inventorySaveDatas"]:
        if e.get("ItemUniqueId"):
            inventory[e["Index"]] = (str(inst.get(e["ItemUniqueId"], 0)), e.get("Quantity", 1))
    for e in p["materialStashSaveDatas"]:
        if e.get("Quantity", 0) > 0:
            materials.append((str(e["ItemKey"]), e["Quantity"]))
    materials.sort(key=lambda kv: int(kv[0]))
    return {"仓库": stash, "背包": inventory, "材料库": materials}


def draw_outlined(draw, x, y, text, font, fill="#ffffff", outline="#000000", anchor="la"):
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=outline, anchor=anchor)
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


GRADE_ZH = {
    "COMMON": "普通级", "UNCOMMON": "精良级", "RARE": "稀有级",
    "ARCANA": "神秘级", "DIVINE": "神圣级", "LEGENDARY": "传说级",
    "IMMORTAL": "永恒级", "BEYOND": "超凡级", "CELESTIAL": "天界级",
    "COSMIC": "宇宙级",
}
GRADE_UI_COLOR = {
    "COMMON": "#e8e8e8", "UNCOMMON": "#1eff00", "RARE": "#3b9bff",
    "ARCANA": "#c26bff", "DIVINE": "#ff8a2a", "LEGENDARY": "#ffcc00",
    "IMMORTAL": "#d4f0ff", "BEYOND": "#00ffc8", "CELESTIAL": "#ff6ec7",
    "COSMIC": "#ff3c3c",
}
PART_ZH = {"MAIN_WEAPON": "主武器", "SUB_WEAPON": "副武器", "HELMET": "头盔",
           "ARMOR": "铠甲", "GLOVES": "手套", "BOOTS": "靴子", "BRACER": "腕甲",
           "RING": "戒指", "AMULET": "护符"}
MAT_ZH = {"DECORATION": "装饰材料", "ENGRAVING": "雕文材料", "INSCRIPTION": "铭文材料",
          "CRAFTING": "工艺材料", "OFFERING": "祭品材料", "SOULSTONE": "灵魂石"}


def type_label(rec):
    t = rec.get("类型", "")
    if t == "GEAR":
        part = PART_ZH.get(rec.get("部位", ""), rec.get("部位", ""))
        return f"⚔ 装备 · {part}"
    if t == "MATERIAL":
        return f"🔶 {MAT_ZH.get(rec.get('材料类型', ''), '材料')}"
    if t == "STAGEBOX":
        return "📦 关卡宝箱"
    return f"◆ {t}"


class ItemTooltip(tk.Toplevel):
    """原版风格物品提示窗：金框深底、标题条、图标/品阶/类型、市场价。
    持久单实例：换内容用 rebuild()——Windows 下频繁 create/destroy 无边框
    Toplevel 会触发 Tcl 原生崩溃（0xD000014F），显隐一律 withdraw/deiconify。"""

    def __init__(self, viewer):
        super().__init__(viewer)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#c9a25a")                     # 外框金色
        self._photos = []

        outer = tk.Frame(self, bg="#c9a25a", padx=2, pady=2)
        outer.pack(fill="both", expand=True)
        self._mid = tk.Frame(outer, bg="#5a4a2e", padx=1, pady=1)
        self._mid.pack(fill="both", expand=True)
        self.withdraw()                                  # 等首次 rebuild 再显示

    def rebuild(self, rec, anchor_widget):
        """重填物品内容并定位到锚点旁显示。"""
        self._photos = []
        for w in self._mid.winfo_children():
            w.destroy()
        mid = self._mid
        box = tk.Frame(mid, bg="#14100f", padx=10, pady=8)
        box.pack(fill="both", expand=True)

        name = rec.get("名称", "未知物品")
        grade = rec.get("品阶", "")
        gc = GRADE_UI_COLOR.get(grade, "#e8e8e8")
        gz = GRADE_ZH.get(grade, grade or "?")

        # 标题条（深底 + 金字，双线框）
        title = tk.Frame(box, bg="#262014", highlightthickness=2,
                         highlightbackground="#c9a25a")
        title.pack(fill="x")
        tk.Label(title, text=name, bg="#262014", fg="#ffd76e",
                 font=("Microsoft YaHei UI", 12, "bold"), pady=4).pack()

        # 图标 + 品阶/类型
        row = tk.Frame(box, bg="#14100f")
        row.pack(fill="x", pady=(8, 4))
        icon_path = rec.get("图标", "")
        fp = os.path.join(ICONS, f"{icon_path}.png") if icon_path else None
        holder = tk.Label(row, bg="#23262d", bd=0)
        if fp and os.path.exists(fp):
            # 与主界面格子一致：品阶底图 CELL 大小 + 图标撑满（ICON_PX）
            v = self.master
            bgimg = self._grade_bg(grade)                       # CELL 大小
            ic = Image.open(fp).convert("RGBA").resize((v.ICON_PX, v.ICON_PX),
                                                       Image.NEAREST)
            cell = Image.new("RGBA", (v.CELL, v.CELL), (0, 0, 0, 0))
            cell.alpha_composite(bgimg)
            cell.alpha_composite(ic, ((v.CELL - v.ICON_PX) // 2,
                                      (v.CELL - v.ICON_PX) // 2))
            ph = ImageTk.PhotoImage(cell)
            self._photos.append(ph)
            holder.configure(image=ph)
        holder.pack(side="left", padx=(0, 10))
        side = tk.Frame(row, bg="#14100f")
        side.pack(side="left", fill="y")
        tk.Label(side, text=gz, bg="#14100f", fg=gc,
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(anchor="w")
        tk.Label(side, text=type_label(rec), bg="#14100f", fg="#ffb84d",
                 font=("Microsoft YaHei UI", 10), anchor="w").pack(anchor="w", pady=(2, 0))

        # 描述（有数据才显示）
        desc = rec.get("_desc")
        if desc:
            tk.Label(box, text=desc, bg="#14100f", fg="#cfc6b8", justify="left",
                     wraplength=300, font=("Microsoft YaHei UI", 9)).pack(fill="x",
                                                                         pady=(6, 2))

        # 市场价格（Steam，人民币）：最低挂牌价来自 priceoverview currency=23 的后台刷新；
        # 尚未取到时暂显美元兜底价，悬停后会现拉人民币价并原位替换（set_cny）
        price = rec.get("_price")
        self._hash = None
        self._lbl_price = None
        self._lbl_recv = None
        self._lbl_ref = None
        self._lbl_now = None
        self._lbl_arrow = None
        if price:
            self._hash = price.get("hash", "")
            tk.Label(box, text="Steam 市场价格（人民币）", bg="#14100f", fg="#f0e6d2",
                     font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(
                fill="x", pady=(8, 0))
            pr = tk.Frame(box, bg="#14100f")
            pr.pack(fill="x")
            cny = price.get("cny_cents")
            has_cny = isinstance(cny, int) and cny > 0
            main_txt = fmt_cny(cny) if has_cny else (
                price.get("price_text", "--") + "  （人民币价获取中…）")
            self._lbl_price = tk.Label(pr, text=main_txt,
                                       bg="#14100f", fg="#ffd76e",
                                       font=("Microsoft YaHei UI", 13, "bold"),
                                       anchor="w")
            self._lbl_price.pack(side="left")
            self._lbl_recv = tk.Label(pr, text="", bg="#14100f", fg="#7fd7ff",
                                      font=("Microsoft YaHei UI", 11), anchor="w")
            if has_cny:
                recv = steam_recv_cents(cny)                  # Steam 官方费用逆推（人民币）
                self._lbl_recv.configure(text=f"  → 收入 {fmt_cny(recv)}")
            self._lbl_recv.pack(side="left")
            tk.Label(pr, text=f"  挂牌 {price.get('listings', 0)} 件",
                     bg="#14100f", fg="#8f8f8f",
                     font=("Microsoft YaHei UI", 9), anchor="w").pack(side="left")
            # 参考价（成交中位）：悬停时从 pricehistory 拉取，抗恶意压价
            pr2 = tk.Frame(box, bg="#14100f")
            pr2.pack(fill="x")
            self._lbl_ref = tk.Label(pr2, text="参考价（24h成交中位）—",
                                     bg="#14100f", fg="#8f8f8f",
                                     font=("Microsoft YaHei UI", 10), anchor="w")
            self._lbl_ref.pack(side="left")
            # 当前成交价 + 24h 趋势箭头（pricehistory 最新点 vs 窗口中位）
            self._lbl_now = tk.Label(pr2, text="", bg="#14100f", fg="#cfc6b8",
                                     font=("Microsoft YaHei UI", 10), anchor="w")
            self._lbl_now.pack(side="left")
            self._lbl_arrow = tk.Label(pr2, text="", bg="#14100f", fg="#8f8f8f",
                                       font=("Microsoft YaHei UI", 12, "bold"),
                                       anchor="w")
            self._lbl_arrow.pack(side="left")
            # Steam 市场页面跳转（steam://openurl → 拉起客户端内置浏览器）
            link = tk.Label(box, text="↗ 在 Steam 客户端中打开", bg="#14100f",
                            fg="#6ab0ff",
                            font=("Microsoft YaHei UI", 9, "underline"), cursor="hand2",
                            anchor="w")
            link.pack(fill="x", pady=(2, 0))
            hname = urllib.parse.quote(self._hash)
            url = f"https://steamcommunity.com/market/listings/{STEAM_APPID}/{hname}"
            link.bind("<Button-1>",
                      lambda e, u=url: os.startfile(f"steam://openurl/{u}"))
        else:
            tk.Label(box, text="Steam 市场：无挂牌", bg="#14100f", fg="#7a736a",
                     font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x",
                                                                      pady=(8, 0))

        # 可交易 / 堆叠
        foot = []
        if rec.get("_marketable"):
            foot.append("可交易")
        if rec.get("堆叠上限"):
            foot.append(f"堆叠上限 {rec['堆叠上限']}")
        if foot:
            tk.Label(box, text="  ·  ".join(foot), bg="#14100f", fg="#7fd7ff",
                     font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", pady=(8, 0))
        upd = rec.get("_prices_updated")
        self._lbl_upd = tk.Label(box, text=f"价格更新: {upd}" if upd else "",
                                 bg="#14100f",
                                 fg="#5a5348", font=("Microsoft YaHei UI", 8),
                                 anchor="w")
        self._lbl_upd.pack(fill="x", pady=(6, 0))

        # 定位（锚点控件右侧，越屏自动翻到左侧）
        sw = self.master.winfo_screenwidth()
        sh = self.master.winfo_screenheight()
        self.update_idletasks()
        ww, wh = self.winfo_reqwidth(), self.winfo_reqheight()
        ax = anchor_widget.winfo_rootx() + anchor_widget.winfo_width() + 8
        ay = anchor_widget.winfo_rooty()
        if ax + ww > sw - 4:
            ax = max(4, anchor_widget.winfo_rootx() - ww - 8)
        ay = min(max(4, ay), sh - wh - 4)
        self.geometry(f"+{ax}+{ay}")
        self.deiconify()
        # 显示时重申置顶并抬到主窗上方：游戏模式下主窗也置顶，若在悬停窗
        # 显示之后发生过层级变动，保证提示窗不被仓库面板压住
        self.attributes("-topmost", True)
        self.lift()

    def set_cny(self, data):
        """人民币行情到达后原位刷新（窗口仍显示同一物品时才被调用）：
        主价格行 = 当前最低挂牌 + 卖家收入；参考价 = 24h 成交中位；
        箭头 = 最低挂牌相对中位的偏离（▲红 挂得比中位高 / ▼绿 低于中位 / — 持平）。"""
        if not self._lbl_ref:
            return

        def _clear_trend():
            if self._lbl_now:
                self._lbl_now.configure(text="")
            if self._lbl_arrow:
                self._lbl_arrow.configure(text="")

        try:
            low = data.get("lowest_cents")
            med = data.get("median_cents")
            has_low = isinstance(low, int) and low > 0
            if has_low and self._lbl_price:
                self._lbl_price.configure(text=fmt_cny(low))
                if self._lbl_recv:
                    recv = steam_recv_cents(low)
                    self._lbl_recv.configure(
                        text=f"  → 收入 {fmt_cny(recv)}" if recv else "")
            if not (isinstance(med, int) and med > 0):
                self._lbl_ref.configure(
                    text="参考价（24h成交中位）—" if has_low else "无挂牌 · 近 24h 无成交",
                    fg="#8f8f8f")
                _clear_trend()
                return
            txt = f"参考价（24h成交中位） {fmt_cny(med)}"
            recv = steam_recv_cents(med)
            if recv:
                txt += f"  → 收入 {fmt_cny(recv)}"
            self._lbl_ref.configure(text=txt, fg="#ffd76e")
            if has_low:
                trend = ("up" if low > med * 1.02
                         else "down" if low < med * 0.98 else "flat")
                arrow, color = {"up": ("▲", "#ff5a52"),
                                "down": ("▼", "#3ddc84")}.get(trend, ("—", "#8f8f8f"))
                if self._lbl_now:
                    self._lbl_now.configure(text=f"  当前最低 {fmt_cny(low)} ",
                                            fg="#cfc6b8")
                if self._lbl_arrow:
                    self._lbl_arrow.configure(text=arrow, fg=color)
            else:
                if self._lbl_now:
                    self._lbl_now.configure(text="  当前无挂牌 ", fg="#cfc6b8")
                if self._lbl_arrow:
                    self._lbl_arrow.configure(text="")
        except Exception:
            pass

    def _grade_bg(self, grade):
        gname = GRADE_TO_BG.get(grade, "NORMAL")
        fp = os.path.join(UI, f"ItemSlot_GradeBg_{gname}.png")
        return Image.open(fp).convert("RGBA").resize((self.master.CELL,
                                                      self.master.CELL),
                                                     Image.NEAREST)


class Viewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.configure(bg=TRANSPARENT_KEY)
        self.attributes("-transparentcolor", TRANSPARENT_KEY)
        # 不置顶：其他程序激活时自然盖到本窗前面（悬停提示窗仍置顶保证可见）
        # 四角缩放：不可见纯几何热区（全局绑定捕获，光标形状提示可拖拽）
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_motion, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        # 悬停闸门：Windows 只把鼠标事件投递给指针下的窗口。查看器被其他窗口
        # 盖住时收不到 <Motion>，据此判定"当前真的在与人交互"，悬停窗不误弹
        self._motion_ts = 0.0
        self.bind("<Motion>", self._on_motion_seen, add="+")
        # 尺寸与位置由 _relayout/_geometry_apply 按缩放系数设置
        self.bind("<Escape>", lambda e: self.destroy())
        self.items = {r["ItemKey"]: r for r in
                      csv.DictReader(open(ITEMS, encoding="utf-8-sig"))}
        try:
            self.market = {r["ItemKey"]: r.get("IsCanExchangeMarketable") == "True"
                           for r in csv.DictReader(
                               open(os.path.join(BASE, "csv", "ItemInfoData.csv"),
                                    encoding="utf-8-sig"))}
        except Exception:
            self.market = {}
        # Steam 市场价格缓存 + CNY 实时价缓存（悬停按需拉取）
        self.prices = {}
        self._load_prices()
        self._cny_cache = {}
        self._cny_lock = threading.Lock()
        self._cny_last = 0.0
        self._cny_client = None
        self._cny_inflight = set()
        self._cny_done = {}          # 拉取线程写入，主线程 _cny_drain 轮询消费
        self._cny_cooldown = 0.0     # 429 冷却截止时间
        # 描述映射（材料类 149 条）
        self.desc = {}
        try:
            import csv as _csv
            loc = json.load(open(os.path.join(BASE, "localization_zh.json"),
                                 encoding="utf-8"))["ItemTable"]
            for r in _csv.DictReader(open(os.path.join(BASE, "csv", "ItemInfoData.csv"),
                                          encoding="utf-8-sig")):
                dk = (r.get("DescriptionKey") or "").strip()
                if dk and dk in loc:
                    self.desc[r["ItemKey"]] = loc[dk]
        except Exception:
            pass
        threading.Thread(target=self._price_loop, daemon=True).start()
        # 悬停轮询前置状态（_rebuild_hotspots 会填充，先初始化防空窗期报错）
        self._cell_recs = {}
        self._cell_labels = {}
        self._tip_cell = None
        self._tip_key = None
        self._tipwin = None
        self._tip_shown = False
        self.after(150, self._cny_drain)
        self.after(120, self._hover_poll)
        self.data = {"仓库": {}, "背包": {}, "材料库": []}
        self.tab = ("仓库", 0)
        self._last_mtime = 0
        self._last_sig = None
        self._img_cache = {}
        self._zdrag = None
        self._corner_cur = None

        # 缩放偏好 → 布局常量 → 窗口尺寸 → 尺寸相关素材
        self.zoom = self._load_zoom()
        self._relayout()
        self._geometry_apply(center=True)

        # 素材（与缩放无关的源图；尺寸相关的在 _relayout/_build_scale_assets）
        self._panel_src = nearest(os.path.join(UI, "Ui_Stash_Bg.png"))          # 318x468 原始
        self._title_src = Image.open(os.path.join(UI, "TextImage_Stash_SC.png")).convert("RGBA")
        self._tab_active_src = Image.open(os.path.join(UI, "TabButton_Stash_Active.png")).convert("RGBA").crop((2, 6, 34, 28))
        self._tab_stem_src = Image.open(os.path.join(UI, "TabButton_Stash_Active.png")).convert("RGBA").crop((2, 30, 34, 36))
        self._tab_inactive_src = Image.open(os.path.join(UI, "TabButton_Stash_DeActive_Active.png")).convert("RGBA").crop((2, 6, 34, 28))
        self._tab_dlc_src = Image.open(os.path.join(UI, "TabButton_Stash_DLC_Active.png")).convert("RGBA").crop((2, 6, 34, 28))
        # 底部手动刷新按钮（原版 Button_Large 底图）
        self._btn_src = Image.open(os.path.join(UI, "Btn_Large_Plain.png")).convert("RGBA")
        # 棕色原色版（由 DeActive 按原版小按钮的棕色色板重绘，只在正常/成功状态用）
        self._btn_on_src = Image.open(os.path.join(UI, "Btn_Large_Active.png")).convert("RGBA")
        self._mkt_busy = False
        self._mkt_dirty = False
        self._mkt_status = ""
        self._mkt_lock = threading.Lock()
        self._stop_evt = threading.Event()   # 置位 = 通知正在跑的刷新停下来（退出时用）
        self._mkt_fill = "#e8d9b0"
        self._mkt_pct = None                # 0..100 = 画进度条；None = 不画
        self._mkt_anim = False               # True = 状态按钮播放旋转图标（主线程 _anim_tick 重画）
        self._anim_scheduled = False

        self._base_holder = tk.Label(self, borderwidth=0, highlightthickness=0,
                                     bg=TRANSPARENT_KEY)
        self._base_holder.place(x=0, y=0)
        self._base_holder.bind("<ButtonPress-1>", self._drag_start)
        self._base_holder.bind("<B1-Motion>", self._drag_move)

        self._hotspots = []
        self._pages_def = []
        self.compose()
        self._rebuild_hotspots()
        self.after(500, self._poll)

    # ---------- 布局 / 缩放 ----------
    def _load_zoom(self):
        """默认 1.0 = 原版游戏内 318x468 的原始尺寸。"""
        try:
            z = float(json.load(open(PREFS_PATH, encoding="utf-8")).get("zoom", 1.0))
        except Exception:
            z = 1.0
        return min(ZMAX, max(ZMIN, z))

    def _save_zoom(self):
        """保存缩放系数。"""
        try:
            json.dump({"zoom": round(self.zoom, 3)},
                      open(PREFS_PATH, "w", encoding="utf-8"))
        except Exception:
            pass

    def _relayout(self):
        """按 zoom 重算全部布局常量与尺寸相关素材（asset 像素 × zoom）。"""
        Z = self.zoom
        self.W = int(318 * Z)
        self.H = int(468 * Z)
        self.INNER_X0 = int(8 * Z) + int(14 * Z)
        self.INNER_Y0 = int(115 * Z) + int(14 * Z)
        self.INNER_X1 = int(8 * Z) + int(287 * Z)
        self.INNER_Y1 = int(115 * Z) + int(287 * Z)
        self.CELL = int(36 * Z)
        self.COL_PITCH = (self.INNER_X1 - self.INNER_X0 - self.CELL) / (COLS - 1)
        self.ROW_PITCH = (self.INNER_Y1 - self.INNER_Y0 - self.CELL) / (ROWS - 1)
        self.ICON_PX = max(8, self.CELL - int(3 * Z))
        self.TITLE_X = (self.W - int(160 * Z)) // 2
        self.TITLE_Y = int(40 * Z) - 2
        self.CLOSE_X = self.W - int(35 * Z)
        self.CLOSE_Y = int(35 * Z)
        self.CLOSE_S = int(32 * Z)
        self.TAB_X0 = int(10 * Z)
        self.TAB_STEP = int(37.5 * Z)
        self.BORDER_Y = int(114 * Z)
        self._cell_pos_list = [(i, self.cell_pos(i)) for i in range(49)]
        try:
            self.qty_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf",
                                               max(9, int(10 * Z)))
            self.tab_font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc",
                                               max(9, int(9 * Z)))
            self.sw_font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc",
                                              max(8, int(7 * Z)))
        except Exception:
            self.tab_font = self.qty_font
            self.sw_font = self.qty_font
        self._img_cache = {}                    # 尺寸相关的缓存全部失效
        self._build_scale_assets()

    def _build_scale_assets(self):
        self._close_src = nearest(os.path.join(UI, "Button_Close_Active.png"),
                                  self.CLOSE_S)
        self._close_hover_src = nearest(os.path.join(UI, "Button_Close_Hover.png"),
                                        self.CLOSE_S)
        self._slot_bg = {g: nearest(os.path.join(UI, f"ItemSlot_{g}.png"), self.CELL)
                         for g in ["Bg"] + [f"GradeBg_{v}" for v in GRADE_TO_BG.values()]}

    def cell_pos(self, i):
        """格位 i (0..48) -> 显示坐标（左上角），落位在装饰层内部净空区。"""
        r, c = divmod(i, COLS)
        return (int(self.INNER_X0 + c * self.COL_PITCH),
                int(self.INNER_Y0 + r * self.ROW_PITCH))

    def _geometry_apply(self, center=False, anchor=None):
        """按当前 W/H 设窗口尺寸：center 首次居中；anchor=(corner,ax,ay) 缩放时
        保持对角锚点屏幕位置不动。"""
        w, h = self.W, self.H
        if anchor:
            corner, ax, ay = anchor
            x = ax if "e" in corner else ax - w
            y = ay if "s" in corner else ay - h
            self.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
            return
        if center:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
        else:
            self.geometry(f"{w}x{h}+{self.winfo_x()}+{self.winfo_y()}")

    # ---------- 四角拖拽缩放（不可见纯几何热区） ----------
    def _corner_at(self, lx, ly):
        """窗口内坐标 -> 四角缩放热区名，不在热区返回 None。"""
        g = max(12, int(12 * self.zoom))
        if lx < g or lx >= self.W - g:
            if ly < g:
                return "nw" if lx < g else "ne"
            if ly >= self.H - g:
                return "sw" if lx < g else "se"
        return None

    def _on_press(self, e):
        self.lift()
        try:
            lx = e.x_root - self.winfo_rootx()   # 统一换算成窗口内坐标
            ly = e.y_root - self.winfo_rooty()
        except Exception:
            return
        corner = self._corner_at(lx, ly)
        if corner:
            self._zoom_start(corner)

    def _on_motion(self, e):
        if self._zdrag:
            self._zoom_move(e)

    def _on_motion_seen(self, e):
        """任意指针移动（含子控件）：悬停闸门的时间戳。"""
        self._motion_ts = time.time()

    def _on_release(self, e):
        if self._zdrag:
            self._zoom_end(e)

    def _zoom_start(self, corner):
        wx, wy = self.winfo_x(), self.winfo_y()
        ax = wx if "e" in corner else wx + self.W     # 对角锚点（固定不动）
        ay = wy if "s" in corner else wy + self.H
        px, py = self.winfo_pointerx(), self.winfo_pointery()
        self._zdrag = {"corner": corner, "px": px, "py": py,
                       "z0": self.zoom, "ax": ax, "ay": ay,
                       "d0": max(1.0, math.hypot(px - ax, py - ay))}

    def _zoom_move(self, e):
        if not self._zdrag:
            return
        self._zdrag["lx"], self._zdrag["ly"] = e.x_root, e.y_root
        if not getattr(self, "_zdrag_scheduled", False):
            self._zdrag_scheduled = True
            self.after(60, self._zoom_apply)

    def _zoom_apply(self):
        self._zdrag_scheduled = False
        if not self._zdrag:
            return
        zg = self._zdrag
        lx = zg.get("lx", zg["px"])
        ly = zg.get("ly", zg["py"])
        d1 = math.hypot(lx - zg["ax"], ly - zg["ay"])
        z = min(ZMAX, max(ZMIN, zg["z0"] * d1 / zg["d0"]))
        if abs(z - self.zoom) < 0.02:
            return
        self.zoom = z
        self._relayout()
        self._geometry_apply(anchor=(zg["corner"], zg["ax"], zg["ay"]))
        self.compose()
        self._rebuild_hotspots()

    def _zoom_end(self, e):
        if self._zdrag:
            self._save_zoom()
        self._zdrag = None

    # ---------- 合成 ----------
    def _img(self, key, loader):
        if key not in self._img_cache:
            self._img_cache[key] = loader()
        return self._img_cache[key]

    def compose(self):
        Z = self.zoom
        base = self._panel_src.resize((self.W, self.H), Image.NEAREST).convert("RGBA")
        d = ImageDraw.Draw(base)

        # InStashEdge 装饰层（四角回形花纹 + 格区边框线，预制体 302x302 @ 面板 (8,115)）
        if "decor" not in self._img_cache:
            self._img_cache["decor"] = nearest(
                os.path.join(UI, "Ui_Stash_Bg_Decor.png"),
            ).resize((int(302 * Z), int(302 * Z)), Image.NEAREST)
        base.alpha_composite(self._img_cache["decor"], (int(8 * Z), int(115 * Z)))

        # 页签图形：未选中先画；选中的放大上移，最后再画保证完整显示
        tab_font = self.tab_font
        pages = ([("仓库", i) for i in range(self._pages_for("仓库"))] +
                 [("背包", 0), ("材料库", 0)])
        self._pages_def = pages
        self._tab_rects = []
        x = self.TAB_X0
        active_rect = None
        for i, key in enumerate(pages):
            active = key == self.tab
            gw, gh = ((int(36 * Z), int(27 * Z)) if active
                      else (int(32 * Z), int(22 * Z)))
            y_abs = ((self.BORDER_Y - int(30 * Z)) if active
                     else (self.BORDER_Y - gh))            # 选中再上移
            x_abs = x - (int(2 * Z) if active else 0)
            if not active:
                img = self._tab_inactive_src.resize((gw, gh), Image.NEAREST)
                base.alpha_composite(img, (x_abs, y_abs))
                label = str(key[1] + 1) if key[0] == "仓库" else {"背包": "包", "材料库": "材"}[key[0]]
                draw_outlined(d, x_abs + gw // 2, y_abs + gh // 2, label, tab_font,
                              anchor="mm")
            else:
                active_rect = (x_abs, y_abs, gw, gh, key)
            self._tab_rects.append((x_abs, y_abs, gw, gh, key))
            x += self.TAB_STEP
        # DLC 页签（绿色，未选中态尺寸，静态占位）
        img = self._tab_dlc_src.resize((int(32 * Z), int(22 * Z)), Image.NEAREST)
        base.alpha_composite(img, (x, self.BORDER_Y - int(22 * Z)))

        # 标题 + 关闭钮
        base.alpha_composite(self._title_src.resize((int(160 * Z), int(28 * Z)),
                                                    Image.NEAREST),
                             (self.TITLE_X, self.TITLE_Y))
        close_img = self._close_hover_src if getattr(self, "_close_hot", False) else self._close_src
        base.alpha_composite(close_img, (self.CLOSE_X, self.CLOSE_Y))

        # 格子（当前页 49 槽）
        slots = self._slot_map()
        for i in range(49):
            cx, cy = self.cell_pos(i)
            if i in slots:
                k, q = slots[i]
                rec = self.items.get(str(k), {})
                grade = rec.get("品阶", "")
                bg = self._slot_bg[f"GradeBg_{GRADE_TO_BG.get(grade, 'NORMAL')}"]
                base.alpha_composite(bg, (cx, cy))
                icon_path = rec.get("图标", "")
                fp = os.path.join(ICONS, f"{icon_path}.png") if icon_path else None
                if fp and os.path.exists(fp):
                    icon = self._img(("icon", k), lambda fp=fp: nearest(fp, self.ICON_PX))
                    base.alpha_composite(icon, (cx + 3, cy + 3))
                try:
                    qn = int(q)
                except Exception:
                    qn = 1
                if qn != 1:
                    draw_outlined(d, cx + self.CELL - int(13 * Z),
                                  cy + self.CELL - int(15 * Z), f"x{qn}",
                                  self.qty_font)
                rec["_tip"] = (f"{rec.get('名称', f'未知({k})')}\n"
                               f"品阶: {grade or '?'}   类型: {rec.get('类型', '?')}\n"
                               f"{self.tab[0]} 槽位 {self.tab[1] * 49 + i} · 数量 {qn} · ItemKey {k}")
            else:
                base.alpha_composite(self._slot_bg["Bg"], (cx, cy))

        # 底部状态按钮 + 价格更新开关：只记矩形，绘制放在透明键色处理之后（_repaint_bottom）
        Z = self.zoom
        # 底部空档里居中：格区边框下沿 ≈414，面板内框 ≈460 → 空档中心 ≈437，按钮 38 → 顶 ≈418
        self._refresh_btn_rect = (int(92 * Z), int(419 * Z), int(134 * Z), int(38 * Z))

        # 选中的页签最后绘制（放大 + 上移；底部突起与边框同色衔接，完整显示在最上层）
        if active_rect:
            x_abs, y_abs, gw, gh, key = active_rect
            img = self._tab_active_src.resize((gw, gh), Image.NEAREST)
            base.alpha_composite(img, (x_abs, y_abs))
            # 突起：取激活图底部框带向下延伸，宽度与上方页签一致，盖过边框线
            stem_h = self.BORDER_Y + int(2 * Z) - (y_abs + gh)   # 伸到边框线下沿
            stem = self._tab_stem_src.resize((gw, max(1, stem_h)), Image.NEAREST)
            base.alpha_composite(stem, (x_abs, y_abs + gh))
            label = str(key[1] + 1) if key[0] == "仓库" else {"背包": "包", "材料库": "材"}[key[0]]
            draw_outlined(d, x_abs + gw // 2, y_abs + gh // 2, label, tab_font, anchor="mm")

        # 顶部/边缘全透明区压成透明键色（窗口 -transparentcolor 抠除 → 真透明）
        alpha0 = base.split()[3].point(lambda a: 255 if a == 0 else 0)   # 全透明掩码
        key_img = Image.new("RGBA", (self.W, self.H), (255, 0, 255, 255))
        base = Image.composite(key_img, base, alpha0)
        self._canvas_pre = base        # 未画底部控件的底图：动画/状态变化时只重画底部
        self._repaint_bottom()

    # ---------- 底部：状态按钮 + 价格更新开关 ----------
    @staticmethod
    def _keyed(img):
        """小图的全透明像素压成透明键色（与整图处理一致），以便直接贴到底图上。"""
        a0 = img.split()[3].point(lambda a: 255 if a == 0 else 0)
        return Image.composite(Image.new("RGBA", img.size, TRANSPARENT_KEY), img, a0)

    def _status_view(self):
        """状态按钮的 (文字, 颜色, 是否播放旋转图标)。"""
        return (getattr(self, "_mkt_status", "") or "刷新市场",
                getattr(self, "_mkt_fill", "#e8d9b0"),
                getattr(self, "_mkt_anim", False),
                getattr(self, "_mkt_pct", None))

    def _set_status(self, text, anim=False, fill="#e8d9b0", pct=None):
        """设置状态按钮。pct=0..100 时在按钮下方画进度条（None 不画）。
        后台线程可调：只写属性，由主线程 _poll / _anim_tick 重绘。"""
        self._mkt_status = text
        self._mkt_fill = fill
        self._mkt_anim = anim
        self._mkt_pct = pct
        self._mkt_dirty = True

    def _result_view(self, up):
        """一轮人民币刷新结束后的按钮状态 → (文字, 颜色)。
        三种含义要能一眼分开：
          已完成 · N 件      本轮成功结束（N=本轮实际更新件数，0 表示仓库里的价都还新鲜）
          限流 · 已存 N 件    逐件接口被 429，市场页兜底补到了 N 件，剩下的等冷却结束再刷
          限流中 · HH:MM 重试 这轮一件都没取到（逐件 429 + 兜底也没成），到点自动重试"""
        r = up.LAST_RUN.get("result")
        n = up.LAST_RUN.get("updated") or 0
        if r == "cooldown":
            t = time.strftime("%H:%M", time.localtime(time.time() + up.overview_cooldown_left() + 30))
            if n:
                return f"限流 · 已存 {n} 件", "#ffb36b"
            return f"限流中 · {t} 重试", "#ffb36b"
        if r == "stopped":
            return "已暂停", "#8d8a85"
        if r == "failed":
            return "更新失败 · 将重试", "#ff8a80"
        return "已完成 · %d 件" % n, "#e8d9b0"

    def _mk_plog(self, phase):
        """给 update_prices 的 log 回调：进度行 → 按钮显示「<phase> xx%」并转圈；
        其余行写 prices_log.txt。"""
        def plog(msg):
            s = str(msg).strip()
            m = re.match(r"(?:回退)?进度 (\d+)/(\d+)", s)
            if m:
                pct = int(m.group(1)) * 100 // max(1, int(m.group(2)))
                self._set_status(f"{phase} {pct}%", anim=True, pct=pct)
            elif s:
                self._price_log(s)
        return plog

    def _draw_spinner(self, d, cx, cy):
        """像素风旋转图标：8 个方块围一圈，亮块 + 两格拖尾，帧号取自系统时间。"""
        Z = self.zoom
        ring = ((0, -1), (0.71, -0.71), (1, 0), (0.71, 0.71),
                (0, 1), (-0.71, 0.71), (-1, 0), (-0.71, -0.71))
        frame = int(time.time() * 8) % 8
        for i, (ux, uy) in enumerate(ring):
            k = (i - frame) % 8
            col = ("#fff3b0", "#ffd76e", "#b08a3c")[k] if k < 3 else "#6b5a33"
            s = max(2, int(round((2.6 if k < 2 else 2.1) * Z)))
            x, y = cx + ux * 4.6 * Z, cy + uy * 4.6 * Z
            x0, y0 = int(x - s / 2), int(y - s / 2)
            d.rectangle((x0, y0, x0 + s, y0 + s), fill=col)

    def _repaint_bottom(self):
        """只重画底部两个控件（状态按钮 + 开关），其余沿用 compose 存下的 _canvas_pre。
        旋转动画每 125ms 走一次，不必整图重新合成。"""
        pre = getattr(self, "_canvas_pre", None)
        if pre is None:
            return
        base = pre.copy()
        Z = self.zoom
        bx, by, bw, bh = self._refresh_btn_rect
        text, fill, anim, pct = self._status_view()
        # 正常/成功/更新中用原版棕色（Active），限流或失败才回落到灰底（DeActive）
        src_img = self._btn_on_src if ("限流" not in text and "失败" not in text) else self._btn_src
        btn = src_img.resize((bw, bh), Image.NEAREST)
        d = ImageDraw.Draw(btn)
        if anim:
            self._draw_spinner(d, int(18 * Z), bh // 2)
        draw_outlined(d, bw // 2 + (int(8 * Z) if anim else 0),
                      bh // 2 - (int(2 * Z) if pct is not None else 0), text,
                      self.tab_font, fill=fill, anchor="mm")
        if pct is not None:
            # 进度条：按钮底部一条细金条（像素风：深色凹槽 + 金色已完成部分）
            P = lambda v: max(1, int(round(v * Z)))
            gx0, gx1 = int(14 * Z), bw - int(14 * Z)
            gy0, gy1 = bh - int(8 * Z), bh - int(5 * Z)
            d.rectangle((gx0, gy0, gx1, gy1), fill="#1c1612")
            fw = int((gx1 - gx0 + 1) * max(0, min(100, pct)) / 100)
            if fw > 0:
                d.rectangle((gx0, gy0, gx0 + fw - 1, gy1), fill="#ffd76e")
        base.paste(self._keyed(btn), (bx, by))
        self._base = base
        ph = ImageTk.PhotoImage(base)
        self._photos = [ph]
        self._base_holder.configure(image=ph)
        # 这两个控件的热区是盖在底图上的不透明小图：底图变了它们必须跟着换，
        # 否则滑块、状态文字和旋转图标都会停在热区创建时的那一帧（点了没反应的假象）
        for hs, rect in ((getattr(self, "_btn_hs", None), self._refresh_btn_rect),):
            if hs is not None and hs.winfo_exists():
                hph = self._crop_photo(*rect)
                hs.configure(image=hph)
                hs.image = hph

    def _anim_tick(self):
        self._anim_scheduled = False
        if self._mkt_anim:
            self._repaint_bottom()
            self._anim_scheduled = True
            self.after(125, self._anim_tick)

    def _pages_for(self, tab):
        if tab == "仓库":
            idxs = self.data["仓库"].keys()
            return max(1, (max(idxs) // 49 + 1) if idxs else 1)
        if tab == "背包":
            idxs = self.data["背包"].keys()
            return max(1, (max(idxs) // 49 + 1) if idxs else 1)
        return max(1, (len(self.data["材料库"]) + 49 - 1) // 49)

    def _slot_map(self):
        tab, pi = self.tab
        start = pi * 49
        if tab in ("仓库", "背包"):
            src = self.data[tab]
            return {i: src[start + i] for i in range(49) if start + i in src}
        return {i: kv for i, kv in enumerate(self.data["材料库"][start:start + 49])}

    # ---------- 热区 ----------
    def _crop_photo(self, x, y, w, h):
        return ImageTk.PhotoImage(self._base.crop((x, y, x + w, y + h)))

    def _clear_hotspots(self):
        # 注意：这里不销毁悬停窗——游戏自动存档每几秒触发一次重建，
        # 销毁会让悬停窗跟着存档节奏消失。是否刷新/销毁交给 _hover_poll
        # 按「格位+物品」对账（120ms 内自动收敛）。
        for h in self._hotspots:
            h.destroy()
        self._hotspots = []
        self._btn_hs = None

    def _add_hotspot(self, x, y, w, h, on_click=None):
        ph = self._crop_photo(x, y, w, h)
        # 背景取面板石纹底色：按钮图边缘的半透明阴影若落在浅色默认背景上会被衬出一圈亮边
        lbl = tk.Label(self, image=ph, borderwidth=0, highlightthickness=0,
                       bg="#2b2b2b",
                       cursor="hand2" if on_click else "arrow")
        lbl.image = ph
        lbl.place(x=x, y=y, width=w, height=h)
        if on_click:
            lbl.bind("<Button-1>", on_click)
        self._hotspots.append(lbl)
        return lbl

    # ---------- 物品悬停提示窗 ----------
    def _manual_refresh(self):
        """底部状态按钮：手动刷新仓库/背包物品的人民币价（与后台自动刷新互斥）。"""
        if self._mkt_busy:
            return                                  # 开关关闭时按钮不响应
        if not self._mkt_lock.acquire(blocking=False):
            self._set_status("自动刷新中…")
            return
        self._mkt_busy = True
        self._set_status("更新中 0%", anim=True)
        threading.Thread(target=self._manual_refresh_run, daemon=True).start()

    def _manual_refresh_run(self):
        """后台执行 fast_refresh；状态写回 _mkt_status，由主线程 _poll / _anim_tick 重绘。"""
        try:
            sys.path.insert(0, BASE)
            import update_prices as up

            # 只刷仓库/背包里的物品（与后台自动刷新范围一致），且只补已经过期的那些
            up.fast_refresh(priority=self._owned_keys(), log=self._mk_plog("更新中"),
                            only_priority=True, on_save=self._load_prices,
                            stop=self._stop_evt)
            if self._stop_evt.is_set():
                self._set_status("已暂停", fill="#8d8a85")
            else:
                self._set_status(*self._result_view(up))
            self._price_log("manual refresh 完成")
            self._load_prices()
        except Exception as e:
            self._price_log(f"manual refresh 失败: {type(e).__name__}: {e}")
            self._set_status("更新失败", fill="#ff8a80")
        finally:
            self._mkt_anim = False
            self._mkt_busy = False
            self._mkt_lock.release()
            self._mkt_dirty = True

    def _load_prices(self):
        try:
            j = json.load(open(os.path.join(BASE, "item_prices.json"), encoding="utf-8"))
            self.prices = j.get("prices", {})
            self.prices_updated = j.get("updated", "")
        except Exception:
            self.prices = {}
            self.prices_updated = ""

    def _price_log(self, msg):
        """价格维护日志落文件：pythonw 下 print 全部丢失，异常不可见。"""
        try:
            with open(os.path.join(BASE, "prices_log.txt"), "a",
                      encoding="utf-8") as f:
                f.write(time.strftime("%m-%d %H:%M:%S ") + str(msg) + "\n")
        except Exception:
            pass

    def _owned_keys(self):
        """当前存档里仓库/背包/材料库中的物品 ItemKey 集合（人民币刷新优先处理）。"""
        keys = set()
        try:
            for tab in ("仓库", "背包"):
                for item_key, _qty in self.data.get(tab, {}).values():
                    keys.add(str(item_key))
            for item_key, _qty in self.data.get("材料库", []):
                keys.add(str(item_key))
        except Exception:
            pass
        return keys

    def _price_loop(self):
        """价格维护调度（常开）：
          只维护仓库/背包/材料库里的物品，每 2 小时一轮。
          每轮先逐件 priceoverview(currency=23)（最省流量）；一旦被 429 限流且已登录，
          本轮改用市场搜索页按关键字补 1 小时 45 分以上没更新的物品。
          每落盘一次就重载价格表。底部按钮可随时手动刷一轮（限流时也照刷）。
        启动时立即做一轮；全量扫描已彻底去掉（Steam 的列表 JSON 接口已下线）。"""
        sys.path.insert(0, BASE)
        try:
            import update_prices as up
        except Exception as e:
            self._price_log(f"update_prices import 失败: {e}")
            return

        def reload_():
            self._load_prices()       # 只替换数据、不碰 Tk 控件，后台线程可调

        self._price_log(f"price loop started · v{__version__}")
        last_fast = 0.0
        while True:
            now = time.time()
            try:
                if now - last_fast >= up.FAST_INTERVAL:
                    owned = self._owned_keys()
                    if not owned:
                        # 存档里暂时读不到仓库/背包物品（存档未加载/为空）：什么都不请求，1 分钟后再看
                        # 存档还没解析完（启动瞬间读不到）：10 秒后再看，别白等一轮
                        last_fast = time.time() - up.FAST_INTERVAL + 10
                        if not getattr(self, "_owned_empty_logged", False):
                            self._owned_empty_logged = True
                            self._price_log("仓库/背包为空，跳过人民币刷新（10 秒后再检查）")
                    else:
                        self._owned_empty_logged = False
                        if self._mkt_lock.acquire(blocking=False):
                            try:
                                self._set_status("更新中 0%", anim=True, pct=0)
                                # 只有本次新出现的 ItemKey 才解析市场名（最多 5 件）；
                                # 以前就缺条目的历史积压每轮最多补 1 件，别把一轮拖长
                                seen = getattr(self, "_owned_seen", set())
                                new_keys = owned - seen
                                try:
                                    up.sync_new_items(owned, new_keys=new_keys,
                                                      log=lambda m: self._price_log(m))
                                except Exception as e:
                                    self._price_log(f"[names] {type(e).__name__}: {e}")
                                self._owned_seen = set(owned)
                                up.fast_refresh(priority=owned, only_priority=True,
                                                log=self._mk_plog("更新中"),
                                                on_save=reload_, stop=self._stop_evt)
                                reload_()
                                if self._stop_evt.is_set():
                                    self._set_status("已暂停", fill="#8d8a85")
                                else:
                                    self._set_status(*self._result_view(up))
                            except Exception as e:
                                self._price_log(f"[prices] {type(e).__name__}: {e}")
                                self._set_status("更新失败", fill="#ff8a80")
                            finally:
                                last_fast = time.time()
                                if up.overview_in_cooldown():
                                    # 本轮被限流打断：冷却一过就重试，不等满间隔（冷却时长逐级递增）
                                    last_fast -= up.FAST_INTERVAL - up.overview_cooldown_left() - 30
                                self._mkt_anim = False
                                self._mkt_lock.release()
                                self._mkt_dirty = True
                        else:
                            self._price_log("auto skip: manual busy")
            except Exception as e:
                self._price_log(f"[prices] {type(e).__name__}: {e}")
            time.sleep(10)

    # ---------- 物品悬停提示窗（单实例；指针离开格子和提示窗即销毁，不堆叠） ----------
    def _spawn_tip_at(self, i, rec):
        """在第 i 格弹出物品提示窗（持久单实例换内容，避免 Toplevel 反复销毁）。"""
        if getattr(self, "_tipwin", None) is None:
            self._tipwin = ItemTooltip(self)
        rec = dict(rec)
        k = str(rec.get("ItemKey", ""))
        rec["_marketable"] = self.market.get(k, False)
        rec["_price"] = self.prices.get(k)
        rec["_desc"] = self.desc.get(k)
        rec["_prices_updated"] = getattr(self, "prices_updated", "")
        self._tip_cell = i
        self._tip_key = str(rec.get("ItemKey", ""))
        self._tipwin.rebuild(rec, self._cell_labels.get(i))
        # 先用价格表里的现成数据把「最低价 + 24h 成交中位」立刻填上（每 2 小时一轮维护），
        # 不必等悬停现拉；现拉只用于把这一件刷到最新。
        p = self.prices.get(k) or {}
        if isinstance(p.get("cny_cents"), int) or isinstance(p.get("cny_median_cents"), int):
            self._tipwin.set_cny({"lowest_cents": p.get("cny_cents"),
                                  "median_cents": p.get("cny_median_cents")})
        self._tip_shown = True
        # 参考价：缓存命中立即刷，未命中且不在 429 冷却时后台拉取
        h = getattr(self._tipwin, "_hash", None)
        if h:
            cached = self._cny_cache.get(h)
            if cached and time.time() - cached[0] < CNY_TTL:
                if cached[1]:
                    self._tipwin.set_cny(cached[1])
            elif (time.time() >= self._cny_cooldown
                  and h not in self._cny_inflight):
                threading.Thread(target=self._cny_fetch, args=(h,), daemon=True).start()

    def _cny_fetch(self, h):
        """防重入包装：同一 hash 的拉取在飞时不再起线程（悬停反复进出/重建
        热区会在缓存写入前重复触发）。"""
        if time.time() < self._cny_cooldown or h in self._cny_inflight:
            return
        self._cny_inflight.add(h)
        try:
            self._cny_fetch_run(h)
        finally:
            self._cny_inflight.discard(h)

    def _cny_fetch_run(self, h):
        """悬停时现拉单件人民币行情：update_prices.cny_overview（匿名 priceoverview
        currency=23，不需要登录态），与后台刷新共用进程内限速。
        结果写入 _cny_done，由主线程 _cny_drain 消费（后台线程禁止调 Tk）。"""
        try:
            sys.path.insert(0, BASE)
            import update_prices as up
            res = up.cny_overview(h)
        except Exception as e:
            print(f"[cny] {h[:40]}: {type(e).__name__}: {e}", flush=True)
            return
        if res == "cooldown":
            self._cny_cooldown = time.time() + up.overview_cooldown_left()
            # 逐件接口被限流（429）且已登录：改用市场搜索页取这一件（一张页面约 645KB）
            try:
                if up.get_auth().cookies():
                    res = up.price_one_via_page(h)
            except Exception:
                res = None
            if not res:
                return
            self._cny_cooldown = 0.0
        out = {}
        if res:
            # 现拉没给中位（近 24h 无成交）时，退回价格表里的中位，别把已显示的刷成“—”
            k = getattr(self, "_tip_key", None)
            med = res.get("cny_median_cents")
            if med is None and k:
                med = (self.prices.get(k) or {}).get("cny_median_cents")
            out = {"lowest_cents": res.get("cny_cents"), "median_cents": med}
            # 写回内存价格表：下次悬停即使缓存过期也能立刻显示人民币
            # （只写有值的字段，别用现拉的 None 覆盖掉表里已有的中位价）
            if k and self.prices.get(k, {}).get("hash") == h:
                self.prices[k].update({kk: vv for kk, vv in res.items() if vv is not None})
        self._cny_cache[h] = (time.time(), out)
        self._cny_done[h] = out

    def _cny_drain(self):
        """主线程轮询：消费拉取线程写入的 CNY 结果并刷新当前悬停窗。"""
        if self._cny_done:
            for h in list(self._cny_done):
                d = self._cny_done.pop(h)
                self._apply_cny(h, d)
        self.after(150, self._cny_drain)

    def _apply_cny(self, h, d):
        tw = getattr(self, "_tipwin", None)
        if d and tw and getattr(tw, "_hash", None) == h:
            tw.set_cny(d)

    def _hover_poll(self):
        """每 120ms：指针在当前提示窗上 → 保持；否则几何命中格子 → 换窗/关窗。
        旧方案靠 Tk <Enter> 事件触发 + 独立轮询关闭，前一个提示窗盖住下一格时
        事件时序混乱导致后一个悬停窗丢失；改为纯几何命中后与事件递送彻底解耦。"""
        try:
            px, py = self.winfo_pointerx(), self.winfo_pointery()
            wx, wy = self.winfo_rootx(), self.winfo_rooty()
        except Exception:
            self.after(120, self._hover_poll)
            return
        tw = getattr(self, "_tipwin", None)
        try:
            self._fg_game_tick()                     # 点任务栏 TBH 图标找回工具
            self._game_exit_tick()                   # 游戏关掉 → 工具同步关闭
        except Exception:
            pass

        def inside(w):
            try:
                return (w.winfo_rootx() <= px < w.winfo_rootx() + w.winfo_width()
                        and w.winfo_rooty() <= py < w.winfo_rooty() + w.winfo_height())
            except Exception:
                return False

        if self._tip_shown and tw and inside(tw):
            self.after(120, self._hover_poll)
            return
        lx, ly = px - wx, py - wy
        # 四角缩放热区：不可见，仅以光标形状提示
        corner = self._corner_at(lx, ly)
        if corner != self._corner_cur:
            self._corner_cur = corner
            cur = {"nw": "size_nw_se", "se": "size_nw_se",
                   "ne": "size_ne_sw", "sw": "size_ne_sw"}.get(corner)
            drag_lbl = self._hotspots[0] if self._hotspots else None
            try:
                if cur:
                    if drag_lbl:
                        drag_lbl.configure(cursor=cur)
                    self._base_holder.configure(cursor=cur)
                else:
                    if drag_lbl:
                        drag_lbl.configure(cursor="fleur")
                    self._base_holder.configure(cursor="arrow")
            except Exception:
                pass
        # 悬停闸门（三态）：只有本窗收到过鼠标移动（= 查看器是指针下的命中
        # 窗口）才做格子命中；被其他窗口盖住时收不到 Motion，悬停窗不误弹。
        # 鼠标静止且指针仍在本窗范围内 → 保持现有悬停窗（读价格需要停住不动）。
        fresh = (time.time() - getattr(self, "_motion_ts", 0.0)) < 0.4
        in_bounds = 0 <= lx < self.W and 0 <= ly < self.H
        if fresh:
            hit = None
            for i, (x, y) in self._cell_pos_list:
                if x <= lx < x + self.CELL and y <= ly < y + self.CELL:
                    hit = i
                    break
            rec = self._cell_recs.get(hit) if hit is not None else None
            hit_key = str(rec.get("ItemKey", "")) if rec else None
            # 「格位+物品」对账：物品没变就保持悬停窗（存档重建格子不再打断），
            # 格位或物品变了才销毁重建。
            if hit != getattr(self, "_tip_cell", None) or \
                    hit_key != getattr(self, "_tip_key", None):
                self._destroy_tip()
                if rec:
                    self._spawn_tip_at(hit, rec)
        elif in_bounds and getattr(self, "_tip_cell", None) is not None:
            pass                      # 鼠标停在本窗区域内：保持悬停窗不动
        elif getattr(self, "_tip_cell", None) is not None:
            self._destroy_tip()       # 指针已不在本窗范围（移到别的窗口）→ 关闭
        self.after(120, self._hover_poll)

    def _destroy_tip(self):
        if getattr(self, "_tipwin", None):
            try:
                self._tipwin.withdraw()
            except Exception:
                pass
        self._tip_cell = None
        self._tip_key = None
        self._tip_shown = False

    def _rebuild_hotspots(self):
        self._clear_hotspots()
        Z = self.zoom
        # 标题区拖动热区（先建，处于最底层，避免盖住其他按钮）
        drag = self._add_hotspot(0, 0, self.W, int(70 * Z))  # 整个标题栏可拖拽
        drag.bind("<ButtonPress-1>", self._drag_start)
        drag.bind("<B1-Motion>", self._drag_move)
        drag.configure(cursor="fleur")
        # 关闭钮
        self._add_hotspot(self.CLOSE_X, self.CLOSE_Y, self.CLOSE_S, self.CLOSE_S,
                          on_click=lambda e: self.destroy())
        # 页签（热区与合成图形矩形一致）
        for x_abs, y_abs, gw, gh, key in self._tab_rects:
            self._add_hotspot(x_abs, y_abs, gw, gh,
                              on_click=lambda e, key=key: self.select_tab(key))
        x = self.TAB_X0 + self.TAB_STEP * len(self._tab_rects)
        self._add_hotspot(x, self.BORDER_Y - int(22 * Z),
                          int(32 * Z), int(22 * Z))  # DLC 静态
        # 底部刷新按钮热区
        bx, by, bw, bh = getattr(self, "_refresh_btn_rect", (0, 0, 0, 0))
        if bw:
            self._btn_hs = self._add_hotspot(bx, by, bw, bh,
                                             on_click=lambda e: self._manual_refresh())
        # 格子悬停窗：记录格位几何与物品，由 _hover_poll 几何命中驱动
        self._cell_recs = {}
        self._cell_labels = {}
        for i in range(49):
            cx, cy = self.cell_pos(i)
            k, q = self._slot_map().get(i, ("", 0))
            rec = dict(self.items.get(str(k), {})) if k else None
            self._cell_labels[i] = self._add_hotspot(cx, cy, self.CELL, self.CELL)
            if rec:
                rec["_marketable"] = self.market.get(str(k), False)
                self._cell_recs[i] = rec

    def select_tab(self, key):
        self.tab = key
        self.compose()
        self._rebuild_hotspots()

    # ---------- 拖动 ----------
    def _drag_start(self, e):
        if self._zdrag:
            return                              # 四角缩放进行中，不移动窗口
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        if self._zdrag or not hasattr(self, "_dx"):
            return
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---------- 实时 ----------
    def _poll(self):
        try:
            m = os.path.getmtime(SAVE)
            if m != self._last_mtime:
                try:
                    data = parse_save()
                except Exception:
                    # 游戏正在写存档时解密可能失败：不记 mtime，0.5 秒后再试（否则会一直停在空仓库）
                    data = None
                if data is not None:
                    self._last_mtime = m
                    sig = json.dumps(data, sort_keys=True)
                    if sig != self._last_sig:
                        self._last_sig = sig
                        self.data = data
                        if self.tab not in self._pages_def:
                            self.tab = ("仓库", 0)
                        self.compose()
                        self._rebuild_hotspots()
        except Exception:
            pass
        if getattr(self, "_mkt_anim", False) and not getattr(self, "_anim_scheduled", False):
            self._anim_tick()       # 更新进行中：启动旋转图标（125ms 一帧，只重画底部）
        if getattr(self, "_mkt_dirty", False):
            self._mkt_dirty = False
            self.compose()          # 按钮状态/价格时间戳变化 → 重绘（主线程）
        self.after(500, self._poll)

    def _fg_game_tick(self):
        """点任务栏 TBH 图标/切回游戏窗口时，把查看器一并抬到最上层。
        本窗是无边框工具窗，没有任务栏按钮也不进 alt-tab，被其他窗口盖住后
        只能靠这个手势找回。只做普通 lift（和原版一致：聚焦时在前，
        不用时被其他窗口自然盖住），不进置顶带。"""
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return
        if not fg:
            return
        cache = getattr(self, "_fg_names", None)
        if cache is None:
            cache = self._fg_names = {}
        pid = ctypes.c_ulong(0)
        ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        pid = pid.value
        name = cache.get(pid)
        if name is None:
            if len(cache) > 64:
                cache.clear()
            name = cache[pid] = _proc_name_of_pid(pid)
        is_game = (name == GAME_EXE)
        was = getattr(self, "_fg_game", False)
        self._fg_game = is_game
        if pid != getattr(self, "_fg_last_pid", None):
            self._fg_last_pid = pid
            _fg_log(f"fg change: pid={pid} name={name} game={is_game}")
        if is_game and not was:
            self._lift_retry(0)

    def _game_exit_tick(self):
        """游戏进程消失 → 查看器同步退出（每秒查一次，宽限 2 秒即关）。
        只有「本窗运行期间见过游戏」才生效：手动单独开工具时游戏没开也不会被误关。"""
        now = time.time()
        if now - getattr(self, "_game_chk_ts", 0.0) < 1.0:
            return
        self._game_chk_ts = now
        try:
            alive = bool(_game_pids())
        except Exception:
            return                                  # 查不到就别误关
        if alive:
            self._game_seen = True
            self._game_gone_since = 0.0
            return
        if not getattr(self, "_game_seen", False):
            return
        if not getattr(self, "_game_gone_since", 0.0):
            self._game_gone_since = now
            _fg_log("game gone → grace %.0fs" % GAME_EXIT_GRACE)
            return
        if now - self._game_gone_since >= GAME_EXIT_GRACE:
            _fg_log("game exited → close viewer")
            try:
                self._tipwin.withdraw()
            except Exception:
                pass
            try:
                self.destroy()
            except Exception:
                pass

    def _lift_retry(self, attempt):
        """游戏窗口被激活 → TOPMOST→NOTOPMOST 双切换抬升，失败自动重试。
        任务栏点击瞬间 shell 正在激活动画，SetWindowPos 会瞬时失败（实测），
        重试 250ms 后即可成功。"""
        try:
            root = _user32.GetAncestor(self.winfo_id(), 2)
            ok = False
            if root:
                ok = bool(_user32.SetWindowPos(root, HWND_TOPMOST,
                                               0, 0, 0, 0, 0x53))
                _user32.SetWindowPos(root, HWND_NOTOPMOST, 0, 0, 0, 0, 0x53)
            _fg_log(f"lift attempt {attempt}: root={root} ok={ok}")
            if not ok and attempt < 4:
                self.after(250, lambda: self._lift_retry(attempt + 1))
        except Exception:
            pass


def _missing_assets():
    """启动前检查素材：这些文件不随仓库分发，缺了会在合成界面时崩，pythonw 下什么也看不到。"""
    need = [
        os.path.join(UI, "Ui_Stash_Bg.png"),
        os.path.join(UI, "Ui_Stash_Bg_Decor.png"),
        os.path.join(UI, "TextImage_Stash_SC.png"),
        os.path.join(UI, "TabButton_Stash_Active.png"),
        os.path.join(UI, "TabButton_Stash_DeActive_Active.png"),
        os.path.join(UI, "Btn_Large_Active.png"),
        os.path.join(UI, "Btn_Large_Plain.png"),
        os.path.join(UI, "Button_Close_Active.png"),
        os.path.join(UI, "ItemSlot_Bg.png"),
        os.path.join(UI, "ItemSlot_GradeBg_NORMAL.png"),
        os.path.join(UI, "Btn_Small_Active.png"),
        ITEMS,
        os.path.join(BASE, "localization_zh.json"),
    ]
    missing = [os.path.relpath(p, BASE) for p in need if not os.path.exists(p)]
    return missing


def _startup_failure(err):
    """pythonw 没有控制台：把栈写进 launch_log.txt，再弹一个框，别让用户只看到"没反应"。"""
    import traceback
    text = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    try:
        with open(os.path.join(BASE, "launch_log.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "启动失败\n" + text + "\n")
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(
            0, f"{type(err).__name__}: {err}\n\n"
               f"如果是缺素材/缺依赖，双击 setup.bat 会自动补齐。\n"
               f"详细信息见 {os.path.join(BASE, 'launch_log.txt')}",
            "TaskbarHero 仓库查看器 · 启动失败", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    # 单实例保护：游戏启动联动会反复拉起本脚本，已运行时第二个实例静默退出
    _mtx = ctypes.windll.kernel32.CreateMutexW(None, False, "TaskbarHeroStashViewer")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    try:
        miss = _missing_assets()
        if miss:
            raise RuntimeError(
                "缺少游戏素材，无法启动：\n  " + "\n  ".join(miss) +
                "\n\n这些文件不随仓库分发。从能用的机器上把 ui\\、icons\\、csv\\、"
                "items_zh.csv、localization_zh.json 复制过来，"
                "或用 tools\\extract_sprites.py 从本机游戏目录提取。")
        Viewer().mainloop()
    except Exception as e:              # 缺素材 / 缺依赖 / 存档解析异常都不该静默退出
        _startup_failure(e)
        sys.exit(1)
