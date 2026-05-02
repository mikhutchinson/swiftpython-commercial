# Chapter 4 — ProcessPool

`PythonProcessPool` is a Swift actor that manages N isolated Python worker processes. Each worker has its own GIL — true parallelism.

## Creating a pool

```swift
// Simple
let pool = try await PythonProcessPool(workers: 4)

// With resource limits and backpressure
let pool = try await PythonProcessPool(
    workers: 4,
    resourceLimits: WorkerResourceLimits(maxMemoryBytes: 4 * 1024 * 1024 * 1024),
    backpressure: .suspend(maxInFlight: 16)
)

// Scoped lifetime — always prefer this
try await withProcessPool(workers: 4) { pool in
    // pool.shutdown() is awaited automatically on exit
}
```

**`withProcessPool` is strongly preferred** over manual `init`/`shutdown`. It guarantees cleanup on both success and error paths.

## Core execution APIs

All methods come in two flavours:
- **`eval`/`invoke`/`method`** — returns `PyHandle` (object stays on worker)
- **`evalResult`/`invokeResult`/`methodResult`** — pickles result back, returns `T: PythonConvertible`

```swift
// eval — run arbitrary code, keep result on worker
let handle: PyHandle = try await pool.eval("import numpy as np; np.arange(100)")

// evalResult — run code, get Swift value back
let values: [Double] = try await pool.evalResult("list(range(10))")

// invoke — call a module-level function
let h = try await pool.invoke(module: "numpy", function: "zeros", args: [1024])
let r: [Double] = try await pool.invokeResult(module: "math", function: "sqrt", args: [144.0])

// method — call a method on a remote handle
let result = try await pool.method(handle: h, name: "tolist")
let arr: [Double] = try await pool.methodResult(handle: h, name: "flatten")
```

### Pinning to a specific worker

```swift
// explicit worker index
let h = try await pool.eval("build_model()", worker: 2)

// WorkerContext — binds a series of calls to one worker
let ctx = pool.worker(2)
let h1 = try await ctx.eval("import sklearn; clf = sklearn.ensemble.RandomForestClassifier()")
let h2 = try await ctx.invoke(module: "numpy", function: "random.randn", args: [100, 4])
let result: [Int] = try await ctx.methodResult(handle: h1, name: "predict", args: [r.arg(h2)])
```

### Builder syntax for args/kwargs

```swift
try await pool.invoke(module: "numpy", function: "arange") {
    0; 100        // positional args (RemoteArgsBuilder)
} kwargs: {
    ("dtype", "float32")   // (String, Value) tuples (RemoteKwargsBuilder)
}
```

### Scoped handle helpers

```swift
// Auto-release when body exits
try await pool.worker(0).withEvalHandle("np.eye(4)") { h in
    let tr: Double = try await pool.methodResult(handle: h, name: "trace")
}

try await pool.withTemporaryHandle(createdBy: { try await pool.eval("expensive()") }) { h in
    // h is released after this block
}
```

## `pool.map` — bulk transform

```swift
// Run the same code on each handle in parallel, across workers
let results: [PyHandle] = try await pool.map(handles, code: "item * 2")
// 'item' is bound to each handle by default; customise with bindingName:
let results: [PyHandle] = try await pool.map(handles, code: "process(x)", bindingName: "x")
```

Each handle is dispatched to a worker; results come back in input order.

## Persistent namespace

`eval` calls on the same worker share a **persistent Python namespace** (like a REPL). Variables defined in one `eval` are visible in subsequent `eval` calls on that worker.

```swift
_ = try await pool.eval("x = 42", worker: 0)
let val: Int = try await pool.evalResult("x * 2", worker: 0)  // 84
```

Sentinel variables used internally (`__result__`, `__swiftpython_*`, etc.) are scrubbed at the start of each call.

## Worker states & quarantine

```swift
public enum WorkerState: Sendable {
    case cold                      // reserved slot, no process — spawns on demand
    case healthy                   // connected, ready
    case quarantined(until: Date)  // skipped for 10s after 5 consecutive soft failures
    case respawning                // being respawned after a crash
    case dead                      // exhausted respawn budget
}
```

- After **5 consecutive soft failures** a worker enters `.quarantined(until:)` and is skipped by `selectWorker()` for 10 seconds.
- Worker 0 is never shed (always at least one warm worker).
- Cold workers re-spawn on demand when `selectWorker()` finds no warm workers.

## Backpressure policies

```swift
.unbounded                    // no limit (default)
.reject(maxInFlight: 16)      // throw .backpressure when full
.suspend(maxInFlight: 16)     // FIFO cooperative throttle — callers wait
```

## Resource monitor (Darwin)

```swift
ResourceMonitorConfig(
    sampleInterval: 2.0,
    memoryPressureThrottle: 0.85,   // ≥85% → force-suspend new submissions
    memoryPressureReject: 0.95,     // ≥95% → throw .backpressure
    thermalThrottleLevel: .fair,    // ≥.fair → force-suspend
    workerCPUThrottlePercent: 90.0, // skip CPU-hot workers in selectWorker
    enabled: true                   // default true on Darwin, false on Linux
)

// Inspect current state
let snap: ResourceSnapshot = await pool.resourceSnapshot()
// snap.systemMemoryPressure, .freeMemoryBytes, .thermalLevel, .workerStats
```

Memory-pressure-aware worker lifecycle:
- Workers can be **shed** when idle (worker 0 is never shed)
- Shed workers enter `.cold` state and are re-spawned on demand
- Sampling is adaptive: **2s** when idle, **0.5s** when under pressure
- `ProcessInfo.thermalStateDidChangeNotification` triggers an immediate sample on thermal change (Darwin only)
- Same 0.85/0.95 thresholds gate both task submission **and** worker spawning

## Lifecycle

```swift
// Warmup — run setup code on all workers in parallel
try await pool.warmup("import numpy as np; import sklearn")

// Drain — wait for in-flight work, block new submissions
try await pool.drain(timeout: .seconds(30))

// Resume — re-enable after drain
pool.resume()

// Health check — returns [Bool] per worker
let health = try await pool.healthCheck()

// Shutdown — clean teardown (5s default timeout)
await pool.shutdown()
```

## `pool.events()` — lifecycle broadcast (v0.2.0+ / Phase B3)

`pool.events(bufferSize:)` returns an `AsyncStream<PoolEvent>` that broadcasts every lifecycle transition the pool tracks: worker spawns, respawns, deaths, quarantine entry/expiry, idle-shedding, drain/resume/shutdown, and resource-pressure events.

```swift
Task {
    for await event in pool.events() {
        switch event {
        case .workerSpawned(let id, let pid, let gen):
            logger.info("worker \(id) spawned (pid=\(pid), gen=\(gen))")
        case .workerRespawned(let id, let oldPID, let newPID, _, let reason):
            logger.warn("worker \(id) respawned: \(oldPID) → \(newPID), reason=\(reason)")
        case .workerDied(let id, let reason):
            logger.error("worker \(id) died: \(reason)")
        case .poolStateChanged(_, .shutdown):
            return  // pool is shutting down — exit the consumer
        case .eventsDropped(let n):
            logger.warn("event subscriber missed \(n) events (consumer too slow)")
        default:
            continue
        }
    }
}
```

### Subscriber model

- Each call to `pool.events()` returns a **fresh, independent** `AsyncStream`. Multiple subscribers each receive their own copy of every emission.
- Bounded buffer per subscriber (default 256, drop-oldest). Slow consumers do not back-pressure other subscribers.
- When events are dropped, the next successful delivery is preceded by a synthesised `.eventsDropped(count:)` event so loss is detectable (rather than silent).
- Subscribers auto-unregister when the iterator drops or the pool deallocates.

### `PoolEvent` cases

| Case | When it fires |
|------|---------------|
| `.workerSpawned(workerID:pid:generation:)` | Initial pool init AND lazy cold→warm spawns |
| `.workerRespawned(workerID:oldPID:newPID:generation:reason:)` | After successful respawn |
| `.workerStateChanged(workerID:from:to:)` | Every worker-state transition (paired with the more specific events) |
| `.workerDied(workerID:reason:)` | Respawn budget exhausted, VM unhealthy, or terminal respawn failure |
| `.workerQuarantined(workerID:until:)` | After 5 consecutive soft failures |
| `.workerQuarantineExpired(workerID:)` | When the quarantine deadline passes (lazy, on next `selectWorker`) |
| `.workerIdleShed(workerID:)` | When the idle-shed policy retires a worker to `.cold` |
| `.poolStateChanged(from:to:)` | Pool-level state transitions |
| `.drainCompleted(durationMs:)` | After `drain()` completes (in-flight count reaches zero) |
| `.spawnRejected(workerID:memoryPressure:freeBytes:)` | When `waitForSpawnPressure` rejects pre-spawn (Darwin) |
| `.memoryPressureChanged(level:)` | OS memory pressure transitions (Darwin DispatchSourceMemoryPressure) |
| `.resourcePressureSampled(snapshot:)` | Per-monitor-tick — opt-in via `IPCConfiguration.broadcastResourceSamples` |
| `.eventsDropped(count:)` | Synthesised when a subscriber's buffer overflowed |

### Notes and constraints

- **Subscribers attached AFTER `init` returns will not see the initial `.workerSpawned` events** for the original pool topology. Subscribe before init if you need that, or rely on `lazy spawn → .workerSpawned` for the on-demand topology changes that follow.
- **Cross-subscriber ordering is not guaranteed.** Within a single subscriber events are FIFO; two subscribers may interleave the same emissions differently relative to other concurrent work.
- **No synthetic terminator on `shutdown()`.** The `AsyncStream` naturally terminates when the pool deallocates; the terminal lifecycle event is `.poolStateChanged(.shuttingDown, .shutdown)`.
- `PoolEvent` is **non-`@frozen`** — handle a `default:` arm in exhaustive switches so future v0.2.x phases can add cases without breaking your call sites.

## Worker executable discovery

The pool auto-discovers `SwiftPythonWorker` by searching (in order):
1. Bundle auxiliary executables (app bundles)
2. Same directory as main executable
3. Dynamic detection from `.build/` location
4. Arch-specific paths (`arm64-apple-macosx/`, etc.)
5. Generic fallback (`debug/`, `release/`)

If not found: `swift build --product SwiftPythonWorker`

For packaged apps: set `SWIFTPYTHON_WORKER_PATH` env var.

## Error types

```swift
PythonWorkerError.pythonException(type:message:traceback:)
PythonWorkerError.backpressure
PythonWorkerError.socketError(String)
PythonWorkerError.protocolError(String)
PythonWorkerError.poolDrained
PythonWorkerError.spawnRejected(memoryPressure:freeBytes:workerID:)
PythonWorkerError.invalidConfiguration(String)
PythonWorkerError.workerNotFound(Int)
```

## Common pitfalls

| Issue | Fix |
|-------|-----|
| Handle used on wrong worker | Worker affinity is validated; use matching `worker:` index |
| `withProcessPool` not used | `defer { Task { await pool.shutdown() } }` is fire-and-forget — use `withProcessPool` |
| Worker not found at startup | `swift build --product SwiftPythonWorker` |
| Pool hangs at high memory | ResourceMonitor thresholds gate spawning; check `resourceSnapshot()` |
