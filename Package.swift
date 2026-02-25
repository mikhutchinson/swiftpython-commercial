// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SwiftPython",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "SwiftPythonRuntime", targets: ["SwiftPythonRuntime"]),
    ],
    targets: [
        .binaryTarget(
            name: "SwiftPythonRuntime",
            path: "SwiftPythonRuntime.xcframework"
        ),
    ]
)
