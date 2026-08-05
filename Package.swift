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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.5/SwiftPythonRuntime.xcframework.zip",
            checksum: "f7a684fbb994610c49f2cd80b8806b84024cb87445416b3fa7bba70908038f61"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.5/SwiftPythonEngine.xcframework.zip",
            checksum: "dc667767df2e621de61dd8cb216d488f8a3c4b9e60353dfe3226a9d5f9dfbd09"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.5/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "75b73d4f2a576f59402526d12b74c78d004ee89b52ff99b4c4518d37a0e97010"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.5/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "0eebc62673d1511be1991933f13b55ac2a5d66ed85ff2bc208304c94d4eb0dd6"
        ),
    ]
)
