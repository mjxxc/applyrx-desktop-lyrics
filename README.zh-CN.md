# Applyrx

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <strong>macOS 上帧级对齐的 Apple Music 原生歌词。</strong><br>
  桌面悬浮歌词 · 菜单栏歌词 · 完整歌词窗口 · CLI
</p>

---

Applyrx 是一款 macOS 上的 Apple Music 实时歌词应用。和那些从 LRClib、网易云、
QQ 音乐等第三方抓词的工具不同，Applyrx 直接从本地 `NSURLCache` 里读取 Apple
Music 自己发出去的已签名 TTML 歌词响应。你在 Applyrx 里看到的每一个字，和
Apple Music 应用内歌词面板**字节级一致**——包括官方翻译、罗马音，以及 Apple
提供了逐字时间戳时的逐字歌词。

## 为什么选 Applyrx

| | Applyrx | LyricsX / LyricFever | sptlrx |
|---|---|---|---|
| 歌词来源 | Apple Music 官方 TTML | 第三方 LRC 歌词站 | 第三方 LRC 歌词站 |
| 时间精度 | 毫秒级（TTML） | 行级，尽力而为 | 行级，尽力而为 |
| 官方翻译 | 有，只要 Apple 提供 | 无 | 无 |
| 串歌保护 | adam_id + 元数据严格匹配 | — | — |
| 首次播放后可离线 | 可以 | 取决于第三方站 | 不能 |
| 需要 API Key | 不需要 | 不需要 | 不需要 |

Applyrx 从不猜歌词。如果当前播放的歌曲不能通过 Apple 专辑 id、歌名、艺术家和
时长明确匹配到某条缓存 TTML，Applyrx 会显示明确的错误提示，而不是展示另一首
歌的歌词。

## 工作原理

```
Apple Music.app ──播放──▶ 向 /ttmlLyrics 发已签名请求 ──▶ NSURLCache (Cache.db)
                                                                    │
                                                                    ▼
                                        ┌───────────────────────────────────┐
                                        │ Applyrx                           │
                                        │                                   │
    AppleScript（读播放状态） ─────────▶│  1. 读取当前曲目元数据             │
                                        │  2. iTunes Lookup 解析 adam_id    │
                                        │  3. 在缓存中定位匹配的 TTML 条目  │
                                        │  4. 用相同的头复放请求（curl）    │
                                        │  5. 解析 TTML → 带时间戳的歌词行  │
                                        │  6. 渲染 UI / 发出 CLI 事件       │
                                        └───────────────────────────────────┘
                                                         │
                         ┌───────────────────────────────┼───────────────────────────────┐
                         ▼                               ▼                               ▼
                桌面悬浮歌词                        菜单栏歌词                          CLI
```

Applyrx 不依赖任何 private entitlement，不借助 Accessibility hack，也不尝试
重新实现 Apple 的请求签名。它只是复用 Music.app 已经签好并缓存下来的请求，用
相同的 URL 和请求头再问一次服务器。

## 功能

- **Apple Music 原生歌词**：直接读取本地 TTML 缓存，和应用内歌词面板同源。
- **桌面悬浮歌词**：位置可拖动，字号可调，背景可隐藏。
- **菜单栏歌词**：显示当前句歌词，可一键关闭。
- **完整歌词窗口**：当前行高亮，平滑滚动。
- **CLI**（`applyrx_cli.py`）：提供 `state`、`current-line`、`lyrics`、`watch`
  四个子命令，输出对脚本友好的 JSON。
- **严格匹配策略**：要求 catalog id + 歌名 + 艺术家 + 时长四项一致。繁简中文
  自动归一化，时长唯一命中时作为兜底条件。
- **多区查询**（CN/TW/US），确保 Apple Music 中国区的 catalog id 能被解析。
- **本地 JSON 配置**：`~/.applyrx/config.json`，大部分字段可在菜单栏直接调整。

## 环境要求

- macOS，安装了 Apple Music.app
- Python 3.11 或更新版本
- 当前歌曲至少在 Apple Music 里打开过一次歌词面板，这样 TTML 才会进缓存
- macOS 首次运行时会请求"允许控制 Music.app"的自动化权限

## 快速开始

```bash
git clone https://github.com/rakel/applyrx.git
cd applyrx
./scripts/bootstrap.sh
./run_lyricsx_ui.sh
```

在 Apple Music 里播放一首歌、打开一次自带歌词面板，Applyrx 就会从 TTML 缓存
中读取并开始显示同步歌词。

## 打包成 App

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

当前的 `.app` bundle 属于本地开发版，运行时会依赖项目目录下的 `venv` 和源码
辅助文件。完整独立、已公证的发行包见
[`RELEASE_CHECKLIST.md`](./RELEASE_CHECKLIST.md)。

## CLI

Applyrx 自带一个脚本友好的 CLI，复用和 GUI 完全相同的匹配逻辑。

```bash
# 输出完整当前状态（JSON）
./venv/bin/python applyrx_cli.py state

# 只输出当前这一句歌词
./venv/bin/python applyrx_cli.py current-line

# 把当前歌曲的歌词导出成 LRC
./venv/bin/python applyrx_cli.py lyrics --format lrc

# 持续监听歌词变化，输出 JSON Lines
./venv/bin/python applyrx_cli.py watch

# 全局时间偏移（正数表示提前）
./venv/bin/python applyrx_cli.py --offset 1.2 watch
```

`watch` 子命令尤其适合接到 Raycast、Alfred、BTT、tmux 状态栏、Stream Deck
这些工具里。

## 配置

Applyrx 的配置文件位于：

```
~/.applyrx/config.json
```

常用字段：

| 字段 | 说明 |
|---|---|
| `offset` | 歌词时间偏移秒数 |
| `desktop_visible` | 显示/隐藏桌面悬浮歌词 |
| `menubar_lyrics_visible` | 菜单栏是否显示当前歌词 |
| `font_size_current` | 桌面主歌词行字号 |
| `panel_width` / `panel_height` | 桌面歌词面板宽高 |
| `background_visible` | 显示/隐藏桌面歌词背景 |

大部分字段都能在菜单栏面板里直接修改，不需要手动改 JSON。

## 项目结构

```
applyrx/
├── apple_music_ttml.py      # NSURLCache 读取、请求复放、TTML 解析
├── main.py                  # 严格匹配主流程（adam_id + 元数据）
├── lyricsx_style_app.py     # 桌面悬浮歌词 + 菜单栏 GUI
├── applyrx_cli.py           # 命令行工具
├── applyrx_state.py         # 共享运行时状态
├── lyrics_state.py          # 歌词进度 / 当前行跟踪
├── scripts/                 # bootstrap.sh、build_app.sh
├── run_lyricsx_ui.sh        # GUI 启动入口
└── dist/Applyrx.app         # 本地 App bundle
```

## 设计原则

1. **绝不显示错的歌词**。明确报错永远优于自信地串歌。
2. **以 Apple 为唯一真实源**。默认路径不接入任何第三方歌词站。
3. **复用而不是重签**。Applyrx 从不尝试重新实现 Apple 的请求签名，只复放
   Music.app 已经签好的请求。
4. **天然脚本友好**。GUI 显示的每一项状态，CLI 都能以 JSON 形式输出。
5. **首次播放后可离线**。一首歌的 TTML 进缓存后，之后完全不需要网络。

## 常见问题

**Applyrx 需要我的 Apple ID 或 API Key 吗？**
不需要。它只读本地 Apple Music 缓存，并复放已经签过名的请求。

**Applyrx 会修改 Apple Music 的任何数据吗？**
不会。它只在一个临时目录里开一份只读的缓存副本，不写 Music.app 的任何状态。

**为什么某首歌提示"找不到匹配的歌词"？**
请先在 Apple Music 里打开那首歌的歌词面板一次，让 TTML 进入缓存，然后切回
Applyrx 即可。

**能不能用在 Spotify / YouTube Music / 网易云音乐？**
设计上不行。Applyrx 有意只支持 Apple Music，只用 Apple 的 TTML。

**跨区怎么保证匹配可靠？**
Applyrx 通过 `itunes.apple.com/lookup` 依次在 CN、TW、US 三个区解析
Apple 的 catalog id，然后把歌名、艺术家、时长和播放器状态对比。繁体中文和
简体中文的歌名会被先归一化再比对。

## 路线图

- 独立、可公证、可分发的 `.app` bundle
- Homebrew Cask 发布
- Apple 提供时渲染逐字（卡拉 OK 风格）歌词
- 用原生 Swift 重写菜单栏宿主以降低闲时 CPU

## 贡献

欢迎提 Issue 和 PR。请务必保持"严格匹配"这一核心约束：任何会导致 Applyrx
显示非当前播放歌曲歌词的改动都不会被合并。

## 许可证

[MIT](./LICENSE)
