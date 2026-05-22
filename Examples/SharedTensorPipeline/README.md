# SharedTensorPipeline

A runnable end-to-end demo of the two SwiftPython facilities most other
Swift/Python bridges cannot offer:

- **Pool shared-memory arena** — `PythonProcessPool.createSharedTensor(shape:dtype:)`
  allocates a POSIX-shm region and broadcast-attaches it to every worker. Swift,
  worker 0, and worker 1 each see the *same* bytes via `mmap`. No pickling, no
  IPC payloads, no `numpy.tobytes()`.
- **Out-of-band streaming** — `PythonProcessPool.startOutOfBandStream(buffer:)`
  pipes a Python generator into a `SharedRingBuffer` over a *separate* POSIX-shm
  region. The worker's IPC socket is never touched, so regular `evalResult`
  calls on that same worker stay responsive while the stream runs.

The example proves both with hard numbers and exact equality checks. It also
ties them together: while the workers are mid-reduction over the shared tensor,
a generator emits live JSON telemetry over OOB, and concurrent `evalResult`
probes confirm the IPC socket isn't blocked.

## Run

From the distribution root (worker resolves to the sibling `SwiftPythonWorker`):

```bash
SWIFTPYTHON_WORKER_PATH="$PWD/SwiftPythonWorker" \
  swift run -c release \
  --package-path Examples/SharedTensorPipeline
```

`-c release` matters for step 2 — debug builds bounds-check every element of
the 1 Mi double write and bury the real bandwidth. Release exposes the actual
`memset`-class store throughput.

From inside the package directory:

```bash
SWIFTPYTHON_WORKER_PATH="$PWD/../../SwiftPythonWorker" swift run -c release
```

## Pipeline (six numbered stages)

### 1. Pool arena allocates one POSIX-shm region

```swift
let shared = try await pool.createSharedTensor(shape: [1_048_576], dtype: .float64)
```

The pool allocates an 8 MiB region inside its `SharedMemoryArena` and sends an
`attachSharedMemory` command to every commandable worker. The returned
`PyHandle` carries a `PyHandleSharedMemoryRef` (shm name, offset, size, shape,
dtype) — pass it through `bindings:` and any worker sees the same `mmap`.

### 2. Swift seeds via `withSharedBuffer` (direct mmap write)

```swift
try await pool.withSharedBuffer(shared, as: Double.self) { buf in
    for i in 0..<buf.count { buf[i] = Double(i) }
}
```

`withSharedBuffer` validates the dtype/size/Swift type and hands back a typed
`UnsafeMutableBufferPointer` straight into the mmap'd region. There's no copy,
no socket round-trip, no pickle: those Swift stores commit to memory pages the
workers will read in the next step. Release builds sustain ~100 GiB/s on Apple
silicon.

### 3. Two workers reduce disjoint halves in parallel

```swift
async let leftSum: Double = pool.worker(0).evalResult(
    "float(arr[:524288].sum())", bindings: ["arr": shared])
async let rightSum: Double = pool.worker(1).evalResult(
    "float(arr[524288:].sum())", bindings: ["arr": shared])
```

Each worker resolves the binding by `mmap`-attaching to the same POSIX-shm
segment (cached after the initial `attachSharedMemory`). NumPy wraps it
zero-copy. Both halves run on different CPUs in different processes against the
same pages.

The example asserts `left + right == N·(N-1)/2` exactly — `549,755,289,600` for
`N = 1,048,576`. Float64 is exact for that sum (well below 2^53).

### 4. Spin up an out-of-band Python telemetry generator

```swift
let ring = try SharedRingBuffer(capacity: 64 * 1024)
try await pool.startOutOfBandStream(
    generatorCode: "_spt_telemetry(80, sleep_ms=5)",
    worker: 0,
    buffer: ring
)
```

`startOutOfBandStream` runs the generator on a daemon thread inside worker 0
through the **side channel** (separate UDS socket). Each yielded `str` is
UTF-8 encoded and `memcpy`'d into the ring buffer's circular data region; a
monotonically increasing `writePos` header is updated last (8-byte aligned
store, ordered on arm64/x86_64). Swift polls `ring.readAvailable()` on a
~10 ms cadence and consumes line-framed JSON frames.

Key invariant: this never touches the worker's main IPC socket lock.

### 5. Liveness proof under concurrent OOB writes

While the OOB writer is busy:

```swift
let result: Int = try await pool.evalResult(
    "sum(range(\(probe * 1000)))", worker: 0, timeout: 2.0)
```

The probes complete in <1.5 ms each because they go through the normal IPC
socket — which is free. If you tried the same with `evalStream` instead of OOB,
each probe would block until the stream finished.

The drain task verifies all `80` telemetry frames arrived in order and prints
their `elapsed_ms` (measured by the Python generator from its own
`time.monotonic()` baseline). After the writer flips `isWriterDone`, the demo
unregisters the segment from Python's `multiprocessing.resource_tracker` so
worker shutdown doesn't print a spurious "leaked shared_memory" warning —
`SharedRingBuffer.deinit` is what actually unlinks the POSIX segment.

### 6. Zero-copy readback through Swift's mmap view

```swift
try await pool.withSharedBuffer(shared, as: Double.self) { buf in
    print(buf[0], buf[buf.count - 1]) // 0.0, 1048575.0
}
```

Same mapping, no copy — and `buf.last - buf.first == 1048575.0` confirms Swift
sees exactly what the workers reduced.

## Sample output (release build, M-series Mac)

```
─── 1. Pool arena allocates one POSIX-shm region, broadcast-attached to every worker ───
   shm name : /swiftpython-shm-DA1EC04C
   shape    : [1048576]  dtype=float64
   bytes    : 8388608 (8.00 MiB)
   offset   : 0
   workers  : attached to all 2 commandable workers

─── 2. Swift seeds every element through pool.withSharedBuffer (direct mmap write) ───
   wrote 0…1048575 into mmap region in    0.07 ms  (108.82 GiB/s sustained)

─── 3. Two workers reduce disjoint halves of the same mmap bytes, in parallel ───
   worker 0 sum[0 ..<524288)        = 137438691328
   worker 1 sum[524288..<1048576)   = 412316598272
   total                            = 549755289600
   expected  (N·(N-1)/2)            = 549755289600
   parallel wall time               =   50.21 ms
   ✓ exact float64 match (no copies, no pickle, just two mmap views)

─── 5. Liveness proof: regular evalResult on worker 0 while OOB is streaming ───
   probe #1: sum(range(1000)) = 499500   (   0.68 ms IPC round-trip while OOB is hot)
   OOB frame   0  elapsed=  0.003 ms
   OOB frame   1  elapsed=  6.499 ms
   OOB frame   2  elapsed= 12.949 ms
   probe #2: sum(range(2000)) = 1999000  (   1.10 ms IPC round-trip while OOB is hot)
   probe #3: sum(range(3000)) = 4498500  (   0.76 ms IPC round-trip while OOB is hot)
   OOB frame  20  elapsed=121.242 ms
   probe #4: sum(range(4000)) = 7998000  (   0.71 ms IPC round-trip while OOB is hot)
   …
   drained 80 telemetry frames, …  writerDone=true writePos=2922 B
```

## API surface exercised

| API | Where |
|-----|-------|
| `PythonProcessPool.createSharedTensor(shape:dtype:)` | step 1 |
| `PythonProcessPool.withSharedBuffer(_:as:_:)` | steps 2, 6 |
| `pool.worker(_:).evalResult(_:bindings:)` | step 3 (binding a shared handle by handle metadata, not by copy) |
| `SharedRingBuffer(capacity:)` | step 4 |
| `PythonProcessPool.startOutOfBandStream(generatorCode:worker:buffer:)` | step 4 |
| `SharedRingBuffer.readAvailable()` / `isWriterDone` / `writePosition` | step 5 |
| `PythonProcessPool.evalResult(_:worker:timeout:)` (regular IPC under OOB) | step 5 |
| `PythonProcessPool.release(_:)` | step 6 |

See [`docs/api-guide/ch2-data-interop.md`](../../docs/api-guide/ch2-data-interop.md)
for shared memory and [`docs/api-guide/ch5-streaming.md`](../../docs/api-guide/ch5-streaming.md)
for streaming background.
