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

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Progress never appears | Use `evalEvents` / `invokeEvents` / `methodEvents`, not value-only streams |
| Stream times out while doing real work | Increase stream timeout or keep the default keepalive enabled |
| Cancel does not stop a tight loop | Add `swift_bridge.check_cancel()` checkpoints or use forced respawn |
| A stream returns huge objects | Yield smaller chunks or use shared memory handles |
| UI updates arrive too fast | Coalesce stream events before publishing to the main actor |
