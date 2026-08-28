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

do {
    try await pool.drain(timeout: .seconds(30))
} catch PythonWorkerError.drainTimedOut(let inFlight, let seconds) {
    logger.warning("drain still has \(inFlight) operations after \(seconds)s")
    // Admission remains closed. Explicitly resume, await later completion,
    // or shut down; do not infer that the pool drained.
}
pool.resume()

await pool.shedIdleWorkersAndWait(force: true)
try await pool.respawnWorker(0, reason: .userInitiated, force: true)
await pool.shutdown()
```

| API | Use |
|-----|-----|
| `warmup` | Run setup code on every worker |
| `healthCheck` | Confirm workers respond |
| `drain` | Stop accepting new work and wait for authoritative in-flight work; timeout fails only that wait |
| `resume` | Leave drained mode |
| `shedIdleWorkers` | Start an idle shed while preserving the existing synchronous compatibility entry point |
| `shedIdleWorkersAndWait` | Shed idle workers and await exact custom-transport endpoint release |
| `respawnWorker` | Replace a worker, optionally force-killing it |
| `addWorkers` | Add local process-backed workers through the same configuration path |
| `shutdown` | Stop all workers |

A `drain(timeout:)` timeout throws
`PythonWorkerError.drainTimedOut(inFlight:seconds:)`. It does not reopen
admission or claim that the outstanding work stopped: the pool remains in
`.draining` until the authoritative count reaches zero, or until the caller
explicitly resumes or shuts down. Cancelling the drain waiter has the same
waiter-only scope.

The synchronous `shedIdleWorkers(force:)` remains source compatible. For a
custom transport its endpoint release can continue after that call returns.
Use `shedIdleWorkersAndWait(force:)` when subsequent work depends on exact
release completion. Neither form sheds a busy worker, even with `force: true`.

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
        case .hostAsyncCallbackQuiescenceTimedOut(let id, let activeCount):
            logger.warning("worker \(id) retained \(activeCount) callback tasks")
        case .workerLifecycleQuiescenceTimedOut(let ids):
            logger.warning("worker lifecycle still retiring: \(ids)")
        case .workerTransportCleanupTimedOut(let ids):
            logger.warning("transport cleanup still owned in background: \(ids)")
        case .workerTransportCleanupFailed(let ids, let messages):
            logger.error("transport cleanup failed for \(ids): \(messages)")
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
pressure, orphaned callbacks, lifecycle quiescence, transport cleanup, and
dropped subscriber events.

`hostAsyncCallbackQuiescenceTimedOut`,
`workerLifecycleQuiescenceTimedOut`, and
`workerTransportCleanupTimedOut` can accompany a bounded shutdown that has
already transitioned the public pool state to `.shutdown`. Exact invalidated
operations and endpoints remain owned by their retirement receipts; these
events are not permission to signal a numeric PID, retry a destructive
operation, or attach an old endpoint to a new generation.
`workerTransportCleanupFailed` means retirement completed but at least one
endpoint or managed-backend cleanup step reported an error.

## Structured Telemetry

Use `pool.telemetry(bufferSize:)` when the host needs authoritative worker
diagnostics instead of reconstructing lifecycle boundaries around each call.
Telemetry is optional and low overhead; if nobody subscribes, the pool does not
build per-command event payloads.

```swift
let telemetryTask = Task {
    for await event in pool.telemetry() {
        switch event {
        case .commandEnded(let command):
            if case .failure(let diagnostic)? = command.outcome {
                logger.error("""
                span=\(command.spanID) surface=\(command.descriptor.surface.rawValue) \
                worker=\(command.worker.workerID) pid=\(command.worker.pid) \
                classification=\(diagnostic.classification.rawValue) \
                noTraceback=\(diagnostic.noPythonTracebackCaptured)
                """)
            }
        case .respawn(let respawn):
            logger.warning("""
            worker \(respawn.workerID) respawned \
            oldPID=\(respawn.oldPID) newPID=\(respawn.newPID) \
            reason=\(String(describing: respawn.reason)) \
            crashEvidence=\(respawn.crashEvidenceExists)
            """)
        case .poolStateChanged(let state) where state.to == .shutdown:
            return
        default:
            break
        }
    }
}

let hostContext = ProcessPoolTelemetryContext(
    traceID: "session-42",
    metadata: [
        "active_turn_id": "turn-7",
        "app_lifecycle_state": "switching",
        "host_command_name": "summarize"
    ]
)

let summary: String = try await pool.invokeResult(
    module: "summarizer",
    function: "run",
    args: [.python("document-id-123")],
    telemetry: hostContext
)

telemetryTask.cancel()
```

For broader scopes, wrap work in `ProcessPoolTelemetry.withContext(...)`.
Explicit `telemetry:` arguments win over task-local context.

```swift
try await ProcessPoolTelemetry.withContext(hostContext) {
    _ = try await pool.eval("import numpy as np")
    _ = try await pool.invoke(module: "numpy", function: "arange", args: [.python(10)])
}
```

Telemetry events include:

| Event | Carries |
|-------|---------|
| `.commandStarted` / `.commandEnded` / `.commandRejected` | Span id, descriptor, worker id, PID, generation, channel, timestamps, duration, outcome |
| `.callback` | Registration, invocation, timeout, orphan, unregister, callback kind, duration, inherited context |
| `.stream` | Stream id, channel, active/first chunk/last chunk/timeout/cancel/termination state |
| `.sideChannel` | Submitted, accepted, unavailable, or timeout/no-response side-channel state |
| `.respawn` | Reason, old/new PID, generation, causing span, and whether crash evidence exists |
| `.poolStateChanged` / `.workerStateChanged` | Running, draining, shutting down, shutdown, degraded/quarantined worker states where available |

`ProcessPoolErrorDiagnostic` classifies failures from runtime state, not log
strings. Python exceptions include type, message, and traceback when the worker
captured one. Timeout, no-response, transport, supervisor, and crash evidence
events set `noPythonTracebackCaptured` explicitly when no traceback exists.

Telemetry deliberately omits command payloads, prompts, callback arguments,
tool output, and arbitrary Python values. Host metadata is echoed as opaque
strings only; SwiftPython does not interpret it.

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

The current public sidecar is built for Apple Silicon. If you need Intel Mac
worker execution, build and ship a matching `SwiftPythonWorker` for that
architecture with the same runtime release.

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
| `.drainTimedOut(inFlight:seconds:)` | This bounded drain wait expired; admission remains closed in `.draining` |
| `.staleHandle(handleID:workerID:)` | Handle belonged to an older worker generation |
| `.workerNotFound(searchedPaths:)` | `SwiftPythonWorker` could not be located |
| `.protocolError(String)` | Runtime and sidecar protocol mismatch |

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Worker cannot start in packaged app | Copy and re-sign `SwiftPythonWorker` inside `Contents/MacOS` |
| `Bad CPU type in executable` launching the worker | Use Apple Silicon for the shipped sidecar, or ship a matching Intel worker |
| Calls fail after worker respawn | Recreate worker-owned objects; old handles are stale |
| Large results hit payload limits | Return a handle or use shared memory instead of pickling the full object |
| Oversized command looks like protocol corruption | The current release routes channel-0 decode failures to the sole waiter as the typed payload error; verify that runtime and worker are from one tag |
| UI blocks waiting for a pool call | Keep pool use behind an actor/task and update UI from the main actor |
| Multiple tenants need isolation | Use `SandboxPool` instead of a shared process pool |
