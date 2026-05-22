import Darwin
import Foundation
import SwiftPythonRuntime

/// Worker-local Python we install on a single pinned interpreter. Python calls
/// back into Swift through `swift_bridge`; Swift can re-enter the same worker
/// from callbacks, and consumes Python generators plus progress hints via pool
/// streams (`evalEvents`).
private let ringWorkerBootstrap = """
import swift_bridge

def streamed_scale(steps):
    for i in range(steps):
        swift_bridge.progress("scale-%d" % (i,))
        swift_bridge.check_cancel()
        yield i * 7

def fusion_report(addend):
    return float(swift_bridge.call("merge", float(addend)))

def host_probe():
    return str(swift_bridge.call("hardware_tag"))

accumulator = 9000.0
"""

private func hardwareTagLine() -> String {
    let model = sysctlString("hw.model")
    let os = ProcessInfo.processInfo.operatingSystemVersionString
    return "\(model) · macOS \(os)"
}

private func sysctlString(_ name: String) -> String {
    name.withCString { key in
        var size = 0
        guard sysctlbyname(key, nil, &size, nil, 0) == 0, size > 0 else {
            return "unknown"
        }
        var buffer = [CChar](repeating: 0, count: Int(size))
        var len = size_t(buffer.count)
        guard sysctlbyname(key, &buffer, &len, nil, 0) == 0 else {
            return "unknown"
        }
        if let terminator = buffer.firstIndex(of: 0) {
            let slice = buffer[..<terminator].map(UInt8.init(bitPattern:))
            return String(decoding: slice, as: UTF8.self)
        }
        return String(decoding: buffer.map(UInt8.init(bitPattern:)), as: UTF8.self)
    }
}

@main
enum BridgingRing {
    static func main() async {
        do {
            try await withProcessPool(workers: 1) { pool in
                let mergeReg = try await pool.registerReentrantCallback(name: "merge") {
                    @Sendable (ctx: WorkerCallbackContext, addend: Double) -> Double in
                    let baseline: Double = try ctx.evalResult("accumulator")
                    return baseline + addend
                }

                let hardwareReg = try await pool.registerCallback(name: "hardware_tag") {
                    @Sendable (_ arguments: [Any]) throws -> Any in
                    hardwareTagLine()
                }

                let regs: [CallbackRegistration] = [mergeReg, hardwareReg]
                _ = regs

                _ = try await pool.eval(ringWorkerBootstrap, worker: 0)

                let fused: Double = try await pool.evalResult(
                    "fusion_report(0.125)",
                    worker: 0
                )

                let hostLine: String = try await pool.evalResult(
                    "host_probe()",
                    worker: 0
                )

                print(
                    """

                    ═══════════════════════════════════════════════════════════════
                    BridgingRing — worker-isolated Swift ↔ Python loops
                      • Straight callback: Python asks Swift for host-only facts (`hw.model`).
                      • Reentrant callback: Swift reads THAT worker's `accumulator` mid-call.
                      • evalEvents: Python yields values + semantic progress on one Swift stream.
                    ═══════════════════════════════════════════════════════════════
                    """
                )
                print("fusion_report(0.125) → \(fused)  (expects 9000.125)")
                print("host_probe()        → \(hostLine)")

                print("\n--- evalEvents streamed_scale(4) ---")
                let events: CancellableStream<StreamEvent<Int>> = try await pool.evalEvents(
                    "streamed_scale(4)",
                    options: .pinned(worker: 0)
                )
                for try await event in events {
                    switch event {
                    case let .value(n):
                        print("  · value \(n)")
                    case let .progress(_, hint):
                        print("  · progress \(hint ?? "(no hint)")")
                    @unknown default:
                        break
                    }
                }
            }
        } catch {
            fputs("BridgingRing failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
