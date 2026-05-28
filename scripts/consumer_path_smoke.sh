#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/swiftpython-consumer-path-smoke.XXXXXX")"
PYTHON_LIB_DIR="${SWIFTPYTHON_PYTHON_LIB_DIR:-}"

trap 'rm -rf "$WORK_DIR"' EXIT

if [ -z "$PYTHON_LIB_DIR" ]; then
    for candidate in \
        "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib" \
        "/usr/local/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib" \
        "/opt/homebrew/opt/python@3.13/lib" \
        "/usr/local/opt/python@3.13/lib"; do
        if [ -d "$candidate" ]; then
            PYTHON_LIB_DIR="$candidate"
            break
        fi
    done
fi

if [ -z "$PYTHON_LIB_DIR" ]; then
    echo "Python 3.13 library directory not found; set SWIFTPYTHON_PYTHON_LIB_DIR." >&2
    exit 1
fi

mkdir -p "$WORK_DIR/Sources/ConsumerSmoke"

cat > "$WORK_DIR/Package.swift" <<EOF
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ConsumerSmoke",
    platforms: [.macOS(.v15)],
    dependencies: [
        .package(name: "swiftpython-commercial", path: "$REPO_DIR"),
    ],
    targets: [
        .executableTarget(
            name: "ConsumerSmoke",
            dependencies: [
                .product(name: "SwiftPythonRuntime", package: "swiftpython-commercial"),
            ],
            linkerSettings: [
                .unsafeFlags([
                    "-L$PYTHON_LIB_DIR",
                    "-lpython3.13",
                ])
            ]
        ),
    ]
)
EOF

cat > "$WORK_DIR/Sources/ConsumerSmoke/main.swift" <<'EOF'
import SwiftPythonRuntime

@main
enum ConsumerSmoke {
    static func main() async throws {
        let version: String = try await Python.run {
            try String(pythonObject: Python.sys.version)
        }
        print("Python \(version)")

        try await withProcessPool(workers: 1) { pool in
            let value: Double = try await pool.invokeResult(
                module: "math",
                function: "sqrt",
                args: [.python(144.0)]
            )
            print("math.sqrt(144.0) = \(value)")
        }
    }
}
EOF

swift run --package-path "$WORK_DIR"
