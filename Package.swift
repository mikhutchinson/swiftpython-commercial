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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.5/SwiftPythonWorkerService.xcframework.zip",
            checksum: "50f39b99de662aaead1386a0520fa8022e0edbd44587323b1e8e0dff7888aa6a"
        ),
        .binaryTarget(
            name: "SwiftPythonRuntime",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.5/SwiftPythonRuntime.xcframework.zip",
            checksum: "29913cdcd252486490c7a3e27256cfa0108c45a7a22abdae8cf70e2453a30278"
        ),
        // Link/embed dependency only. Deliberately absent from products.
        .binaryTarget(
            name: "SwiftPythonEngine",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.5/SwiftPythonEngine.xcframework.zip",
            checksum: "26900b5af9d9b5d4ea35c28646367514269429e528419b2189c0fed33c76be43"
        ),
        // Private CPython runtime. Inclusion in each product makes Xcode embed
        // and sign it automatically; consumers install no system Python.
        .binaryTarget(
            name: "Python",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.5/Python.xcframework.zip",
            checksum: "cda177986c532b31f20b2396247b60adb4753d34af8138ca5f4758a3809b837e"
        ),
        .binaryTarget(
            name: "SwiftPythonAudioInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.5/SwiftPythonAudioInterop.xcframework.zip",
            checksum: "311c750d2136249e5832bd3cbdcb91456802765938bc80b7b94b70624da635ce"
        ),
        .binaryTarget(
            name: "SwiftPythonMetalInterop",
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.7.0-preview.1.5/SwiftPythonMetalInterop.xcframework.zip",
            checksum: "ee05e950c80544c127b28cbdff91646936aa70a2b5a983f075ded70b5a5fb355"
        ),
    ]
)
