# Chapter 3 — Concurrency & Handles

## `PythonExecutor` — the GIL-owning actor

All in-process Python work runs on a single dedicated Python thread managed by `PythonExecutor.shared`. The actor dispatches closures to `PythonThreadExecutor`'s dedicated thread via `runOnThreadAsync`, which acquires/releases the GIL and preserves TLS state for PyTorch/pybind11.

> **Note:** `run` / `execute` / `Python.run` are `async throws` (not `rethrows`) because errors are transported across threads. Closures are `@escaping @Sendable`. All callsites need `try await`.

```swift
// Preferred — via Python.run (delegates to executor)
let result = try await Python.run { /* GIL held on dedicated thread */ }

// Direct — when you need TLS/thread continuity (e.g. PyTorch training loops)
let result = try await PythonExecutor.shared.run {
    // same thread across repeated calls
}

// Typed convenience
let count: Int = try await PythonExecutor.shared.execute {
    try Python.import("os").cpu_count()
}
```

**Rule:** Use `Python.run` for most cases. Use `PythonExecutor.shared.run` only when you need TLS/thread continuity across multiple calls (e.g. PyTorch training state).

## `withGIL` — sync GIL helper

For non-async contexts that already run on the Python thread:

```swift
let value = withGIL {
    // GIL acquired for this scope
    PyObjectRef.none
}
```

## `PyHandle` — cross-boundary object reference

`PyObjectRef` cannot leave the Python thread. `PyHandle` is its `Sendable` counterpart — an opaque token you can freely pass across actors and task boundaries.

```swift
// Store → get a handle
let handle: PyHandle = try await PythonExecutor.shared.store(someRef)

// Use → scoped access
let result: String = try await PythonExecutor.shared.withObject(handle) { ref in
    try String(pythonObject: ref)
}

// Multi-object scoped access
try await PythonExecutor.shared.withObjects(h1, h2) { ref1, ref2 in
    // both refs valid here
}

// Release when done
try await PythonExecutor.shared.release(handle)
```

### `PyHandle` anatomy

```swift
public struct PyHandle: Sendable, Hashable {
    public let id: UUID
    public let processID: PyHandleProcessID?  // nil = main process
    public let sharedMemory: PyHandleSharedMemoryRef?
    public var isMainProcess: Bool
    public var isShared: Bool
}

public enum PyHandleProcessID: Sendable, Hashable {
    case main
    case worker(index: Int, generation: UInt64)
}
```

A handle with `processID == .worker(index:generation:)` is owned by a specific ProcessPool worker. Pass it back to the **same pool** via `pool.method(handle:…)` — cross-worker handle use is rejected with a diagnostic error.

### `storeSync` — sync store for app startup code

```swift
let handle = PythonExecutor.storeSync(ref)  // no await needed
```

## `OwnedPyHandle` — auto-release handles (v0.2.0+ / Phase B2)

`OwnedPyHandle` is a `final class` wrapper around `PyHandle` whose `deinit`
automatically releases the underlying Python object. Eliminates the
`defer { Task { try? await pool.release(h) } }` boilerplate and the leak risk
on `throw` paths.

```swift
do {
    let model = try await pool.evalOwned("load_model('foo')")
    let result: Float = try await pool.method(
        handle: model, name: "predict", args: [.python(input)]
    )
    // exit scope: model deinit fires, release dispatched
}
```

### Constructors

| Method | Returns |
|--------|---------|
| `pool.evalOwned(_:bindings:timeout:)` | `OwnedPyHandle` |
| `pool.evalOwned(_:bindings:worker:timeout:)` | `OwnedPyHandle` |
| `pool.invokeOwned(module:function:args:kwargs:timeout:)` | `OwnedPyHandle` |
| `pool.invokeOwned(module:function:args:kwargs:worker:timeout:)` | `OwnedPyHandle` |
| `pool.methodOwned(handle:name:args:kwargs:timeout:)` | `OwnedPyHandle` |
| `pool.methodOwned(handle:name:args:kwargs:worker:timeout:)` | `OwnedPyHandle` |

Each `*Owned` method has a sibling that takes `OwnedPyHandle` for the target
or bindings:

```swift
// Drop-in: pass OwnedPyHandle directly, no .handle unwrap
let lengthHandle = try await pool.method(handle: model, name: "__len__")

// Bindings can be [String: OwnedPyHandle]
let owned = try await pool.evalOwned(
    "sum(model)", bindings: ["model": model]
)

// Streams accept it too
let stream: CancellableStream<Int> = try await pool.methodStream(
    handle: model, name: "__iter__", options: .pinned(worker: 0)
)
```

### Explicit release

For deterministic early release inside a scope:

```swift
let h = try await pool.evalOwned("expensive_temp()")
let result = try await pool.method(handle: h, name: "compute")
try await h.release()       // free `h` now, before downstream long work
try await longDownstreamWork(result)
```

`release()` is idempotent — calling twice no-ops the second call. After
`release()`, the wrapper's `deinit` becomes a no-op.

### When to use raw `PyHandle` instead

`OwnedPyHandle` is the right default. Use raw `PyHandle` when:

- The handle's lifetime crosses logical boundaries the type system can't
  easily express (long-lived caches, manual transfer-of-ownership patterns).
- You want explicit lifetime visible in the call site (e.g. for diagnostic
  logging at release time).
- You're working in a context where ARC's release timing is inconvenient
  (typically rare).

`OwnedPyHandle.handle` exposes the underlying `PyHandle` for explicit
unwrap when an API requires it.

## Concurrency patterns

### Independent parallel work

```swift
async let r1 = Python.run { try compute1() }
async let r2 = Python.run { try compute2() }
let (a, b) = try await (r1, r2)
```

Each `Python.run` hops to the executor; Swift schedules concurrency around the GIL.

### Actor-isolated model with PyHandle

```swift
actor ModelService {
    private var modelHandle: PyHandle?

    func load() async throws {
        modelHandle = try await Python.run {
            let sklearn = try Python.import("sklearn.ensemble")
            return try PythonExecutor.shared.store(try sklearn.RandomForestClassifier())
        }
    }

    func predict(_ data: [Double]) async throws -> Int {
        guard let h = modelHandle else { throw ... }
        return try await PythonExecutor.shared.withObject(h) { model in
            let result = try model.predict([data])
            return try Int(pythonObject: result[0])
        }
    }
}
```

## Common pitfalls

| Issue | Fix |
|-------|-----|
| `PyObjectRef` captured across `await` | Store it first → `PyHandle` |
| Handle used on wrong worker | Use `pool.method(handle:worker:)` with matching worker index |
| `storeSync` called outside Python thread | Only safe during init or `Python.run` context |
