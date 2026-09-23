# linux_recall

KDE Plasma 6 (Wayland) 上的 Windows Recall 式工具：按一个快捷键，保存整个桌面的截图，同时记录当时的活动窗口、浏览器网址和窗口里的文字（OCR），存成一张图片加一个 JSON 文件，之后可以用命令行搜索。

## 安装

```sh
cd ~/WorkSpace/windows_recall_linux
uv sync
./scripts/install-hotkey.sh        # 注册全局快捷键 Meta+Alt+R
```

需要：
- `spectacle`（截图）
- `plasma-browser-integration`，并在浏览器里装对应扩展（获取网址）
- `PADDLEOCR_TOKEN` 环境变量（云端 OCR；没有或超时时自动改用本地 RapidOCR）

命令行搜索还需要 `jq`；交互式搜索需要 `fzf`；标出匹配文字需要 ImageMagick（`magick`）。

## 截图

- 按 **`Meta+Alt+R`**，或者在命令行运行：

  ```sh
  uv run linux-recall-capture                     # 截整个桌面，只 OCR 活动窗口
  uv run linux-recall-capture --mode window       # 只截活动窗口
  uv run linux-recall-capture --ocr-timeout 30    # 云端 OCR 最多等 30 秒，超时改用本地
  uv run linux-recall-capture --no-ocr            # 不做 OCR
  ```

- 截图和窗口信息约 2 秒内保存好；OCR 结果稍后写进同一个 JSON（云端通常 5–60 秒）
- 完成后会弹一个系统通知

## 数据在哪里

```
~/.local/share/linux_recall/captures/
└── 2026-09-23/
    ├── 20260923-205142-366.webp    ← 整个桌面的截图
    └── 20260923-205142-366.json    ← 窗口、网址、OCR
~/.cache/linux_recall/capture.log   ← 日志，可以随时删除
```

JSON 的主要字段：

```jsonc
{
  "captured_at": "2026-09-23T20:51:42.366+02:00",
  "screenshot": {"file": "20260923-205142-366.webp", "width": 6098, "height": 4626},
  "window": {
    "app_name": "Firefox",                          // 程序名
    "caption": "Why the US-China Moon race … — Mozilla Firefox",  // 窗口标题
    "desktop_file": "firefox", "pid": 2770, "exe": "/usr/lib/firefox/firefox",
    "output": "DP-1"                                 // 在哪块显示器上
  },
  "browser": {"url": "https://theconversation.com/…"},  // 不是浏览器时为 null
  "ocr": {
    "engine": "paddleocr-cloud PP-OCRv6",           // 或 "rapidocr 3.9.2"（本地兜底）
    "fallback_reason": null,                         // 为什么改用本地 OCR
    "region": {"left": 2160, "top": 610, "right": 6098, "bottom": 2746},  // 识别的区域
    "text": "全部文字，一行一条",
    "lines": [{"text": "…", "score": 0.99, "box": [[x, y], [x, y], [x, y], [x, y]]}]
  }
}
```

`box` 和 `region` 都是整张截图里的像素坐标。OCR 失败时 `ocr` 为 `null`。

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
| `lr [关键词]` | 用 fzf 交互式搜索所有截图：右侧预览程序、标题、网址和 OCR 文字，回车用默认看图程序打开截图 |
| `lr-highlight <json> <关键词> [输出.png]` | 在截图上用红框标出匹配的行，并裁剪到 OCR 区域，默认输出 `/tmp/lr-highlight.png` |

```sh
lr-search 截图保存
lr moon                      # fzf 默认是模糊匹配；输入 'lanyue（前面加单引号）表示原样匹配
lr-highlight 2026-09-23/20260923-205142-366.json lunar && xdg-open /tmp/lr-highlight.png
```

在 kitty 里可以直接在终端显示图片：`kitty +kitten icat /tmp/lr-highlight.png`。

## 隐私

截图和 OCR 文字都**没有加密**，而且云端 OCR 会把活动窗口的截图上传到百度 AI Studio。还没有排除名单，所以截图前请确认屏幕上没有密码或其他敏感信息。不想上传到云端时，就不要设置 `PADDLEOCR_TOKEN`，这样只用本地 OCR。注意快捷键启动的程序从 `~/.config/environment.d/` 读取这个变量，只在终端里 `unset` 对快捷键无效；单次命令行运行可以用 `env -u PADDLEOCR_TOKEN uv run linux-recall-capture`。
