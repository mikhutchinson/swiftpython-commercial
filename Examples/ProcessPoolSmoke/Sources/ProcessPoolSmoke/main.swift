import Foundation
import SwiftPythonRuntime

@main
enum ProcessPoolSmoke {
    static func main() async {
        do {
            try await withProcessPool(workers: 2) { pool in
                let sqrtResult: Double = try await pool.invokeResult(
                    module: "math",
                    function: "sqrt",
                    args: [.python(144.0)]
                )
                print("math.sqrt(144.0) = \(sqrtResult)")
            }
        } catch {
            fputs("Process pool smoke failed: \(error.localizedDescription)\n", stderr)
            fputs(
                """
                Hint: ensure SwiftPythonWorker is discoverable (see README: \
                SWIFTPYTHON_WORKER_PATH / bundle layout).

                """,
                stderr
            )
            exit(1)
        }
    }
}
