# CLAUDE.md

在 KDE Plasma 6 (KWin Wayland) 上自建一个 Windows Recall 式的工具：采集（截图 + 窗口上下文 + OCR）自己写、可审计；索引/检索以后复用成熟组件（SQLite FTS5）。

调研背景见 `其他方案.md`（现有项目对比、自建方案）和 `x11_vs_wayland.md`（Wayland 上哪些能力拿得到）。`ref/` 是参考用的 OpenRecall、screenpipe 源码，只读，不要改。

## 环境

- Arch Linux，Plasma / KWin 6.7.5 Wayland，三块显示器、混合缩放（整桌面截图 6098×4626）
- Python 3.14，用 uv 管理：`uv sync`、`uv run ...`
- 系统依赖：`spectacle`、`plasma-browser-integration`（浏览器里也要装对应扩展）

## 常用命令

```sh
uv run linux-recall-capture [--mode window|fullscreen|monitor] [--full-res] [--no-ocr] [--ocr-timeout 60] [--no-notify] [--trigger test]
uv run linux-recall-daemon --interval 60 --threshold 0.95 [-v]   # 定时截图 + dHash 去重（systemd/linux-recall.service）
journalctl --user -u linux-recall -f -o cat                      # 每轮一行 KEEP/SKIP + 相似度；统计在 ~/.cache/linux_recall/daemon.jsonl
uv run python -m linux_recall.kwin       # 打印当前活动窗口 JSON
uv run python -m linux_recall.browser    # 打印活动浏览器标签的 URL
./scripts/install-hotkey.sh              # 注册全局快捷键 Meta+Alt+R（KEY=... 可改）
qdbus org.kde.kglobalaccel /component/net_local_linux_recall_capture_desktop \
    org.kde.kglobalaccel.Component.invokeShortcut _launch   # 不按键地测试快捷键路径
journalctl --user -t linux-recall-capture                    # 快捷键启动时的输出
source scripts/lr.zsh   # lr-search / lr (fzf，scripts/lr，kitty 里预览高亮截图) / lr-highlight，用法见 README
```

面向用户的用法（jq 查询、搜索）写在 `README.md`；改了 JSON 字段要同步更新 README 里的例子。

改了包名或 entry point 后要 `uv sync --reinstall-package linux-recall`，普通 `uv sync` 不会重建。

## 命名与路径

所有对外可见的名字都用 `linux_recall`，不要用单独的 `recall`（容易跟别的程序混）。路径统一从 `linux_recall/paths.py` 取：

- 数据：`~/.local/share/linux_recall/captures/YYYY-MM-DD/<id>.webp` + `<id>.json`
- 缓存：`~/.cache/linux_recall/`（`capture.log`、KWin 临时脚本），随时可删
- 快捷键 desktop 文件：`~/.local/share/applications/net.local.linux-recall-capture.desktop`

## 代码结构（`linux_recall/`）

- `capture.py`：入口。顺序是 活动窗口（约 50 ms）→ 截图（约 1.7 s）→ 浏览器 URL → 写 JSON → OCR → 再写 JSON
- `kwin.py`：活动窗口和各显示器的几何信息（`get_kwin_state()`）。用 jeepney 往 `org.kde.KWin /Scripting` 加载一段临时 KWin 脚本，脚本读 `workspace.activeWindow` 后用 `callDBus` 回调到我们的 unique bus name，用完就 unload（和 kdotool 同一个思路，但一次调用拿全所有字段）
- `browser.py`：Plasma Browser Integration `/TabsRunner`（krunner1 接口）。把窗口标题去掉浏览器后缀，找 `relevance == 1` 且标题完全相同的标签页
- `screenshot.py`：`spectacle -b -n -f -o x.webp`；`window_region()` 把窗口的逻辑坐标换算成截图像素。spectacle 用最高缩放比（2）把整个桌面渲染成一张图，所以 像素 = (逻辑坐标 − 桌面左上角) × 图宽 / 桌面逻辑宽度
- `ocr.py`：先用 PaddleOCR 云端 API（`PADDLEOCR_TOKEN`），**只上传活动窗口那块裁剪图**，不上传整张桌面。在 `--ocr-timeout`（默认 60 秒，限制整个过程，包括每次上传和轮询）内没拿到结果，或者出任何错误，就改用本地 RapidOCR 识别同一块裁剪图。返回 `{engine, fallback_reason, elapsed_s, region, text, lines:[{text, score, box: 4 个角点}]}`，box 坐标换算回整张截图的像素；`engine` 记录实际用了哪个引擎，`fallback_reason` 记录为什么改用本地
- 截图模式是 `window`/`monitor` 时直接识别整张图；`fullscreen` 模式下没有活动窗口就跳过 OCR
- 默认模式是 `window`（`spectacle -a -S`，`-S` 去掉透明阴影）。`take()` 把原始分辨率截图放到缓存目录；`save()` 按屏幕缩放比缩小到逻辑分辨率后保存（`screenshot.scale`），OCR 用原图识别，再把坐标换算到保存的图片上，最后删掉原图。缩小后再 OCR，kitty 窗口会从 105 行掉到 42 行，所以一定要先用原图识别。`spectacle --scaled` 只对 `-f` 有效
- `daemon.py`：每 `--interval` 秒 `take()` 一次，同一个程序且 dHash 相似度 ≥ `--threshold` 就丢弃，否则 `save()`；每轮写一行日志和一条 `daemon.jsonl` 记录
- `similarity.py`：16×16 dHash。实测：完全相同 1.0，时钟跳一下 0.988，多一行字 0.977，多一段 0.93，滚动 300px 0.82，内容完全不同 0.6–0.73

## 数据格式

每次截图对应一个 JSON 文件，这是原始记录；以后的 SQLite FTS5 索引要能完全从这些 JSON 重建。

- 顶层字段：`schema_version`（当前是 2）、`id`、`captured_at`（带时区的 ISO 8601）、`trigger`、`screenshot`、`screens`、`window`、`browser`、`ocr`
- 改字段含义时递增 `SCHEMA_VERSION`
- JSON 分两次原子写入（先写 tmp 再 rename）：第一次 `ocr` 为 `null`；OCR 失败只记日志，截图保留，`ocr` 仍为 `null`，以后可以另写一个程序补跑

## 已踩过的坑

- **KWin 上没有 grim / wlr-screencopy / ext-image-copy-capture**。`org.kde.KWin.ScreenShot2` 只接受 desktop 文件白名单里的调用方，所以借用 spectacle
- **TabsRunner 会缓存标签列表**：第一次 `Match` 之后一直用旧数据，直到调用 `Teardown()`。每次查询前后都必须 `Teardown`，否则拿到的是旧标题
- TabsRunner 不标记哪个是活动标签，只能按标题匹配；标题相同的多个标签会标成 `"match": "ambiguous"`。Firefox 隐私窗口直接跳过
- **PaddleOCR 云端 API**（`PADDLEOCR_TOKEN`，参考 `~/WorkSpace/anki_agent/anki_ocr_paddle.py`）：默认 `limit_side_len=960, limit_type=max`，6098 px 的整桌面图检测前被缩到 960 px，只认出 2 行（本地 RapidOCR 认出 57 行）；服务端 `max_side_limit=4000`；把 `textDetLimitSideLen` 调大（试过 3938/4000/6098）任务会以 500 失败。所以现在只识别活动窗口、用默认的 960：3938×2136 的 Firefox 窗口裁剪图能认出约 100 行，质量很好
- PaddleOCR 返回 400 且 `code 10010`（“任务提交队列已满”）表示服务端繁忙，`_submit` 会退避重试；服务拥堵时任务会在队列里 `pending` 好几分钟（实际处理只要 1–10 秒），所以 `_wait` 的超时设成 600 秒。判断问题出在我们还是服务端：用 `anki_ocr_paddle.fetch_lines` 跑一张小图对照
- 本地 RapidOCR 只作为兜底，而且只识别窗口裁剪图（识别整个三屏桌面要占约 6 个核心、7 秒）。它默认 `Global.max_side_len=2000`，会把图缩小，所以按裁剪图的最长边设置
- `PADDLEOCR_TOKEN` 定义在 `~/.config/environment.d/74-api-keys.conf`，所以快捷键启动的进程（systemd scope）也能拿到
- shell 里的 `ls` 是 `eza --git-ignore` 的别名，会隐藏 `.venv/` 等被 gitignore 的文件，查这些目录要用 `/bin/ls`
- 快捷键：kglobalaccel 在 kwin_wayland 进程里，没法重启；用 D-Bus 的 `doRegister` + `setForeignShortcutKeys` 注册，按键码格式是 Qt 的 `Meta|Alt|key`（例如 `Meta+Alt+R` = `402653266`）

## 路线图

1. ✅ 快捷键触发：截图 + 活动窗口 + 浏览器 URL + OCR → JSON
2. 自动触发：dHash 去重；按活动窗口变化 / idle（`ext-idle-notify-v1`）/ 定时兜底来截图
3. SQLite FTS5 索引（中文用 jieba 或 simple tokenizer），加一个查询脚本或简单 UI
4. 更多应用上下文：Okular、kitty（`kitty @ ls`）、Anki（AnkiConnect）、Dolphin
5. 隐私：排除名单（密码管理器、隐私窗口、银行网站）、加密存储、一键暂停
