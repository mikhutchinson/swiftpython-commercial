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
            url: "https://github.com/mikhutchinson/swiftpython-commercial/releases/download/v0.1.0/SwiftPythonRuntime.xcframework.zip",
            checksum: "9e8e6b57a1db4e65524e7909c30f97a0576db3790c30391ec3add5a547fdff53"
        ),
    ]
)
