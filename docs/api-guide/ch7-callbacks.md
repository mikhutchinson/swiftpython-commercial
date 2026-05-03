# Chapter 7 - Callbacks

Callbacks let Python code running in a worker call Swift. Use them for host
services, progress decisions, scoring functions, cancellation policy, streaming
data sources, or any operation that must stay in Swift while Python controls the
loop.

Pool callbacks are registered on `PythonProcessPool` and reinstalled when a
worker respawns.

## Synchronous Callbacks

```swift
let registration = try await pool.registerCallback(name: "add") {
    @Sendable (a: Int, b: Int) -> Int in
    a + b
}

let value: Int = try await pool.evalResult("""
import swift_bridge
swift_bridge.call("add", 3, 7)
""")
```

Keep `registration` alive for as long as Python should be able to call it.
`CallbackRegistration` unregisters when it is deallocated.

Typed callback overloads support one-argument and two-argument forms. There is
also a raw `[Any]` form for dynamic argument lists:

```swift
let anyArgs = try await pool.registerCallback(name: "describe") {
    @Sendable (args: [Any]) -> Any in
    "received \(args.count) args"
}
```

Use simple JSON-compatible values for cross-process callbacks: numbers, strings,
booleans, and arrays of those values. For large data, pass a handle or file path
and let Python fetch the data where it already lives.

## Reentrant Callbacks

Use a reentrant callback when Swift needs to call back into the same worker that
triggered the callback.

```swift
let registration = try await pool.registerReentrantCallback(name: "objective") {
    @Sendable (ctx: WorkerCallbackContext, params: [Double]) -> Double in
    let baseline: Double = try ctx.evalResult("current_baseline_score()")
    return baseline + penalty(params)
}
```

`WorkerCallbackContext` gives you same-worker access:

| API | Use |
|-----|-----|
| `workerID` | Identify the worker that called Swift |
| `eval` | Run code and keep a handle descriptor on that worker |
| `evalResult` | Run code and return a Swift value |
| `release(id:)` | Release a handle created through the context |
| `sendNestedCommand` | Low-level escape hatch for custom integrations |

Reentrant callbacks avoid deadlocks by routing nested work through the worker's
callback-safe path.

## Raw Callbacks

Use raw callbacks when your Python side already serializes data.

```swift
let registration = try await pool.registerRawCallback(name: "uppercase_json") {
    @Sendable data in
    let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
    let text = object?["text"] as? String ?? ""
    return try JSONSerialization.data(withJSONObject: ["text": text.uppercased()])
}
```

Raw reentrant callbacks receive `WorkerCallbackContext` as a second argument.

## Streaming Callbacks

Streaming callbacks let Swift provide an iterator to Python.

```swift
let registration = try await pool.registerStreamingCallback(name: "numbers") {
    @Sendable (count: Int) throws -> StreamingCallbackIterator in
    StreamingCallbackIterator(bufferCapacity: 8) { yield in
        for i in 0..<count {
            let data = try JSONSerialization.data(withJSONObject: i)
            try yield(data)
        }
    }
}
```

Python side:

```python
import swift_bridge

for item in swift_bridge.call_stream("numbers", 100):
    print(item)
```

Iterator options:

| Initializer | Behavior |
|-------------|----------|
| `StreamingCallbackIterator(produce:)` | Eager producer; simple but can buffer all output |
| `StreamingCallbackIterator(bufferCapacity:produce:)` | Bounded producer; `yield` can throw on cancellation |

Prefer the bounded initializer for unbounded or large streams.

## Python `swift_bridge`

Inside worker Python code:

```python
import swift_bridge

swift_bridge.call(name, *args, **kwargs)
swift_bridge.call_stream(name, *args)
swift_bridge.is_registered(name)
swift_bridge.registered_names()
swift_bridge.progress("optional hint")
swift_bridge.check_cancel()
```

`progress` and `check_cancel` are covered in
[Chapter 5](ch5-streaming.md). They are useful inside Python generators even
when no Swift callback is registered.

## Error Propagation

| Swift side | Python side |
|------------|-------------|
| Callback throws | Python receives a runtime error |
| Callback name missing | Python receives a key error |
| Argument conversion fails | Python receives a type error |
| Reentrant Python call fails | Error propagates through the callback |

Design callback errors as part of your API. If Python can recover, throw clear
messages and catch them in Python.

## Callback Lifetime

Store registrations in the owning service:

```swift
actor HostBridge {
    private let pool: PythonProcessPool
    private var registrations: [CallbackRegistration] = []

    init(pool: PythonProcessPool) {
        self.pool = pool
    }

    func install() async throws {
        let log = try await pool.registerCallback(name: "host_log") {
            @Sendable (message: String) -> Bool in
            print(message)
            return true
        }
        registrations.append(log)
    }
}
```

Explicit removal is also available:

```swift
try await pool.unregisterCallback(name: "host_log")
```

## Observability

When a worker dies with callbacks in flight, `pool.events()` emits
`.callbackOrphaned`. This is diagnostic information; the original pool command
still fails through its normal error path.

```swift
Task {
    for await event in pool.events() {
        if case .callbackOrphaned(let workerID, _, let name, let kind, _) = event {
            logger.warning("callback \(name) on worker \(workerID) orphaned: \(String(describing: kind))")
        }
    }
}
```

Use this to annotate logs, cancel dependent UI work, or explain why a callback
never returned.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Callback stops working unexpectedly | Keep the returned `CallbackRegistration` alive |
| Large payloads make callbacks slow | Pass handles, file paths, or shared memory instead |
| Callback needs same-worker Python state | Use `registerReentrantCallback` |
| Python iterator should stop when consumer leaves | Use bounded `StreamingCallbackIterator` and handle thrown cancellation |
| Callback errors are hard to debug | Include stable callback names and log `.callbackOrphaned` events |
