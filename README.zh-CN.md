# linux_recall

[English](README.md)

KDE Plasma 6 (Wayland) 上的 Windows Recall 式工具：按一个快捷键（或者由后台服务每分钟自动），保存活动窗口的截图，同时记录窗口信息、浏览器网址和窗口里的文字（OCR），存成一张图片加一个 JSON 文件，之后可以用命令行搜索。

## 安装

```sh
cd ~/WorkSpace/windows_recall_linux
uv sync
./scripts/install-hotkey.sh        # 注册全局快捷键 Meta+Alt+R（截图）和 Meta+Alt+C（Click to Do）
```

需要：
- `spectacle`（截图）
- `plasma-browser-integration`，并在浏览器里装对应扩展（获取网址）
- Zotero 7+：在 设置 → 高级 里勾选「允许此计算机上的其他应用程序与 Zotero 通信」（获取正在读的论文和页码）
- Anki + [AnkiConnect](https://ankiweb.net/shared/info/2055492159) 插件（获取正在复习的卡片）
- Obsidian 1.12+：在 设置 → 通用 里打开「命令行界面」（获取正在看的笔记和屏幕上的原文）
- kitty：`kitty.conf` 里设置 `allow_remote_control yes` 和 `listen_on unix:/tmp/kitty`（获取当前 tab 运行的程序、Neovim 打开的文件、Claude Code 的会话）；打开 shell integration（默认开启）才能记录上一条命令和它的输出
- `PADDLEOCR_TOKEN` 环境变量（云端 OCR；没有或超时时自动改用本地 RapidOCR）

命令行搜索还需要 `jq`；交互式搜索需要 `fzf`；标出匹配文字需要 ImageMagick（`magick`）。

## 截图

- 按 **`Meta+Alt+R`**，或者在命令行运行：

  ```sh
  uv run linux-recall-capture                     # 只截活动窗口（默认）
  uv run linux-recall-capture --mode fullscreen   # 截整个桌面，只 OCR 活动窗口
  uv run linux-recall-capture --full-res          # 保留 HiDPI 原始分辨率，不缩小
  uv run linux-recall-capture --ocr-timeout 30    # 云端 OCR 最多等 30 秒，超时改用本地
  uv run linux-recall-capture --no-ocr            # 不做 OCR
  ```

- 截图和窗口信息约 1 秒内保存好；OCR 结果稍后写进同一个 JSON（云端通常 5–60 秒）
- 为了省空间，保存的图片会按屏幕缩放比缩小到逻辑分辨率（2× 屏幕上每边缩小一半，一张窗口截图约 150 KB）。OCR 仍然用原始分辨率识别，识别完就删除原图，所以缩小不影响识别效果
- 完成后会弹一个系统通知

## Click to Do：选取窗口里的文字

按 **`Meta+Alt+C`**（或运行 `uv run linux-recall-click`）：截取活动窗口，用百度云 OCR 识别（按单词给出位置），然后在 Firefox 新窗口里打开一个页面，页面上是截图，文字可以像网页一样**按单词拖动选择**，`Ctrl+C` 复制。

- 识别时会弹通知“正在识别…”；云端通常要 10–30 秒
- 只用云端 OCR：失败或超过 `--timeout`（默认 60 秒）就弹通知报错，不会改用本地 OCR
- 页面上按 `t` 可以显示文字层（红色），用来检查位置是否对齐
- 跨行选择时按列排序：比如左边的侧边栏和右边的正文不会被选到一起
- 页面和截图放在 `~/.cache/linux_recall/clicktodo/`，一天后自动删除；不会存进截图记录

## 数据在哪里

```
~/.local/share/linux_recall/captures/
└── 2026-09-23/
    ├── 20260923-205142-366.webp    ← 活动窗口的截图
    └── 20260923-205142-366.json    ← 窗口、网址、OCR
~/.cache/linux_recall/capture.log   ← 日志，可以随时删除
```

JSON 的主要字段：

```jsonc
{
  "captured_at": "2026-09-23T20:51:42.366+02:00",
  "screenshot": {"file": "20260923-205142-366.webp", "mode": "window", "width": 1969, "height": 1068,
                 "scale": 0.5128,                  // 保存的图片相对原始截图的缩放比
                 "captured_width": 3840, "captured_height": 2083},
  "window": {
    "app_name": "Firefox",                          // 程序名
    "caption": "Why the US-China Moon race … — Mozilla Firefox",  // 窗口标题
    "desktop_file": "firefox", "pid": 2770, "exe": "/usr/lib/firefox/firefox",
    "output": "DP-1"                                 // 在哪块显示器上
  },
  "browser": {"url": "https://theconversation.com/…"},  // 不是浏览器时为 null
  "zotero": {                                        // 不是 Zotero 阅读器时为 null
    "title": "Finite element exterior calculus", "creators": ["Arnold"], "date": "2018",
    "doi": "10.1137/1.9781611975543", "url": "https://doi.org/10.1137/1.9781611975543",
    "page": 48,                                      // 截图时读到的页码
    "open_link": "zotero://open-pdf/library/items/I2R4MPPK?page=48",  // 打开 PDF 并跳到这一页
    "select_link": "zotero://select/library/items/NU7ER76Q"          // 在文库里选中这篇
  },
  "anki": {                                          // 不是 Anki 时为 null
    "mode": "review",                                // review 复习中 / browse 在 Browse 窗口 / other
    "card_id": 1706122964826, "note_id": 1706122964826, "deck": "Custom Study Session", "model": "Basic",
    "fields": {"Front": "…", "Back": "…"},          // 纯文本，可以搜索
    "browse_query": "cid:1706122964826"              // ctrl-o 用 guiBrowse 打开这张卡片
  },
  "kitty": {                                         // 不是 kitty 或没开远程控制时为 null
    "address": "unix:/tmp/kitty-7201",               // kitty 远程控制的 socket，ctrl-o 用它切回那个窗口
    "tab": {"id": 35, "title": "nvim"},              // 获得焦点的 tab
    "window": {"id": 37, "pid": 16134, "cmdline": ["/bin/zsh"], "cwd": "…"},  // tab 里获得焦点的 kitty window（shell）
    "foreground_processes": [{"pid": 98870, "cmdline": ["nvim"], "cwd": "…"}],  // shell 前台运行的程序
    "nvim": {                                        // 前台不是 Neovim 时为 null
      "pid": 98870, "server_pid": 98871,             // TUI 进程和 nvim --embed 服务进程
      "address": "/run/user/1000/nvim.98871.0",
      "file": "/home/…/notes.md", "line": 17, "col": 1, "filetype": "markdown", "modified": false,
      "mode": "n", "cwd": "…", "buffers": ["/home/…/notes.md"],  // 当前文件、光标、所有打开的 buffer
      "windows": [{                                  // 当前 tabpage 的每个窗口（不含浮动窗口）
        "file": "/home/…/notes.md", "buftype": "", "current": true,
        "first": 3,                                  // lines[i] 是文件的第 first + i 行
        "lines": ["…", "…"]                          // 截图时看得到的原文；插件界面和 .env、~/.ssh 等敏感文件为 null
      }]
    },
    "shell": {                                       // 需要 kitty 的 shell integration
      "cmdline": "make test",                        // 上一条命令；at_prompt 为 false 时是正在运行的命令
      "at_prompt": true, "exit_status": 1,           // 运行中时 exit_status 为 null
      "output_lines": 312, "output": ["…"]           // 命令的输出(准确的终端文字，不用 OCR)，只留最后 200 行；
                                                     // 全屏程序和 pass/gpg/printenv/cat .env 等只记命令
    },
    "claude": {                                      // 前台是 Claude Code 时
      "session_id": "c26e9654-…", "cwd": "…", "status": "busy", "name": "…",
      "title": "Kitty API 获取活动进程和文件",       // 会话记录里最新的 ai-title
      "last_prompt": "…",                            // 最后一次提问
      "transcript": "~/.claude/projects/…/c26e9654-….jsonl"
    }
  },
  "neovide": {"nvim": {…}},                          // Neovide 窗口，字段和 kitty.nvim 一样；不是 Neovide 时为 null
  "obsidian": {                                      // 不是 Obsidian 或没有打开笔记（关系图等）时为 null
    "vault": "Notes", "vault_path": "/home/…/Notes",
    "file": "diary/2026/09/2026-09-23.md",           // 相对 vault 的路径
    "view": "markdown",                              // 或 pdf、canvas 等（这时没有下面的字段）
    "mode": "preview",                               // preview 阅读视图 / source 编辑视图（这时还有光标 line、col）
    "tabs": ["diary/2026/09/2026-09-23.md"],         // 所有打开的笔记
    "heading": "💡 杂谈", "tags": ["#diary"], "aliases": [],  // heading 是看得到的第一行所在的标题
    "first": 5,                                      // lines[i] 是笔记的第 first + i 行
    "lines": ["…", "…"],                             // 截图时看得到的 Markdown 原文；敏感文件为 null
    "open_link": "obsidian://open?vault=Notes&file=diary/2026/09/2026-09-23.md"  // ctrl-o 打开这篇笔记
  },
  "ocr": {
    "engine": "paddleocr-cloud PP-OCRv6",           // 或 "rapidocr 3.9.2"（本地兜底）
    "fallback_reason": null,                         // 为什么改用本地 OCR
    "region": {"left": 2160, "top": 610, "right": 6098, "bottom": 2746},  // 识别的区域
    "text": "全部文字，一行一条",
    "lines": [{"text": "…", "score": 0.99, "box": [[x, y], [x, y], [x, y], [x, y]]}]
  }
}
```

`box` 和 `region` 都是保存下来的那张图片里的像素坐标。OCR 失败时 `ocr` 为 `null`。旧的截图（`schema_version` < 4）是整个桌面、没有缩小。

## 后台自动截图

`systemd/linux-recall.service` 每分钟截一次活动窗口；如果和上一次保存的截图是同一个程序、而且画面相似度 ≥ 0.95（dHash），就不保存。锁屏时跳过。

OCR 在后台做，不会拖慢截图：

- 保存时 `ocr` 先是 `null`，原始分辨率的图放进 `~/.cache/linux_recall/pending/`，后台线程从最新的一张开始调用云端 OCR，识别完写回 JSON 并删掉原图。daemon 重启后接着识别；最多留 200 张，超出就删最旧的（那张的 `ocr` 保持 `null`）
- daemon 的 `--ocr-timeout` 默认 180 秒（快捷键仍是 60 秒）
- 云端失败后暂停调用 1 分钟，之后每次失败翻倍，最多 30 分钟；暂停结束后的第一张就是试探。没有 `PADDLEOCR_TOKEN` 或 token 被拒（401/403）时直接暂停 30 分钟，并弹一次通知
- 云端不可用时，等了一小时以上的截图才会用本地 RapidOCR（限 2 个线程）识别，而且要满足：看起来没人在用（锁屏，或者连续 3 轮画面没变且 CPU 压力低）并且接着电源

资源占用（实测，3840×2080 的窗口）：截图约 0.6 秒 CPU（大部分是 spectacle），云端 OCR 约 0.1 秒 CPU；本地 OCR 约 20–40 秒 CPU、1.3 GB 内存，所以放在子进程里跑，跑完内存就释放。

```sh
cp systemd/linux-recall.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now linux-recall
```

**每次运行的结果**：每一轮都会在日志里写一行，说明这次保存了（`KEEP`）还是因为太相似而跳过了（`SKIP`）：

```sh
journalctl --user -u linux-recall -f -o cat
```

```
INFO KEEP  kitty        similarity 0.543 (threshold 0.95)  changed  [kept 2, skipped 0]
INFO SKIP  kitty        similarity 1.000 (threshold 0.95)  similar  [kept 2, skipped 1]
INFO KEEP  firefox      similarity 0.612 (threshold 0.95)  app kitty -> firefox  [kept 3, skipped 1]
INFO SKIP  screen locked  [kept 3, skipped 2]
INFO SKIP  excluded window (org.keepassxc.KeePassXC)  [kept 3, skipped 3]
```

`reason` 的含义：`first` 服务启动后的第一张；`changed` 同一个程序但画面变了；`similar` 太相似，没保存；`app A -> B` 换了程序（不管多相似都保存）；`locked` 锁屏；`excluded` 活动窗口在排除名单里（见[隐私](#隐私)），`app` 是被排除的程序或标题里匹配到的词。

**去重效果统计**：每一轮也会追加一条记录到 `~/.cache/linux_recall/daemon.jsonl`：

```json
{"time": "2026-09-23T21:44:14+02:00", "decision": "keep", "reason": "changed", "app": "kitty", "similarity": 0.543, "threshold": 0.95, "file": ".../20260923-214413-457.json"}
```

```sh
cd ~/.cache/linux_recall

# 总共几轮、保存几张、跳过几张、跳过比例
jq -s '{cycles: length, kept: map(select(.decision == "keep")) | length,
        skipped: map(select(.decision == "skip")) | length}
       | .skip_rate = "\(if .cycles > 0 then .skipped * 100 / .cycles | round else 0 end)%"' daemon.jsonl

# 相似度分布（按 0.05 分组），用来判断阈值是否合适
jq -r 'select(.similarity != null) | "\(.similarity * 20 | floor / 20)"' daemon.jsonl | sort | uniq -c

# 每个程序各保存/跳过了多少
jq -s -c 'map(select(.reason == "similar" or .reason == "changed")) | group_by(.app)
          | map({app: .[0].app, kept: map(select(.decision == "keep")) | length,
                 skipped: map(select(.decision == "skip")) | length})' daemon.jsonl
```

想多保存一些，就调低 service 里的 `--threshold`；想少保存一些就调高，然后 `systemctl --user daemon-reload && systemctl --user restart linux-recall`。

## 用 jq 查看

下面的命令都在截图目录里运行：

```sh
cd ~/.local/share/linux_recall/captures
```

**某张截图的 OCR 文字**

```sh
jq -r '.ocr.text' 2026-09-23/20260923-205142-366.json
```

**时间线：每张截图的时间、程序、标题**

```sh
jq -r '[.captured_at[11:19], .window.app_name, .window.caption[:60]] | @tsv' */*.json
```

```
20:18:34  kitty    Yazi: scripts
20:51:42  Firefox  Why the US-China Moon race could turn into a lunar …
```

**只看某个程序的截图**

```sh
jq -r 'select(.window.app_name == "Firefox") | [.captured_at[:16], .browser.url] | @tsv' */*.json
```

**只看某一天**：把 `*/*.json` 换成 `2026-09-23/*.json`。

**读过的论文和页码（Zotero）**

```sh
jq -r 'select(.zotero) | [.captured_at[:16], .zotero.title, "p.\(.zotero.page)", .zotero.open_link] | @tsv' */*.json
xdg-open 'zotero://open-pdf/library/items/I2R4MPPK?page=48'   # 在 Zotero 里打开并跳到那一页
```

**复习过的 Anki 卡片**

```sh
jq -r 'select(.anki.card_id) | [.captured_at[:16], .anki.deck, (.anki.fields | first(.[]) | .[:60])] | @tsv' */*.json
```

**看过的 Obsidian 笔记**

```sh
jq -r 'select(.obsidian) | [.captured_at[:16], .obsidian.vault, .obsidian.file, .obsidian.heading // ""] | @tsv' */*.json
xdg-open 'obsidian://open?vault=Notes&file=diary/2026/09/2026-09-23.md'   # 在 Obsidian 里打开
```

**在 Neovim 里编辑过的文件**

```sh
jq -r 'select(.kitty.nvim.file) | [.captured_at[:16], "\(.kitty.nvim.file):\(.kitty.nvim.line)"] | @tsv' */*.json
```

**在 Neovim 里看到过的代码**（buffer 原文，没有 OCR 误差，带行号）

```sh
jq -r --arg q _children '.captured_at[:16] as $t | .kitty.nvim.windows // [] | .[] | select(.lines) | . as $w
  | .lines | to_entries[] | select(.value | contains($q)) | [$t, "\($w.file):\($w.first + .key)", .value] | @tsv' */*.json
```

**跑过的失败命令**（kitty shell integration）

```sh
jq -r 'select(.kitty.shell.exit_status // 0 | . != 0) | [.captured_at[:16], .kitty.shell.exit_status, .kitty.shell.cmdline] | @tsv' */*.json | sort -u -k3
```

**Claude Code 的会话**（`claude --resume <id>` 可以接着聊）

```sh
jq -r 'select(.kitty.claude.session_id) | [.kitty.claude.session_id, .kitty.claude.cwd, .kitty.claude.title] | @tsv' */*.json | sort -u
```

**浏览过的网址（去重）**

```sh
jq -r '.browser.url // empty' */*.json | sort -u
```

**OCR 失败或用了本地兜底的截图**（daemon 的截图在后台识别完之前 `ocr` 也是 `null`）

```sh
jq -r 'select(.ocr == null or .ocr.fallback_reason != null) | input_filename' */*.json
```

## 搜索：某段文字出现在哪张截图、当时在用什么程序

### 一条 jq 命令

在 OCR 文字、窗口标题和网址里搜 `lunar`（原样匹配、不区分大小写，中文也可以）：

```sh
jq -r --arg q "lunar" '
  select([.ocr.text, .window.caption, .browser.url] | map(. // "") | join("\n")
         | ascii_downcase | contains($q | ascii_downcase))
  | [.captured_at[:19], .window.app_name, (.browser.url // .window.caption),
     (input_filename | sub("\\.json$"; ".webp"))] | @tsv' */*.json
```

```
2026-09-23T20:51:42  Firefox  https://theconversation.com/…lunar-land-grab-285421  2026-09-23/20260923-205142-366.webp
```

输出依次是：时间、程序、网址（不是浏览器时是窗口标题）、截图路径。想用正则，把 `contains($q | ascii_downcase)` 换成 `test($q; "i")`。

**找到后，看具体是哪几行匹配、在截图的什么位置**

```sh
jq -r --arg q "lunar" '.ocr.lines[] | select(.text | ascii_downcase | contains($q))
                        | "\(.box[0][0]),\(.box[0][1])\t\(.text)"' 2026-09-23/20260923-205142-366.json
```

```
3177,1728  China has been developing a lunar landing vehicle called Lanyue (CCTV Video News Agency).
3173,1803  So how should we expect this lunar contest to unfold? The US-led effort has a
```

**截图很多时，先用 rg 快速筛出文件**

```sh
rg -l -i 'lunar' --glob '*.json' . | xargs jq -r '[.captured_at[:16], .window.app_name, input_filename] | @tsv'
```

### 现成的 shell 函数

在 `~/.zshrc` 里加一行：

```sh
source ~/WorkSpace/windows_recall_linux/scripts/lr.zsh
```

之后可以用三个命令：

| 命令 | 作用 |
|---|---|
| `lr-search <关键词>` | 就是上面那条 jq 命令，输出 时间 / 程序 / 网址或标题 / 截图路径 |
| `lrf [关键词]` | 用 fzf 交互式搜索：每行是一处匹配（程序 + 截图时间（月-日 时:分）│ 所在那行文字，关键词标红 │ 网站/论文页码）；右侧预览截图（kitty 里显示图片，选中的行红框、其他匹配橙框；不在 kitty 里显示 OCR 文字）。回车输出选中截图的 JSON 路径（可以接着用 jq 处理），`ctrl-f` 输出截图时 Neovim 打开的文件路径（kitty 前台是 Neovim 的截图，程序名显示为 `kitty(neovim)`；没有文件时不退出），`ctrl-s` 打开截图，`ctrl-o` 回到当时的内容：打开网址；在 Zotero 里打开论文并跳到那一页；在 Anki 的 Browse 窗口里打开那张卡片（`guiBrowse`）；在 Obsidian 里打开那篇笔记（`obsidian://open`）；Neovim 的截图：那个 nvim 还开着，就在它里面打开文件、跳到那一行，并切到它所在的 kitty 窗口，已经关了就新开一个 kitty tab 运行 `nvim +行号 文件`；Neovide 的截图同理（还开着就跳过去并用 `kdotool` 把窗口提到前面，关了就 `neovide -- +行号 文件`）；其他 kitty 截图：切到当时那个 kitty 窗口，窗口关了但 tab 还在就切到那个 tab，都关了时如果截图时在跑 Claude Code，就新开一个 tab 运行 `claude --resume <会话 id>`，否则提示。shell 的命令和输出、Claude Code 的会话标题和最后一次提问也能搜索。Anki 卡片的字段、Neovim 截图时看得到的 buffer 原文和 Obsidian 看得到的笔记原文也能搜索，匹配的每一行单独一行显示（Neovim 和 Obsidian 显示成 `文件名:行号`，`ctrl-o` 跳到这一行，`ctrl-f` 输出这个文件） |
| `lr-highlight <json> <关键词> [输出.png]` | 在截图上用红框标出匹配的行，并裁剪到 OCR 区域，默认输出 `/tmp/lr-highlight.png` |

```sh
lr-search 截图保存
lrf moon                     # fzf 默认是模糊匹配；输入 'lanyue（前面加单引号）表示原样匹配
lr-highlight 2026-09-23/20260923-205142-366.json lunar && xdg-open /tmp/lr-highlight.png
```

在 kitty 里可以直接在终端显示图片：`kitty +kitten icat /tmp/lr-highlight.png`。

## 隐私

截图和 OCR 文字都**没有加密**，而且云端 OCR 会把活动窗口的截图上传到百度 AI Studio。排除名单只覆盖少数程序，所以截图前请确认屏幕上没有密码或其他敏感信息。

**排除名单**（`linux_recall/exclude.py`）：活动窗口是下面这些时，定时和快捷键都不截图（连 spectacle 都不调用，缓存里也不会有图片）；快捷键会弹通知 “Not captured”。

- `APPS`（按 `desktop_file` 匹配）：KeePassXC、Bitwarden、1Password、KWallet、Seahorse、polkit 认证框、ksshaskpass、pinentry-qt，以及 Spectacle 本身（它的框选界面是整个桌面的静止画面）
- `CAPTIONS`（窗口标题包含）：`Private Browsing`（Firefox 隐私窗口）、`(Incognito)`、`(Private)`、`[InPrivate]`
- `SITES`（活动标签页网址的域名，包括子域名）：`boc.cn`（中国银行）。网址来自 Plasma Browser Integration，所以浏览器扩展没连上时拿不到网址，也就不会排除

要加别的程序，把它的 `desktop_file` 加进 `APPS`（用 `uv run python -m linux_recall.kwin` 查），改完 `systemctl --user restart linux-recall`。只检查活动窗口：`--mode fullscreen` 截的整个桌面里仍然可能有别的窗口；加网站就把域名加进 `SITES`。不想上传到云端时，就不要设置 `PADDLEOCR_TOKEN`，这样只用本地 OCR。注意快捷键启动的程序从 `~/.config/environment.d/` 读取这个变量，只在终端里 `unset` 对快捷键无效；单次命令行运行可以用 `env -u PADDLEOCR_TOKEN uv run linux-recall-capture`。
