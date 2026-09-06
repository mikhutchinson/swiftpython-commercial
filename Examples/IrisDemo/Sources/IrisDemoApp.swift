import AppKit
import SwiftUI

@MainActor
final class IrisAppDelegate: NSObject, NSApplicationDelegate {
    var model: IrisViewModel?
    func applicationDidFinishLaunching(_ notification: Notification) { NSApp.activate(ignoringOtherApps: true) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task { await model?.stop(); sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

struct IrisDemoApp: App {
    @NSApplicationDelegateAdaptor(IrisAppDelegate.self) private var delegate
    @State private var model = IrisViewModel()
    var body: some Scene {
        Window("Iris", id: "iris") {
            ContentView(model: model).onAppear { delegate.model = model }
        }
        .defaultSize(width: 1280, height: 860)
    }
}
