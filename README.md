# TaskbarHero 仓库查看器（TBH Stash Viewer）

给《TaskBar Hero》（Steam appid 3678970）按原版 UI 复刻的仓库/背包查看器：脱离游戏窗口单独打开，能看全部页签的物品，并显示**人民币**最低价与近 24 小时成交中位价。

**版本：v1.0.9**

![界面截图](docs/screenshot.png)

## 功能

- 复刻原版仓库面板：仓库多页 / 背包 / 材料库页签、品阶底色、图标、数量角标，无边框窗口 + 点击标题栏拖动、四角拖拽缩放（1.0×～4.0×）。
- 价格全人民币：**不需要登录**，匿名接口 `priceoverview?currency=23` 一次就返回最低价、近 24 小时成交中位与成交量；登录态是可选的，只在 429 限流后的兜底路径里有用（见下面「登录态（可选）」）。
- 悬停格位显示：最低价、近 24 小时成交量加权中位、成交量、更新时间；价格表里已有数据时先秒显，再按需现拉。
- 按 2 小时节奏维护：只刷**仓库 / 背包 / 材料库**里的物品，只补超过 105 分钟没更新的；新进物品先解析市场名（英文名 + 品阶核验）再取价。
- 底部状态按钮：手动补刷一轮过期物品；状态分「已完成 · N 件 / 限流 · 已存 N 件 / 限流中 · HH:MM 重试 / 更新失败 · 将重试」，限流或失败时使用灰色按钮底图，其余为原版棕色。
- 关游戏即退：游戏进程消失 2 秒后查看器自动关闭；工具是手动单独启动、游戏本来没开时不会自爆。

## 环境要求

- Windows 10/11
- Python 3.10+（实测 3.12）
- 本机装有《TaskBar Hero》
- （可选）代理：默认走 `http://127.0.0.1:7890`，可用环境变量 `STEAM_PROXY` 覆盖；不设代理也能直连，只是更早触发限流

## 安装（一键）

**双击 `setup.bat` 就行**：它会自己找 Python 3.10+、装 `requirements.txt` 里的依赖、找到《TaskBar Hero》的安装目录、把 `ui/`、`icons/`、`items_zh.csv`、`localization_zh.json` 全部生成好，最后弹窗告诉你结果。第一次要几分钟，之后重跑只要十几秒。

- 全过程写进仓库根目录的 `setup_log.txt`（每次运行覆盖），失败会弹窗，里面带日志路径；
- 游戏不在默认的 Steam 库也没关系，会自动扫 `libraryfolders.vdf` 与常见盘符，也可以手动指定：`setup.bat "D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"`；
- 找不到 Python 时窗口会停住并提示去下载官方安装包（勾选 Add to PATH）；Windows Store 那个 `python.exe` 占位别名会被跳过（它不是真的 Python，跑起来只会得到 `exit 9009`）。

## 素材准备（重要）

仓库**不附带游戏素材**：`ui/`、`icons/`、`items_zh.csv`、`localization_zh.json` 都从你自己机器上的游戏副本生成，版权归游戏方。`setup.bat` 做的就是下面三条命令，想手动跑、或者只想补其中一部分时才用到（`--game-dir` 指向游戏的 `TaskBarHero_Data` 目录，省略时依次看 `TBH_GAME_DIR` 和常见 Steam 路径）：

```bat
python tools\setup_all.py   :: 一条命令跑完下面三步，并写 setup_log.txt、出错弹窗
python tools\extract_sprites.py   --game-dir "D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"
python tools\build_item_tables.py --game-dir "D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"
python tools\extract_icons.py     --game-dir "D:\SteamLibrary\steamapps\common\TaskbarHero\TaskBarHero_Data"
```

- `extract_sprites.py` 一次性写出 `ui/` 全套：仓库面板与装饰、标题图、普通/DLC 页签、格子底与十个品阶底、按钮组。棕色 `Btn_Large_Active` 和深灰 `Btn_Large_Plain` 游戏里没有现成 Sprite，是从灰色 `Button_Large_DeActive` 按原版配色派生的；同名多档的 Sprite（关闭按钮 12/28/32）会自动取与旧版素材一致的那一档。
- `build_item_tables.py` 从本地化 bundle 拼出 `localization_zh.json`（`ItemTable` 1504 条 + `StringTable` 1775 条），物品表/材料表/装备表直接从 `sharedassets0.assets` 里的 csv 资源读，拼出 `items_zh.csv`（7031 行）。
- `extract_icons.py` 按 `items_zh.csv` 的「图标」列（`IconPath`，如 `AMULET_600001`）从同一个资源文件里取出全部 609 张 16×16 图标，写进 `icons/`。格子里的物品图标就靠它。

`items_zh.csv` 里带「可交易」「描述键」两列，可交易标记和物品描述都从它读；`csv/` 目录已经不需要了（留着只作老版本兜底）。

启动必需：`ui/` 里的面板图、`items_zh.csv`、`localization_zh.json`——缺任何一项都会启动失败，程序会弹框列出缺的文件（不再静默退出）。`icons/` 是软依赖：缺了照样能开，只是格子不显示物品图标；上面第三条命令就是补它。

## 运行

```bat
start.bat
```

`start.bat` 会按这个顺序找 Python：`TBH_PYTHON` 环境变量 → `py -3` 启动器 → `%LOCALAPPDATA%\Programs\Python\Python31x` 等常见目录 → PATH 里的 `pythonw.exe`；找不到或用不了会停住并提示，不会闪一下就没。

### 双击没反应怎么查

1. 先跑 `start_debug.bat` —— 带控制台启动，异常会直接打印出来；素材没准备好就直接双击 `setup.bat`，它会把依赖和素材问题一次说清；
2. 或者看 `setup_log.txt`（准备素材的日志）与 `launch_log.txt` —— 启动失败时会把完整 traceback 写进去，同时弹出提示框（`pythonw` 没有控制台，以前是静默退出）；
3. 依赖没装全时 `start.bat` 会先告诉你缺什么：`python -m pip install -r requirements.txt`（`tkinter` 由官方 Python 安装包自带，别用精简/嵌入版）；
4. 确认素材齐全：`ui\`、`items_zh.csv`、`localization_zh.json`（`icons\` 可选）；
5. `setup.bat` 报 `exit 9009` 表示它找到的 `python.exe` 根本起不来（最常见是 Windows Store 的占位别名，或 `TBH_PYTHON` 指错）——装官方 Python 3.10+，或用 `set TBH_PYTHON=C:\...\python.exe` 指定真身。

等价于：

```bat
pythonw -X utf8 stash_viewer.py
```

### 随游戏一起启动（Steam 启动选项）

Steam → 库 → 右键《TaskBar Hero》→ 属性 → 启动选项：

```
C:\Windows\System32\wscript.exe "<本文件夹路径>\start_with_game.vbs" %command%
```

`start_with_game.vbs` 会先无窗口拉起查看器，再把 `%command%` 原样转给游戏（Steam 直接执行首个 token，所以不用 `cmd /c`）。查看器内部有单实例互斥量，重复拉起只会静默退出。

## 登录态（可选）

**不登录也能完整使用**：最低价、近 24 小时成交中位、成交量都来自匿名 `priceoverview?currency=23`，2 小时一轮的自动维护、底部按钮手动补过期、悬停看价都不依赖登录。

登录态只影响两处兜底路径（这两条都必须登录才走得通）：

- **429 限流后的补价**：没登录时，`fast_refresh` 在 `get_auth().cookies()` 为空时直接返回，本轮只能等冷却（按钮显示「限流中 · HH:MM 重试」）；有登录则改用市场搜索页按 `q=` 关键字补那些 105 分钟以上没更新的物品。
- **中位/成交量补算**：`pricehistory` 接口匿名返回 400，只有登录能读；没登录时中位就用 `priceoverview` 自带的 `median_price` 字段。

需要的话，两种方式任选其一：

```bat
:: 方式一：开登录窗口，抓到 Cookie 后自动关窗（长效令牌约 200 天，续期自动）
pythonw -X utf8 steam_login.py
pythonw -X utf8 steam_login.py --status      :: 只看令牌有效期

:: 方式二：从本机 Steam / Chrome / Edge 的 cookie 库直接读令牌
python extract_steam_token.py
```

不想用登录就什么都不用做；已有的 `steam_cookies.json`、`steam_token_cache.json`、`edge_login_profile\` 也可以直接删掉，查看器会自动跳过兜底分支。冷却期间悬停不会发请求，只显示价格表里已有的数据。

## 目录结构

```
stash_viewer.py        查看器主程序（Tk + 原版 UI 拼图）
update_prices.py       人民币取价：priceoverview / 市场页兜底 / pricehistory 中位 / 冷却与限速
market_names.py        新物品市场名解析（本地化英文名 + 品阶核验 + 缓存）
steam_login.py         登录窗口 + CDP 抓 Cookie + 自动关窗（**可选**，--status / --selftest）
extract_steam_token.py 备用：从 Steam/Chrome/Edge 的 cookie 库读登录令牌
start_with_game.vbs    Steam 联动启动（先开查看器，再转游戏命令）
start.bat              手动启动
setup.bat              **一键准备**：装依赖 + 找游戏目录 + 生成全部素材 + 弹窗报结果
tools/extract_sprites.py   从游戏本体提取 ui/ 全套素材（含派生按钮）
tools/build_item_tables.py 从游戏 bundle + 内置表生成 items_zh.csv 与 localization_zh.json
tools/extract_icons.py    从游戏本体提取 icons/ 物品图标
tools/setup_all.py         一键准备的执行体：依赖自检、找游戏目录、跑上面三个工具、写日志、弹窗
ui/ icons/              游戏素材（.gitignore，用上面的 tools 自行生成）
item_prices.json       价格表（本地缓存，含最低价 / 中位 / 成交量 / 时间戳）
```

## 数据与隐私

运行时会生成这些**仅存本机**的文件（都已写进 `.gitignore`）：

- `item_prices.json`：价格表
- `steam_cookies.json` / `steam_token_cache.json` / `edge_login_profile/`：Steam 登录态（**可选**，不登录不会生成；含明文令牌，**不要分享或上传**）
- `market_names.json` / `en_names_cache.json`：市场名解析缓存
- `viewer_prefs.json`：缩放比例
- `prices_log.txt` / `fg_debug.log` / `launch_log.txt`：运行日志

## 已知限制

- `priceoverview` 有额度：实测约 500 件 / 周期后开始 429，换 IP 与换 UA 都不改变结论；脚本按 5→15→30→60→60 分钟阶梯冷却，冷却结束后自动续刷。
- 未登录时 429 后没有市场页兜底，这段时间只能等冷却；想缩短就用「登录态（可选）」里的任一方式补一次登录。
- Steam 的 `search/render`、`listrender` 列表接口已整站下线（真实登录浏览器同样 404），所以没有「一页拉全部」的通道，市场搜索页只能取到第一页 100 条，`start`/`sort` 参数被服务端忽略。
- 中位数是按小时聚合后按成交量加权的口径，与 Steam 官方逐笔成交中位可能相差几点（实测例子：188 vs 208）。
- 近 24 小时没有成交的物品，中位显示“—”。
- 仅支持 Windows。

## 版本与迭代

- 语义化版本 `MAJOR.MINOR.PATCH`，版本号写在 `stash_viewer.py` 的 `__version__`，启动时打一行 `price loop started · v1.0.0` 到 `prices_log.txt`。
- 每个版本：改代码 → 更新 `CHANGELOG.md` → `git tag -a vX.Y.Z` → 从 tag 出 Release。
- 分支约定：`main` 永远可运行；功能改动走 `feat/xxx`，修 bug 走 `fix/xxx`，合并前至少在本机跑通启动 + 一轮手动刷新。
- 配置不写死：`TBH_DATA_DIR`（数据根目录）、`TBH_SAVE`（存档路径）、`TBH_SAVE_PASSWORD`（存档解密口令）、`TBH_GAME_DIR`（游戏安装目录）、`TBH_PYTHON`（启动脚本用的 pythonw）、`STEAM_PROXY`（代理）都可用环境变量覆盖。

## 路线图

- v1.1：首次运行配置向导（`ui/`、`icons/`、数据表在 v1.0.5～v1.0.6 已全部自动化）。
- v1.2：中位改用官方逐笔口径；价格历史本地化，支持折线小图。
- 之后：多语言、非 Windows 平台的取价后端。

## 免责声明

本项目仅用于查看本机存档内容，与游戏开发方、Valve 无关联。请自行遵守游戏与 Steam 的服务条款；读取存档、访问市场接口的风险自负。代码按 MIT 许可，游戏素材与 Steam 数据不在许可范围内、也不随仓库分发。
