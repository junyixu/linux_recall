# CLAUDE.md

在 KDE Plasma 6 (KWin Wayland) 上自建一个 Windows Recall 式的工具：定时或按快捷键截取活动窗口，记录窗口信息、浏览器网址和 OCR 文字，每次截图存成一张 WebP 加一个 JSON。采集部分自己写、可审计；索引和检索以后复用成熟组件（SQLite FTS5）。

- 调研背景：`其他方案.md`（现有项目对比、自建方案）和 `x11_vs_wayland.md`（Wayland 上哪些能力拿得到）
- `ref/` 是参考用的 OpenRecall、screenpipe 源码（被 gitignore），只读，不要改
- 面向用户的用法（安装、jq 查询、搜索、去重统计）写在 `README.md`；改了 JSON 字段或命令行参数，要同步更新 README 里的例子

## 环境

- Arch Linux，Plasma / KWin 6.7.5 Wayland，三块显示器、混合缩放（DP-2 ×2 竖屏、DP-1 ×1.95、eDP-1 ×2）
- Python 3.14，用 uv 管理：`uv sync`、`uv run ...`
- 系统依赖：`spectacle`、`plasma-browser-integration`（浏览器里也要装对应扩展）；`jq`、`fzf`、`magick`、kitty 用于搜索
- `PADDLEOCR_TOKEN` 定义在 `~/.config/environment.d/74-api-keys.conf`，所以快捷键和 systemd 启动的进程都能拿到

## 常用命令

```sh
uv run linux-recall-capture [--mode window|fullscreen|monitor] [--full-res] [--no-ocr] \
    [--ocr-timeout 60] [--no-notify] [--trigger test] [--data-dir DIR]   # 截一次（快捷键跑的就是它）
uv run linux-recall-daemon --interval 60 --threshold 0.95 [-v]           # 定时截图 + 去重
uv run linux-recall-click [--timeout 60]                                 # Click to Do（快捷键 Meta+Alt+C）
uv run python -m linux_recall.kwin       # 打印活动窗口和各显示器信息
uv run python -m linux_recall.browser    # 打印活动浏览器标签的 URL
./scripts/install-hotkey.sh              # 注册 Meta+Alt+R（截图）和 Meta+Alt+C（Click to Do），CAPTURE_KEY/CLICK_KEY 可改
source scripts/lr.zsh                    # lr-search / lr（fzf，kitty 里预览高亮截图）/ lr-highlight

systemctl --user restart linux-recall            # 改了 Python 代码后必须重启，daemon 才会用新代码
journalctl --user -u linux-recall -f -o cat      # daemon 每轮一行 KEEP/SKIP + 相似度
journalctl --user -t linux-recall-capture        # 快捷键启动时的输出
qdbus org.kde.kglobalaccel /component/net_local_linux_recall_capture_desktop \
    org.kde.kglobalaccel.Component.invokeShortcut _launch   # 不按键地测试快捷键路径
```

- 测试时用 `--data-dir` 指向临时目录，不要往真实数据目录里写测试截图；daemon 测试会往真实的 `~/.cache/linux_recall/daemon.jsonl` 追加记录，测完要清理
- `~/.config/systemd/user/linux-recall.service` 是 `systemd/linux-recall.service` 的**拷贝**，不是软链接：改了仓库里的 unit 要重新 `cp`，再 `systemctl --user daemon-reload`
- 改了包名或 entry point 后要 `uv sync --reinstall-package linux-recall`，普通 `uv sync` 不会重建

## 命名与路径

所有对外可见的名字都用 `linux_recall`，不要用单独的 `recall`（容易跟别的程序混）。路径统一从 `linux_recall/paths.py` 取：

- 数据：`~/.local/share/linux_recall/captures/YYYY-MM-DD/<id>.webp` + `<id>.json`
- 缓存（随时可删）：`~/.cache/linux_recall/`
  - `capture.log`：快捷键截图的日志
  - `daemon.jsonl`：daemon 每轮的决定（keep/skip、相似度），用来统计去重效果
  - `preview/`：`lr` 生成的高亮预览图
  - 截图过程中的原始分辨率图片、KWin 临时脚本（用完即删）
- 快捷键 desktop 文件：`~/.local/share/applications/net.local.linux-recall-{capture,click}.desktop`
- Click to Do 的页面和截图：`~/.cache/linux_recall/clicktodo/`（一天后自动删除），日志 `~/.cache/linux_recall/clicktodo.log`

## 代码结构（`linux_recall/`）

- `capture.py`：`take()` 和 `save()`，快捷键入口 `main()`
  - `take()`：活动窗口（约 50 ms）→ 截图到缓存目录（`window` 模式约 0.5 s）→ 浏览器 URL → 计算 dHash 和屏幕缩放比
  - `save()`：把截图按屏幕缩放比缩小到逻辑分辨率后存进数据目录（`--full-res` 不缩小），写 JSON；然后用**原始分辨率**的图做 OCR，把坐标换算到保存的图片上，再写一次 JSON，最后删掉缓存里的原图。缩小后再 OCR，kitty 窗口会从 105 行掉到 42 行，所以一定要先用原图识别
- `daemon.py`：每 `--interval` 秒 `take()` 一次；如果和上一次保存的是同一个程序，而且 dHash 相似度 ≥ `--threshold`，就丢弃，否则 `save()`。换了程序一定保存；锁屏时跳过（`org.freedesktop.ScreenSaver.GetActive`）。每轮写一行日志，并往 `daemon.jsonl` 追加一条记录。收到 SIGTERM 会先跑完当前这一轮
- `similarity.py`：16×16 dHash，相似度 = 1 − 汉明距离 / 256。实测：完全相同 1.0，时钟跳一下 0.988，多一行字 0.977，多一段 0.93，滚动 300px 0.82，内容完全不同 0.5–0.73
- `kwin.py`：`get_kwin_state()` 返回活动窗口和各显示器的几何信息。用 jeepney 往 `org.kde.KWin /Scripting` 加载一段临时 KWin 脚本，脚本读 `workspace.activeWindow`、`workspace.screens` 后用 `callDBus` 回调到我们的 unique bus name，用完就 unload（和 kdotool 同一个思路，但一次调用拿全所有字段）
- `browser.py`：Plasma Browser Integration `/TabsRunner`（krunner1 接口）。把窗口标题去掉浏览器后缀，找 `relevance == 1` 且标题完全相同的标签页
- `screenshot.py`：调用 spectacle。`window` 模式是 `-a -S`（`-S` 去掉约 250px 的透明阴影），`fullscreen` 是 `-f`；都加 `--new-instance`，否则快捷键和 daemon 同时截图时，第二次调用会被转发给第一个进程，`--output` 丢失。`window_region()` 把窗口的逻辑坐标换算成整桌面截图的像素：spectacle 用最高的缩放比（2）渲染整个桌面，所以 像素 = (逻辑坐标 − 桌面左上角) × 图宽 / 桌面逻辑宽度
- `ocr.py`：先用 PaddleOCR 云端 API，只上传要识别的区域（`window` 模式是整张图，`fullscreen` 模式是活动窗口那块）。`--ocr-timeout`（默认 60 秒）限制整个云端过程，包括排队重试、每次上传和轮询；超时或出任何错误，就用本地 RapidOCR 识别同一块图
- `clicktodo.py` + `clicktodo.html`：Click to Do。`spectacle -a -S` 原始分辨率截图 → `ocr.cloud_result(..., word_boxes=True)`（只用云端，失败就报错退出）→ 把截图和 OCR 结果写成一个 HTML 页面 → `firefox --new-window`。页面用 pdf.js 的做法：截图上面叠一层透明文字，**每个字符**是一个绝对定位的 `<span>`，用 `scaleX` 拉伸到正好盖住它的框；每行一个 `<div>`，复制时保留换行。`reading_order()` 先把行分成列再排序，否则跨行选择会把侧边栏一起选进来。测试可以用 `firefox --headless --no-remote --profile <临时目录> --screenshot`，页面里加 `show` class 就能看到文字层
- `textfit.py`：把百度的文字重新对齐到截图的像素上，每个字符一个框（百度的行框和文字准，词框不准，见下面的坑）。步骤：
  - 墨迹：行框上面去掉 1/8（上一行的下伸部分），下面保留（`_` 在最底下）；阈值按这一行的对比度自适应（灰字和背景只差约 55，黑字差约 250）
  - 去掉图标：墨迹按“比空格宽得多的空白”分块，和任何词的百度范围都不重叠的块是图标
  - 分词：每个空格取离百度给的位置最近的空白（不能取最宽的空白：全角标点“），”自带的留白比空格还宽）
  - 分字符：词里每段连续墨迹是一个字形，用动态规划 `_align` 分配：一个字符可以占几个字形（中文偏旁），几个字符可以共用一个字形（字母粘连），图标字形可以跳过；代价比较的是相邻字符的间距（百度的绝对位置在行中间会偏一个字符以上，间距却准）。**数量相等也不能直接一一对应**：“机制：Windows”里“制”是两个字形、“ws”粘成一个，数量刚好相等，一一对应会让后面全部错一位
  - 哪一步失败，这一行就保留百度的位置（按 `_advance` 的粗略字宽分给每个字符）
  - 改了这里要跑 `uv run python scripts/check_textfit.py`：用 `~/.cache/linux_recall/clicktodo/` 里保存的百度原始结果（`<id>.ocr.json`）重新对齐，标出可疑的行，再渲染出来看
- `scripts/lr`：fzf 搜索。每行是一处 OCR 匹配；fzf 调用 `lr --preview` 生成预览（和 `~/.config/kitty/bin/kitty_fzf_tab.sh` 同一个模式），用 `kitten icat --unicode-placeholder` 在 kitty 里显示高亮后的截图

## 数据格式

每次截图对应一个 JSON 文件，这是原始记录；以后的 SQLite FTS5 索引要能完全从这些 JSON 重建。

- 顶层字段：`schema_version`（当前是 4）、`id`、`captured_at`（带时区的 ISO 8601）、`trigger`（`hotkey` / `timer` / 测试时自定义）、`screenshot`、`screens`、`window`、`browser`、`ocr`
- `screenshot`：`file`、`mode`、保存的 `width`/`height`、`scale`（保存的图片相对原始截图的缩放比）、`captured_width`/`captured_height`、`window_region`（只有 `fullscreen` 模式有）、`dhash`
- `ocr`：`{engine, fallback_reason, elapsed_s, region, text, lines: [{text, score, box: 4 个角点}]}`。`box` 和 `region` 都是**保存下来的那张图片**里的像素坐标。`engine` 是实际用的引擎，`fallback_reason` 是为什么改用本地
- 版本变化：2 加了 `screens`，OCR 只识别活动窗口；3 加了 `window_region`、`dhash`；4 默认只截活动窗口，并且缩小保存。旧的截图是整个桌面、没有缩小，搜索和预览脚本要兼容（比如没有 `ocr.region` 时不裁剪）
- 改字段含义时递增 `capture.py` 里的 `SCHEMA_VERSION`
- JSON 分两次原子写入（先写 tmp 再 rename）：第一次 `ocr` 为 `null`；OCR 失败只记日志，截图保留，`ocr` 仍为 `null`，以后可以补跑

## 已踩过的坑

- **KWin 上没有 grim / wlr-screencopy / ext-image-copy-capture**。`org.kde.KWin.ScreenShot2` 只接受 desktop 文件白名单里的调用方，所以借用 spectacle。`spectacle --scaled` 只对 `-f` 有效，`-a` 截图的缩小要自己做
- **TabsRunner 会缓存标签列表**：第一次 `Match` 之后一直用旧数据，直到调用 `Teardown()`。每次查询前后都必须 `Teardown`，否则拿到的是旧标题。它也不标记哪个是活动标签，只能按标题匹配；标题相同的多个标签会标成 `"match": "ambiguous"`。Firefox 隐私窗口直接跳过
- **PaddleOCR 云端 API**（参考 `~/WorkSpace/anki_agent/anki_ocr_paddle.py`）：
  - `optionalPayload` 里加 `"returnWordBox": true`，结果会多出 `text_word`（每行的词）和 `text_word_boxes`（每个词的 `[x0, y0, x1, y1]`）；把一行的词直接拼起来就等于这一行的文字
  - **词框不能直接用**：在 Firefox 窗口上左边缘中位数偏右 7px（最多 11px），“System Settings” 这一行的框整个是错的（System 宽了一倍、从左边的图标开始），shell 提示符里 environment 晚了 12px，导致从 n 开始拖会选到 e。所以要用 `textfit.py` 对齐到像素。修 textfit 时每一步都要在**所有**保存的页面、**所有**行上验证（`scripts/check_textfit.py`），只看出问题的那一行会顾此失彼
  - 检测时默认把最长边缩到 960 px，所以整张三屏桌面（6098 px）只认出 2 行；只上传一个窗口就能认出 100 多行
  - 把 `textDetLimitSideLen` 调大（试过 3938/4000/6098），任务会以 500 失败；服务端 `max_side_limit=4000`，超过 4000 的图先缩小再上传
  - 返回 400 且 `code 10010`（“任务提交队列已满”）表示服务端繁忙，会退避重试；拥堵时任务会在队列里 `pending` 好几分钟，实际处理只要 1–10 秒
  - 怀疑是我们的问题还是服务端的问题时，用 `anki_ocr_paddle.fetch_lines` 跑一张小图对照
- 本地 RapidOCR 默认 `Global.max_side_len=2000`，会把图缩小，所以按图的最长边设置；识别整个三屏桌面要占约 6 个核心、7 秒
- 快捷键：kglobalaccel 在 kwin_wayland 进程里，没法重启；用 D-Bus 的 `doRegister` + `setForeignShortcutKeys` 注册，按键码格式是 Qt 的 `Meta|Alt|key`（例如 `Meta+Alt+R` = `402653266`）
- kitty 图片预览：`kitten icat` 需要真正的 kitty 终端（能报告像素尺寸），在 `script` 伪终端里会报错；要测试就用 `kitty @ launch --type=tab --keep-focus` 开一个后台标签页，测完关掉
- shell 里的 `ls` 是 `eza --git-ignore` 的别名，会隐藏 `.venv/` 等被 gitignore 的文件，查这些目录要用 `/bin/ls`；zsh 里 `$var` 不会按空格拆成多个参数，要用 `${=var}`

## 路线图

1. ✅ 快捷键截图：活动窗口 + 窗口信息 + 浏览器 URL + OCR → JSON
2. ✅ 定时截图 + dHash 去重（daemon）；待做：按活动窗口切换 / idle（`ext-idle-notify-v1`）触发，而不是只靠定时
3. ✅ 命令行搜索（jq、`lr`）；待做：SQLite FTS5 索引（中文用 jieba 或 simple tokenizer）
4. ✅ Click to Do 第一版（Firefox 页面、按单词选择、复制）；待做：全屏覆盖层（pywebview）、更快的识别
5. 省空间：✅ 只存活动窗口、缩小到逻辑分辨率；待做：旧图片只留 N 天（JSON 永久保留）、降低 WebP 质量
6. 更多应用上下文：Okular、kitty（`kitty @ ls`）、Anki（AnkiConnect）、Dolphin
7. 隐私：排除名单（密码管理器、隐私窗口、银行网站）、加密存储、一键暂停；补跑 `ocr` 为 `null` 的截图
