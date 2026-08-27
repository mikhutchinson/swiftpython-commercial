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
            #if os(macOS)
                let probe = try DuplexAudioHardwareProbeConfiguration(
                    wireFormat: audio,
                    durationSeconds: 2,
                    timeoutSeconds: 30,
                    requiresNonIdentityCaptureConversion: true
                )
                _ = probe
                let permission = DuplexAudioHardwareProbeLauncher.permissionState
            #endif
            print(
                "Optional interop OK: \(audio.sampleRate) Hz, "
                    + "\(ledger.snapshot.observedBytes) Metal bytes observed"
            )
            #if os(macOS)
                print("Audio probe launcher API OK: permission \(permission)")
            #endif
        } catch {
            fputs("SwiftPython smoke failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
