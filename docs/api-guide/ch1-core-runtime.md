# Chapter 1 - Core Runtime

Use the core runtime when you want Python in the current process: lightweight
scripts, direct access to Python packages, custom conversion, or setup work that
does not need a separate worker process.

For CPU-heavy, crash-prone, or tenant-isolated work, prefer `PythonProcessPool`
or `SandboxPool` instead.

## Minimal In-Process Call

```swift
import SwiftPythonRuntime

let message: String = try await Python.run {
    let json = try Python.import("json")
    let payload = try pyDict(("language", "Swift"), ("runtime", "Python"))
    let encoded = try json.dumps(payload)
    return try String(pythonObject: encoded)
}
```

`Python.run` is the primary async entry point:

- The closure executes on SwiftPython's dedicated Python thread.
- The GIL is held while the closure runs.
- The closure is synchronous; do not `await` inside it.
- The return value must be `Sendable`.

Keep `Python.run` closures narrow. Do Python work inside the closure, convert or
store the result, and return a Swift value or `PyHandle`.

## Importing Modules

```swift
let version: String = try await Python.run {
    let sys = try Python.sys
    return try String(pythonObject: sys.version)
}

let sqrt: Double = try await Python.run {
    let math = try Python.math
    return try Double(pythonObject: try math.sqrt(144.0))
}

let npMean: Double = try await Python.run {
    let np = try Python.import("numpy")
    let arr = try np.array([1.0, 2.0, 3.0, 4.0])
    return try Double(pythonObject: try np.mean(arr))
}
```

Convenience module properties include `sys`, `os`, `asyncio`, `builtins`,
`json`, `io`, `re`, `math`, `datetime`, `pathlib`, and `collections`. For any
other package, use `Python.import("package_name")`.

The package must be importable by the Python 3.13 runtime your app launches
with. See the root README for `PYTHONHOME`, app bundle, and linker setup.

## `PyObjectRef`

`PyObjectRef` wraps a CPython object pointer and gives you dynamic access:

```swift
let count: Int = try await Python.run {
    let pathlib = try Python.pathlib
    let path = try pathlib.Path("/tmp")
    let name = try path.name
    return try Int(pythonObject: try Python.len(name))
}
```

Supported operations:

| Operation | Example |
|-----------|---------|
| Attribute lookup | `try object.name` or `try object.getAttribute("name")` |
| Function or method call | `try callable(1, 2)` or `try callable.call(args: [...])` |
| Indexing | `try object[0]`, `try object[pyKey: "key"]` |
| Assignment | `try object.setAttribute("name", value: ref)` |
| Python display | `try Python.str(ref)`, `try Python.repr(ref)`, `try Python.type(ref)` |

`PyObjectRef` is for GIL-held scopes. Do not keep it in long-lived Swift state or
move it across `await` boundaries. Store it as a `PyHandle` when you need a
sendable reference; see [Chapter 3](ch3-concurrency-handles.md).

## Context Managers

Use `withPythonContext` for Python APIs that normally use `with ...:`.

```swift
let contents: String = try await Python.run {
    let builtins = try Python.builtins
    let file = try builtins.open("/tmp/input.txt", "r")

    return try withPythonContextSync(file) { handle in
        try String(pythonObject: try handle.read())
    }
}
```

Async variants are available when the context manager object itself is modeled
outside a `Python.run` closure:

```swift
try await withPythonContext(contextManager) { resource in
    // resource is the Python __enter__ return value
}
```

If Python suppresses an exception in `__exit__`, Swift throws
`ContextManagerExceptionSuppressed` with the original Swift error attached.

## Python Errors

Python exceptions surface as `PythonError`.

```swift
do {
    _ = try await Python.run {
        try Python.import("definitely_missing_package")
    }
} catch PythonError.importError(let message) {
    print("Import failed: \(message)")
} catch PythonError.exception(let type, let message) {
    print("\(type): \(message)")
}
```

For low-level CPython calls, `PythonError.fetch()` and
`PythonError.fetchDetailed()` read and clear the current Python error indicator.
Most application code should not need them.

## Raw C API Escape Hatch

The XCFramework exposes a small CPython C API surface for integration code that
must talk to lower-level Python constructs. Prefer `Python.run`, `PyObjectRef`,
and `PythonConvertible` first. Use raw C calls only when you have a specific
reason and are already inside a GIL-held scope.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| `PyObjectRef` used after an `await` | Convert to a Swift value or store as `PyHandle` before leaving the GIL-held scope |
| Import works in Terminal but not Finder launch | Set `PYTHONHOME` and app launch environment for the bundled/Homebrew Python |
| Python package missing at runtime | Install or bundle it for the same Python 3.13 environment the app uses |
| CPU-bound code blocks the app process | Move it to `PythonProcessPool` |
| Native extension crash takes down the app | Run that workload in a worker process or VM tenant |
