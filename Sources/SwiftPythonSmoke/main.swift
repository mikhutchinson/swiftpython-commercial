import Foundation
import SwiftPythonRuntime

@main
enum SwiftPythonSmoke {
    static func main() async {
        do {
            let version: String = try await Python.run {
                try String(pythonObject: Python.sys.version)
            }
            print("SwiftPythonRuntime OK")
            print(version)
        } catch {
            fputs("SwiftPython smoke failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
