import Foundation

/// Runtime helpers for the Studio app/CLI (hand-written; not generated).
public enum StudioRuntime {
    /// When a bundled Python framework is present (i.e. running from a packaged
    /// `.app` produced with `build_app.sh --bundle-python`), point the interpreter
    /// at the bundled stdlib + site-packages so the worker uses the bundled Python
    /// instead of a system/Homebrew one. The worker inherits these env vars as a
    /// child process. No-op in dev (`swift run`), where the system Python is used.
    public static func configureBundledPythonIfPresent() {
        // Resolve relative to the running executable (works for both the app's main
        // binary and a helper CLI inside Contents/MacOS), not Bundle.main.bundleURL
        // which differs between the two.
        guard let exeDir = Bundle.main.executableURL?.resolvingSymlinksInPath().deletingLastPathComponent() else {
            return
        }
        let contents = exeDir.deletingLastPathComponent()   // .../Contents
        let versionDir = contents.appendingPathComponent("Frameworks/Python.framework/Versions/3.13")
        guard FileManager.default.fileExists(atPath: versionDir.path) else { return }

        setenv("PYTHONHOME", versionDir.path, 1)

        let site = contents.appendingPathComponent("Resources/Python/site-packages").path
        if let existing = ProcessInfo.processInfo.environment["PYTHONPATH"], !existing.isEmpty {
            setenv("PYTHONPATH", site + ":" + existing, 1)
        } else {
            setenv("PYTHONPATH", site, 1)
        }
    }
}
