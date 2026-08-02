# Chapter 5 - Streaming

Use streaming when Python produces values over time: token generation, progress
updates, sensor data, long transforms, search results, or incremental parsing.

All pool streaming APIs return `CancellableStream`, a custom `AsyncSequence`
that sends a cooperative cancel signal when the consumer stops iterating.

## Value Streams

```swift
let stream: CancellableStream<Int> = try await pool.evalStream("range(10)")

for try await value in stream {
    print(value)
}
```

For a module function:

```swift
let tokens: CancellableStream<String> = try await pool.invokeStream(
    module: "my_inference",
    function: "generate_tokens",
    args: [.python("Write a haiku")]
)
```

For a method on a remote object:

```swift
let model = try await pool.invokeOwned(
    module: "my_inference",
    function: "load_model",
    args: [.python("/models/model.bin")]
)

let tokens: CancellableStream<String> = try await pool.methodStream(
    handle: model,
    name: "stream",
    args: [.python("Hello")]
)
```

## Decode Tiers

Each stream verb has two public forms:

| Form | Use |
|------|-----|
| `T: PythonConvertible` | Python yields pickle-compatible values that map directly to Swift |
| `decode: (Data) async throws -> T` | You want to decode the raw pickled payload into your own type |

```swift
struct Token: Sendable, Decodable {
    let text: String
    let logprob: Double
}

let stream = try await pool.invokeStream(
    module: "my_inference",
    function: "json_token_stream",
    args: [.python(prompt)]
) { data in
    try JSONDecoder().decode(Token.self, from: data)
}
```

If your Python code naturally returns JSON, msgpack, protobuf, or another
format, decode it in the closure and keep your Swift app strongly typed.

## `StreamOptions`

`StreamOptions` carries per-stream timeout, worker affinity, and channel
capacity.

```swift
let stream: CancellableStream<Int> = try await pool.evalStream(
    "slow_generator()",
    options: .longRunning(timeout: 1800)
)

let pinned: CancellableStream<Double> = try await pool.methodStream(
    handle: model,
    name: "predict_stream",
    options: .pinned(worker: 0)
)

let custom = StreamOptions(
    timeout: 120,
    workerAffinity: 0,
    channelCapacity: 8
)
```

`WorkerContext` stream helpers are already pinned to the context worker.

## Stopping a Stream

Breaking out of the loop cancels the stream promptly.

```swift
let stream: CancellableStream<Double> = try await pool.evalStream("""
import itertools
itertools.count(0.0, 0.1)
""")

for try await value in stream {
    if value >= 1.0 {
        break
    }
}
```

Python generators can cooperate with cancellation:

```python
from swift_bridge import check_cancel

def generate():
    for item in expensive_source():
        check_cancel()
        yield item
```

`check_cancel()` raises `KeyboardInterrupt` when the Swift consumer has stopped
iterating. Outside an active stream it is a no-op.

For code without checkpoints, use a timeout and then replace the worker if the
workload stays wedged:

```swift
try await pool.respawnWorker(0, reason: .userInitiated, force: true)
```

C extensions blocked in syscalls may not stop until the syscall returns. Design
long-running Python iterators with checkpoints when you need graceful cancel.

## Timeouts and Keepalive

```swift
let ipc = IPCConfiguration(
    receiveTimeout: 30,
    streamKeepaliveInterval: 5,
    respawnOnTimeout: true
)
```

`receiveTimeout` is the maximum time the pool waits for stream activity.
`streamKeepaliveInterval` lets the worker prove the stream is alive even when no
user value is ready. With `respawnOnTimeout`, a timed-out stream can replace the
wedged worker so later work starts fresh.

For long-running but healthy streams, prefer `StreamOptions.longRunning(timeout:)`
or a custom timeout rather than disabling timeouts globally.

Timeout cleanup is stream-scoped. When a stream times out, the runtime aborts
that stream and drains stale frames through the stream's demux channel before
returning, so a per-call timeout override does not fall through to the
pool-wide receive timeout.

## Progress Events

Use `swift_bridge.progress` in Python to report semantic progress.

```python
from swift_bridge import progress

def train():
    progress("loading data")
    data = load_data()
    for epoch in range(10):
        train_one_epoch(data, epoch)
        progress(f"epoch {epoch + 1}/10")
        yield epoch
```

Value-only `*Stream` APIs drop progress frames. Use `*Events` APIs to receive
both values and progress.

```swift
let events: CancellableStream<StreamEvent<Int>> = try await pool.evalEvents("train()")

for try await event in events {
    switch event {
    case .value(let epoch):
        print("completed epoch \(epoch)")
    case .progress(_, let hint):
        print(hint ?? "working")
    }
}
```

The event-stream entry points are:

| Verb | API |
|------|-----|
| Eval code | `evalEvents(_:bindings:options:)` |
| Module function | `invokeEvents(module:function:args:kwargs:options:)` |
| Handle method | `methodEvents(handle:name:args:kwargs:options:)` |

Each also has a custom `decode:` form.

## Ordering Contract

For `*Events`, progress and values are delivered in the order Python produced
them. If Python calls `progress("a")`, yields `1`, calls `progress("b")`, and
yields `2`, Swift observes those four events in that order.

## Streaming From a Specific Worker

```swift
let worker = pool.worker(1)
let stream: CancellableStream<String> = try await worker.invokeStream(
    module: "my_jobs",
    function: "tail_logs",
    args: [.python("/tmp/job.log")],
    timeout: 300
)
```

Worker contexts are useful when the stream depends on a handle or Python state
that exists on a specific worker.

## Out-of-Band Streaming

`evalStream` / `invokeStream` / `methodStream` send each chunk over the
worker's main IPC socket. For the duration of the stream, that socket is held
by the streaming command — any other `eval` / `invoke` on the same worker has
to wait its turn.

When you need a long-lived stream that does *not* tie up the worker, use the
out-of-band path. A Python generator runs on the worker's side-channel daemon
thread and writes into a host-owned buffer; the host polls that buffer; the
worker's main socket stays free for normal commands.

There are two buffer types and matching entrypoints, picked by where the
worker lives:

| Worker backend | Buffer | Entrypoint | Transport |
|---|---|---|---|
| Local process (`SwiftPythonWorker`) | `SharedRingBuffer` | `startOutOfBandStream(generatorCode:worker:buffer:)` | POSIX shared memory |
| VM tenant / cross-isolation | `SocketOOBStreamBuffer` | `startOutOfBandSocketStream(generatorCode:worker:capacity:)` | UDS (process backend) or vsock (VM backend) |

The two share the same Swift consumer shape — `readAvailable() -> Data`,
`signalAbort()`, `isWriterDone`, `isAborted` — so a polling loop written
against one switches to the other by changing the buffer type. The socket
entrypoint is a current public API, not a compatibility shim. Use it explicitly
for VM/vsock tenants; use the `SharedRingBuffer` overload for a local process
worker whose negotiated OOB transport is shared memory.

### `SharedRingBuffer` (local process backend)

A single-producer (Python) / single-consumer (Swift) ring buffer backed by a
fresh POSIX shm segment.

| API | Purpose |
|-----|---------|
| `init(capacity:)` | Create a ring buffer with `capacity` bytes of data region (header is separate). Default capacity is 64 KiB. |
| `shmName` | POSIX shm name. Pass to a Python writer to attach. |
| `dataCapacity`, `totalSize` | Capacity (bytes) and segment size including header. |
| `readAvailable()` | Returns all bytes written since the last call. Empty if nothing new. |
| `signalAbort()` | Tells the writer to stop. The flag is checked between iterations. |
| `isWriterDone` | `true` once the writer thread has exited (normal completion, abort, or exception). |
| `isAborted` | `true` once `signalAbort()` has been called. |
| `writePosition` | Monotonic byte count the writer has produced. |

The segment is unlinked when `SharedRingBuffer` is deinitialized; the host
owns the lifecycle.

### `startOutOfBandStream`

```swift
try await pool.startOutOfBandStream(
    generatorCode: "(json.dumps(frame) for frame in frames())",
    worker: 0,
    buffer: ring
)
```

`generatorCode` must be a Python expression that evaluates to an iterable of
`str` or `bytes`. It runs on the worker's side-channel daemon thread, so it
can reference names you already imported into that worker's persistent
namespace via regular `pool.eval`.

The call ships the bootstrap via the **side channel**, never the main IPC
socket — so a regular `pool.evalResult` on the same worker continues to
complete in normal IPC round-trip time while the OOB writer is producing.

Each yielded value is written into the ring buffer's circular data region;
the writer updates the 8-byte `writePos` header last so the reader sees a
consistent snapshot. If Python yields faster than Swift drains and laps the
buffer, `readAvailable()` detects the overrun and skips forward to the
earliest recoverable position.

### `SocketOOBStreamBuffer` (cross-isolation / VM backend)

POSIX shared memory cannot cross a VM boundary, so the VM backend (and the
process backend when explicitly requested) uses a connected socket instead.
The Python writer sends **length-prefixed** chunks:

```
[4-byte LE length][data bytes][4-byte LE length][data bytes]...
```

Done: Python closes its end → Swift's `recv` returns 0 → `isWriterDone` flips
to `true`. Abort: `signalAbort()` calls `shutdown(SHUT_RDWR)` on the socket
so the writer's next `sendall` raises `BrokenPipeError`.

```swift
// VM / cross-isolation worker
let socketBuffer = try await pool.startOutOfBandSocketStream(
    generatorCode: "(json.dumps(frame) for frame in frames())",
    worker: 0,
    capacity: 65_536
)
// Same consumer shape as SharedRingBuffer:
while !socketBuffer.isWriterDone {
    let chunk = socketBuffer.readAvailable()
    if !chunk.isEmpty { handle(chunk) }
    try await Task.sleep(nanoseconds: 10_000_000)
}
```

`startOutOfBandSocketStream` selects the transport automatically: vsock
inside a VM tenant (host connects via `Virtualization.framework`), UDS for
the process backend. Either way the worker's main IPC socket is untouched,
matching the `SharedRingBuffer` invariant.

### Polling Pattern

```swift
let ring = try SharedRingBuffer(capacity: 64 * 1024)
try await pool.startOutOfBandStream(
    generatorCode: "telemetry(n=200)",
    worker: 0,
    buffer: ring
)

var lineBuffer = Data()
while !ring.isWriterDone || !lineBuffer.isEmpty {
    let chunk = ring.readAvailable()
    if !chunk.isEmpty {
        lineBuffer.append(chunk)
        while let nl = lineBuffer.firstIndex(of: 0x0A) {
            let line = Data(lineBuffer[..<nl])
            lineBuffer.removeSubrange(...nl)
            handle(line) // parse one framed message
        }
    }
    try await Task.sleep(nanoseconds: 10_000_000) // ~10 ms
}
```

To stop early, call `ring.signalAbort()`. The writer checks the flag between
iterations and exits within a few milliseconds.

### When to Reach for OOB Streaming vs `evalStream`

| Need | Use |
|------|-----|
| Bounded iteration where the worker has nothing else to do | `evalStream` / `invokeStream` / `methodStream` |
| Long-lived telemetry, log tail, or progress feed against an in-process worker | `startOutOfBandStream` + `SharedRingBuffer` |
| Same, but the generator lives in a VM tenant (or you want a socket-based transport) | `startOutOfBandSocketStream` + `SocketOOBStreamBuffer` |
| Three independent flows on one worker (main commands + side namespace injection + long-running output stream) | Main IPC + `sideEval` + an OOB buffer of either kind |

The
[`Examples/SharedTensorPipeline`](../../Examples/SharedTensorPipeline/) demo
exercises the full path end-to-end (shared-memory tensor + OOB telemetry +
concurrent `evalResult` on the same worker, all measured).

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Progress never appears | Use `evalEvents` / `invokeEvents` / `methodEvents`, not value-only streams |
| Stream times out while doing real work | Increase stream timeout or keep the default keepalive enabled |
| Cancel does not stop a tight loop | Add `swift_bridge.check_cancel()` checkpoints or use forced respawn |
| A stream returns huge objects | Yield smaller chunks or use shared memory handles |
| UI updates arrive too fast | Coalesce stream events before publishing to the main actor |
| `evalStream` blocks other work on the same worker | Move the long-lived producer to `startOutOfBandStream` |
| OOB consumer never sees the last bytes | Drain `readAvailable()` once more after `isWriterDone` is `true` |
