# Applyrx

<p align="center">
  <a href="./README.md">English</a> |
  <a href="./README.zh-CN.md">简体中文</a>
</p>

Applyrx 是一个 macOS Apple Music 实时歌词应用，体验参考 LyricsX。它会读取当前 Apple Music 播放状态，从 Apple Music 本地 TTML 歌词缓存中严格匹配当前歌曲，并把同步歌词显示成桌面悬浮歌词、菜单栏歌词、完整歌词窗口，同时提供方便二次开发的 CLI JSON 接口。

Applyrx 的核心原则是“宁可不显示，也不串歌”：如果当前歌曲不能通过 Apple song id、元数据和时长明显匹配，Applyrx 会显示明确错误，而不是猜一首可能错误的歌词。

## 功能

- 从 Apple Music 本地 TTML 缓存读取同步歌词。
- 桌面悬浮歌词，可拖动位置。
- 菜单栏歌词，可显示或关闭。
- 完整歌词窗口，当前行高亮。
- 桌面歌词大小滑杆：左边变小，右边变大。
- 桌面歌词背景可显示或隐藏。
- CLI 接口，适合脚本和二次开发。
- 本地 JSON 配置文件：`~/.applyrx/config.json`。

## 环境要求

- macOS
- Apple Music.app
- 推荐 Python 3.11+
- 当前歌曲需要至少在 Apple Music 中打开过一次歌词面板

Applyrx 通过 AppleScript 读取 Apple Music 当前播放状态。首次运行时，macOS 可能会请求 Automation 权限，允许 Applyrx 控制 Music.app。

## 快速开始

```bash
git clone https://github.com/rakel/applyrx.git
cd applyrx
./scripts/bootstrap.sh
./run_lyricsx_ui.sh
```

打开 Apple Music，播放歌曲，并打开一次 Apple Music 自带歌词面板。Applyrx 只使用 Apple Music TTML 缓存，不会默认使用 LRClib、网易云、QQ 音乐等第三方歌词源。

## 构建 App

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

当前 App 是本地开发版 bundle，适合在本项目目录中运行，可能依赖本地 `venv` 和源码 helper 文件。真正可独立分发的安装包会作为后续打包阶段处理。

## CLI

输出当前完整状态 JSON：

```bash
./venv/bin/python applyrx_cli.py state
```

只输出当前这一句歌词：

```bash
./venv/bin/python applyrx_cli.py current-line
```

输出当前歌曲 LRC 歌词：

```bash
./venv/bin/python applyrx_cli.py lyrics --format lrc
```

实时监听歌词变化，输出 JSON Lines：

```bash
./venv/bin/python applyrx_cli.py watch
```

所有 CLI 命令都支持 `--offset`。正数表示提前显示歌词：

```bash
./venv/bin/python applyrx_cli.py --offset 1.2 watch
```

## 配置

Applyrx 的配置文件在：

```text
~/.applyrx/config.json
```

常用字段：

- `offset`：歌词时间偏移秒数。
- `desktop_visible`：显示或隐藏桌面悬浮歌词。
- `menubar_lyrics_visible`：是否在菜单栏显示当前歌词。
- `font_size_current`：桌面歌词主行字号。
- `panel_width` 和 `panel_height`：桌面歌词背景尺寸。
- `background_visible`：显示或隐藏桌面歌词背景。

大部分配置都可以直接从菜单栏应用里调整。

## 注意事项

- Applyrx 当前只支持 Apple Music。
- 默认不使用 LRClib、网易云、QQ 音乐或其他第三方歌词 API。
- 如果没有匹配到 TTML 缓存，请先在 Apple Music 中打开当前歌曲的歌词面板。
- 如果仍然无法匹配，Applyrx 不会猜歌词，因为避免显示错误歌词是核心规则。

## 许可证

MIT
