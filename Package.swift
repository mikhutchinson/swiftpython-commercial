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
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.4/SwiftPythonRuntime.xcframework.zip",
            checksum: "be8970031930d6acfdf70c19dafdd7c96d1424c5ed1a32fbcbaa851227e4c34f"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.4/SwiftPythonEngine.xcframework.zip",
            checksum: "0fc5bcbf25102b261b06f3cfe6645fc883dbf1b73fb51893bc962a89317efc61"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.4/Python.xcframework.zip",
            checksum: "5f3811253ba81e068d0af8a57539f2bfd146d1ea9bca03f5524894e5d7ac583f"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.4/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "9b26707b627d043427b2e2155dbe8e7c61eed426e77595b154d8ddd0dbced7e1"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.6.0-duplex.8.4/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "15fbf56d06a3308185a9741bcab28b48c0aaa2f0fe595f36f9f42dfde8e78a14"
        ),
    ]
)
