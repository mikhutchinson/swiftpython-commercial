import Foundation
import SwiftPythonRuntime

private let N = 1_048_576           // 1 Mi float64 elements = 8 MiB
private let RING_CAPACITY = 64 * 1024
private let TELEMETRY_FRAMES = 80
private let TELEMETRY_INTERVAL_MS = 5

@main
enum SharedTensorPipeline {
    static func main() async {
        do {
            try await withProcessPool(workers: 2) { pool in
                try await runDemo(pool: pool)
            }
        } catch {
            fputs("SharedTensorPipeline failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }

    static func runDemo(pool: PythonProcessPool) async throws {
        printBanner()

        // ─── 1) Allocate a worker-shared float64 tensor in the pool arena ───
        section("1. Pool arena allocates one POSIX-shm region, broadcast-attached to every worker")
        let shared = try await pool.createSharedTensor(shape: [N], dtype: .float64)
        guard let shm = shared.sharedMemory else {
            throw DemoError.message("createSharedTensor returned a handle with no shared-memory backing")
        }
        let mib = Double(shm.size) / (1024.0 * 1024.0)
        print("   shm name : \(shm.shmName)")
        print("   shape    : \(shm.shape)  dtype=\(shm.dtype)")
        print("   bytes    : \(shm.size) (\(String(format: "%.2f", mib)) MiB)")
        print("   offset   : \(shm.offset)")
        let workerCount = await pool.workerCount
        print("   workers  : attached to all \(workerCount) commandable workers")

        // ─── 2) Swift seeds via mmap, no pickle, no IPC ───
        section("2. Swift seeds every element through pool.withSharedBuffer (direct mmap write)")
        let seedClock = ContinuousClock()
        let seedStart = seedClock.now
        try await pool.withSharedBuffer(shared, as: Double.self) { buf in
            precondition(buf.count == N, "buffer count mismatch: \(buf.count) vs \(N)")
            for i in 0..<buf.count {
                buf[i] = Double(i)
            }
        }
        let seedMs = milliseconds(seedClock.now - seedStart)
        let seedGBps = (Double(shm.size) / (1024 * 1024 * 1024)) / (seedMs / 1000.0)
        print("   wrote 0…\(N - 1) into mmap region in \(fmt(seedMs)) ms  (\(String(format: "%.2f", seedGBps)) GiB/s sustained)")

        // ─── 3) Two workers reduce halves of the SAME bytes, in parallel ───
        section("3. Two workers reduce disjoint halves of the same mmap bytes, in parallel")
        let half = N / 2
        let reduceStart = seedClock.now
        async let leftSum: Double = pool.worker(0).evalResult(
            "float(arr[:\(half)].sum())",
            bindings: ["arr": shared]
        )
        async let rightSum: Double = pool.worker(1).evalResult(
            "float(arr[\(half):].sum())",
            bindings: ["arr": shared]
        )
        let (left, right) = try await (leftSum, rightSum)
        let reduceMs = milliseconds(seedClock.now - reduceStart)
        let total = left + right
        let expected = Double(N - 1) * Double(N) / 2.0
        print("   worker 0 sum[0 ..<\(half))      = \(fmtBig(left))")
        print("   worker 1 sum[\(half)..<\(N))    = \(fmtBig(right))")
        print("   total                            = \(fmtBig(total))")
        print("   expected  (N·(N-1)/2)            = \(fmtBig(expected))")
        print("   parallel wall time               = \(fmt(reduceMs)) ms")
        guard total == expected else {
            throw DemoError.sumMismatch(expected: expected, actual: total)
        }
        print("   ✓ exact float64 match (no copies, no pickle, just two mmap views)")

        // ─── 4) Install the telemetry generator on worker 0 ───
        section("4. Out-of-band streaming: bring up a Python generator on worker 0")
        _ = try await pool.eval(
            """
            import json, time
            def _spt_telemetry(n, sleep_ms=5):
                start = time.monotonic()
                for i in range(n):
                    elapsed_ms = (time.monotonic() - start) * 1000.0
                    yield json.dumps({
                        "frame": i,
                        "elapsed_ms": round(elapsed_ms, 3),
                    }) + "\\n"
                    time.sleep(sleep_ms / 1000.0)
            """,
            worker: 0
        )
        print("   defined _spt_telemetry(n, sleep_ms) in worker 0 persistent namespace")

        let ring = try SharedRingBuffer(capacity: RING_CAPACITY)
        print("   ring buffer: name=\(ring.shmName)  capacity=\(ring.dataCapacity) B")
        try await pool.startOutOfBandStream(
            generatorCode: "_spt_telemetry(\(TELEMETRY_FRAMES), sleep_ms=\(TELEMETRY_INTERVAL_MS))",
            worker: 0,
            buffer: ring
        )
        print("   started OOB writer thread on worker 0 — frames flow through shared memory")
        print("   key invariant: this side channel never touches the worker IPC socket lock")

        // ─── 5) Concurrent proof: regular evalResult on the same worker stays responsive ───
        section("5. Liveness proof: regular evalResult on worker 0 while OOB is streaming")
        let drainState = TelemetryDrain()
        let drainTask = Task { @Sendable [ring] in
            await drainState.drain(from: ring)
        }

        for probe in 1...4 {
            let pStart = seedClock.now
            let result: Int = try await pool.evalResult(
                "sum(range(\(probe * 1_000)))",
                worker: 0,
                timeout: 2.0
            )
            let pMs = milliseconds(seedClock.now - pStart)
            print("   probe #\(probe): sum(range(\(probe * 1_000))) = \(result)  (\(fmt(pMs)) ms IPC round-trip while OOB is hot)")
            try? await Task.sleep(nanoseconds: 60_000_000)
        }

        await drainTask.value

        let drained = await drainState.snapshot()
        print(
            "   drained \(drained.frames) telemetry frames, "
            + "first elapsed_ms=\(fmt(drained.firstElapsedMs)) "
            + "last elapsed_ms=\(fmt(drained.lastElapsedMs))  "
            + "writerDone=\(ring.isWriterDone) writePos=\(ring.writePosition) B"
        )
        guard drained.frames == TELEMETRY_FRAMES else {
            throw DemoError.frameCount(expected: TELEMETRY_FRAMES, actual: drained.frames)
        }
        print("   ✓ every telemetry frame accounted for, no socket contention with eval probes")

        // ─── 6) Zero-copy readback from Swift's side of the same mmap ───
        section("6. Zero-copy readback from Swift's mmap view")
        try await pool.withSharedBuffer(shared, as: Double.self) { buf in
            precondition(buf.count == N)
            let head = (0..<min(8, buf.count)).map { String(format: "%.0f", buf[$0]) }
            let tailStart = max(0, buf.count - 8)
            let tail = (tailStart..<buf.count).map { String(format: "%.0f", buf[$0]) }
            print("   arr[0..<8]                 = [\(head.joined(separator: ", "))]")
            print("   arr[\(tailStart)..<\(buf.count)] = [\(tail.joined(separator: ", "))]")
            print("   arr.last - arr.first       = \(buf[buf.count - 1] - buf[0])")
        }

        try? await pool.release(shared)
        printFooter(seedMs: seedMs, seedGBps: seedGBps, reduceMs: reduceMs, frames: drained.frames)
    }
}

// MARK: - Telemetry drain (line-framed JSON over the ring buffer)

private actor TelemetryDrain {
    struct Snapshot: Sendable {
        let frames: Int
        let firstElapsedMs: Double
        let lastElapsedMs: Double
    }

    private var lineBuffer = Data()
    private var frames = 0
    private var firstElapsedMs: Double = .nan
    private var lastElapsedMs: Double = .nan

    func snapshot() -> Snapshot {
        Snapshot(frames: frames, firstElapsedMs: firstElapsedMs, lastElapsedMs: lastElapsedMs)
    }

    func drain(from ring: SharedRingBuffer) async {
        // Poll the ring buffer until the Python writer flips the writerDone flag
        // and we have consumed any remaining bytes.
        while !Task.isCancelled {
            let chunk = ring.readAvailable()
            if !chunk.isEmpty {
                lineBuffer.append(chunk)
                while let nlIndex = lineBuffer.firstIndex(of: 0x0A) {
                    let line = lineBuffer[..<nlIndex]
                    lineBuffer.removeSubrange(...nlIndex)
                    handleLine(Data(line))
                }
            }
            if ring.isWriterDone && lineBuffer.isEmpty { break }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        // Flush any trailing partial line that ends without a newline.
        if !lineBuffer.isEmpty {
            handleLine(Data(lineBuffer))
            lineBuffer.removeAll()
        }
    }

    private func handleLine(_ data: Data) {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let frame = object["frame"] as? Int,
            let elapsed = object["elapsed_ms"] as? Double
        else {
            return
        }

        let visible: Bool = frames < 3
            || frame == TELEMETRY_FRAMES - 1
            || frames % 20 == 0
        if visible {
            print("   OOB frame \(String(format: "%3d", frame))  elapsed=\(String(format: "%7.3f", elapsed)) ms")
        }

        if frames == 0 { firstElapsedMs = elapsed }
        lastElapsedMs = elapsed
        frames += 1
    }
}

// MARK: - Output helpers

private func section(_ title: String) {
    print("")
    print("─── \(title) ───")
}

private func printBanner() {
    print("""
    ╔══════════════════════════════════════════════════════════════════════╗
    ║  SharedTensorPipeline — POSIX shared memory + out-of-band streaming  ║
    ╠══════════════════════════════════════════════════════════════════════╣
    ║  • Pool arena allocates ONE shared float64 tensor                    ║
    ║  • Swift seeds via mmap (no pickle, no IPC)                          ║
    ║  • Two workers reduce halves of the same bytes in parallel           ║
    ║  • A separate SharedRingBuffer streams Python telemetry              ║
    ║  • Regular evalResult on the same worker stays responsive            ║
    ║  • Swift reads the result back through its mmap view                 ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """)
}

private func printFooter(seedMs: Double, seedGBps: Double, reduceMs: Double, frames: Int) {
    print("")
    print("""
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Pipeline OK
      • mmap seed     : \(fmt(seedMs)) ms  (~\(String(format: "%.2f", seedGBps)) GiB/s)
      • parallel sum  : \(fmt(reduceMs)) ms across 2 workers (exact float64 match)
      • OOB telemetry : \(frames) JSON frames over SharedRingBuffer, IPC socket free
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """)
}

private func milliseconds(_ duration: Duration) -> Double {
    let components = duration.components
    return Double(components.seconds) * 1_000.0
        + Double(components.attoseconds) / 1_000_000_000_000_000.0
}

private func fmt(_ ms: Double) -> String {
    String(format: "%7.2f", ms)
}

private func fmtBig(_ value: Double) -> String {
    if value.rounded() == value, abs(value) < 1e18 {
        return String(format: "%.0f", value)
    }
    return String(value)
}

// MARK: - Errors

private enum DemoError: LocalizedError {
    case message(String)
    case sumMismatch(expected: Double, actual: Double)
    case frameCount(expected: Int, actual: Int)

    var errorDescription: String? {
        switch self {
        case .message(let s):
            return s
        case .sumMismatch(let e, let a):
            return "Shared tensor reduction mismatch: expected \(e), got \(a)"
        case .frameCount(let e, let a):
            return "OOB telemetry frame count mismatch: expected \(e), got \(a)"
        }
    }
}
