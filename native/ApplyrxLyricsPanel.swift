import AppKit
import Carbon
import SwiftUI

private struct PanelMessage: Decodable {
    var title: String
    var artist: String
    var playbackState: String
    var matchStatus: String
    var previous: String?
    var current: String?
    var next: String?
    var message: String?

    static let empty = PanelMessage(
        title: "",
        artist: "",
        playbackState: "unknown",
        matchStatus: "loading",
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
        VStack(spacing: 12) {
            HStack(spacing: 8) {
                Text(model.message.title.isEmpty ? "Apple Music" : model.message.title)
                    .font(.system(size: 13, weight: .medium))
                    .lineLimit(1)
                if !model.message.artist.isEmpty {
                    Text("—")
                        .foregroundStyle(.white.opacity(0.36))
                    Text(model.message.artist)
                        .font(.system(size: 13, weight: .regular))
                        .foregroundStyle(.white.opacity(0.70))
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                Text(model.moveMode ? "拖动中" : statusText)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(model.moveMode ? .cyan : .white.opacity(0.55))
                    .lineLimit(1)
            }

            VStack(spacing: 7) {
                if isMatched, let previous = model.message.previous {
                    Text(previous)
                        .font(.system(size: 16, weight: .regular))
                        .foregroundStyle(.white.opacity(0.34))
                        .lineLimit(1)
                        .transition(.opacity)
                }

                if isMatched, let current = model.message.current {
                    Text(current)
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                        .shadow(color: .black.opacity(0.45), radius: 8, y: 1)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                } else {
                    Text(statusText)
                        .font(.system(size: 18, weight: .medium))
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(1)
                        .transition(.opacity)
                }

                if isMatched, let next = model.message.next {
                    Text(next)
                        .font(.system(size: 16, weight: .regular))
                        .foregroundStyle(.white.opacity(0.48))
                        .lineLimit(1)
                        .transition(.opacity)
                }
            }
            .frame(maxWidth: .infinity)
            .animation(.easeInOut(duration: 0.28), value: model.message.current)
        }
        .padding(.horizontal, 30)
        .padding(.vertical, 18)
        .background {
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(.black.opacity(0.54))
                .overlay {
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .strokeBorder(.white.opacity(0.10), lineWidth: 1)
                }
        }
        .frame(width: 900, height: 174)
        .contentShape(Rectangle())
        .allowsHitTesting(false)
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
        let size = NSSize(width: 900, height: 174)
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
        let result: [String: Bool] = [
            "panelVisible": panel?.isVisible == true,
            "nonActivating": panel?.styleMask.contains(.nonactivatingPanel) == true
                && panel?.canBecomeKey == false,
            "floating": panel?.level == .floating,
            "clickThrough": panel?.ignoresMouseEvents == true,
            "registeredGlobalHotKeys": hotKeys.count == 2,
            "hotKeyVisibilityToggle": hideWorks && showWorks,
            "temporaryDragMode": dragModeWorks && restoreClickThroughWorks,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: result),
           let output = String(data: data, encoding: .utf8) {
            FileHandle.standardOutput.write(Data((output + "\n").utf8))
        }
        NSApp.terminate(nil)
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
