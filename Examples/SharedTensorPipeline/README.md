# SharedTensorPipeline

A runnable public-package demonstration of two high-level data paths:

- `PythonProcessPool.createManagedTensor` and `withManagedTensor` share one
  opaque float64 tensor with two workers;
- `PythonProcessPool.startOutputStream` returns a `ManagedOutputBuffer` while
  regular calls remain responsive on the same worker.

The demo intentionally does not expose storage names, offsets, mapping layout,
ring headers, endpoints, or generation counters.

## Run

```bash
swift run -c release
```

The runtime discovers the matched `SwiftPythonWorker` from this checkout. A
shipping app must keep Runtime, private Engine, worker, and helper assets on the
same exact release.

## Pipeline

1. Allocate one managed float64 tensor:

   ```swift
   let shared = try await pool.createManagedTensor(
       shape: [1_048_576],
       dtype: .float64
   )
   ```

2. Seed it through scoped host access:

   ```swift
   try await pool.withManagedTensor(shared, as: Double.self) { buffer in
       for index in buffer.indices { buffer[index] = Double(index) }
   }
   ```

3. Bind the same handle in two worker calls and reduce disjoint halves.

4. Start a runtime-managed output stream:

   ```swift
   let output = try await pool.startOutputStream(
       generatorCode: "_telemetry(80)",
       worker: 0,
       capacity: 64 * 1_024
   )
   ```

5. Drain `output.readAvailable()` until `output.isFinished`, while normal
   `evalResult` calls continue on worker 0.

6. Read the tensor through `withManagedTensor` and verify its contents.

Reported throughput is an observation from the current machine and build, not
a portable performance guarantee.
