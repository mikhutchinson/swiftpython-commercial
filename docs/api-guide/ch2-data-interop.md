# Chapter 2 - Data Interop

SwiftPython gives you three levels of data exchange:

1. Convert normal Swift values with `PythonConvertible`.
2. Pass remote worker objects by handle with `PyHandle` / `OwnedPyHandle`.
3. Share larger numeric buffers with `PythonBuffer` or pool shared memory.

Use the simplest level that fits the size and lifetime of your data.

## `PythonConvertible`

```swift
public protocol PythonConvertible: Sendable {
    init(pythonObject: PyObjectRef) throws
    func toPythonObject() throws -> PyObjectRef
}
```

Built-in conformances include:

- `Int`, fixed-width integer types, `Float`, `Double`, `Bool`, `String`, `Data`
- `Array`, `Set`, `Dictionary`, and `Optional` when their elements conform
- `PyObjectRef`

```swift
let swiftValue: [Double] = try await Python.run {
    let builtins = try Python.builtins
    let values = try builtins.list([1.0, 2.0, 3.0])
    return try [Double](pythonObject: values)
}
```

## Collection Helpers

Use these inside a GIL-held scope when you need Python containers containing
mixed Swift values:

```swift
let list = try pyList(1, "two", 3.0)
let tuple = try pyTuple("x", 42)
let set = try pySet("red", "green", "blue")
let dict = try pyDict(("name", "Ada"), ("score", 99))
```

For homogeneous Swift arrays and dictionaries, direct `toPythonObject()` usually
reads better.

## Remote Arguments

`PythonProcessPool` calls accept `RemotePythonValue`. It can hold either a
plain converted Swift value or a handle to an object already living on a worker.

```swift
let mean: Double = try await pool.invokeResult(
    module: "statistics",
    function: "mean",
    args: [.python([1.0, 2.0, 3.0, 4.0])]
)

let model = try await pool.evalOwned("load_model()")
let prediction: [Double] = try await pool.methodResult(
    handle: model,
    name: "predict",
    args: [.python([[0.2, 0.4, 0.6]])]
)
```

Convenience initializers let you write `.python(value)` or `.handle(handle)`.
`WorkerContext` also has builder overloads that accept plain
`PythonConvertible` values and `PyHandle` values directly:

```swift
let values: [Double] = [1, 2, 3, 4]
let worker = pool.worker(0)

let result: Double = try await worker.invokeResult(
    module: "statistics",
    function: "fmean"
) {
    values
}
```

For `eval` bindings, only handles are accepted because the binding namespace is
remote:

```swift
let arr = try await pool.invokeOwned(
    module: "numpy",
    function: "array",
    args: [.python([1.0, 2.0, 3.0])]
)

let total: Double = try await pool.evalResult(
    "float(x.sum())",
    bindings: ["x": arr].handles
)
```

## Slices and Indexing

The runtime includes Python-style index helpers for dynamic objects and wrapper
types that expose Python indexing.

```swift
let slice = PythonSlice(start: 1, stop: 10, step: 2)
let matrixIndex = MultiIndex(.index(0), .slice(PythonSlice(stop: 5)))

let value = try await Python.run {
    let np = try Python.import("numpy")
    let arr = try np.arange(20)
    return try [Int](pythonObject: arr[slice])
}
```

## `PythonBuffer`

`PythonBuffer` exposes the Python buffer protocol. It is useful for NumPy
arrays, byte arrays, image buffers, and other contiguous binary data.

```swift
let average: Double = try await Python.run {
    let np = try Python.import("numpy")
    let arr = try np.array([1.0, 2.0, 3.0, 4.0], dtype: "float64")
    let buffer = try PythonBuffer(object: arr)
    defer { buffer.release() }

    return try buffer.withValidatedBufferPointer { (ptr: UnsafeBufferPointer<Double>) in
        ptr.reduce(0, +) / Double(ptr.count)
    }
}
```

Useful members:

| API | Purpose |
|-----|---------|
| `pointer`, `length`, `itemSize` | Raw storage information |
| `shape`, `strides`, `ndim`, `format` | Array metadata |
| `toArray<T>()` | Copy into a Swift array |
| `toData()` | Copy into `Data` |
| `withValidatedBufferPointer` | Scoped typed read-only access |
| `withValidatedMutableBufferPointer` | Scoped typed mutable access |

The buffer is valid only while the underlying Python object is alive and the
buffer has not been released.

## Shared Memory in `PythonProcessPool`

For large numeric arrays that should stay out of pickle payloads, use the pool
shared-memory helpers.

`pool.createSharedTensor(shape:dtype:)` allocates a region in the pool's
`SharedMemoryArena` (POSIX shm) and broadcast-attaches it to every commandable
worker. The returned `PyHandle` carries `PyHandleSharedMemoryRef` metadata —
`shmName`, `offset`, `size`, `shape`, `dtype` — that workers use to `mmap` the
same bytes zero-copy. Pass the handle through `bindings:` to make it visible
as a NumPy array inside any `eval` / `invoke`.

```swift
let shared = try await pool.createSharedTensor(
    shape: [1024, 1024],
    dtype: .float32
)

try await pool.writeToShared(Array(repeating: Float(1), count: 1024 * 1024), to: shared)

let total: Float = try await pool.evalResult("""
import numpy as np
float(x.sum())
""", bindings: ["x": shared])
```

### Direct Host-Side Access with `withSharedBuffer`

`writeToShared` / `readFromShared` are convenience wrappers that iterate
element by element. For the truly zero-copy path — useful when the host is
generating or consuming millions of elements — use `withSharedBuffer`:

```swift
try await pool.withSharedBuffer(shared, as: Double.self) { buf in
    // buf is an UnsafeMutableBufferPointer<Double> aliased onto the mmap'd region.
    // Stores commit directly to memory pages workers will read; no IPC, no copy.
    for i in 0..<buf.count {
        buf[i] = Double(i)
    }
}
```

`withSharedBuffer` validates that the Swift type matches the region's `dtype`
and that the region size is a clean multiple of the element stride, then hands
back an `UnsafeMutableBufferPointer<T>` aliased onto the mmap'd region. There
is no Swift-side copy and no IPC traffic — the bytes you store land on the
same memory pages that any worker resolves the binding to.

You can also copy an existing worker object into shared memory:

```swift
let arr = try await pool.invoke(
    module: "numpy",
    function: "ones",
    args: [.python([2048, 2048])],
    kwargs: ["dtype": .python("float32")]
)
let sharedArr = try await pool.copyToShared(arr)
let bytes: [Float] = try await pool.readFromShared(sharedArr, as: Float.self)
```

Shared memory is a performance feature, not the default. Start with normal
`PythonConvertible` arguments and handles, then move hot paths to shared memory
after profiling.

For a runnable end-to-end demo — Swift seeds an 8 MiB float64 region through
`withSharedBuffer`, two workers reduce halves in parallel through `bindings:`,
and Swift reads back zero-copy — see
[`Examples/SharedTensorPipeline`](../../Examples/SharedTensorPipeline/).

## Capsules

Use the generic `PyCapsuleRef<T: AnyObject>` API to pass an opaque Swift
object reference through Python code.

```swift
final class Engine {}

let recovered: Engine = try await Python.run {
    let capsule = try PyCapsuleRef(
        Engine(),
        name: "com.example.Engine"
    )
    return try PyCapsuleRef.extract(
        from: capsule.pyObject,
        name: "com.example.Engine"
    )
}
```

Use a stable capsule name and treat the reference as process-local. Capsules are
not portable across `PythonProcessPool` worker processes.

## Choosing a Transfer Shape

| Data shape | Recommended approach |
|------------|----------------------|
| Small scalar/list/dict | `PythonConvertible` |
| Python object reused across calls | `PyHandle` or `OwnedPyHandle` |
| Large local array inspected in-process | `PythonBuffer` |
| Large worker array reused across calls | `PyHandle` |
| Large worker array read/write from Swift | pool shared memory |
| Long-lived duplex bytes | `DuplexBuffer` or bounded logical messages |
| Local zero-copy duplex ingress | session-owned `DuplexSharedBufferLease` |
| Tenant-isolated file or process output | `SandboxPool.execShell*` |

The pool-wide `SharedMemoryArena` tensor API and the duplex shared-ingress
pool are different ownership models. Duplex accepts only opaque leases minted
for one session, worker generation, pool, slot, and slot generation; it never
accepts a caller-provided `SharedMemoryRegion` as authority. See
[Chapter 10](ch10-full-duplex.md).
