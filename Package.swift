// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "swiftpython-commercial-binaries",
    platforms: [
        .macOS(.v15)
    ],
    products: [
        .library(
            name: "SwiftPythonRuntime",
            targets: ["SwiftPythonRuntime", "SwiftPythonEngine"]
        ),
        .library(
            name: "SwiftPythonAudioInterop",
            targets: [
                "SwiftPythonAudioInterop", "SwiftPythonRuntime", "SwiftPythonEngine"
            ]
        ),
        .library(
            name: "SwiftPythonMetalInterop",
            targets: [
                "SwiftPythonMetalInterop", "SwiftPythonRuntime", "SwiftPythonEngine"
            ]
        ),
    ],
    targets: [
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.3/SwiftPythonRuntime.xcframework.zip",
            checksum: "c844cce3f52248dcdcf9fbb66db8dd8ce765d171d25cc2705695932b517b5c8e"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.3/SwiftPythonEngine.xcframework.zip",
            checksum: "f467de0374aa549f05847dd16758a60bb81581e7600d64238b6d33bbf73d33d4"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.3/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "2ab2ebb67c64f17194d3385851a69ba7097128c17391b37c3f8253e3b1a15ffb"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.3/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "de0ac1b1df5f86290ee5a4c9041cc71ae6fb3a2147591239b780e7024229e997"
        ),
    ]
)
