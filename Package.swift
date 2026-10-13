// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "swiftpython-commercial-binaries",
    platforms: [
        .macOS(.v15)
    ],
    products: [
        .library(
            name: "SwiftPythonWorkerService",
            targets: [
                "SwiftPythonWorkerService", "SwiftPythonRuntime", "SwiftPythonEngine", "Python"
            ]
        ),
        .library(
            name: "SwiftPythonRuntime",
            targets: ["SwiftPythonRuntime", "SwiftPythonEngine", "Python"]
        ),
        .library(
            name: "SwiftPythonAudioInterop",
            targets: [
                "SwiftPythonAudioInterop", "SwiftPythonRuntime", "SwiftPythonEngine", "Python"
            ]
        ),
        .library(
            name: "SwiftPythonMetalInterop",
            targets: [
                "SwiftPythonMetalInterop", "SwiftPythonRuntime", "SwiftPythonEngine", "Python"
            ]
        ),
    ],
    targets: [
        .binaryTarget(
            name: "SwiftPythonWorkerService",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.4/SwiftPythonWorkerService.xcframework.zip",
            checksum: "3915cb56909044a9d7ddab715f1f9f4365706e924ed3df4a3ec12c95fda8c0cb"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.4/SwiftPythonRuntime.xcframework.zip",
            checksum: "81404f1f75810d5924fee38fd25fe4512ce4ec40aed4081ecbea6c183f438ee3"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.4/SwiftPythonEngine.xcframework.zip",
            checksum: "b418bac66fb78dd5123c9235b67c288b1dd158e3836241a58a4e37d53422427f"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.4/Python.xcframework.zip",
            checksum: "67283176a5fa69598a77c65fca1b4385e55157b58a1a2262bb001024e2c691c3"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.4/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "36214ea4de98316e46f8b25c4dc02dbeeb8d317c14d3440e3cc1824e96c1df3f"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.4/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "a72844a5a4f669b9fa86cbad3cda6ef78c0dcc9fd9aa21448397bf4759b93bdc"
        ),
    ]
)
