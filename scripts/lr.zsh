# linux_recall 的命令行搜索函数。在 ~/.zshrc 里 source 这个文件：
#   source ~/WorkSpace/windows_recall_linux/scripts/lr.zsh
# 依赖: jq, fzf, xdg-open, ImageMagick；lr 的图片预览需要 kitty

LR_CAPTURES=${XDG_DATA_HOME:-$HOME/.local/share}/linux_recall/captures
LR_SCRIPTS=${${(%):-%x}:A:h}  # directory of this file

# lr-search <关键词>: 在 OCR 文字、窗口标题、URL、Anki 字段、Neovim / Obsidian 看得到的原文、
#   kitty 里 shell 的命令和输出、Claude Code 的会话标题和提问里搜索（原样匹配，不区分大小写）
# 输出: 时间 <TAB> 程序 <TAB> URL 或窗口标题 <TAB> 截图路径
lr-search() {
    jq -r --arg q "$1" '
      select([.ocr.text, .window.caption, .browser.url, .zotero.title, .zotero.doi,
              (.anki | .fields // .notes[0].fields // {} | [.[]] | join("\n")),
              ((.kitty.nvim // .neovide.nvim).windows // [] | map(.lines // [] | join("\n")) | join("\n")),
              .obsidian.file, (.obsidian.lines // [] | join("\n")),
              .kitty.shell.cmdline, (.kitty.shell.output // [] | join("\n")),
              .kitty.claude.title, .kitty.claude.last_prompt] | map(. // "") | join("\n")
             | ascii_downcase | contains($q | ascii_downcase))
      | [.captured_at[:19], .window.app_name, (.browser.url // .zotero.open_link // .anki.browse_query // .obsidian.open_link // .window.caption),
         (input_filename | sub("\\.json$"; ".webp"))] | @tsv' "$LR_CAPTURES"/*/*.json
}

# lr [关键词]: fzf 交互式搜索，在 kitty 里直接预览截图，见 scripts/lr
alias lr="$LR_SCRIPTS/lr"

# lr-highlight <json> <关键词> [输出.png]: 在截图上用红框标出匹配的 OCR 行，裁剪到 OCR 区域
lr-highlight() {
    local f=$1 q=$2 out=${3:-/tmp/lr-highlight.png}
    local -a crop
    # schema v1 captures OCR'd the whole screen and have no region
    crop=($(jq -r '.ocr.region // empty | "-crop", "\(.right - .left)x\(.bottom - .top)+\(.left)+\(.top)", "+repage"' "$f"))
    magick "${f%.json}.webp" -fill none -stroke red -strokewidth 6 \
        -draw "$(jq -r --arg q "$q" '[.ocr.lines[] | select(.text | ascii_downcase | contains($q | ascii_downcase))
                 | "rectangle \(.box[0][0]),\(.box[0][1]) \(.box[2][0]),\(.box[2][1])"] | join(" ")' "$f")" \
        "${crop[@]}" "$out" && echo "$out"
}
