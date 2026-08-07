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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.6/SwiftPythonRuntime.xcframework.zip",
            checksum: "6d77f017021068db66adb677e7099a4541b2a07af6fb750d8836a4922799a3ae"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.6/SwiftPythonEngine.xcframework.zip",
            checksum: "c02e247716a9facf5406e263cf59afe5a95747e63e87bf6b519a42880a35eb9d"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.6/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "473bc340ac6f2204e63ef43ab8db71d71ddeae8d882ae31834b45a791bfb5bc1"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.6/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "3393e13d0907731a570f24a5aa2ebc2a9d570133ee2410febc102e7cb367e4cb"
        ),
    ]
)
