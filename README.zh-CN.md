# Applyrx

Applyrx 是一款面向 Apple Music 的非官方 macOS 桌面歌词工具。它从 Apple
Music 本地缓存读取带时间戳的歌词，并通过同步桌面浮窗及菜单栏显示。

本项目基于并受 MIT 许可的
[Applyrx](https://github.com/rakei076/applyrx) 项目启发，是独立的非官方第三方
工具，与 Apple 无关联，也未获得 Apple 的认可或赞助。

## 演示

![Applyrx 桌面歌词演示](./assets/applyrx-demo.gif)

[下载无声 MP4 演示视频](./assets/applyrx-demo.mp4)。

## 功能

- 在桌面浮窗显示与 Apple Music 同步的歌词。
- 菜单栏集成和原生 SwiftUI 歌词面板。
- 从 Apple Music 本地 `Cache.db` 和 `fsCachedData` 读取歌曲目录响应及
  `syllable-lyrics` TTML。
- 将 TTML 解析为带时间戳的歌词行；Apple 提供逐字时间时会保留该数据。当前面板
  显示同步歌词行，不提供卡拉 OK 式逐字高亮。
- 严格匹配当前歌曲；不根据不完整或冲突的元数据猜测。
- 当前歌曲首次未匹配时，会重试本地缓存查询。
- 跟随 Music 播放位置，支持暂停、继续和 seek 定位。
- 默认点击穿透的浮窗，以及临时拖动模式；拖动模式会自动恢复点击穿透。
- 全局快捷键：`Control-Option-Command-L` 显示/隐藏桌面歌词；
  `Control-Option-Command-M` 临时启用拖动模式。

## 环境要求

- 安装 Apple Music.app 的 macOS。
- 开发和构建需要 Python 3.10 或更新版本；v0.1.0 Release 使用 Python 3.12
  构建。
- Apple Music 本地缓存中必须已有该歌曲的歌词。必要时可在 Music 中打开内建歌词
  面板以产生本地缓存。
- macOS 自动化权限，以便读取 Music.app 当前曲目信息。

## 安装

克隆仓库，并在项目专用虚拟环境中安装依赖：

```bash
git clone https://github.com/<owner>/applyrx-desktop-lyrics.git
cd applyrx-desktop-lyrics
./scripts/bootstrap.sh
```

启动开发版：

```bash
./run_applyrx.sh
```

应用会显示菜单栏图标和桌面歌词浮窗。播放歌曲后，应用读取当前曲目元数据，并且
只在 Apple Music 本地缓存中查找严格匹配的歌词响应。

## 构建

构建 macOS App：

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

构建流程会编译 `native/ApplyrxLyricsPanel.swift`，并将程序打包到
`dist/Applyrx.app`。脚本会使用本机已安装的兼容 Python，不会修改系统 Python。
必要时可通过 `APPLYRX_PYTHON` 指定解释器。

## 使用

1. 启动 Applyrx，并允许 macOS 自动化权限请求读取 Music.app。
2. 在 Apple Music 中播放歌曲。
3. 如果暂时没有歌词，可在 Music 中打开该歌曲的内建歌词面板，并等待本地缓存更新。
   Applyrx 会对当前歌曲重试本地查询。
4. 使用菜单栏控制面板显示状态及外观。

如果歌曲歌词尚未进入本地缓存、缓存的是不同版本、元数据不完整/冲突，或严格匹配
无法确认歌词归属，歌词可能不会显示。

## 快捷键

| 快捷键 | 操作 |
| --- | --- |
| `Control-Option-Command-L` | 显示或隐藏桌面歌词浮窗 |
| `Control-Option-Command-M` | 临时启用拖动模式；之后自动恢复点击穿透 |

## 架构

```text
Apple Music.app
      │ 只读当前歌曲元数据和播放位置
      ▼
CurrentTrackManager ──▶ AppleMusicCacheProvider
                              │ 只读
                              ▼
                 Cache.db + fsCachedData
                              │
                         歌曲目录缓存响应
                              │ 严格身份及元数据匹配
                              ▼
             syllable-lyrics TTML ──▶ TTMLParser
                                           │ 定时歌词行 / 可用的逐字时间
                                           ▼
                                     LyricsEngine
                                           │
                             Python 面板桥接（stdin 上的 JSON）
                                           ▼
                                SwiftUI 桌面浮窗
```

浮窗以 Music 报告的播放位置作为同步参考。歌词来自本地缓存中带有 Apple Music
`syllable-lyrics` 扩展的歌曲目录响应；Applyrx 不会向第三方歌词服务请求歌曲识别
或歌词内容。

## 限制

- Apple Music 没有向普通第三方开发者公开提供完整定时歌词的通用接口。本项目依赖
  Apple Music 本地缓存格式这一实现细节；Apple Music 或 macOS 更新可能改变格式并
  导致兼容性问题。
- 歌词响应必须已存在于本地缓存中。并非所有歌曲、地区或版本都一定可用。
- 为避免显示错误歌词，当歌曲身份或元数据不足、不完整或冲突时，严格匹配会拒绝显示。
- 若缓存包含逐字时间，解析器会保留；当前面板尚未逐字高亮。
- 本项目仅面向 macOS 和 Apple Music，不支持其他播放器或操作系统。
- 当前 Release App 未经过公证。

## 隐私

- 桌面歌词 Provider 以只读方式访问 Apple Music 的 `Cache.db` 及关联的
  `fsCachedData` 响应内容，不修改 Apple Music 缓存。
- Applyrx 从 Music.app 读取当前曲目元数据和播放位置，不发送播放控制命令。
- Applyrx 不读取 Apple ID 密码或认证凭据。
- 桌面歌词查询不会将曲目信息或歌词内容上传到第三方歌词服务。
- 应用偏好设置保存在 `~/.applyrx`；不会在该目录保存 Apple Music 凭据。

## 故障排查

**浮窗显示没有匹配歌词。** 确认 Music.app 正在播放目标歌曲，然后在 Music 中打开该
歌曲的内建歌词面板，使本地缓存有机会更新。Applyrx 会重试；如果严格身份校验无法
确认候选属于当前歌曲，仍会拒绝显示。

**应用无法读取当前歌曲。** 在“系统设置 → 隐私与安全性 → 自动化”中检查并允许
Applyrx 访问 Music.app。更改权限后重启 Applyrx。

**没有看到歌词面板。** 检查菜单栏中的 Applyrx 控件，并按
`Control-Option-Command-L` 切换桌面歌词。确认 macOS 允许应用运行。

**无法移动浮窗。** 按 `Control-Option-Command-M` 进入临时拖动模式；拖动时限结束后
会自动恢复点击穿透。

## 路线图

- 改进发行包分发、签名和公证。
- 在保持严格匹配的同时改进缓存兼容性诊断。
- 在时间数据可靠时探索逐字视觉高亮。

## 贡献

欢迎提交 Issue 和 Pull Request。请勿在问题报告中附上 Apple Music 缓存文件、个人配置、
凭据或包含隐私数据的日志。所有改动都应保持缓存只读、本地歌词查询和严格曲目匹配。

## 许可证

Applyrx 使用 MIT License 发布。原始版权和许可文本见 [LICENSE](./LICENSE)。

## 免责声明

Applyrx 是非官方第三方项目。Apple Music、Apple 和 macOS 是 Apple Inc. 的商标。本
项目与 Apple 无关联，也未获 Apple 认可。随着 Apple 产品演进，本项目不保证持续兼容。
