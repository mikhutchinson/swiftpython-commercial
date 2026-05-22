# Chapter 4 - ProcessPool

`PythonProcessPool` runs Python work in separate worker processes. Each worker
has its own interpreter and GIL, so CPU-bound Python can run in parallel and
native extension crashes do not take down your app process.

Use a process pool for model inference, data transforms, document processing,
image/video pipelines, and any workload that is too heavy or risky for
`Python.run`.

## Creating a Pool

```swift
let pool = try await PythonProcessPool(workers: 4)
defer { Task { await pool.shutdown() } }
```

For scoped lifetimes, prefer `withProcessPool`:

```swift
try await withProcessPool(workers: 4) { pool in
    let value: Double = try await pool.invokeResult(
        module: "math",
        function: "sqrt",
        args: [.python(144.0)]
    )
    print(value)
}
```

`withProcessPool` awaits shutdown on success and error paths.

## Configuration

```swift
let ipc = IPCConfiguration(
    receiveTimeout: 60,
    streamKeepaliveInterval: 5,
    maxPayloadBytes: 32 * 1024 * 1024
)

let pool = try await PythonProcessPool(
    workers: PythonProcessPool.recommended(for: .cpuBound),
    workerExecutablePath: "/path/to/SwiftPythonWorker",
    ipc: ipc,
    maxRespawns: 3,
    resourceLimits: WorkerResourceLimits(maxMemoryBytes: 4 * 1024 * 1024 * 1024),
    backpressure: .suspend(maxInFlight: 32)
)
```

The common knobs are:

| Option | Use |
|--------|-----|
| `workers` | Number of worker processes |
| `workerExecutablePath` | Explicit sidecar path when auto-discovery is not enough |
| `ipc` | Timeouts, payload caps, protocol version, keepalive |
| `maxRespawns` | Crash recovery budget per worker |
| `resourceLimits` | Per-worker memory guardrails |
| `backpressure` | What to do when too many commands are in flight |
| `resourceMonitor` | Darwin memory/thermal sampling and throttling |

## Core Calls

The pool has three verbs:

| Verb | Purpose |
|------|---------|
| `eval` | Run Python code in the worker namespace |
| `invoke` | Import a module and call a module-level function |
| `method` | Call a method on an object referenced by a handle |

Each verb has two result shapes:

| Shape | Returns | Use when |
|-------|---------|----------|
| `eval` / `invoke` / `method` | `PyHandle` | The Python object should stay on the worker |
| `evalResult` / `invokeResult` / `methodResult` | `T: PythonConvertible` | You want a Swift value back |

```swift
let arr = try await pool.invoke(
    module: "numpy",
    function: "arange",
    args: [.python(10_000)]
)

let count: Int = try await pool.methodResult(handle: arr, name: "__len__")
let total: Double = try await pool.methodResult(handle: arr, name: "sum")

try await pool.release(arr)
```

## Persistent Worker Namespace

`eval` calls on the same worker share a namespace, similar to a REPL.

```swift
_ = try await pool.eval("x = 21", worker: 0)
let doubled: Int = try await pool.evalResult("x * 2", worker: 0)
```

Prefer module functions for application code you own. Use `eval` for setup,
quick glue, or expressions that are easier to keep local.

## Worker Affinity

Handles live on the worker that created them. Use `WorkerContext` when a flow
needs to stay pinned.

```swift
let worker = pool.worker(0)

let model = try await worker.invoke(
    module: "my_model",
    function: "load",
    args: [.python("/models/model.bin")]
)

let output: [Double] = try await worker.methodResult(
    handle: model,
    name: "predict",
    args: [.python([[0.1, 0.2, 0.3]])]
)
```

`WorkerContext` is also useful for warming worker-local imports, caches, and
GPU state.

## Remote Arguments

Most pool APIs accept `[RemotePythonValue]` for arguments and
`[String: RemotePythonValue]` for keyword arguments.

```swift
let values = [1.0, 2.0, 3.0, 4.0]

let mean: Double = try await pool.invokeResult(
    module: "statistics",
    function: "fmean",
    args: [.python(values)]
)
```

Keyword arguments use a dictionary:

```swift
let rounded: Double = try await pool.invokeResult(
    module: "builtins",
    function: "round",
    args: [.python(3.14159)],
    kwargs: ["ndigits": .python(2)]
)
```

`WorkerContext` additionally offers builder overloads when that reads better:

```swift
let worker = pool.worker(0)
let rounded: Double = try await worker.invokeResult(
    module: "builtins",
    function: "round"
) {
    3.14159
} kwargs: {
    ("ndigits", 2)
}
```

## Lifecycle

```swift
try await pool.warmup("import numpy as np")
let health: [Bool] = try await pool.healthCheck()

try await pool.drain(timeout: .seconds(30))
pool.resume()

try await pool.respawnWorker(0, reason: .userInitiated, force: true)
await pool.shutdown()
```

| API | Use |
|-----|-----|
| `warmup` | Run setup code on every worker |
| `healthCheck` | Confirm workers respond |
| `drain` | Stop accepting new work and wait for in-flight work |
| `resume` | Leave drained mode |
| `respawnWorker` | Replace a worker, optionally force-killing it |
| `shutdown` | Stop all workers |

## Events

`pool.events(bufferSize:)` returns an independent `AsyncStream<PoolEvent>` for
each subscriber.

```swift
Task {
    for await event in pool.events() {
        switch event {
        case .workerRespawned(let id, _, let newPID, _, let reason):
            logger.warning("worker \(id) respawned as \(newPID): \(String(describing: reason))")
        case .workerDied(let id, let reason):
            logger.error("worker \(id) died: \(String(describing: reason))")
        case .eventsDropped(let count):
            logger.warning("missed \(count) pool events")
        case .poolStateChanged(_, .shutdown):
            return
        default:
            break
        }
    }
}
```

Event cases cover worker spawn/respawn/death, worker state changes,
quarantine, idle shedding, pool state changes, drain completion, resource
pressure, orphaned callbacks, and dropped subscriber events.

## Backpressure

```swift
let rejecting = try await PythonProcessPool(
    workers: 4,
    backpressure: .reject(maxInFlight: 16)
)

let suspending = try await PythonProcessPool(
    workers: 4,
    backpressure: .suspend(maxInFlight: 16)
)
```

Use `.suspend` for app workflows where callers can wait. Use `.reject` when you
need immediate overload feedback and can retry at a higher level.

## Resource Monitoring

On Darwin, the resource monitor can throttle submissions during memory or
thermal pressure.

```swift
let monitor = ResourceMonitorConfig(
    memoryPressureThrottle: 0.85,
    memoryPressureReject: 0.95,
    thermalThrottleLevel: .fair,
    enabled: true
)

let pool = try await PythonProcessPool(
    workers: 4,
    resourceMonitor: monitor
)

if let snapshot = await pool.resourceSnapshot() {
    print(snapshot.freeMemoryBytes)
}
```

Resource samples can also be broadcast through `pool.events()` by setting
`IPCConfiguration.broadcastResourceSamples`.

## Worker Discovery and Packaging

The process pool needs the `SwiftPythonWorker` binary from the same commercial
release as the XCFramework.

During development, either keep the package checkout available or pass an
explicit path:

```swift
let pool = try await PythonProcessPool(
    workers: 2,
    workerExecutablePath: "/absolute/path/to/SwiftPythonWorker"
)
```

For `.app` bundles, copy `SwiftPythonWorker` into
`YourApp.app/Contents/MacOS/` and re-sign it with the provided entitlement
template. The root README has the app bundle and signing commands.

Do not mix worker binaries from older tags with a newer XCFramework. Protocol
mismatches fail fast as `PythonWorkerError.protocolError`.

If pool initialization fails after any worker has already spawned or attached,
the runtime shuts those partial workers down before returning the error. This
keeps protocol-handshake failures from leaking sidecar processes into later
teardown.

## Errors Worth Handling

| Error | Meaning |
|-------|---------|
| `.pythonException(type:message:traceback:workerID:)` | Python raised an exception |
| `.backpressure(inFlight:maxInFlight:)` | The selected backpressure policy rejected work |
| `.timeout(workerID:seconds:)` | Worker did not respond within the configured timeout |
| `.workerCrashed(workerID:exitCode:)` | Worker exited during a command |
| `.workerForciblyRespawned(workerID:)` | Caller killed the worker explicitly |
| `.staleHandle(handleID:workerID:)` | Handle belonged to an older worker generation |
| `.workerNotFound(searchedPaths:)` | `SwiftPythonWorker` could not be located |
| `.protocolError(String)` | Runtime and sidecar protocol mismatch |

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Worker cannot start in packaged app | Copy and re-sign `SwiftPythonWorker` inside `Contents/MacOS` |
| Calls fail after worker respawn | Recreate worker-owned objects; old handles are stale |
| Large results hit payload limits | Return a handle or use shared memory instead of pickling the full object |
| Oversized command fails as protocol corruption | Update to 0.5.1 or newer; channel-0 decode failures now route to the sole waiter and surface the typed payload error |
| UI blocks waiting for a pool call | Keep pool use behind an actor/task and update UI from the main actor |
| Multiple tenants need isolation | Use `SandboxPool` instead of a shared process pool |
