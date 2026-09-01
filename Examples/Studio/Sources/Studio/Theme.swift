import SwiftUI

/// Dark, high-contrast palette tuned for screen capture (square 1:1 in-feed video).
enum Theme {
    static let bg = Color(red: 0.05, green: 0.06, blue: 0.09)
    static let panel = Color(red: 0.09, green: 0.10, blue: 0.14)
    static let panelStroke = Color.white.opacity(0.07)
    static let text = Color(red: 0.92, green: 0.94, blue: 0.98)
    static let dim = Color(red: 0.55, green: 0.58, blue: 0.66)
    static let accent = Color(red: 1.0, green: 0.58, blue: 0.0)        // Swift orange
    static let python = Color(red: 0.21, green: 0.49, blue: 0.74)      // Python blue
    static let good = Color(red: 0.30, green: 0.85, blue: 0.55)
    static let busy = Color(red: 1.0, green: 0.78, blue: 0.25)
    static let idle = Color.white.opacity(0.12)
    static let term = Color(red: 0.02, green: 0.03, blue: 0.05)

    static let mono = Font.system(.body, design: .monospaced)
    static let monoSmall = Font.system(size: 12, design: .monospaced)
    static let title = Font.system(size: 30, weight: .heavy, design: .rounded)

    static let modelColors: [Color] = [
        Color(red: 1.0, green: 0.58, blue: 0.0),
        Color(red: 0.36, green: 0.74, blue: 1.0),
        Color(red: 0.62, green: 0.45, blue: 1.0),
        Color(red: 0.30, green: 0.85, blue: 0.55),
    ]
}
