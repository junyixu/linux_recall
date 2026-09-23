# linux_recall

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
```

`reason` 的含义：`first` 服务启动后的第一张；`changed` 同一个程序但画面变了；`similar` 太相似，没保存；`app A -> B` 换了程序（不管多相似都保存）；`locked` 锁屏。

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

**浏览过的网址（去重）**

```sh
jq -r '.browser.url // empty' */*.json | sort -u
```

**OCR 失败或用了本地兜底的截图**

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
| `lr [关键词]` | 用 fzf 交互式搜索：每行是一处匹配（程序 │ 所在那行文字，关键词标红 │ 时间和网站/论文页码）；右侧预览截图（kitty 里显示图片，选中的行红框、其他匹配橙框；不在 kitty 里显示 OCR 文字）。回车打开截图，`ctrl-o` 回到当时的内容：打开网址；在 Zotero 里打开论文并跳到那一页；在 Anki 的 Browse 窗口里打开那张卡片（`guiBrowse`）。Anki 卡片的字段也能搜索，匹配的每一行单独一行显示 |
| `lr-highlight <json> <关键词> [输出.png]` | 在截图上用红框标出匹配的行，并裁剪到 OCR 区域，默认输出 `/tmp/lr-highlight.png` |

```sh
lr-search 截图保存
lr moon                      # fzf 默认是模糊匹配；输入 'lanyue（前面加单引号）表示原样匹配
lr-highlight 2026-09-23/20260923-205142-366.json lunar && xdg-open /tmp/lr-highlight.png
```

在 kitty 里可以直接在终端显示图片：`kitty +kitten icat /tmp/lr-highlight.png`。

## 隐私

截图和 OCR 文字都**没有加密**，而且云端 OCR 会把活动窗口的截图上传到百度 AI Studio。还没有排除名单，所以截图前请确认屏幕上没有密码或其他敏感信息。不想上传到云端时，就不要设置 `PADDLEOCR_TOKEN`，这样只用本地 OCR。注意快捷键启动的程序从 `~/.config/environment.d/` 读取这个变量，只在终端里 `unset` 对快捷键无效；单次命令行运行可以用 `env -u PADDLEOCR_TOKEN uv run linux-recall-capture`。
