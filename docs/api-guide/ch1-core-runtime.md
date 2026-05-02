# Chapter 1 — Core Runtime

## `Python.run` — the primary async entrypoint

```swift
let result = try await Python.run {
    let np = try Python.import("numpy")
    let arr = try np.array([1, 2, 3])
    return try np.sum(arr)
}
```

- All code inside the closure runs under the GIL on the dedicated Python thread.
- Return value must be `Sendable`.
- Use this for any in-process Python work. Keep the closure as narrow as possible.

## Convenience module properties

```swift
// Available without explicit import:
Python.builtins, .sys, .os, .math, .json, .io, .re,
.datetime, .pathlib, .collections, .asyncio
```

```swift
let sys = try Python.sys
let version = try sys.version  // dynamic member lookup
```

## `PyObjectRef` — in-process Python object handle

`PyObjectRef` is an RAII wrapper around a CPython `PyObject *`. It:
- Decrefs automatically on `deinit` (must be inside GIL)
- Supports `@dynamicMemberLookup` → `try obj.attr`
- Supports `@dynamicCallable` → `try obj(args…)`
- Supports subscript → `obj[idx]`

```swift
let result = try await Python.run {
    let obj = try Python.import("json")
    let encoded = try obj.dumps(["key": "value"])  // dynamic call
    return try String(pythonObject: encoded)
}
```

Use `PyObjectRef` only inside `Python.run {}` or `withGIL {}`. Never store it outside.

## `PythonError` — typed Swift errors from Python exceptions

```swift
public enum PythonError: Error {
    case importError(String)
    case attributeError(String)
    case typeError(String)
    case valueError(String)
    case runtimeError(String)
    case notImplementedError(String)
    case exception(type: String, message: String)
}
```

```swift
do {
    let result = try await Python.run { try Python.import("nonexistent") }
} catch PythonError.importError(let msg) {
    print("Import failed:", msg)
}
```

`PythonError.fetch()` / `fetchDetailed()` — manual fetch from the CPython error indicator (only needed in raw C API usage).

## Context managers (`with` statement equivalent)

```swift
// Typed context manager protocol
try await withPythonContext(someManager) { ctx in
    // ctx is the __enter__ return value
}

// Untyped (PyObjectRef)
try await withPythonContext(fileObj) { f in
    let content = try f.read()
}

// Sync variant
try withPythonContextSync(lockObj) { _ in
    // ...
}
```

If `__exit__` suppresses an exception, Swift receives `ContextManagerExceptionSuppressed`.

## Escape hatch: dynamic access on unbound APIs

When a Python API has no generated binding, use dynamic access and immediately re-wrap:

```swift
let arr: ndarray = try await Python.run {
    let np = try Python.import("numpy")
    let rng = try np.random.default_rng(42)
    return ndarray(pythonObject: try rng.normal(0.0, 1.0, size: 1024))
}
```

Rule: keep dynamic access local; re-wrap into typed wrappers at the boundary.

## Common pitfalls

| Issue | Fix |
|-------|-----|
| Segfault or "GIL not held" | Wrap in `Python.run {}` or `withGIL {}` |
| `PyObjectRef` crossing async boundary | Use `PyHandle` instead (see ch3) |
| `try obj.method` throws `attributeError` | Check spelling; Python names are case-sensitive |
