import Foundation
import SwiftPythonAudioInterop
import SwiftPythonMetalInterop
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
            let audio = try DuplexAudioFormat(
                sampleRate: 24_000,
                channels: 1,
                sampleType: .signedInteger16,
                interleaving: .interleaved
            )
            let ledger = DuplexCopyLedger()
            print(
                "Optional interop OK: \(audio.sampleRate) Hz, "
                    + "\(ledger.snapshot.observedBytes) Metal bytes observed"
            )
        } catch {
            fputs("SwiftPython smoke failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
