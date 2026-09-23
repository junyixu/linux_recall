# linux_recall 的命令行搜索函数。在 ~/.zshrc 里 source 这个文件：
#   source ~/WorkSpace/windows_recall_linux/scripts/lr.zsh
# 依赖: jq, fzf, xdg-open

LR_CAPTURES=${XDG_DATA_HOME:-$HOME/.local/share}/linux_recall/captures

# lr-search <关键词>: 在 OCR 文字、窗口标题、URL 里搜索（原样匹配，不区分大小写）
# 输出: 时间 <TAB> 程序 <TAB> URL 或窗口标题 <TAB> 截图路径
lr-search() {
    jq -r --arg q "$1" '
      select([.ocr.text, .window.caption, .browser.url] | map(. // "") | join("\n")
             | ascii_downcase | contains($q | ascii_downcase))
      | [.captured_at[:19], .window.app_name, (.browser.url // .window.caption),
         (input_filename | sub("\\.json$"; ".webp"))] | @tsv' "$LR_CAPTURES"/*/*.json
}

# lr [初始查询]: 用 fzf 交互式地模糊搜索所有截图；右侧预览 OCR 文字，回车打开截图
lr() {
    jq -r '[input_filename, .captured_at[:16], .window.app_name,
            (.browser.url // .window.caption), (.ocr.text // "" | gsub("\n"; " "))] | @tsv' \
        "$LR_CAPTURES"/*/*.json |
    fzf --delimiter '\t' --with-nth 2.. --query "${1:-}" \
        --preview 'jq -r ".window.app_name, .window.caption, .browser.url, \"\", .ocr.text" {1}' \
        --preview-window 'right,50%,wrap' \
        --bind 'enter:execute-silent(f={1}; xdg-open "${f%.json}.webp")'
}

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
