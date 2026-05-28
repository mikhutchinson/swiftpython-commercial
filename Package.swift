// swift-tools-version: 6.0
import Foundation
import PackageDescription

private func pythonLibraryDirectory() -> String {
    let env = ProcessInfo.processInfo.environment
    if let explicit = env["SWIFTPYTHON_PYTHON_LIB_DIR"]?
        .trimmingCharacters(in: .whitespacesAndNewlines),
       !explicit.isEmpty {
        return explicit
    }
    let pythonHome = env["PYTHON_HOME"] ?? env["PYTHONHOME"]
    if let home = pythonHome?.trimmingCharacters(in: .whitespacesAndNewlines),
       !home.isEmpty {
        let frameworkLib = "\(home)/Frameworks/Python.framework/Versions/3.13/lib"
        if FileManager.default.fileExists(atPath: frameworkLib) {
            return frameworkLib
        }
        return "\(home)/lib"
    }

    let candidates = [
        "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib",
        "/usr/local/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib",
        "/opt/homebrew/opt/python@3.13/lib",
        "/usr/local/opt/python@3.13/lib",
    ]
    return candidates.first { FileManager.default.fileExists(atPath: $0) } ?? candidates[0]
}

private let pythonLinkerSettings: [LinkerSetting] = [
    .unsafeFlags([
        "-L\(pythonLibraryDirectory())",
        "-lpython3.13",
    ]),
]

let package = Package(
    name: "swiftpython-commercial",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "SwiftPythonRuntime", targets: ["SwiftPythonRuntime"]),
        .executable(name: "swiftpython-smoke", targets: ["SwiftPythonSmoke"]),
    ],
    targets: [
        .binaryTarget(
            name: "SwiftPythonRuntime",
            path: "SwiftPythonRuntime.xcframework"
        ),
        .executableTarget(
            name: "SwiftPythonSmoke",
            dependencies: ["SwiftPythonRuntime"],
            linkerSettings: pythonLinkerSettings
        ),
        .testTarget(
            name: "SwiftPythonSmokeTests",
            dependencies: ["SwiftPythonRuntime"],
            linkerSettings: pythonLinkerSettings
        ),
    ]
)
