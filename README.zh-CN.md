# Applyrx

**Applyrx v0.2.0 —— 原生 Liquid Glass 歌词体验**

Applyrx 是一款面向 Apple Music 的非官方 macOS 桌面歌词工具。它从 Apple Music
本地缓存读取带时间戳的歌词，并通过可点击穿透的 Liquid Glass 浮窗及菜单栏显示。

本项目基于并受 MIT 许可的
[Applyrx](https://github.com/rakei076/applyrx) 项目启发，是独立的非官方第三方
工具，与 Apple 无关联，也未获得 Apple 的认可或赞助。

## 演示

![Applyrx 桌面歌词演示](./assets/applyrx-demo.gif)

[下载无声 MP4 演示视频](./assets/applyrx-demo.mp4)。

## 功能

- macOS 26 原生 **Liquid Glass** 歌词浮窗（`NSGlassEffectView`，clear 样式），
  紧凑浮窗形态。
- **连续向上滚动**：单一歌词轨道 + 裁切视口，当前句与下一句一起上移；当前句的
  样式与滚动同步过渡，滚动结束时不会再跳变。
- **显示行数模式**：

  | 模式 | 内容 |
  | --- | --- |
  | 1 行 | 当前句 |
  | 2 行（默认） | 当前句 + 下一句 |
  | 3 行 | 上一句 + 当前句 + 下一句 |

- 前奏身份卡：首句歌词开始前显示**歌曲名 + 歌手**。
- 歌词行之间的间隙里保持显示当前句，直到下一句的起始时间到达。
- Apple Music 提供逐字时间时显示 **word-level highlighting**；没有逐字时间时
  自动回退到行级歌词。
- 跟随 Apple Music 播放状态：暂停/继续、seek、大幅 seek 直接定位（不逐句滚动）、
  切歌直接切换。
- 默认点击穿透的浮窗，以及临时拖动模式；拖动模式会自动恢复点击穿透。
- 全局快捷键：`Control-Option-Command-L` 显示/隐藏歌词浮窗；
  `Control-Option-Command-M` 临时启用拖动模式。
- 原生设置窗口：当前句字号、上下文行字号、上下文透明度、显示行数（1/2/3）、
  玻璃强度、记住浮窗位置、启动时恢复位置，以及"恢复默认"。
- 严格匹配当前歌曲；不根据不完整或冲突的元数据猜测；当前歌曲首次未匹配时会重试
  本地缓存查询。

## 环境要求

- **macOS 26.0 或更新版本。** 歌词浮窗是原生 SwiftUI 面板，使用
  `NSGlassEffectView`，需要 macOS 26。
- **Apple Silicon Mac。** 原生歌词面板仅以 `arm64` 构建，当前版本不支持 Intel Mac。
- 安装 Apple Music.app，且本地缓存中已有该歌曲的歌词。必要时可在 Music 中打开
  内建歌词面板以产生本地缓存。
- macOS 自动化权限，以便读取 Music.app 当前曲目信息。
- Python 3.10 或更新版本，但**仅用于从源码构建**。v0.2.0 Release 使用 Python 3.12
  构建。

## 安装

从 GitHub Releases 页面下载发布资源 `Applyrx-v0.2.0-macOS.zip`，解压后把
`Applyrx.app` 拖到"应用程序"文件夹（也可以就地运行）。

### 首次打开

当前 macOS 发布包**只做了 ad-hoc 签名**：**没有 Apple Developer ID 签名**，也
**没有经过公证**，因此 macOS Gatekeeper 可能提示无法验证开发者。

打开方式：

1. 在访达中右键（或按住 Control 点击）`Applyrx.app`。
2. 选择"打开"。
3. 在随后弹出的对话框中再次确认"打开"。

也可以在首次被拦截后，前往**系统设置 → 隐私与安全性**，选择"仍要打开"放行一次。
由于没有公证，应用不携带 Apple 验证过的开发者身份；请仅在你愿意运行这个由本仓库
提供的未签名构建时安装它。

## 构建

从源码构建 macOS App：

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

构建流程会为 `arm64-apple-macos26.0` 编译 `native/ApplyrxLyricsPanel.swift`（使用
macOS 26.5 Command Line Tools SDK，可用环境变量 `MACOS_SDK` 覆盖），再用 py2app
打包为 `dist/Applyrx.app`。脚本会使用本机已安装的兼容 Python，不会修改系统
Python；必要时可通过 `APPLYRX_PYTHON` 指定解释器。

生成发布压缩包及其校验和：

```bash
./scripts/release_macos.sh
# → Applyrx-v0.2.0-macOS.zip
# → Applyrx-v0.2.0-macOS.zip.sha256
```

`scripts/release_macos.sh` 会在打包前校验版本信息、最低系统版本、架构，并确认
bundle 内不含构建机路径与用户数据。它**不会**执行 `git commit`、`git tag`、
`git push` 或任何 GitHub Release 操作。

## 开发与测试

在仓库根目录运行 Python 测试：

```bash
./venv/bin/python -m unittest \
  tests.test_build_environment \
  tests.test_lyrics_panel \
  tests.test_lyrics_provider \
  tests.test_lyrics_sync \
  tests.test_music_cache_diagnostic \
  tests.test_no_fixed_home_path \
  tests.test_word_timing_diagnostic
# 预期：Ran 120 tests — OK
```

`tests/` 中没有 `__init__.py`，因此 `python -m unittest discover` 不可用；请使用上面
的显式模块列表（或单个模块，例如 `./venv/bin/python -m unittest tests.test_lyrics_sync`）。

运行原生面板自检：

```bash
./scripts/build_app.sh
./build/ApplyrxLyricsPanel --self-test
# 预期：53 项自检全部为 true
```

提交前检查空白字符问题：

```bash
git diff --check
```

## 使用

1. 启动 Applyrx，并允许 macOS 自动化权限请求读取 Music.app。
2. 在 Apple Music 中播放歌曲。
3. 如果暂时没有歌词，可在 Music 中打开该歌曲的内建歌词面板，并等待本地缓存更新。
   Applyrx 会对当前歌曲重试本地查询。
4. 使用菜单栏控制面板显示状态；选择"Settings…"可实时调整歌词和窗口外观。

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
                       SwiftUI Liquid Glass 浮窗 + 设置窗口
                                           │
                     单一歌词轨道 + 裁切视口 + 整体位移
```

浮窗以 Music 报告的播放位置作为同步参考。歌词来自本地缓存中带有 Apple Music
`syllable-lyrics` 扩展的歌曲目录响应；Applyrx 不会向第三方歌词服务请求歌曲识别
或歌词内容。

## Apple Music 数据来源

- Applyrx 以**只读**方式访问 Apple Music 本地 `Cache.db` 及关联的 `fsCachedData`
  响应内容，并把缓存中的 `syllable-lyrics` TTML 解析为定时歌词行和逐字时间。
- Applyrx 从 Music.app 读取当前曲目元数据和播放位置，不发送播放控制命令。
- Applyrx **不**读取 Apple ID 密码、cookie 或认证 token。
- **没有云端歌词 API**：歌词查询完全在本地完成，不会上传曲目信息或歌词内容。
- 外观偏好和可选浮窗位置保存在 macOS 用户默认值域 `com.applyrx.desktoplyrics`。
  旧版应用偏好仍保存在 `~/.applyrx`。两处都不会保存 Apple Music 凭据。

## 限制

- Apple Music 没有向普通第三方开发者公开提供完整定时歌词的通用接口。本项目依赖
  Apple Music 本地缓存格式这一实现细节；Apple Music 或 macOS 更新可能改变格式并
  导致兼容性问题。
- 歌词响应必须已存在于本地缓存中。并非所有歌曲、地区或版本都一定可用。
- 为避免显示错误歌词，当歌曲身份或元数据不足、不完整或冲突时，严格匹配会拒绝显示。
- 逐字高亮和上下文行取决于 Apple Music 提供的时间数据及缓存歌词；没有逐字时间时
  会自动回退到行级歌词。
- **需要 macOS 26.0 或更新版本以及 Apple Silicon Mac。** 不支持其他播放器或操作系统。
- 当前 Release App 只做了 ad-hoc 签名：没有 Developer ID 签名，也没有公证。

## 故障排查

**浮窗显示没有匹配歌词。** 确认 Music.app 正在播放目标歌曲，然后在 Music 中打开该
歌曲的内建歌词面板，使本地缓存有机会更新。Applyrx 会重试；如果严格身份校验无法
确认候选属于当前歌曲，仍会拒绝显示。

**应用无法读取当前歌曲。** 在"系统设置 → 隐私与安全性 → 自动化"中检查并允许
Applyrx 访问 Music.app。更改权限后重启 Applyrx。

**macOS 提示无法验证应用。** 当前版本没有公证。请右键选择"打开"，或在"系统设置 →
隐私与安全性"中放行。

**没有看到歌词面板。** 检查菜单栏中的 Applyrx 控件，并按
`Control-Option-Command-L` 切换桌面歌词。确认 macOS 允许应用运行。

**无法移动浮窗。** 按 `Control-Option-Command-M` 进入临时拖动模式；拖动时限结束后
会自动恢复点击穿透。

## 发布状态

- 发布资源：`Applyrx-v0.2.0-macOS.zip` 与 `Applyrx-v0.2.0-macOS.zip.sha256`。
- 签名：ad-hoc（`codesign -s -`），无 Developer ID，`TeamIdentifier` 未设置。
- 公证：未公证。
- 分发：仅 GitHub Releases，没有 App Store 版本。

## 路线图

- 改进发行包分发、Developer ID 签名和公证。
- 在保持严格匹配的同时改进缓存兼容性诊断。

## 贡献

欢迎提交 Issue 和 Pull Request。请勿在问题报告中附上 Apple Music 缓存文件、个人配置、
凭据或包含隐私数据的日志。所有改动都应保持缓存只读、本地歌词查询和严格曲目匹配。

## 许可证

Applyrx 使用 MIT License 发布。原始版权和许可文本见 [LICENSE](./LICENSE)。

## 免责声明

Applyrx 是非官方第三方项目。Apple Music、Apple 和 macOS 是 Apple Inc. 的商标。本
项目与 Apple 无关联，也未获 Apple 认可。随着 Apple 产品演进，本项目不保证持续兼容。
