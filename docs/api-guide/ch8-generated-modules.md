# Chapter 8 - Python Packages and App Facades

The commercial package includes the runtime, worker and integration templates.
Use Python packages through its Swift API without building SwiftPython from source.

Build application features by importing Python packages dynamically, calling
module functions through `PythonProcessPool`, and wrapping those calls in your
own small Swift facades.

## In-Process Package Use

For lightweight calls:

```swift
let result: Double = try await Python.run {
    let statistics = try Python.import("statistics")
    return try Double(pythonObject: try statistics.fmean([1.0, 2.0, 3.0]))
}
```

Use this when the work is fast, trusted, and safe to run in the app process.

## ProcessPool Package Use

For CPU-heavy or crash-prone packages:

```swift
try await withProcessPool(workers: 2) { pool in
    let norm: Double = try await pool.invokeResult(
        module: "numpy.linalg",
        function: "norm",
        args: [.python([3.0, 4.0])]
    )

    print(norm)
}
```

`invoke` imports the module inside the worker and calls the named function.
`invokeResult` pickles the result back to Swift. Use plain `invoke` when the
result should stay on the worker as a handle.

## App-Level Swift Facade

Hide stringly Python calls behind a small Swift type owned by your app.

```swift
actor EmbeddingService {
    private let pool: PythonProcessPool
    private var model: OwnedPyHandle?

    init(pool: PythonProcessPool) {
        self.pool = pool
    }

    func load(modelPath: String) async throws {
        model = try await pool.invokeOwned(
            module: "my_embeddings",
            function: "load_model",
            args: [.python(modelPath)]
        )
    }

    func embed(_ text: String) async throws -> [Float] {
        guard let model else { return [] }
        return try await pool.methodResult(
            handle: model,
            name: "embed",
            args: [.python(text)]
        )
    }
}
```

This keeps Python module names, method names, and conversion choices in one
place. The rest of your app gets a normal Swift API.

## Recommended Python Module Shape

Put reusable Python code in modules importable by the app's Python 3.13
environment:

```python
# my_embeddings.py

def load_model(path):
    return Model.load(path)

def summarize(text):
    return {"chars": len(text), "words": len(text.split())}
```

Then call it from Swift:

```swift
let summary: [String: Int] = try await pool.invokeResult(
    module: "my_embeddings",
    function: "summarize",
    args: [.python("Swift calling Python")]
)
```

Avoid embedding large Python programs as Swift string literals. Use `eval` for
small glue and `invoke` for stable app logic.

## Packaging Python Dependencies

SwiftPython includes Python 3.13 for macOS. Bundle additional packages for that
runtime and the architectures your app supports.

Common deployment patterns:

| Pattern | Use |
|---------|-----|
| Commercial private Python framework | Every app consuming this package |
| Additional bundled site packages | Third-party Python dependencies not present in the sealed standard library |
| VM tenant image | Isolated Linux tools, untrusted jobs, or per-tenant dependencies |

Bundle dependencies where the workload runs: in the app's Python environment
for local calls and workers, or in the Linux image for sandbox workloads.

Finder/Dock launches use the same private framework as Terminal launches. Do
not add `PYTHONHOME`, `PATH`, Homebrew discovery, or linker setup to the app;
bundle any additional Python packages as application-owned resources.

## Numeric Packages

For NumPy-like workloads:

```swift
let arr = try await pool.invoke(
    module: "numpy",
    function: "array",
    args: [.python([1.0, 2.0, 3.0, 4.0])]
)

let mean: Double = try await pool.methodResult(handle: arr, name: "mean")
```

For large arrays, keep the object on the worker or move to shared memory:

```swift
let shared = try await pool.copyToManagedTensor(arr)
let values: [Double] = try await pool.readManagedTensor(shared, as: Double.self)
```

## Long-Running Package Calls

If a Python package can report incremental values, expose a generator and use
streaming:

```python
# my_inference.py
from swift_bridge import progress, check_cancel

def generate(prompt):
    progress("starting")
    for token in model.generate(prompt):
        check_cancel()
        yield token
```

```swift
let stream: CancellableStream<StreamEvent<String>> = try await pool.invokeEvents(
    module: "my_inference",
    function: "generate",
    args: [.python(prompt)],
    options: .longRunning(timeout: 1800)
)
```

## Tenant or Tool Packages

When the package is a CLI tool, needs Linux, or should not share the user's
local Python environment, put it in a SandboxPool image and call it through
`execShell`, `execShellStream`, or `execShellPTY`.

See [Chapter 9](ch9-sandbox-vm.md).

## Package your app

Keep package imports and conversion code in your Swift wrapper. Bundle the
Python dependencies and matched worker with your application.

[Packaging guide](https://swiftpython.dev/docs/packaging/) ·
[Worker pools](ch4-process-pool.md)
