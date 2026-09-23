# X11 与 Wayland 架构差异，以及在 KWin Plasma 6 Wayland 上实现 event-driven capture 的可行性（2026 年）

## TL;DR

- **架构层面**：X11 是一个独立进程的 X server，所有 client 通过 X protocol 与之通信，任何 client 都能读取其他 client 的 buffer、input 和 window 列表；Wayland 里 compositor 就是 display server，采用 per-client isolation 和 buffer-based rendering，默认没有全局 window list、没有全局 input、没有无授权的 screen capture。X11 能做而 Wayland（在 KWin 上）不能无额外授权做的核心三件事是：任意 window 的全局 screen capture、全局 window 枚举/active window 查询、全局 input monitoring。
- **对你的 event-driven capture 用例的直接结论（2026 年，KWin Plasma 6）**：*app switch* 可以用 KWin scripting API（over D-Bus，`windowActivated` 类信号 / kdotool / kwst）可靠实现，这是目前唯一稳妥的 window-context 来源，因为 KWin 至今**不实现** `ext-foreign-toplevel-list-v1`；*idle/pause* 可以用 `ext-idle-notify-v1`（KWin 自 Plasma 5.27 起支持）可靠、event-driven 地实现；*click* 无法在无授权下做全局监听，只能走 `/dev/input`（evdev/libinput，需 `input` group，且没有 window context）或 portal InputCapture（需权限，且语义是转发而非监听）；*scroll* 在全局层面基本不可能检测，只能靠对周期 capture 做 image diff 近似。
- **capture 本身**：KWin 不向普通 client 暴露任何 Wayland capture 协议（无 wlr-screencopy，无 `ext-image-copy-capture-v1`），唯一 sanctioned 的第三方路径是 xdg-desktop-portal 的 ScreenCast（PipeWire + 权限弹窗 + restore token）。因此一个真正 unprivileged 的 daemon 能做到的最佳形态是：portal ScreenCast 持久授权拿一路 PipeWire stream（含 `SPA_META_VideoDamage` 可用于跳过未变化帧）+ KWin script 拿 active window/title/geometry + `ext-idle-notify-v1` 拿 idle 触发，其余触发（click/scroll）要么降级为 image diff，要么需要用户额外授予 `input` group 或 portal 权限。

## X11 与 Wayland 的架构/协议差异

### X11 的模型
X 起源于 1984 年 MIT 的 Project Athena，但 X protocol 自 1987 年 9 月 15 日发布 X11 起就固定在版本 11（“X11”即由此得名），per Wikipedia “X Window System”：“X originated as part of Project Athena at Massachusetts Institute of Technology (MIT) in 1984. The X protocol has been at version 11 (hence 'X11') since September 1987... the release of X11 finally occurred on 15 September 1987.” 它是 network-transparent 的 client-server 架构：一个独立进程的 X server 拥有显示硬件，applications 作为 clients 通过 X protocol（本地 socket 或 TCP）与之通信。X server 内部维护一个全局的 window 树（root window 及其所有子 window），并通过一系列 extension 把能力暴露给任何能打开该 display 的 client：

- **XComposite**：把 window 内容 redirect 到离屏 pixmap，使 compositing manager 能拿到每个 window 的像素。
- **XDamage**：通知某个 drawable 的哪些区域发生了变化（damage region）。
- **XTEST**：合成 input（synthetic pointer/keyboard events）。
- **XInput2 (XI2)**：设备与事件模型，可在 root window 上注册 raw events，从而全局读取所有键鼠输入。
- **XRecord**：记录/回放协议层事件流。
- **XGrabKey / XGrabKeyboard**：全局抢占按键组合。
- **XShape**：非矩形 window。
- EWMH（`_NET_ACTIVE_WINDOW`、`_NET_CLIENT_LIST` 等 root window property）+ `xprop` / `xdotool` / `wmctrl`：枚举 window、查 active window、标题、geometry。

关键点是 X11 **没有 client isolation**：任何 client 都能截屏任意其他 client（`XGetImage` on root，或 `xwd`）、注入 input 到任意 window、读取全局键盘（`xinput test-xi2 --root`）、枚举并查询任意 window 的 property。freedesktop 的 Wayland architecture 文档直言 X server 如今“只是应用与 compositor、以及 compositor 与硬件之间多出来的一个中间人”，大量原本由 X server 承担的复杂度（KMS、evdev、mesa、fontconfig、freetype、cairo）已经下沉到 kernel 或独立库。

### Wayland 的模型
Wayland 是**协议**，不是某个具体 server；每个桌面环境自带一个 compositor 实现（KWin、Mutter、Sway/wlroots、Weston 等）。核心设计：

- **compositor 就是 display server**：window manager、compositor、display server 三者合一。freedesktop 文档原话：“In wayland the compositor is the display server. We transfer the control of KMS and evdev to the compositor.” 这消除了 X11 那一层额外 IPC round-trip。
- **buffer-based rendering + direct rendering**：client 直接渲染到自己在显存里的 buffer，把 damage 直接提交给 compositor；compositor 把这些 buffer 作为 texture 合成。input 事件由 kernel evdev → compositor，compositor 根据自己的 scenegraph 决定送给哪个 window，并做屏幕坐标到 window-local 坐标的逆变换。
- **per-client isolation（安全模型）**：core protocol 只定义 client 如何把数据写进自己的 surface。client **看不到**其他 client 的 buffer，**收不到**没有 focus 时的 input，**拿不到**全局 window list 对象。所有“越界”能力都被移到 extension protocol，且通常要 compositor 授权。
- **frame 原子性 / 无 tearing**：Wayland 的每次 surface commit 是原子的，一帧要么完整呈现要么不呈现，不会出现 X11 那种撕裂或半更新（除非用 `tearing-control-v1` 显式请求）。

Wikipedia 的 windowing system 条目总结得准确：Wayland core protocol 的 scope 远小于 X11 core protocol，只定义 client 如何把数据写进 surface，其余一切都交给 extension protocol（在 wayland-protocols 仓库里以 stable/staging/ext- 等分级维护）。

## X11 能做、Wayland（KWin）不能无额外机制做的事

下面按你的用例逐条给出机制、授权要求、以及 2026 年在 KWin/Plasma 6 上的确切状态。作为版本锚定：per Wikipedia “KDE Plasma 6”，Plasma 6.6 发布于 2026-02-17（引入截图 OCR 文本提取），Plasma 6.7 发布于 2026-06-16，且是“the final release of Plasma to have support for an X11 Session”。

### 1. 任意 window/screen 的全局 screen capture

**X11**：`XGetImage`、XComposite redirect，任何 client 零授权即可截任意 window/整个 root。

**Wayland**：core 无此能力。存在的机制分三类：

- **wlr-screencopy-unstable-v1**：只有 wlroots 系（Sway、Hyprland、niri 等）实现，KWin **不实现**。
- **`ext-image-copy-capture-v1`（staging）+ `ext-image-capture-source-v1`**：这是新的、compositor 无关的替代，支持 output 和单个 toplevel 捕获。wlroots/Sway 已经合入（emersion 的 PR），但多份 2026 年的实机报告显示 **KWin 6.6.x / 6.7.x 均不 advertise 这些 global**（`wayland-info` 里 65–66 个 global 中都没有）。所以在 KWin 上这条路目前走不通。**注意**：该协议对本用例有价值的一点是，`ext_image_copy_capture_frame_v1` 会发 `damage` 事件——协议原文：“The first captured frame in a session will always carry full damage. Subsequent frames' damaged regions describe which parts of the buffer have changed since the last ready event.” 即 capturer 可以拿到“哪些区域变了”，正好用于 dedup。但 KWin 不暴露它，等于拿不到。
- **KWin `org.kde.KWin.ScreenShot2` D-Bus 接口**：受限。client 必须在其**已安装的 .desktop 文件**里声明 `X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`，否则报 `org.kde.KWin.ScreenShot2.Error.NoAuthorized: "The process is not authorized to take a screenshot"`。这个授权机制由 KDE 的 Méven Car 在 Phabricator D29407（2020 年 5 月）引入，最可能随 Plasma 5.20 发布（版本号为推断）。它不是给任意 unprivileged 进程用的，且历史上被 KDE 自己称为临时 workaround。
- **xdg-desktop-portal ScreenCast（+ Screenshot）**：这是**唯一 sanctioned 的第三方路径**。通过 `org.freedesktop.portal.ScreenCast` 协商，实际像素走 PipeWire stream。首次需要用户在 xdg-desktop-portal-kde 的弹窗里选择要共享的 output/window。**restore token / persist_mode** 可实现持久授权：`persist_mode=2`（“until explicitly revoked”，version 4 起）拿到 restore token 后，后续可跳过弹窗。要点：restore token 是 single-use 的——portal 文档原话：“The restore token is invalidated after using it once. To restore the same session again, use the new restore token sent in response to starting this session.”

**结论**：unprivileged daemon 在 KWin 上要 capture，只能走 portal ScreenCast + PipeWire，接受一次性用户授权 + restore token 持久化。这就是我们上一轮讨论里那些 X11-only 工具（OpenRecall/screenpipe/ActivityWatch）在 KWin 上失灵的根因。

### 2. 枚举 window / active window / 标题 / geometry

**X11**：`_NET_ACTIVE_WINDOW`、`_NET_CLIENT_LIST`、EWMH、`xdotool`、`xprop`，零授权。

**Wayland**：core 无对应能力给普通 client。可选项：

- **`ext-foreign-toplevel-list-v1`（staging）**：这是标准化的“列出所有 toplevel 及其 title/app_id/identifier”协议。**KWin 至今不实现**。KDE bug 483227 “Support ext_foreign_toplevel_list_v1” 据第三方转述状态为 **RESOLVED NOT A BUG**（此状态我无法在 bugs.kde.org 直接核实，标注为**未验证**；但该 bug 在 2025 年 11 月仍有评论活动）。KWin 主力开发者 Xaver Hugl 在该 bug 的评论（经由 KeePassXC 讨论转述，同样为二手）表达了反对：认为这是“password manager API 缺失的 workaround”，且“with libei you just get global input, but if some other window pops up during the 'typing', it would just type into the wrong window”。多份 2026 年实机报告（KWin 6.6.6）确认 `ext_foreign_toplevel_list_v1` global 不存在。
- **wlr-foreign-toplevel-management-unstable-v1**：wlroots-only，KWin 不实现（KDE bug 502647 仅 REPORTED）。
- **`org_kde_plasma_window_management`（KWin 私有）**：存在，但是**受限 global**。KWin 自 Plasma 5.17 起（Aleix Pol 的 Phabricator D22571，2019 年 7 月合入）通过 client .desktop 文件里的 `X-KDE-Wayland-Interfaces=` 白名单来过滤这些私有 global（在 advertisement 阶段就 block 掉）。开发者自己承认这不是真正的安全边界（fvogt：“comparing executables is just completely flawed and does not provide any security”），但对普通第三方 daemon 而言，它就是拿不到。
- **KWin scripting API（over D-Bus）**：**这是 KWin Wayland 上事实上唯一可用、且可靠的 window-context 来源**。`kdotool`、`kwst` 等工具的做法是：动态生成一段 KWin JavaScript，通过 D-Bus 的 `org.kde.KWin` load/run/unload。可以拿 active window 的 UUID、title、geometry，也能监听 `windowActivated` 信号做 app-switch 触发。**代价**：kdotool 每次调用都 load/run/unload 一个临时 script，开销大；有实机报告（OpenDeck #425）显示 250ms 轮询会把 D-Bus session bus 打爆（约 486 connections/min）。正确做法是**装一个常驻 KWin script**，用信号推送 `windowActivated` 而非轮询。
- **GNOME 对照**：GNOME 走 Shell extension（JS）经 D-Bus 暴露，同样是 compositor 特定方案。

**结论**：app-switch/active-window/title/geometry 在 KWin 上用常驻 KWin script + D-Bus 信号可以可靠 event-driven 地拿到，这是最佳方案；不要依赖 `ext-foreign-toplevel-list-v1`（KWin 没有）。

### 3. 全局 input monitoring / 全局 hotkey / synthetic input

**X11**：XRecord、XInput2 raw events、XGrabKey、XTEST，零授权即可全局键鼠监听、抢键、注入。这也正是 X11 被称为“keylogger's paradise”的原因。

**Wayland**：设计上**没有**全局 input monitoring。可选项：

- **libinput / evdev 直接读 `/dev/input`**：绕过 compositor，需要用户在 `input` group（否则无权限）。拿到的是**原始设备事件，没有任何 window context**，也无法知道事件最终进了哪个 window。可以用来检测“有 click / 有 scroll 发生”，但无法关联到 window。
- **xdg-desktop-portal RemoteDesktop + libei**：用于**注入** input（sender 模式）。KWin 自 Plasma 6.1 起实现（kwin 6.0.90.1，2024 年 6 月）。需要权限；语义是模拟输入，不是监听。libei 自 portal 1.17（2023 年年中）成为 RemoteDesktop 和 InputCapture 的传输层；session persistence 自 portal 1.21.0 起（主要由 Jonas Ådahl 完成）。
- **xdg-desktop-portal InputCapture + libei**：用于**捕获**（receiver 模式），面向 Synergy/InputLeap/Input Leap 这类“把本机键鼠转发到另一台机器”的场景。KWin 自 Plasma 6.1 实现。但它的语义是：应用先协商 pointer barrier（屏幕边缘），越过 barrier 才开始捕获；**不是**给你做后台全局 keylogger 的通用接口，且需要用户授权。Peter Hutterer 明确：EIS 实现（compositor）“is in control of virtually everything”。
- **xdg-desktop-portal GlobalShortcuts portal**：注册**全局快捷键**，激活时收到 `Activated` 信号。KWin 自 **Plasma 5.27**（发布于 2023-02-14）起支持——KDE 官方公告原话：“Plasma on Wayland has also gained support for the Global Shortcuts portal. This allows apps on Wayland to offer a standardized user interface for setting and editing global shortcuts.” GNOME 侧 xdg-desktop-portal-gnome 自 **GNOME 48**（发布于 2025-03-19）起实现——GNOME 48 developer release notes 原话：“With GNOME 48, it is now possible for apps to register system-wide global shortcuts. This allows apps to setup keyboard shortcuts which can be used while the app does not have focus.” 这可以让你注册若干固定快捷键作为触发，但**不能**把它当作“监听所有按键”的手段。
- **KWin 私有 EIS D-Bus 接口**：存在一个绕过 portal 的私有路径（kwin-mcp 项目利用它做“zero authorization prompts”的 input 注入），但这属于内部接口，不应视为 unprivileged app 的稳定 API。

**结论**：click 的全局检测在无授权下不可能。最佳近似分两档：(a) 若你愿意让用户加入 `input` group，用 evdev/libinput 监听“发生了 click/scroll”这一事实（无 window context），再和 KWin script 的 active-window 合并；(b) 完全 unprivileged 则放弃 click 触发，改用 portal GlobalShortcuts 注册一个“手动截取”热键，其余靠 idle + app-switch + 周期 capture。

### 4. idle / activity 检测

**X11**：XScreenSaver extension 的 `XIdleTime`（`xprintidle`），零授权。

**Wayland/KWin**：

- **`ext-idle-notify-v1`（staging）**：**这是 KWin 上最佳、可靠、event-driven 的 idle 方案**。client 注册一个 timeout，无活动到达阈值时收到 `idled`，活动恢复时收到 `resumed`。KWin 自 **Plasma 5.27** 起支持（KWin GitLab MR !2959 “wayland: Add support for ext-idle-notify-v1”，Vlad Zahorodnii “requested to merge work/zzag/idle-notify-v1 into master Sep 16, 2022”）。协议 version 2+ 还提供 `get_input_idle_notification`，只跟踪真实硬件键鼠输入，避免被应用状态变化/idle inhibitor 造成假 resume。ActivityWatch 的 awatcher 在 KWin 上正是选用 “Wayland idle (ext-idle-notify-v1)” watcher。它是 unprivileged 的，普通 Wayland client 直接 bind global 即可。
- **`org.freedesktop.ScreenSaver` 的 `GetSessionIdleTime`**：在 KWin **Wayland 上被显式禁用**——KDE 在 Plasma 5.26.90 changelog 里有一条 “Screensaver interface: Send an error for GetSessionIdleTime on wayland”（修 bug 449488）。所以这条在 Wayland 下不可用。
- **systemd-logind `IdleHint`**：粒度粗、依赖 session 管理器上报，不适合精细 activity 检测。
- **GNOME 对照**：`org.gnome.Mutter.IdleMonitor.GetIdletime`（KWin 无对应）。

**结论**：用 `ext-idle-notify-v1`，version 2 的 `get_input_idle_notification`。这是整套方案里唯一一个既 unprivileged 又 event-driven 又标准化的触发。

### 5. scroll / “content changed” 检测

**X11**：XDamage 能告诉你某个 window 的哪些区域变了。

**Wayland**：core 里 frame callback 和 damage 都是 client↔compositor 之间的，第三方 client **拿不到**别的 window 的 damage。两个可能的“变化信号”来源：

- **`ext-image-copy-capture-v1` 的 `damage` 事件**：如前所述能给出 damaged region，但 KWin 不暴露该协议。
- **PipeWire screencast 的 `SPA_META_VideoDamage` metadata**：如果你已经通过 portal ScreenCast 拿到一路 PipeWire stream，KWin 的 screencast 插件**确实**会协商并发送 damage metadata——KWin 的 `screencaststream.cpp` 协商的 metadata 类型包括 `SPA_META_Cursor`、`SPA_META_VideoDamage`、`SPA_META_Header`（见 KDE bug 525308 对源码的引用）。`SPA_META_VideoDamage` 定义为“array of struct spa_meta_region with damage”，即每帧“相对上一帧哪块变了”。**这可以用来避免重复 capture 相同帧**，是你 dedup 逻辑的一个真实可用信号（前提是你已经在跑 ScreenCast）。
- **presentation-time 协议**：只给呈现时间戳，不给内容变化语义。

**结论**：全局“检测到用户 scroll 了”这个事件，unprivileged 下基本不可能直接拿到。最佳近似是：既然你无论如何要 capture，就用 PipeWire stream 的 `SPA_META_VideoDamage`（或对帧做 image diff / perceptual hash）判断“内容变了”，把它当作 content-changed 触发，而不是去检测 scroll 这个 input 动作本身。

### 6. accessibility tree（AT-SPI2）作为 OCR 的替代

**AT-SPI2** 基于 D-Bus，因此**与 display server 无关**，在 Wayland 上照常工作（它不依赖 X11）。它能给出 application → window → 各 UI 元素（button/entry/text area/menu item 等）的结构化树，包括**文本内容和 focused element**——这正是 screenpipe 在 macOS/Windows 上用 accessibility API 取代 OCR 的思路。要点与坑：

- **toolkit 覆盖**：GTK、Qt（需 `qt-at-spi` bridge）暴露良好。
- **Chromium / Electron**：默认只暴露 application→frame 骨架，必须用 `--force-renderer-accessibility` 启动，或者在 session 上打开 accessibility 信号（`org.a11y.Status` 的 flags）才会构建完整 tree。cua 的做法是让 daemon 在 session bus 上把 `org.a11y.Status` accessibility flag 置位，Chromium/Electron/GTK/Qt 会“retroactively”建树。**风险**：有 2026 年报告（cua #2915）称在 Chrome 151 上这个 ScreenReaderEnabled 前提“no longer holds”，即该技巧在新版可能失效——标注为**需实测验证**。
- **焦点/坐标**：freedesktop 的 next-gen accessibility 架构文档指出，未来的模型里 provider “must not be required, or even able, to tell clients which provider has the global focus; clients will get that information from the windowing system (e.g. Wayland compositor)”——说明 AT-SPI 的全局 focus/绝对坐标模型正在被重新设计，长期不要过度依赖其绝对屏幕坐标。

**结论**：对“抓屏上文字”这个目标，AT-SPI2 在 GTK/Qt 原生应用上是比 OCR 更精确、更省 CPU 的替代，且 unprivileged。但对 Chromium/Electron 需要额外 flag/信号，且新版 Chrome 的可靠性存疑。务实的架构是 AT-SPI2 优先、OCR（对 capture 的帧）兜底。

## 直接回答：2026 年 KWin Wayland 上，unprivileged daemon 能否做 event-driven capture？

逐触发结论（KWin / Plasma 6）：

| 触发 | 可用机制 | 授权要求 | 可靠性 / 备注 |
|---|---|---|---|
| **app switch** | 常驻 KWin script + D-Bus `windowActivated` 信号（或 kdotool/kwst 轮询） | 无需特殊权限（KWin scripting 对本地 session 开放） | **可靠、event-driven**。`ext-foreign-toplevel-list-v1` 在 KWin 不可用，不要依赖。轮询会打爆 D-Bus，用信号 |
| **idle / pause** | `ext-idle-notify-v1`（v2 `get_input_idle_notification`） | 无（普通 Wayland global） | **最佳**：标准、unprivileged、event-driven。KWin 自 5.27 支持 |
| **click** | evdev/libinput 读 `/dev/input`；或 portal InputCapture | 前者需 `input` group；后者需用户授权 | **无 window context**（evdev）或**语义是转发非监听**（InputCapture）。全局 click 监听在无授权下不可能 |
| **scroll** | 无直接手段；用 PipeWire `SPA_META_VideoDamage` 或 image diff 近似 “content changed” | 需已建立 ScreenCast（portal 授权） | **无法直接检测 scroll 动作**；只能检测“内容变了” |
| **capture 本身** | xdg-desktop-portal ScreenCast + PipeWire + restore token | 首次用户弹窗授权，之后 `persist_mode=2` restore token | 唯一 sanctioned 路径。restore token single-use，每次换新 |
| **on-screen text** | AT-SPI2 over D-Bus（OCR 兜底） | 无（Chromium/Electron 需额外 flag/信号） | GTK/Qt 良好；Chromium 新版存疑 |

**综合判断**：一个真正 unprivileged（不加 `input` group、不装 setuid、不用 KWin 私有 EIS）的 daemon 在 KWin Plasma 6 上，能够构建一个**大部分 event-driven** 的 recall 型工具，但**不是全部触发都 event-driven**：

- 可以 event-driven 的：app-switch（KWin script 信号）、idle/resume（`ext-idle-notify-v1`）、以及“内容变化”（PipeWire damage metadata）。
- 必须降级或放弃的：click 和 scroll 的全局输入级触发。最佳近似是把“capture 时机”从“检测到 input”改成“检测到 active window 变化 OR 内容 damage 变化 OR 周期兜底，且在 idle 时暂停”。
- capture 通道必须走 portal ScreenCast，接受一次授权 + restore token。这是与 X11 时代最大的体验差异：无法零授权后台静默截屏。

如果你愿意放宽“unprivileged”约束，把用户加入 `input` group，则可以额外拿到全局 click/scroll 的**发生事实**（但仍无 window context，需要与 KWin script 的 active-window 结果时间对齐来推断）。这是一个明确的 trade-off：多一点权限，换 click/scroll 触发。

## 反方向：Wayland 做得更好、X11 做不到的

- **frame 原子性 / 无 tearing**：每次 commit 原子呈现，默认无撕裂；需要撕裂（如游戏低延迟）时才用 `tearing-control-v1` 显式开启。
- **per-output scaling / mixed DPI / fractional scaling**：Wayland 原生支持每个 output 独立 scale、混合 DPI，`fractional-scale-v1`（staging）提供分数缩放。X11 的全局单一 DPI 模型在多屏异构 DPI 下很糟。
- **HDR 与 color management**：KWin 自 Plasma 6.0 支持 `frog-color-management-v1`，自 Plasma 6.2 支持 `xx-color-management-v4`；上游 `color-management-v1` 已进 staging。KWin 还支持 per-screen ICC profile。Plasma 6.2 起 HDR 的 nonlinear blending 几乎无性能损失。X11 没有等价的现代 color management 协议路径。（注意：这些是快速演进领域，具体 TF/primaries 支持在版本间有差异，如 gamescope #2221 显示 6.7 的 sRGB TF 行为变化。）
- **isolation / security**：per-client isolation 从根本上杜绝了 X11 那种“任意 client 截屏/keylog/注入”。X11 的对照事实：`xinput test-xi2 --root` 即可全局键盘监听，`xwd` 即可截任意窗口——这正是 Wayland 刻意移除这些能力的直接动机。
- **更简单的 driver / 架构模型**：compositor 直接用 KMS/evdev/GBM，去掉 X server 这个中间人和它的 input driver 层。
- **XWayland 作为兼容层及其局限**：X11 应用通过 XWayland 运行。局限：XWayland 内部**仍是 X11 安全模型**——同一 XWayland 下的 X clients 之间**仍能互相 spy**（keylog/截屏），只有 native Wayland clients 之间是隔离的。此外 XTEST 在 Wayland 下默认不接任何东西，Xwayland 自 23.2.0 起把 XTEST 转译成 libei 事件（走 portal），所以老 X client 的 `xdotool key` 在 GNOME 上会弹权限窗（Mutter 46.2 起的行为变化）。

## 历史 / 设计动机，以及对 portal 模型的批评

Wayland 设计者**刻意**移除了 X11 的这些全局能力，因为 X11 完全没有 client isolation：任何 client 都能 trivially 做 keylogger（XRecord/XGrabKey/XInput2 raw）、截任意 window（`XGetImage`/`xwd`）、注入 input（XTEST）、枚举并查询任意 window。有观点（dec05eba）指出 X11 其实可以用 XACE/SELinux 或改几行 xserver 代码来限制全局 keylogging，即“X11 不安全”并非不可修复；但主流判断是这些能力是 X11 的**默认且被广泛依赖**的行为，无法在不破坏生态的前提下收紧，所以 Wayland 选择从协议层重新开始。

**sanctioned 的替代是 portal 模型**：screen capture 走 ScreenCast portal（PipeWire + 权限 + restore token），input 注入/捕获走 RemoteDesktop / InputCapture portal（libei），全局快捷键走 GlobalShortcuts portal。好处是：能力被显式授权、可撤销、可沙箱化（Flatpak 友好）、且用户能看到谁在发 input。

**对 portal 模型的批评与现实张力**：
- 合法用例长期受损：screen sharing、自动化（autokey/AutoHotkey 类）、screen reader、color picker、密码管理器 auto-type、截图工具等，在 portal 补齐前普遍不可用或体验糟糕。截图工具作者抱怨 portal 截图流程“in it's current form, unusable if you desire a smooth user experience”。
- 协议实现碎片化：`ext-image-copy-capture-v1`、`ext-foreign-toplevel-list-v1` 等标准协议在 wlroots 已实现而 KWin 未实现，导致“标准路径”名存实亡，第三方适配层被迫长期存在。
- KDE 对某些能力的态度是“不做通用协议，改做窄用途 API”（如 Xaver Hugl 主张用专门的 password manager API 而非 foreign-toplevel + libei），这对通用自动化/recall 工具意味着**标准路径被否决**，而非“暂时没人做”。
- KWin 的 `X-KDE-Wayland-Interfaces` / `X-KDE-DBUS-Restricted-Interfaces` 白名单机制被开发者自己承认“不提供真正的安全，只是防止意外使用”（fvogt / meven 的原话），一个恶意程序可以伪造 `$XDG_DATA_DIRS` 绕过——所以它更像是抬高门槛而非真正的授权边界。

## Caveats（未验证 / 需实测项）

- KDE bug 483227 的“RESOLVED NOT A BUG”状态、resolution 日期、以及 Xaver Hugl 评论原文，均来自二手转述，**未能在 bugs.kde.org 直接核实**。
- `X-KDE-Wayland-Interfaces` 白名单机制引入版本（推断为 Plasma 5.17，2019 年 7 月合入）、`X-KDE-DBUS-Restricted-Interfaces`（推断 Plasma 5.20），以及 KWin 6 当前的受限 global 列表与是否要求 binary 在系统路径，均为**推断/未在 KWin 6 源码直接确认**。
- Chromium/Electron 通过 `org.a11y.Status` 置位“retroactively”建 AT-SPI 树的技巧，在 Chrome 151 上据报可能失效，**需在你自己的软件版本上实测**。
- KWin 完全不 advertise capture/foreign-toplevel global 的结论基于 2026 年 KWin 6.6.6 / 6.7.5 的 `wayland-info` 实机报告；不同发行版/版本可能有差异，**建议自行跑 `wayland-info` 确认你机器上的 global 列表**。
- KWin screencast 发送 `SPA_META_VideoDamage` 的行为基于对 `screencaststream.cpp` 的源码引用（KDE bug 525308）；具体 damage 粒度与在你的 driver/硬件上的实际表现**需实测**。
- HDR/color management 的协议支持在 Plasma 6.x 各小版本间快速变化，具体 transfer function / primaries 行为以你所用版本为准。
