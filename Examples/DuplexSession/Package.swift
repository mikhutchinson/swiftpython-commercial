// swift-tools-version: 6.0
import Foundation
import PackageDescription

private func parseSemVer(_ raw: String) -> Version? {
    let buildSplit = raw.split(
        separator: "+",
        maxSplits: 1,
        omittingEmptySubsequences: false
    )
    guard buildSplit.count <= 2 else { return nil }
    let releaseAndPrerelease = buildSplit[0].split(
        separator: "-",
        maxSplits: 1,
        omittingEmptySubsequences: false
    )
    let core = releaseAndPrerelease[0].split(
        separator: ".",
        omittingEmptySubsequences: false
    )
    guard core.count == 3,
          let major = Int(core[0]),
          let minor = Int(core[1]),
          let patch = Int(core[2]),
          major >= 0, minor >= 0, patch >= 0 else {
        return nil
    }
    let prerelease = releaseAndPrerelease.count == 2
        ? releaseAndPrerelease[1].split(
            separator: ".",
            omittingEmptySubsequences: false
        ).map(String.init)
        : []
    let build = buildSplit.count == 2
        ? buildSplit[1].split(
            separator: ".",
            omittingEmptySubsequences: false
        ).map(String.init)
        : []
    guard prerelease.allSatisfy({ !$0.isEmpty }),
          build.allSatisfy({ !$0.isEmpty }) else {
        return nil
    }
    return Version(
        major,
        minor,
        patch,
        prereleaseIdentifiers: prerelease,
        buildMetadataIdentifiers: build
    )
}

private let environment = ProcessInfo.processInfo.environment
private let packageURL = environment[
    "SWIFTPYTHON_COMMERCIAL_PACKAGE_URL"
]
private let versionRaw = environment[
    "SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION"
]
private let dependency: Package.Dependency
private let dependencyPackageName: String
if let packageURL, !packageURL.isEmpty,
   let versionRaw, !versionRaw.isEmpty {
    guard let version = parseSemVer(versionRaw) else {
        fatalError(
            "SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION must be semantic version MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]"
        )
    }
    dependency = .package(url: packageURL, exact: version)
    var trimmedURL = packageURL
    while trimmedURL.hasSuffix("/") {
        trimmedURL.removeLast()
    }
    let component = (trimmedURL as NSString).lastPathComponent
    dependencyPackageName = component.hasSuffix(".git")
        ? String(component.dropLast(4))
        : component
} else {
    dependency = .package(
        name: "swiftpython-commercial",
        path: "../.."
    )
    dependencyPackageName = "swiftpython-commercial"
}

private func pythonLibraryDirectory() -> String {
    if let explicit = environment["SWIFTPYTHON_PYTHON_LIB_DIR"],
       !explicit.isEmpty {
        return explicit
    }
    let pythonHome = environment["PYTHON_HOME"] ?? environment["PYTHONHOME"]
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
    return candidates.first {
        FileManager.default.fileExists(atPath: $0)
    } ?? candidates[0]
}

let package = Package(
    name: "DuplexSession",
    platforms: [.macOS(.v15)],
    dependencies: [dependency],
    targets: [
        .executableTarget(
            name: "DuplexSession",
            dependencies: [
                .product(
                    name: "SwiftPythonRuntime",
                    package: dependencyPackageName
                ),
            ],
            linkerSettings: [
                .unsafeFlags([
                    "-L\(pythonLibraryDirectory())",
                    "-lpython3.13",
                ]),
            ]
        ),
    ]
)
