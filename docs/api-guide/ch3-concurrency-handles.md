# Chapter 3 - Concurrency & Handles

Swift concurrency and CPython have different ownership rules. The safe pattern
is simple:

- Use `Python.run` for in-process GIL-held work.
- Do not move `PyObjectRef` across `await` boundaries.
- Store long-lived Python objects as `PyHandle` or `OwnedPyHandle`.
- Keep worker-owned handles on the pool and worker that created them.

## The Python Executor

`PythonExecutor.shared` owns the dedicated Python thread. `Python.run` delegates
to it and is the right entry point for most application code.

```swift
let version: String = try await Python.run {
    try String(pythonObject: Python.sys.version)
}
```

Use `PythonExecutor.shared.run` directly only when you are building a wrapper
that needs explicit executor access:

```swift
let result: Int = try await PythonExecutor.shared.run {
    let os = try Python.os
    return try Int(pythonObject: try os.cpu_count())
}
```

Both APIs are `async throws` because the closure is executed on the Python
thread and any thrown error must cross an executor boundary.

## `withGIL`

`withGIL` is a synchronous helper for code that already knows it is allowed to
touch CPython directly.

```swift
let none = withGIL {
    PyObjectRef.none
}
```

Application code should rarely need it. Prefer `Python.run` unless you are
inside low-level bridging code.

## `PyHandle`

`PyHandle` is a sendable token for a Python object. It does not expose the
object directly; it lets SwiftPython find that object later on the correct
Python executor or worker.

```swift
let handle: PyHandle = try await Python.run {
    let json = try Python.json
    let obj = try json.loads(#"{"count": 3}"#)
    return PythonExecutor.storeSync(obj)
}

let count: Int = try await PythonExecutor.shared.withObject(handle) { obj in
    try Int(pythonObject: obj[pyKey: "count"])
}

try await PythonExecutor.shared.release(handle)
```

Use `storeSync` inside `Python.run` because the closure itself is synchronous.
Use `PythonExecutor.shared.store(_:)` only from code that already has a
`PyObjectRef` in an executor-safe context.

## Worker-Owned Handles

`PythonProcessPool` returns handles for objects that live in worker processes.

```swift
let pool = try await PythonProcessPool(workers: 2)
let arr = try await pool.invoke(
    module: "numpy",
    function: "arange",
    args: [.python(1_000_000)]
)

let total: Double = try await pool.methodResult(handle: arr, name: "sum")
try await pool.release(arr)
await pool.shutdown()
```

A worker handle carries the worker index and generation that created it. Use it
with the same pool. If you pin follow-up work manually, pin it to the same
worker.

## `OwnedPyHandle`

`OwnedPyHandle` is the default choice for remote objects whose lifetime should
follow Swift scope. It releases the remote object automatically when the wrapper
is deallocated.

```swift
try await withProcessPool(workers: 2) { pool in
    let model = try await pool.evalOwned("load_model()")

    let output: [Double] = try await pool.methodResult(
        handle: model,
        name: "predict",
        args: [.python([[0.1, 0.2, 0.3]])]
    )

    print(output)
} // model release is scheduled when it leaves scope
```

`PyHandle` and `OwnedPyHandle` both conform to `HandleConvertible`, so most
handle-taking APIs accept either form.

```swift
let matrix = try await pool.invokeOwned(
    module: "numpy",
    function: "eye",
    args: [.python(4)]
)

let trace: Double = try await pool.methodResult(handle: matrix, name: "trace")
```

For dictionary bindings, use `.handles`:

```swift
let total: Double = try await pool.evalResult(
    "float(matrix.sum())",
    bindings: ["matrix": matrix].handles
)
```

## Deterministic Release

ARC-driven release is convenient, but deterministic cleanup is sometimes useful
for very large objects.

```swift
let temp = try await pool.evalOwned("make_large_temp()")
let result: Double = try await pool.methodResult(handle: temp, name: "score")
try await temp.release()

try await uploadResult(result)
```

`release()` is idempotent. After it succeeds, `deinit` has nothing left to do.

## Temporary Handle Helpers

Use the pool helpers when you want raw `PyHandle` lifetime to be visibly scoped.

```swift
let count: Int = try await pool.withTemporaryHandle(
    createdBy: {
        try await pool.invoke(module: "numpy", function: "arange", args: [.python(100)])
    }
) { handle in
    try await pool.methodResult(handle: handle, name: "__len__")
}
```

Worker contexts also expose `withEvalHandle`, `withInvokeHandle`, and
`withMethodHandle` for worker-pinned flows.

## Actor Pattern

Wrap Python state behind a Swift actor. Store handles, not `PyObjectRef`.

```swift
actor VectorIndex {
    private let pool: PythonProcessPool
    private var index: OwnedPyHandle?

    init(pool: PythonProcessPool) {
        self.pool = pool
    }

    func load(path: String) async throws {
        index = try await pool.invokeOwned(
            module: "my_search",
            function: "load_index",
            args: [.python(path)]
        )
    }

    func search(_ query: [Double]) async throws -> [Int] {
        guard let index else { return [] }
        return try await pool.methodResult(
            handle: index,
            name: "search",
            args: [.python(query)]
        )
    }
}
```

This keeps Python object lifetime explicit and avoids crossing the GIL boundary
with raw references.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Capturing `PyObjectRef` in a `Task` | Convert it to a Swift value or store it as `PyHandle` first |
| Calling `await` inside `Python.run` | Move async work outside the closure; use `storeSync` for handles |
| Reusing a worker handle after respawn | Recreate the object; stale handles are rejected |
| Passing a handle to a different pool | Keep handles private to the pool or actor that created them |
| Relying on ARC for huge temporary arrays | Call `release()` when the array is no longer needed |
