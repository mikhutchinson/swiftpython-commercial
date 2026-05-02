# Chapter 7 — Bidirectional Callbacks (Python→Swift)

SwiftPython lets Python code call back into Swift across the process boundary. All callbacks are registered on the pool and automatically re-registered on worker respawn.

## Sync callbacks

```swift
// Register
let reg = try await pool.registerCallback(name: "add") {
    @Sendable (a: Int, b: Int) -> Int in a + b
}
// reg is a CallbackRegistration — keeps the callback alive. Store it.
// deinit auto-unregisters from all workers.

// Python side
let result: Int = try await pool.evalResult("""
    import swift_bridge
    swift_bridge.call("add", 3, 7)
""")
// result == 10
```

**Typed overloads:** 0-arg, 1-arg, 2-arg, raw `[Any]` array.
**Supported cross-process types:** `Int`, `Double`, `String`, `Bool`, `[Int]`, `[Double]`, `[String]`.
The pool JSON-encodes args on the worker and JSON-decodes the result back — no GIL needed on the Swift side.

## Reentrant callbacks

When the callback needs to execute Python **on the same worker** that triggered it:

```swift
let reg = try await pool.registerReentrantCallback(name: "objective") {
    @Sendable (ctx: WorkerCallbackContext, params: [Double]) -> Double in
    // Re-enter the same worker — no deadlock
    let value: Double = try ctx.evalResult("rosenbrock(\(params))")
    return value + swiftSideConstraintPenalty(params)
}
```

`WorkerCallbackContext` API:
- `ctx.evalResult<T>(_ code:)` → typed Swift value from Python
- `ctx.eval(_ code:)` → `HandleDescriptor`
- `ctx.release(id:)` — release a handle
- `ctx.workerID` — which worker this is running on
- `ctx.sendNestedCommand(_:timeout:)` — raw IPC (bypass socket lock)

Supports arbitrary nesting: callback → eval → callback → eval → ...

## Streaming callbacks

Register a callback that yields values one at a time back to Python:

```swift
let reg = try await pool.registerStreamingCallback(name: "sensor_stream") {
    (jsonData: Data, _: WorkerCallbackContext) -> StreamingCallbackIterator in
    let args = try JSONSerialization.jsonObject(with: jsonData) as? [Any] ?? []
    let count = (args.first as? NSNumber)?.intValue ?? 100

    return StreamingCallbackIterator { yield in   // eager — all in memory
        for i in 0..<count {
            yield(try JSONSerialization.data(withJSONObject: [i]))
        }
    }
}
```

Python side:
```python
import swift_bridge
for value in swift_bridge.call_stream("sensor_stream", 50):
    print(value)   # 0, 1, 2, ...
```

### Eager vs lazy (bounded) iterators

| | Eager | Lazy |
|---|---|---|
| Memory | O(total items) | O(bufferCapacity) |
| Producer runs on | Same thread | Background thread |
| `yield` signature | `(Data) -> Void` | `(Data) throws -> Void` |
| Backpressure | None | `yield` blocks when buffer full |
| Cancellation | No-op | `yield` throws `CancellationError` |

```swift
// Lazy — bounded memory, producer blocks when full
StreamingCallbackIterator(bufferCapacity: 8) { yield in
    for i in 0..<1_000_000 {
        try yield(try JSONSerialization.data(withJSONObject: [i]))
    }
}
```

Always spell `bufferCapacity:` explicitly (no default, prevents accidental overload resolution).

## Python `swift_bridge` module reference

```python
import swift_bridge

swift_bridge.call(name, *args, **kwargs)        # Sync → blocks until Swift returns
swift_bridge.call_async(name, *args, **kwargs)  # Async → concurrent.futures.Future
swift_bridge.call_stream(name, *args)           # Streaming → Python iterator
swift_bridge.is_registered(name)                # bool
swift_bridge.registered_names()                 # list[str]
```

## Error propagation

| Swift side | Python side |
|---|---|
| `throw SomeError(...)` | `RuntimeError` with error description |
| Callback not registered | `KeyError` |
| Wrong argument type | `TypeError` |
| Reentrant `ctx.evalResult` throws | Propagates through callback stack |

## Lifecycle & cleanup

- `CallbackRegistration` unregisters on `deinit` — store it in your actor/class.
- Worker respawn (crash recovery) re-registers all pool callbacks automatically.
- Explicit removal: `await pool.unregisterCallback(name: "add")`

## Observability — orphan events (v0.2.0+ / Phase C1)

When a worker dies (crash, SIGKILL, idle-shed, respawn, or graceful shutdown) with cross-process callbacks in flight, the pool emits one `PoolEvent.callbackOrphaned(workerID:callID:callbackName:kind:)` event per orphaned callback to every `pool.events()` subscriber.

```swift
Task {
    for await event in pool.events() {
        if case .callbackOrphaned(let workerID, _, let name, let kind) = event {
            logger.warn("callback \(name) (kind: \(kind)) orphaned by worker \(workerID) death")
        }
    }
}
```

`CallbackKind`:
- `.regular` — synchronous request/response handler (`registerCallback` / `registerReentrantCallback` / `registerRawCallback` family).
- `.streaming` — `registerStreamingCallback` iterator. The orphan event carries the user-facing name (e.g. `"sensor_stream"`), NOT the wire init placeholder `"__swift_stream_init__"`.

### What orphan events tell you (and don't)

- **Do** use them to log which named callbacks were live at the moment of worker loss — information the generic `PythonWorkerError.workerCrashed` does not carry.
- **Don't** treat them as a replacement for the existing error path — the parent `eval` / `invoke` / `method` that triggered the callback still throws `PythonWorkerError.workerCrashed` (or similar) through its normal return. Orphan events are observability-only, not a control-flow primitive.

### Excluded callbacks

The internal `__swiftpython_dequeue__` callback (used by `pool.enqueue(...)` for the StreamQueue mechanism) is **deliberately excluded** from orphan reporting. Every Python generator that polls the StreamQueue would otherwise spam events on every dead worker — meaningless noise for an internal mechanism.

## Example scenarios

| Scenario | Proof point |
|----------|-------------|
| Reentrant optimization (scipy.optimize → NumPy on same worker) | `Examples/example_callbacks.swift:103` |
| Competitive parallel search (two workers race, shared Swift state) | `Examples/example_callbacks.swift:185` |
| Adaptive streaming hyperparameter sweep | `Examples/example_callbacks.swift:274` |
| Cross-boundary error recovery | `Examples/example_callbacks.swift:395` |
| Backpressure streaming (50k sensor readings, ring buffer 8) | `Examples/example_callbacks.swift:471` |
