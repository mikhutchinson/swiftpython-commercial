# Chapter 2 — Type Conversion & Buffers

## `PythonConvertible` — the conversion protocol

```swift
public protocol PythonConvertible: Sendable {
    init(pythonObject: PyObjectRef) throws
    func toPythonObject() throws -> PyObjectRef
}
```

**Built-in conformances:** `Int`, `Double`, `Bool`, `String`, `Data`,
`Array where Element: PythonConvertible`,
`Dictionary where Key: PythonConvertible, Value: PythonConvertible`,
`Optional where Wrapped: PythonConvertible`,
`PyObjectRef`, and all generated wrapper types.

```swift
// Swift → Python
let pyVal = try (42).toPythonObject()

// Python → Swift
let n = try Int(pythonObject: someRef)
let d = try Double(pythonObject: someRef)
let s = try String(pythonObject: someRef)
```

## Heterogeneous collection helpers

Build Python lists, tuples, sets, and dicts from mixed Swift values:

```swift
let lst  = try pyList(1, "hello", 3.14)
let tpl  = try pyTuple(1, "hello", 3.14)
let st   = try pySet(1, 2, 3)
let dict = try pyDict(("key", "value"), ("count", 42))
```

## Remote value helpers (ProcessPool call sites)

When passing arguments to `pool.invoke` / `pool.method`:

```swift
// Implicit conversion — preferred
try await pool.invoke(module: "math", function: "sqrt", args: [4.0])

// Explicit — when mixing handles and values
let h: PyHandle = ...
try await pool.method(handle: h, name: "fit", args: [r.arg(xTrain), r.arg(yTrain)])

// Builder syntax
try await pool.invoke(module: "numpy", function: "arange") {
    0; 10          // positional args
} kwargs: {
    ("dtype", "float32")
}
```

`RemotePythonValue` wraps either a plain `PythonConvertible` or a `PyHandle`. Use `r.arg(_:)` or `remoteArg(_:)` to construct explicitly.

## Slicing and indexing

```swift
// Python-style: arr[1:10:2]
let slice = PythonSlice(start: 1, stop: 10, step: 2)

// Multi-dimensional: arr[0, 1:5]
let idx = MultiIndex([.index(0), .slice(PythonSlice(stop: 5))])
```

Types: `PythonSlice`, `StridedSlice`, `MultiIndex`, `IndexElement`, `SliceMarker`.
Examples: `example_slicing.swift`, `example_advanced_slicing.swift`.

## Generic Python buffer API (`PythonBuffer`)

Zero-copy access to any Python object implementing the buffer protocol:

```swift
let buf = try PythonBuffer(object: arr)
// buf.pointer, .length, .itemSize, .ndim, .shape, .strides, .format

let values: [Double] = buf.toArray()
let raw: Data = buf.toData()

try buf.withValidatedBufferPointer { (ptr: UnsafeBufferPointer<Float>) in
    // zero-copy read
}
buf.release()
```

## NumPy `ndarray` typed buffer helpers

Fast-path helpers that are zero-copy when dtype and layout match:

```swift
// Typed async accessors (return Swift arrays — copy)
let doubles = try await arr.toDoubleArray()
let floats  = try await arr.toFloatArray()
let ints64  = try await arr.toInt64Array()
let ints32  = try await arr.toInt32Array()
let bytes   = try await arr.toUInt8Array()

// Scoped zero-copy access
try await arr.withDoubleBuffer { ptr in
    // ptr: UnsafeBufferPointer<Double> — no copy
}
try await arr.withFloatBuffer  { ptr in /* UnsafeBufferPointer<Float>  */ }
try await arr.withInt64Buffer  { ptr in /* UnsafeBufferPointer<Int64>  */ }
try await arr.withInt32Buffer  { ptr in /* UnsafeBufferPointer<Int32>  */ }
try await arr.withUInt8Buffer  { ptr in /* UnsafeBufferPointer<UInt8>  */ }

// dtype inspection (properties, no async)
arr.isFloat64, arr.isFloat32, arr.isInt64, arr.isInt32, arr.isUInt8
arr.dtypeString  // "float64", "int32", etc.
```

## Accelerate integration (vDSP on `ndarray`)

```swift
// Synchronous (call from within Python.run or withGIL)
let mean = try arr.vDSPMean()
let sum  = try arr.vDSPSum()
let min  = try arr.vDSPMin()
let max  = try arr.vDSPMax()

// Async variants
let mean = try await arr.vDSPMeanAsync()
```

These use vDSP under the hood and require contiguous float64 layout. For float32 use `vDSPMeanFloat()` / `vDSPSumFloat()`.

## Swift callables passed to Python

Create a Python callable from a Swift closure:

```swift
// Typed args and return
let fn = try createPythonCallable { (x: Double) throws -> Double in x * x }

// Use with Python functions
let result = try await ScipyOptimize.minimize(fun: fn, x0: [1.0, 2.0])
```

For deterministic cleanup tied to Swift scope, use `PythonCallableWrapper`:

```swift
let wrapper = try PythonCallableWrapper { (x: Double) throws -> Double in x * x }
// wrapper.callable is the PyObjectRef to pass to Python
// wrapper deinit unregisters the closure
```

**Supported overloads:** 0-arg, 1-arg, 2-arg, 3-arg — with or without typed return.

## Capsules (opaque Swift object references)

Wrap a Swift object as an opaque Python capsule and extract it back:

```swift
let cap = try PyObjectRef.capsule(mySwiftObj, name: "MyType")
let obj = try cap.extractCapsule(as: MyClass.self, name: "MyType")
let ok  = cap.isCapsule(name: "MyType")
```
