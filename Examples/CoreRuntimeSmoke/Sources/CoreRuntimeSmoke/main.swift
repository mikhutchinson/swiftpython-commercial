import Foundation
import SwiftPythonRuntime

@main
enum CoreRuntimeSmoke {
    static func main() async {
        do {
            let version: String = try await Python.run {
                try String(pythonObject: Python.sys.version)
            }
            print(version)

            let encoded: String = try await Python.run {
                let json = try Python.import("json")
                let payload = try pyDict(("language", "Swift"), ("via", "Python.run"))
                let pyStr = try json.dumps(payload)
                return try String(pythonObject: pyStr)
            }
            print(encoded)
        } catch {
            fputs("Core runtime smoke failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
