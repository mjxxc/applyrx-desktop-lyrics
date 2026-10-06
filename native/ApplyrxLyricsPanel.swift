import AppKit
import Carbon
import SwiftUI

private struct TimedLyricWord: Decodable, Equatable {
    let startTime: Double
    let endTime: Double
    let text: String
}

private enum WordAppearance: Equatable {
    case completed
    case current
    case upcoming
}

private enum LyricsAppearance {
    static let panelWidth: CGFloat = 900
    static let panelHeight: CGFloat = 252
    static let cornerRadius: CGFloat = 22
    static let horizontalPadding: CGFloat = 38
    static let verticalPadding: CGFloat = 22
    static let lineSpacing: CGFloat = 5
    static let groupSpacing: CGFloat = 9
    static let currentFontSize: CGFloat = 30
    static let contextFontSize = currentFontSize * 0.66
    static let contextOpacity = 0.62
    static let completedWordOpacity = 0.88
    static let upcomingWordOpacity = 0.46
    static let currentLineTransitionDuration = 0.20
}

private struct CurrentLyric: Decodable {
    let text: String
    let startTime: Double?
    let endTime: Double?
    let words: [TimedLyricWord]?

    private enum CodingKeys: String, CodingKey {
        case text
        case startTime
        case endTime
        case words
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        text = try container.decode(String.self, forKey: .text)
        startTime = try? container.decode(Double.self, forKey: .startTime)
        endTime = try? container.decode(Double.self, forKey: .endTime)

        guard let decodedWords = try? container.decode(
            [TimedLyricWord].self,
            forKey: .words
        ), let startTime, let endTime,
           startTime.isFinite, endTime.isFinite, endTime > startTime else {
               words = nil
               return
        }
        var previousStart = -Double.infinity
        let timingsAreValid = decodedWords.allSatisfy { word in
               let valid = word.startTime.isFinite
                   && word.endTime.isFinite
                   && word.startTime >= startTime
                   && word.startTime >= previousStart
                   && word.endTime <= endTime
                   && word.endTime > word.startTime
               previousStart = word.startTime
               return valid
        }
        guard timingsAreValid else {
               words = nil
               return
        }
        words = decodedWords.isEmpty ? nil : decodedWords
    }

    func appearances(at position: Double?) -> [WordAppearance]? {
        guard let words, let position, position.isFinite else {
            return nil
        }
        let activeIndex = words.indices
            .filter { words[$0].startTime <= position && position < words[$0].endTime }
            .last
        return words.indices.map { index in
            if index == activeIndex {
                return .current
            }
            return words[index].endTime <= position ? .completed : .upcoming
        }
    }

    func styledText(at position: Double?) -> AttributedString? {
        guard let words, let appearances = appearances(at: position) else {
            return nil
        }
        var result = AttributedString(text)
        result.foregroundColor = .primary.opacity(LyricsAppearance.upcomingWordOpacity)
        result.font = .system(size: LyricsAppearance.currentFontSize, weight: .medium)
        var searchStart = text.startIndex

        for (index, word) in words.enumerated() {
            guard let textRange = text.range(
                of: word.text,
                range: searchStart..<text.endIndex
            ), let attributedRange = Range(textRange, in: result) else {
                return nil
            }
            let color: Color
            let weight: Font.Weight
            switch appearances[index] {
            case .completed:
                color = .primary.opacity(LyricsAppearance.completedWordOpacity)
                weight = .medium
            case .current:
                color = .accentColor
                weight = .bold
            case .upcoming:
                color = .primary.opacity(LyricsAppearance.upcomingWordOpacity)
                weight = .medium
            }
            result[attributedRange].foregroundColor = color
            result[attributedRange].font = .system(
                size: LyricsAppearance.currentFontSize,
                weight: weight
            )
            searchStart = textRange.upperBound
        }
        return result
    }
}

private struct PanelMessage: Decodable {
    var title: String
    var artist: String
    var playbackState: String
    var matchStatus: String
    var playbackPosition: Double?
    var currentLyric: CurrentLyric?
    var previous: String?
    var current: String?
    var next: String?
    var message: String?

    static let empty = PanelMessage(
        title: "",
        artist: "",
        playbackState: "unknown",
        matchStatus: "loading",
        playbackPosition: nil,
        currentLyric: nil,
        previous: nil,
        current: nil,
        next: nil,
        message: nil
    )
}

@MainActor
private final class PanelModel: ObservableObject {
    @Published var message = PanelMessage.empty
    @Published var moveMode = false

    func accept(_ data: Data) {
        guard let message = try? JSONDecoder().decode(PanelMessage.self, from: data) else {
            return
        }
        self.message = message
    }
}

private struct TimedLyricText: View {
    let lyric: CurrentLyric
    let playbackPosition: Double?

    private var renderedText: Text {
        Text(lyric.styledText(at: playbackPosition) ?? AttributedString(lyric.text))
    }

    var body: some View {
        renderedText
            .multilineTextAlignment(.center)
            .lineLimit(nil)
            .lineSpacing(LyricsAppearance.lineSpacing)
            .fixedSize(horizontal: false, vertical: true)
    }
}

private struct LyricsPanelView: View {
    @ObservedObject var model: PanelModel

    private var isMatched: Bool { model.message.matchStatus == "matched" }

    private var statusText: String {
        if model.message.title.isEmpty {
            return "等待 Apple Music 播放"
        }
        let pausedSuffix = model.message.playbackState == "paused" ? " · 已暂停" : ""
        switch model.message.matchStatus {
        case "loading": return "正在匹配本地歌词" + pausedSuffix
        case "notFound": return "暂无匹配歌词" + pausedSuffix
        case "ambiguous", "invalid": return "无法安全匹配歌词" + pausedSuffix
        case "timeout": return "歌词加载超时" + pausedSuffix
        default:
            if model.message.playbackState == "paused" { return "已暂停" }
            if model.message.playbackState == "playing" { return "正在播放" }
            return model.message.message ?? "等待 Apple Music"
        }
    }

    var body: some View {
        VStack(spacing: LyricsAppearance.groupSpacing) {
            if isMatched {
                if let previous = model.message.previous {
                    contextLyric(previous)
                        .transition(.opacity)
                }

                if let current = model.message.current {
                    Group {
                        if let lyric = model.message.currentLyric,
                           lyric.text == current {
                            TimedLyricText(
                                lyric: lyric,
                                playbackPosition: model.message.playbackPosition
                            )
                        } else {
                            Text(current)
                                .multilineTextAlignment(.center)
                                .lineLimit(nil)
                                .lineSpacing(LyricsAppearance.lineSpacing)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .font(.system(size: LyricsAppearance.currentFontSize, weight: .semibold))
                    .foregroundStyle(.primary)
                    .transition(.asymmetric(
                        insertion: .move(edge: .bottom).combined(with: .opacity),
                        removal: .move(edge: .top).combined(with: .opacity)
                    ))
                    .id(current)
                }

                if let next = model.message.next {
                    contextLyric(next)
                        .transition(.opacity)
                }
            } else {
                Text(model.moveMode ? "拖动中" : statusText)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(model.moveMode ? Color.accentColor : Color.secondary)
                    .lineLimit(1)
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, LyricsAppearance.horizontalPadding)
        .padding(.vertical, LyricsAppearance.verticalPadding)
        .background {
            RoundedRectangle(
                cornerRadius: LyricsAppearance.cornerRadius,
                style: .continuous
            )
            .fill(.ultraThinMaterial)
            .overlay {
                RoundedRectangle(
                    cornerRadius: LyricsAppearance.cornerRadius,
                    style: .continuous
                )
                .strokeBorder(.primary.opacity(0.08), lineWidth: 0.75)
            }
            .shadow(color: .black.opacity(0.10), radius: 14, y: 4)
        }
        .frame(
            width: LyricsAppearance.panelWidth,
            height: LyricsAppearance.panelHeight
        )
        .contentShape(Rectangle())
        .allowsHitTesting(false)
        .animation(
            .easeInOut(duration: LyricsAppearance.currentLineTransitionDuration),
            value: model.message.current
        )
    }

    private func contextLyric(_ text: String) -> some View {
        Text(text)
            .font(.system(size: LyricsAppearance.contextFontSize, weight: .regular))
            .foregroundStyle(.secondary.opacity(LyricsAppearance.contextOpacity))
            .multilineTextAlignment(.center)
            .lineLimit(2)
            .lineSpacing(LyricsAppearance.lineSpacing)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity)
    }
}

private final class LyricsPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
private final class AppDelegate: NSObject, NSApplicationDelegate {
    private let model = PanelModel()
    private var panel: LyricsPanel?
    private var inputSource: DispatchSourceRead?
    private var inputBuffer = Data()
    private var hotKeyHandler: EventHandlerRef?
    private var hotKeys: [EventHotKeyRef] = []
    private var moveTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        createPanel()
        let selfTestMode = CommandLine.arguments.contains("--self-test")
        if !selfTestMode {
            installInputReader()
        }
        installHotKeys()
        if selfTestMode {
            runSelfTest()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        inputSource?.cancel()
        moveTimer?.invalidate()
        for hotKey in hotKeys {
            UnregisterEventHotKey(hotKey)
        }
        if let hotKeyHandler {
            RemoveEventHandler(hotKeyHandler)
        }
    }

    private func createPanel() {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let size = NSSize(
            width: LyricsAppearance.panelWidth,
            height: LyricsAppearance.panelHeight
        )
        let frame = NSRect(
            x: screen.midX - size.width / 2,
            y: screen.minY + 54,
            width: size.width,
            height: size.height
        )
        let panel = LyricsPanel(
            contentRect: frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        panel.contentView = NSHostingView(rootView: LyricsPanelView(model: model))
        panel.orderFrontRegardless()
        self.panel = panel
    }

    private func installInputReader() {
        let source = DispatchSource.makeReadSource(fileDescriptor: STDIN_FILENO, queue: .main)
        source.setEventHandler { [weak self] in
            guard let self else { return }
            var bytes = [UInt8](repeating: 0, count: 8192)
            let count = read(STDIN_FILENO, &bytes, bytes.count)
            guard count > 0 else {
                NSApp.terminate(nil)
                return
            }
            inputBuffer.append(contentsOf: bytes.prefix(count))
            while let newline = inputBuffer.firstIndex(of: 0x0A) {
                let line = inputBuffer.prefix(upTo: newline)
                inputBuffer.removeSubrange(...newline)
                model.accept(Data(line))
            }
        }
        source.setCancelHandler {}
        source.resume()
        inputSource = source
    }

    private func installHotKeys() {
        var eventSpec = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: OSType(kEventHotKeyPressed)
        )
        let userData = Unmanaged.passUnretained(self).toOpaque()
        let installStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, event, userData in
                guard let event, let userData else { return OSStatus(eventNotHandledErr) }
                var identifier = EventHotKeyID()
                let status = GetEventParameter(
                    event,
                    EventParamName(kEventParamDirectObject),
                    EventParamType(typeEventHotKeyID),
                    nil,
                    MemoryLayout<EventHotKeyID>.size,
                    nil,
                    &identifier
                )
                guard status == noErr else { return status }
                let delegate = Unmanaged<AppDelegate>.fromOpaque(userData).takeUnretainedValue()
                Task { @MainActor in delegate.handleHotKey(identifier.id) }
                return noErr
            },
            1,
            &eventSpec,
            userData,
            &hotKeyHandler
        )
        guard installStatus == noErr else { return }

        let modifiers = UInt32(cmdKey | optionKey | controlKey)
        register(keyCode: UInt32(kVK_ANSI_L), id: 1, modifiers: modifiers)
        register(keyCode: UInt32(kVK_ANSI_M), id: 2, modifiers: modifiers)
    }

    private func register(keyCode: UInt32, id: UInt32, modifiers: UInt32) {
        let identifier = EventHotKeyID(signature: OSType(0x41505258), id: id)
        var reference: EventHotKeyRef?
        let status = RegisterEventHotKey(
            keyCode,
            modifiers,
            identifier,
            GetApplicationEventTarget(),
            0,
            &reference
        )
        if status == noErr, let reference {
            hotKeys.append(reference)
        }
    }

    private func handleHotKey(_ id: UInt32) {
        switch id {
        case 1:
            guard let panel else { return }
            if panel.isVisible {
                panel.orderOut(nil)
            } else {
                panel.orderFrontRegardless()
            }
        case 2:
            setMoveMode(!model.moveMode)
        default:
            break
        }
    }

    private func setMoveMode(_ enabled: Bool) {
        moveTimer?.invalidate()
        model.moveMode = enabled
        panel?.ignoresMouseEvents = !enabled
        panel?.isMovableByWindowBackground = enabled
        panel?.orderFrontRegardless()
        if enabled {
            moveTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: false) { [weak self] _ in
                Task { @MainActor in self?.setMoveMode(false) }
            }
        }
    }

    private func runSelfTest() {
        handleHotKey(1)
        let hideWorks = panel?.isVisible == false
        handleHotKey(1)
        let showWorks = panel?.isVisible == true
        setMoveMode(true)
        let dragModeWorks = panel?.ignoresMouseEvents == false
            && panel?.isMovableByWindowBackground == true
        setMoveMode(false)
        let restoreClickThroughWorks = panel?.ignoresMouseEvents == true
            && panel?.isMovableByWindowBackground == false
        let wordChecks = timedLyricsSelfTest()
        let result: [String: Bool] = [
            "panelVisible": panel?.isVisible == true,
            "nonActivating": panel?.styleMask.contains(.nonactivatingPanel) == true
                && panel?.canBecomeKey == false,
            "floating": panel?.level == .floating,
            "clickThrough": panel?.ignoresMouseEvents == true,
            "registeredGlobalHotKeys": hotKeys.count == 2,
            "hotKeyVisibilityToggle": hideWorks && showWorks,
            "temporaryDragMode": dragModeWorks && restoreClickThroughWorks,
        ].merging(wordChecks, uniquingKeysWith: { _, new in new })
        if let data = try? JSONSerialization.data(withJSONObject: result),
           let output = String(data: data, encoding: .utf8) {
            FileHandle.standardOutput.write(Data((output + "\n").utf8))
        }
        NSApp.terminate(nil)
    }

    private func timedLyricsSelfTest() -> [String: Bool] {
        let payload = Data(
            #"""
            {
              "title":"Test",
              "artist":"Artist",
              "playbackState":"playing",
              "matchStatus":"matched",
              "playbackPosition":1.5,
              "current":"我在海中",
              "currentLyric":{
                "text":"我在海中",
                "startTime":0,
                "endTime":4,
                "words":[
                  {"startTime":0,"endTime":1,"text":"我"},
                  {"startTime":1,"endTime":2,"text":"在"},
                  {"startTime":2,"endTime":3,"text":"海"},
                  {"startTime":3,"endTime":4,"text":"中"}
                ]
              }
            }
            """#.utf8
        )
        let decoded = try? JSONDecoder().decode(PanelMessage.self, from: payload)
        let words = decoded?.currentLyric
        let atPlayback = words?.appearances(at: decoded?.playbackPosition)
        let afterSeek = words?.appearances(at: 2.5)
        let afterResume = words?.appearances(at: 3.5)
        let pausedAtSamePosition = words?.appearances(at: 1.5)
        let textPreserved = words?.styledText(at: 1.5)
            .map { String($0.characters) == "我在海中" } ?? false

        let noTimingPayload = Data(
            #"{"title":"Test","artist":"Artist","playbackState":"playing","matchStatus":"matched","current":"plain line"}"#.utf8
        )
        let noTiming = try? JSONDecoder().decode(PanelMessage.self, from: noTimingPayload)

        let invalidTimingPayload = Data(
            #"""
            {
              "title":"Test",
              "artist":"Artist",
              "playbackState":"playing",
              "matchStatus":"matched",
              "current":"fallback line",
              "currentLyric":{
                "text":"fallback line",
                "startTime":0,
                "endTime":2,
                "words":[{"startTime":0,"endTime":3,"text":"invalid"}]
              }
            }
            """#.utf8
        )
        let invalidTiming = try? JSONDecoder().decode(
            PanelMessage.self,
            from: invalidTimingPayload
        )
        let mismatchedWordsPayload = Data(
            #"""
            {
              "text":"complete original line",
              "startTime":0,
              "endTime":2,
              "words":[{"startTime":0,"endTime":1,"text":"different"}]
            }
            """#.utf8
        )
        let mismatchedWords = try? JSONDecoder().decode(
            CurrentLyric.self,
            from: mismatchedWordsPayload
        )

        return [
            "contextTextIsSubordinate": LyricsAppearance.contextFontSize
                / LyricsAppearance.currentFontSize >= 0.60
                && LyricsAppearance.contextFontSize
                / LyricsAppearance.currentFontSize <= 0.70,
            "currentLineTransitionIsShort": LyricsAppearance.currentLineTransitionDuration
                >= 0.15 && LyricsAppearance.currentLineTransitionDuration <= 0.25,
            "panelLayoutSupportsWrap": LyricsAppearance.panelHeight >= 240
                && LyricsAppearance.lineSpacing > 0,
            "wordTimingDecode": words?.words?.count == 4,
            "wordTimingPlayback": atPlayback == [.completed, .current, .upcoming, .upcoming],
            "wordTimingSeek": afterSeek == [.completed, .completed, .current, .upcoming],
            "wordTimingPauseResume": pausedAtSamePosition == atPlayback
                && afterResume == [.completed, .completed, .completed, .current],
            "wordTimingPreservesLineText": textPreserved,
            "missingTimingFallback": noTiming?.current == "plain line"
                && noTiming?.currentLyric == nil,
            "invalidTimingFallback": invalidTiming?.current == "fallback line"
                && invalidTiming?.currentLyric?.words == nil,
            "wordTextMismatchFallback": mismatchedWords?.styledText(at: 0.5) == nil
                && mismatchedWords?.text == "complete original line",
            "noLyricsState": PanelMessage.empty.current == nil
                && PanelMessage.empty.currentLyric == nil,
        ]
    }
}

@main
private struct ApplyrxLyricsPanelApp {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
