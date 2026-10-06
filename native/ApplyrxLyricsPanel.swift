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
    static let minimumPanelHeight: CGFloat = 36
    static let cornerRadius: CGFloat = 18
    static let horizontalPadding: CGFloat = 28
    static let verticalPadding: CGFloat = 6
    static let lineSpacing: CGFloat = 2
    static let groupSpacing: CGFloat = 2
    static let defaultCurrentFontSize = 30.0
    static let defaultContextFontSize = 20.0
    static let defaultContextOpacity = 0.62
    static let defaultDisplayLines = 2
    static let defaultBackgroundOpacity = 1.0
    static let defaultRememberWindowPosition = true
    static let defaultRestoreWindowPosition = true
    static let completedWordOpacity = 0.88
    static let upcomingWordOpacity = 0.46
    static let currentLineTransitionDuration = 0.20

    @MainActor
    static func panelHeight(for settings: LyricsAppearanceSettings) -> CGFloat {
        panelHeight(
            currentLyricSize: settings.currentLyricSize,
            contextLyricSize: settings.contextLyricSize,
            displayLines: settings.displayLines
        )
    }

    static func panelHeight(
        currentLyricSize: Double,
        contextLyricSize: Double,
        displayLines: Int
    ) -> CGFloat {
        let contextCount = CGFloat(max(0, displayLines - 1))
        let lineCount = CGFloat(displayLines)
        let contentHeight = CGFloat(currentLyricSize)
            + contextCount * CGFloat(contextLyricSize)
            + (lineCount - 1) * groupSpacing
            + 2 * verticalPadding
        return max(minimumPanelHeight, contentHeight)
    }
}

@MainActor
private final class LyricsAppearanceSettings: ObservableObject {
    static let suiteName = "com.applyrx.desktoplyrics"
    private static let displayLinesMigrationVersion = 2

    private enum Key {
        static let currentLyricSize = "lyrics.currentSize"
        static let contextLyricSize = "lyrics.contextSize"
        static let contextOpacity = "lyrics.contextOpacity"
        static let displayLines = "lyrics.displayLines"
        static let displayLinesMigrationVersion = "lyrics.displayLinesMigrationVersion"
        static let backgroundOpacity = "window.backgroundOpacity"
        static let rememberWindowPosition = "window.rememberPosition"
        static let restoreWindowPosition = "window.restorePosition"
        static let panelFrame = "window.panelFrame"
    }

    private let defaults: UserDefaults
    var onAppearanceChange: (() -> Void)?
    var onGlassIntensityChange: (() -> Void)?

    @Published private(set) var currentLyricSize: Double
    @Published private(set) var contextLyricSize: Double
    @Published private(set) var contextOpacity: Double
    @Published private(set) var displayLines: Int
    @Published private(set) var backgroundOpacity: Double
    @Published private(set) var rememberWindowPosition: Bool
    @Published private(set) var restoreWindowPosition: Bool

    init(defaults: UserDefaults? = nil) {
        let store = defaults ?? UserDefaults(suiteName: Self.suiteName) ?? .standard
        self.defaults = store
        currentLyricSize = Self.storedDouble(
            store, key: Key.currentLyricSize,
            default: LyricsAppearance.defaultCurrentFontSize, range: 20...48
        )
        contextLyricSize = Self.storedDouble(
            store, key: Key.contextLyricSize,
            default: LyricsAppearance.defaultContextFontSize, range: 12...32
        )
        contextOpacity = Self.storedDouble(
            store, key: Key.contextOpacity,
            default: LyricsAppearance.defaultContextOpacity, range: 0.2...0.9
        )
        let storedLines = store.object(forKey: Key.displayLines) as? Int
        let migrationVersion = store.integer(forKey: Key.displayLinesMigrationVersion)
        let normalizedLines = Self.normalizedDisplayLines(
            storedLines,
            migrationVersion: migrationVersion
        )
        displayLines = normalizedLines
        if storedLines != normalizedLines {
            store.set(normalizedLines, forKey: Key.displayLines)
        }
        store.set(Self.displayLinesMigrationVersion, forKey: Key.displayLinesMigrationVersion)
        backgroundOpacity = Self.storedDouble(
            store, key: Key.backgroundOpacity,
            default: LyricsAppearance.defaultBackgroundOpacity, range: 0...1
        )
        rememberWindowPosition = store.object(forKey: Key.rememberWindowPosition) as? Bool
            ?? LyricsAppearance.defaultRememberWindowPosition
        restoreWindowPosition = store.object(forKey: Key.restoreWindowPosition) as? Bool
            ?? LyricsAppearance.defaultRestoreWindowPosition
    }

    private static func storedDouble(
        _ defaults: UserDefaults,
        key: String,
        default fallback: Double,
        range: ClosedRange<Double>
    ) -> Double {
        guard let number = defaults.object(forKey: key) as? NSNumber else {
            return fallback
        }
        let value = number.doubleValue
        return value.isFinite ? min(range.upperBound, max(range.lowerBound, value)) : fallback
    }

    private static func normalizedDisplayLines(
        _ value: Int?,
        migrationVersion: Int
    ) -> Int {
        guard migrationVersion >= displayLinesMigrationVersion else {
            switch value {
            case .some(1): return 1
            case .some(3): return 2
            case .some(5): return 3
            default: return LyricsAppearance.defaultDisplayLines
            }
        }
        switch value {
        case .some(1): return 1
        case .some(2): return 2
        case .some(3): return 3
        default: return LyricsAppearance.defaultDisplayLines
        }
    }

    func setCurrentLyricSize(_ value: Double) {
        currentLyricSize = min(48, max(20, value.isFinite ? value : LyricsAppearance.defaultCurrentFontSize))
        defaults.set(currentLyricSize, forKey: Key.currentLyricSize)
        onAppearanceChange?()
    }

    func setContextLyricSize(_ value: Double) {
        contextLyricSize = min(32, max(12, value.isFinite ? value : LyricsAppearance.defaultContextFontSize))
        defaults.set(contextLyricSize, forKey: Key.contextLyricSize)
        onAppearanceChange?()
    }

    func setContextOpacity(_ value: Double) {
        contextOpacity = min(0.9, max(0.2, value.isFinite ? value : LyricsAppearance.defaultContextOpacity))
        defaults.set(contextOpacity, forKey: Key.contextOpacity)
    }

    func setDisplayLines(_ value: Int) {
        displayLines = [1, 2, 3].contains(value) ? value : LyricsAppearance.defaultDisplayLines
        defaults.set(displayLines, forKey: Key.displayLines)
        onAppearanceChange?()
    }

    func setBackgroundOpacity(_ value: Double) {
        backgroundOpacity = min(1, max(0, value.isFinite ? value : LyricsAppearance.defaultBackgroundOpacity))
        defaults.set(backgroundOpacity, forKey: Key.backgroundOpacity)
        onGlassIntensityChange?()
    }

    func setRememberWindowPosition(_ value: Bool) {
        rememberWindowPosition = value
        defaults.set(value, forKey: Key.rememberWindowPosition)
    }

    func setRestoreWindowPosition(_ value: Bool) {
        restoreWindowPosition = value
        defaults.set(value, forKey: Key.restoreWindowPosition)
    }

    func restoreDefaults() {
        setCurrentLyricSize(LyricsAppearance.defaultCurrentFontSize)
        setContextLyricSize(LyricsAppearance.defaultContextFontSize)
        setContextOpacity(LyricsAppearance.defaultContextOpacity)
        setDisplayLines(LyricsAppearance.defaultDisplayLines)
        setBackgroundOpacity(LyricsAppearance.defaultBackgroundOpacity)
        setRememberWindowPosition(LyricsAppearance.defaultRememberWindowPosition)
        setRestoreWindowPosition(LyricsAppearance.defaultRestoreWindowPosition)
    }

    func savePanelFrame(_ frame: NSRect) {
        guard rememberWindowPosition,
              frame.origin.x.isFinite, frame.origin.y.isFinite,
              frame.size.width.isFinite, frame.size.height.isFinite else {
            return
        }
        defaults.set(
            [frame.origin.x, frame.origin.y, frame.size.width, frame.size.height],
            forKey: Key.panelFrame
        )
    }

    func hasSavedPanelFrame() -> Bool {
        defaults.object(forKey: Key.panelFrame) != nil
    }

    func restoredPanelFrame(
        size: NSSize,
        screens: [NSRect],
        defaultFrame: NSRect
    ) -> NSRect {
        guard rememberWindowPosition, restoreWindowPosition,
              let stored = defaults.array(forKey: Key.panelFrame) as? [NSNumber],
              stored.count == 4 else {
            return defaultFrame
        }
        let values = stored.map(\.doubleValue)
        guard values.allSatisfy(\.isFinite),
              values[2] > 100, values[3] > 80 else {
            return defaultFrame
        }
        let saved = NSRect(x: values[0], y: values[1], width: size.width, height: size.height)
        guard let screen = screens.max(by: {
            saved.intersection($0).width * saved.intersection($0).height
                < saved.intersection($1).width * saved.intersection($1).height
        }) else {
            return defaultFrame
        }
        let intersection = saved.intersection(screen)
        guard !intersection.isNull, intersection.width >= 100, intersection.height >= 80 else {
            return defaultFrame
        }
        let minX = screen.minX + 8
        let minY = screen.minY + 8
        let maxX = max(minX, screen.maxX - size.width - 8)
        let maxY = max(minY, screen.maxY - size.height - 8)
        let x = min(maxX, max(minX, saved.minX))
        let y = min(maxY, max(minY, saved.minY))
        return NSRect(origin: NSPoint(x: x, y: y), size: size)
    }

    static func defaultsSelfTest() -> [String: Bool] {
        let suiteName = "com.applyrx.desktoplyrics.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let settings = LyricsAppearanceSettings(defaults: defaults)
        let defaultsAreCorrect = settings.currentLyricSize == 30
            && settings.contextLyricSize == 20
            && settings.contextOpacity == 0.62
            && settings.displayLines == 2
            && settings.backgroundOpacity == 1
            && settings.rememberWindowPosition
            && settings.restoreWindowPosition

        settings.setCurrentLyricSize(42)
        settings.setContextLyricSize(28)
        settings.setContextOpacity(0.4)
        settings.setDisplayLines(3)
        settings.setBackgroundOpacity(0.7)
        settings.setRememberWindowPosition(false)
        settings.setRestoreWindowPosition(false)
        let persisted = defaults.double(forKey: Key.currentLyricSize) == 42
            && defaults.double(forKey: Key.contextLyricSize) == 28
            && defaults.double(forKey: Key.contextOpacity) == 0.4
            && defaults.integer(forKey: Key.displayLines) == 3
            && defaults.double(forKey: Key.backgroundOpacity) == 0.7
            && defaults.bool(forKey: Key.rememberWindowPosition) == false
            && defaults.bool(forKey: Key.restoreWindowPosition) == false
        let secondInstance = LyricsAppearanceSettings(defaults: defaults)
        let reloadWorks = secondInstance.currentLyricSize == 42
            && secondInstance.contextLyricSize == 28
            && secondInstance.contextOpacity == 0.4
            && secondInstance.displayLines == 3
            && secondInstance.backgroundOpacity == 0.7
            && !secondInstance.rememberWindowPosition
            && !secondInstance.restoreWindowPosition

        let screen = NSRect(x: 0, y: 0, width: 1440, height: 900)
        let panelSize = NSSize(width: 900, height: 252)
        let defaultFrame = NSRect(x: 270, y: 54, width: 900, height: 252)
        let savedFrame = NSRect(x: 300, y: 240, width: 900, height: 252)
        secondInstance.setRememberWindowPosition(true)
        secondInstance.setRestoreWindowPosition(true)
        secondInstance.savePanelFrame(savedFrame)
        let saveWorks = secondInstance.hasSavedPanelFrame()
        let restored = secondInstance.restoredPanelFrame(
            size: panelSize, screens: [screen], defaultFrame: defaultFrame
        )
        let restoreWorks = restored.origin == savedFrame.origin
        secondInstance.savePanelFrame(NSRect(x: 1300, y: 240, width: 900, height: 252))
        let clampedFrame = secondInstance.restoredPanelFrame(
            size: panelSize, screens: [screen], defaultFrame: defaultFrame
        )
        let resolutionChangeIsClamped = clampedFrame.maxX <= screen.maxX - 8
        secondInstance.savePanelFrame(savedFrame)
        secondInstance.restoreDefaults()
        let resetPreservesPosition = secondInstance.restoredPanelFrame(
            size: panelSize, screens: [screen], defaultFrame: defaultFrame
        ).origin == savedFrame.origin
        secondInstance.savePanelFrame(NSRect(x: 10000, y: 10000, width: 900, height: 252))
        let invalidPositionFallsBack = secondInstance.restoredPanelFrame(
            size: panelSize, screens: [screen], defaultFrame: defaultFrame
        ) == defaultFrame
        let resetWorks = secondInstance.currentLyricSize == 30
            && secondInstance.contextLyricSize == 20
            && secondInstance.contextOpacity == 0.62
            && secondInstance.displayLines == 2
            && secondInstance.backgroundOpacity == 1
            && secondInstance.rememberWindowPosition
            && secondInstance.restoreWindowPosition
            && secondInstance.hasSavedPanelFrame()

        let legacyMigrationWorks: Bool = {
            @MainActor func migrated(
                _ legacyValue: Int?,
                migrationVersion: Int? = nil
            ) -> (value: Int, storedValue: Int?, storedVersion: Int) {
                let migrationSuite = "\(suiteName).migration.\(UUID().uuidString)"
                let migrationDefaults = UserDefaults(suiteName: migrationSuite)!
                defer { migrationDefaults.removePersistentDomain(forName: migrationSuite) }
                if let legacyValue {
                    migrationDefaults.set(legacyValue, forKey: Key.displayLines)
                }
                if let migrationVersion {
                    migrationDefaults.set(
                        migrationVersion,
                        forKey: Key.displayLinesMigrationVersion
                    )
                }
                let migratedSettings = LyricsAppearanceSettings(defaults: migrationDefaults)
                return (
                    migratedSettings.displayLines,
                    migrationDefaults.object(forKey: Key.displayLines) as? Int,
                    migrationDefaults.integer(forKey: Key.displayLinesMigrationVersion)
                )
            }
            let five = migrated(5)
            let three = migrated(3)
            let one = migrated(1)
            let missing = migrated(nil)
            let previouslyMigratedThree = migrated(3, migrationVersion: 1)
            return five.value == 3 && five.storedValue == 3
                && three.value == 2 && three.storedValue == 2
                && one.value == 1 && one.storedValue == 1
                && missing.value == 2 && missing.storedValue == 2
                && previouslyMigratedThree.value == 2
                && previouslyMigratedThree.storedValue == 2
                && [five, three, one, missing, previouslyMigratedThree]
                    .allSatisfy { $0.storedVersion == Self.displayLinesMigrationVersion }
        }()
        return [
            "settingsDefaults": defaultsAreCorrect,
            "settingsCurrentSize": settings.currentLyricSize == 42,
            "settingsContextSize": settings.contextLyricSize == 28,
            "settingsContextOpacity": settings.contextOpacity == 0.4,
            "settingsDisplayLines": settings.displayLines == 3,
            "settingsBackgroundOpacity": settings.backgroundOpacity == 0.7,
            "settingsPersistence": persisted,
            "settingsNewInstanceReadsPersistence": reloadWorks,
            "settingsRestoreDefaults": resetWorks,
            "settingsRestoreDefaultsPreservesPosition": resetPreservesPosition,
            "settingsSaveWindowPosition": saveWorks,
            "settingsRestoreWindowPosition": restoreWorks,
            "settingsResolutionChangeIsClamped": resolutionChangeIsClamped,
            "settingsInvalidPositionFallsBack": invalidPositionFallsBack,
            "settingsLegacyDisplayLineMigration": legacyMigrationWorks,
            "settingsRejectsUnsupportedDisplayLineCount": {
                settings.setDisplayLines(5)
                return settings.displayLines == 2
            }(),
        ]
    }
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

    func styledText(
        at position: Double?,
        fontSize: Double
    ) -> AttributedString? {
        guard let words, let appearances = appearances(at: position) else {
            return nil
        }
        var result = AttributedString(text)
        result.foregroundColor = .primary.opacity(LyricsAppearance.upcomingWordOpacity)
        result.font = .system(size: fontSize, weight: .medium)
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
                size: fontSize,
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
    var previousLines: [String]?
    var current: String?
    var next: String?
    var nextLines: [String]?
    var message: String?
    var panelVisible: Bool?
    var openSettings: Bool?

    func contextLines(displayLines: Int) -> (previous: [String], next: [String]) {
        let previousSource = previousLines ?? previous.map { [$0] } ?? []
        let nextSource = nextLines ?? next.map { [$0] } ?? []
        switch displayLines {
        case 2:
            return ([], Array(nextSource.prefix(1)))
        case 3:
            return (Array(previousSource.suffix(1)), Array(nextSource.prefix(1)))
        default:
            return ([], [])
        }
    }

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

    func accept(_ data: Data) -> PanelMessage? {
        guard let message = try? JSONDecoder().decode(PanelMessage.self, from: data) else {
            return nil
        }
        self.message = message
        return message
    }
}

private struct TimedLyricText: View {
    let lyric: CurrentLyric
    let playbackPosition: Double?
    @ObservedObject var settings: LyricsAppearanceSettings

    private var renderedText: Text {
        Text(lyric.styledText(at: playbackPosition, fontSize: settings.currentLyricSize) ?? AttributedString(lyric.text))
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
    @ObservedObject var settings: LyricsAppearanceSettings

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
        let visibleContext = model.message.contextLines(displayLines: settings.displayLines)
        let previousLines = visibleContext.previous
        let nextLines = visibleContext.next

        return VStack(spacing: LyricsAppearance.groupSpacing) {
            if isMatched {
                ForEach(Array(previousLines.enumerated()), id: \.offset) { _, line in
                    contextLyric(line)
                        .transition(.opacity)
                }

                if let current = model.message.current {
                    Group {
                        if let lyric = model.message.currentLyric,
                           lyric.text == current {
                            TimedLyricText(
                                lyric: lyric,
                                playbackPosition: model.message.playbackPosition,
                                settings: settings
                            )
                        } else {
                            Text(current)
                                .multilineTextAlignment(.center)
                                .lineLimit(nil)
                                .lineSpacing(LyricsAppearance.lineSpacing)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .font(.system(size: settings.currentLyricSize, weight: .semibold))
                    .foregroundStyle(.primary)
                    .transition(.asymmetric(
                        insertion: .move(edge: .bottom).combined(with: .opacity),
                        removal: .move(edge: .top).combined(with: .opacity)
                    ))
                    .id(current)
                }

                ForEach(Array(nextLines.enumerated()), id: \.offset) { _, line in
                    contextLyric(line)
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
        .frame(
            width: LyricsAppearance.panelWidth,
            height: LyricsAppearance.panelHeight(for: settings)
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
            .font(.system(size: settings.contextLyricSize, weight: .regular))
            .foregroundStyle(.secondary.opacity(settings.contextOpacity))
            .multilineTextAlignment(.center)
            .lineLimit(2)
            .lineSpacing(LyricsAppearance.lineSpacing)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity)
    }
}

private struct LyricsSettingsView: View {
    @ObservedObject var settings: LyricsAppearanceSettings

    var body: some View {
        Form {
            Section("Lyrics") {
                settingSlider(
                    "Current lyric size",
                    value: settings.currentLyricSize,
                    range: 20...48,
                    valueText: "\(Int(settings.currentLyricSize.rounded())) pt",
                    set: settings.setCurrentLyricSize
                )
                settingSlider(
                    "Context lyric size",
                    value: settings.contextLyricSize,
                    range: 12...32,
                    valueText: "\(Int(settings.contextLyricSize.rounded())) pt",
                    set: settings.setContextLyricSize
                )
                settingSlider(
                    "Context opacity",
                    value: settings.contextOpacity,
                    range: 0.2...0.9,
                    valueText: "\(Int((settings.contextOpacity * 100).rounded()))%",
                    set: settings.setContextOpacity
                )
                Picker("Display lines", selection: Binding(
                    get: { settings.displayLines },
                    set: settings.setDisplayLines
                )) {
                    Text("1").tag(1)
                    Text("2").tag(2)
                    Text("3").tag(3)
                }
            }

            Section("Window") {
                settingSlider(
                    "Glass tint",
                    value: settings.backgroundOpacity,
                    range: 0...1,
                    valueText: "\(Int((settings.backgroundOpacity * 100).rounded()))%",
                    set: settings.setBackgroundOpacity
                )
                Toggle("Remember window position", isOn: Binding(
                    get: { settings.rememberWindowPosition },
                    set: settings.setRememberWindowPosition
                ))
                Toggle("Restore window position on launch", isOn: Binding(
                    get: { settings.restoreWindowPosition },
                    set: settings.setRestoreWindowPosition
                ))
            }

            HStack {
                Spacer()
                Button("Restore Defaults", action: settings.restoreDefaults)
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(width: 440)
    }

    private func settingSlider(
        _ title: String,
        value: Double,
        range: ClosedRange<Double>,
        valueText: String,
        set: @escaping (Double) -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(title)
                Spacer()
                Text(valueText)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            Slider(value: Binding(
                get: { value },
                set: set
            ), in: range)
        }
    }
}

private final class LyricsPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let model = PanelModel()
    private let settings = LyricsAppearanceSettings()
    private var panel: LyricsPanel?
    private var glassView: NSGlassEffectView?
    private var settingsWindow: NSWindow?
    private var inputSource: DispatchSourceRead?
    private var inputBuffer = Data()
    private var hotKeyHandler: EventHandlerRef?
    private var hotKeys: [EventHotKeyRef] = []
    private var moveTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        settings.onAppearanceChange = { [weak self] in self?.updatePanelFrame() }
        settings.onGlassIntensityChange = { [weak self] in self?.updateGlassTint() }
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
            height: LyricsAppearance.panelHeight(for: settings)
        )
        let defaultFrame = NSRect(
            x: screen.midX - size.width / 2,
            y: screen.minY + 54,
            width: size.width,
            height: size.height
        )
        let frame = settings.restoredPanelFrame(
            size: size,
            screens: NSScreen.screens.map(\.visibleFrame),
            defaultFrame: defaultFrame
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
        let glassView = NSGlassEffectView(frame: NSRect(origin: .zero, size: size))
        glassView.style = .clear
        glassView.cornerRadius = LyricsAppearance.cornerRadius
        glassView.autoresizingMask = [.width, .height]
        glassView.contentView = NSHostingView(
            rootView: LyricsPanelView(model: model, settings: settings)
        )
        panel.contentView = glassView
        panel.delegate = self
        if !CommandLine.arguments.contains("--initially-hidden") {
            panel.orderFrontRegardless()
        }
        self.panel = panel
        self.glassView = glassView
        updateGlassTint()
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
                guard let message = model.accept(Data(line)) else { continue }
                if let visible = message.panelVisible {
                    if visible {
                        panel?.orderFrontRegardless()
                    } else {
                        panel?.orderOut(nil)
                    }
                }
                if message.openSettings == true {
                    showSettings()
                }
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

    private func showSettings() {
        if let settingsWindow, settingsWindow.isVisible {
            NSApp.activate(ignoringOtherApps: true)
            settingsWindow.makeKeyAndOrderFront(nil)
            return
        }
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 440, height: 590),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Applyrx Settings"
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: LyricsSettingsView(settings: settings))
        window.delegate = self
        window.center()
        settingsWindow = window
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    private func updatePanelFrame() {
        guard let panel else { return }
        let frame = panel.frame
        let updated = NSRect(
            x: frame.minX,
            y: frame.minY,
            width: LyricsAppearance.panelWidth,
            height: LyricsAppearance.panelHeight(for: settings)
        )
        panel.setFrame(updated, display: true)
        settings.savePanelFrame(updated)
    }

    private func updateGlassTint() {
        guard let glassView else { return }
        let tintStrength = settings.backgroundOpacity * 0.04
        glassView.tintColor = tintStrength > 0
            ? NSColor.controlAccentColor.withAlphaComponent(tintStrength)
            : nil
    }

    func windowDidMove(_ notification: Notification) {
        guard let movedWindow = notification.object as? NSWindow,
              movedWindow === panel else {
            return
        }
        settings.savePanelFrame(movedWindow.frame)
    }

    func windowDidResize(_ notification: Notification) {
        guard let resizedWindow = notification.object as? NSWindow,
              resizedWindow === panel else {
            return
        }
        settings.savePanelFrame(resizedWindow.frame)
    }

    func windowWillClose(_ notification: Notification) {
        guard let closedWindow = notification.object as? NSWindow,
              closedWindow === settingsWindow else {
            return
        }
        settingsWindow = nil
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
        let settingChecks = LyricsAppearanceSettings.defaultsSelfTest()
        let hostingView = glassView?.contentView as? NSHostingView<LyricsPanelView>
        let result: [String: Bool] = [
            "panelVisible": panel?.isVisible == true,
            "nativeGlassSurface": glassView?.style == .clear
                && glassView?.cornerRadius == LyricsAppearance.cornerRadius
                && hostingView != nil
                && panel?.backgroundColor == .clear
                && panel?.isOpaque == false,
            "panelTransparency": panel?.alphaValue == 1
                && panel?.hasShadow == false
                && panel?.contentView === glassView,
            "glassLayout": glassView?.frame == panel?.contentView?.bounds
                && glassView?.autoresizingMask == [.width, .height],
            "hostingViewHasNoLayerBackground": hostingView?.layer?.backgroundColor == nil,
            "glassTintIsBounded": (glassView?.tintColor?.alphaComponent ?? 0) <= 0.04,
            "nonActivating": panel?.styleMask.contains(.nonactivatingPanel) == true
                && panel?.canBecomeKey == false,
            "floating": panel?.level == .floating,
            "clickThrough": panel?.ignoresMouseEvents == true,
            "registeredGlobalHotKeys": hotKeys.count == 2,
            "hotKeyVisibilityToggle": hideWorks && showWorks,
            "temporaryDragMode": dragModeWorks && restoreClickThroughWorks,
        ].merging(wordChecks, uniquingKeysWith: { _, new in new })
            .merging(settingChecks, uniquingKeysWith: { _, new in new })
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
        let textPreserved = words?.styledText(
            at: 1.5,
            fontSize: LyricsAppearance.defaultCurrentFontSize
        )
            .map { String($0.characters) == "我在海中" } ?? false
        let contextPayload = Data(
            #"{"title":"Test","artist":"Artist","playbackState":"playing","matchStatus":"matched","previousLines":["p0","p1"],"current":"line","nextLines":["n1","n2"]}"#.utf8
        )
        let contextMessage = try? JSONDecoder().decode(PanelMessage.self, from: contextPayload)
        let oneLineContext = contextMessage?.contextLines(displayLines: 1)
        let threeLineContext = contextMessage?.contextLines(displayLines: 3)
        let twoLineContext = contextMessage?.contextLines(displayLines: 2)
        let openingContextPayload = Data(
            #"{"title":"Test","artist":"Artist","playbackState":"playing","matchStatus":"matched","current":"line","nextLines":["n1","n2"]}"#.utf8
        )
        let openingContextMessage = try? JSONDecoder().decode(
            PanelMessage.self,
            from: openingContextPayload
        )
        let openingContext = openingContextMessage?.contextLines(displayLines: 2)
        let endingContextPayload = Data(
            #"{"title":"Test","artist":"Artist","playbackState":"playing","matchStatus":"matched","previousLines":["p1"],"current":"line"}"#.utf8
        )
        let endingContextMessage = try? JSONDecoder().decode(
            PanelMessage.self,
            from: endingContextPayload
        )
        let endingTwoLineContext = endingContextMessage?.contextLines(displayLines: 2)
        let endingThreeLineContext = endingContextMessage?.contextLines(displayLines: 3)
        let openingThreeLineContext = openingContextMessage?.contextLines(displayLines: 3)
        let commandPayload = Data(
            #"{"title":"Test","artist":"Artist","playbackState":"playing","matchStatus":"matched","panelVisible":false,"openSettings":true}"#.utf8
        )
        let commandMessage = try? JSONDecoder().decode(PanelMessage.self, from: commandPayload)

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
            "contextTextIsSubordinate": LyricsAppearance.defaultContextFontSize
                / LyricsAppearance.defaultCurrentFontSize >= 0.60
                && LyricsAppearance.defaultContextFontSize
                / LyricsAppearance.defaultCurrentFontSize <= 0.70,
            "currentLineTransitionIsShort": LyricsAppearance.currentLineTransitionDuration
                >= 0.15 && LyricsAppearance.currentLineTransitionDuration <= 0.25,
            "panelHeightsMatchDisplayLines":
                LyricsAppearance.panelHeight(
                    currentLyricSize: 30, contextLyricSize: 20, displayLines: 1
                ) == 42
                && LyricsAppearance.panelHeight(
                    currentLyricSize: 30, contextLyricSize: 20, displayLines: 2
                ) == 64
                && LyricsAppearance.panelHeight(
                    currentLyricSize: 30, contextLyricSize: 20, displayLines: 3
                ) == 86
                && LyricsAppearance.lineSpacing > 0,
            "displayLineSelections": oneLineContext?.previous.isEmpty == true
                && oneLineContext?.next.isEmpty == true
                && twoLineContext?.previous.isEmpty == true
                && twoLineContext?.next == ["n1"]
                && openingContext?.previous.isEmpty == true
                && openingContext?.next == ["n1"]
                && threeLineContext?.previous == ["p1"]
                && threeLineContext?.next == ["n1"]
                && endingTwoLineContext?.previous.isEmpty == true
                && endingTwoLineContext?.next.isEmpty == true
                && endingThreeLineContext?.previous == ["p1"]
                && endingThreeLineContext?.next.isEmpty == true
                && openingThreeLineContext?.previous.isEmpty == true
                && openingThreeLineContext?.next == ["n1"]
                && LyricsAppearance.defaultDisplayLines == 2,
            "settingsCommandDecode": commandMessage?.panelVisible == false
                && commandMessage?.openSettings == true,
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
            "wordTextMismatchFallback": mismatchedWords?.styledText(
                at: 0.5,
                fontSize: LyricsAppearance.defaultCurrentFontSize
            ) == nil
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
