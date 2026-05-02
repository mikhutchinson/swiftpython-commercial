# Chapter 5 — Streaming

## `CancellableStream<T>` — the streaming type

All pool streaming APIs return `CancellableStream<T>`, a custom `AsyncSequence`. It reliably triggers cooperative abort when the consumer stops iterating — regardless of whether the stream variable stays in scope.

```swift
// Finite generator — exhausts naturally
let stream: CancellableStream<Int> = try await pool.evalStream("range(10)")
for try await value in stream {
    print(value)  // 0..9
}

// Infinite generator — break sends SIGUSR1 to worker, worker stops cleanly
let stream: CancellableStream<Double> = try await pool.evalStream(
    "import itertools; itertools.count(0.0, 0.1)"
)
for try await value in stream {
    if value >= 1.0 { break }  // cleanup fires immediately
}
```

## Three API tiers

All three tiers exist for `evalStream`, `methodStream`, and `invokeStream`. Every `eval*` variant accepts an optional `bindings: [String: PyHandle]` to inject handles into the code's namespace:

```swift
// Tier 1 — raw pickle bytes (you decode manually)
let raw: CancellableStream<Data> = try await pool.evalStreamRaw("gen()")

// Tier 2 — custom decoder closure
let stream = try await pool.evalStream("gen()") { (data: Data) async throws -> MyType in
    try JSONDecoder().decode(MyType.self, from: data)
}

// Tier 3 — PythonConvertible (automatic pickle → T)
let stream: CancellableStream<Double> = try await pool.evalStream("gen()")

// With bindings — inject a remote handle into the stream's namespace
let stream: CancellableStream<[Double]> = try await pool.evalStream(
    "model.predict_stream(data)", bindings: ["model": modelHandle, "data": dataHandle]
)
```

## `methodStream` and `invokeStream`

```swift
// Stream from a method on a remote handle
let stream: CancellableStream<[Double]> = try await pool.methodStream(
    handle: modelHandle, name: "predict_proba_stream"
)

// Stream from a module function
let stream: CancellableStream<String> = try await pool.invokeStream(
    module: "my_module", function: "generate_tokens", args: [r.arg(prompt)]
)
```

## WorkerContext streaming

```swift
let ctx = pool.worker(0)
let stream: CancellableStream<Double> = try await ctx.evalStream("produce()")
```

All three tiers are available on `WorkerContext` as well.

## Per-chunk timeout

```swift
let stream: CancellableStream<Int> = try await pool.evalStream(
    "slow_gen()", timeout: 2.0  // 2s per chunk
)
```

If a chunk doesn't arrive within the timeout, the stream throws `.timeout`, SIGUSR1 is sent to the worker, and the socket is drained — worker remains usable.

## How cooperative abort works

1. Consumer `break`s (or scope exits)
2. `CancellableStream.Iterator`'s `Cleanup.deinit` fires synchronously
3. Sets `cancelled` atomic + sends SIGUSR1 to worker
4. Worker's `DispatchSourceSignal` handler sets `abortRequested`
5. Worker checks before each `next()` → sends `.streamEnd`
6. Socket lock released → worker reusable immediately

This is why `CancellableStream` is used instead of `AsyncThrowingStream` — `AsyncThrowingStream.onTermination` only fires when all references drop, which doesn't happen on `break`.

## Common pitfalls

| Issue | Fix |
|-------|-----|
| Worker stuck after timeout | Timeout drain handles this automatically; no action needed |
| Infinite stream leaks worker | Always use `break` or scope exhaustion — cleanup fires via `deinit` |
| Stream type mismatch | Ensure Python yields pickle-compatible objects matching `T` |

## Wire protocol versioning (v0.2.0+)

The pool and worker negotiate a wire protocol version on every spawn via `WorkerResponse.healthy(protocolVersion:)`. The current version is `2`; legacy v1 workers (no payload on `.healthy`) are decoded as `protocolVersion: 1`.

```swift
// Default: require v2 features. Spawn fails fast if a v1 worker connects.
let pool = try await PythonProcessPool(workers: 4, ipc: .default)

// Emergency rollback: accept v1 workers (degraded mode, no v0.2.0 features).
let legacyPool = try await PythonProcessPool(
    workers: 4,
    ipc: IPCConfiguration(requiredProtocolVersion: 1)
)

// Inspect the negotiated version per worker (useful for diagnostics).
let v: UInt32? = await pool.negotiatedProtocolVersion(for: 0)
```

A version mismatch surfaces as `PythonWorkerError.protocolError("Worker N speaks protocol vM; pool requires vK or higher. ...")` at spawn time — never silently. Mixed-version deploys are safe.

## `StreamOptions` builder (v0.2.0+ / Phase B1)

The recommended v0.2.0 streaming surface. `StreamOptions` collapses the previously-positional `timeout:` and `worker:` parameters into a single struct.

```swift
// Default — no per-call overrides.
let s: CancellableStream<Int> = try await pool.evalStream(
    "range(100)", options: .default
)

// Pin to a specific worker (e.g. for handle affinity).
let s: CancellableStream<Double> = try await pool.methodStream(
    handle: modelHandle, name: "predict_stream",
    options: .pinned(worker: 2)
)

// Long-running stream — bump the per-stream receive timeout.
let s: CancellableStream<MyEvent> = try await pool.invokeStream(
    module: "my_module", function: "long_inference",
    args: [.python(prompt)],
    options: .longRunning(timeout: 1800)   // 30 minutes
) { data in
    try JSONDecoder().decode(MyEvent.self, from: data)
}

// Custom options struct.
let s: CancellableStream<Int> = try await pool.evalStream(
    "slow_gen()",
    options: StreamOptions(timeout: 60, workerAffinity: 0)
)
```

The legacy 18 overloads (positional `timeout:` / `worker:`) remain available in v0.2.0 unchanged. They will be deprecated and removed together in v0.3.0. For new code, prefer the options-based surface.

## Cancellation primitives (v0.2.0+ / Phase 4)

Two new primitives let user iterators cooperate with consumer-initiated cancel without waiting for the next yielded value:

```python
from swift_bridge import check_cancel

def long_pure_python_iterator():
    for i in range(1_000_000):
        # ... a chunk of pure-Python work that does not yield ...
        do_some_computation(i)
        check_cancel()   # ← raises KeyboardInterrupt if consumer cancelled
        if i % 100 == 0:
            yield i
```

`check_cancel()` is the cooperative path. Returns None when no cancel is pending. Raises `KeyboardInterrupt` when the consumer has broken from the `for try await`. Outside an active stream it's a silent no-op.

For workloads where the user iterator may have no `check_cancel()` checkpoints at all (e.g. `while True: pass`), enable interrupt injection via `IPCConfiguration.allowInterruptInjection: true`:

```swift
let ipc = IPCConfiguration(allowInterruptInjection: true)
let pool = try await PythonProcessPool(workers: 4, ipc: ipc)
```

When opted in, the worker's SIGUSR1 handler additionally calls CPython's `PyErr_SetInterrupt()`, which schedules a `KeyboardInterrupt` to fire on the GIL-holding thread at the next bytecode boundary. Off by default — opt-in only because injected `KeyboardInterrupt`s can surface in places user code doesn't expect them.

C extensions in syscalls do NOT yield until they return — `PyErr_SetInterrupt` is not a kernel-level interruption primitive. For those workloads you still need cooperative checkpoints OR an explicit timeout that respawns the worker.

## Stream-scoped respawn on timeout (v0.2.0+ / Phase 4)

When a stream throws `.timeout` and `IPCConfiguration.respawnOnTimeout` is true, the worker is automatically respawned best-effort. With Phase 3 keepalive enabled, a stream `.timeout` means the worker is genuinely wedged (not silent), so respawning is the right response. The failed stream's consumer still sees the timeout error; subsequent commands on the same worker index get a fresh interpreter.

```swift
let ipc = IPCConfiguration(
    receiveTimeout: 30,
    respawnOnTimeout: true,
    streamKeepaliveInterval: 5
)
```

## Keepalive + progress frames (v0.2.0+ / Phase 3)

A long-running streaming generator is now safe even when it sits silent for many seconds:

```swift
// receiveTimeout is 30s by default; streamKeepaliveInterval is 5s.
let stream: CancellableStream<Int> = try await pool.evalStream("""
    import time
    def slow():
        time.sleep(45)   // would have timed out at 30s pre-Phase-3
        yield 1
    slow()
    """)
for try await v in stream { print(v) }   // delivers 1, no timeout
```

The worker emits `WorkerResponse.streamKeepalive` frames every `IPCConfiguration.streamKeepaliveInterval` seconds (default 5s) from a background `DispatchSourceTimer`. The pool reader drops them silently — they never appear as a value to the consumer's `for try await` loop. Their only effect is keeping kernel `SO_RCVTIMEO` fresh.

To opt out, set `streamKeepaliveInterval: 0` (legacy v1 timeout behaviour returns).

### `swift_bridge.progress(hint=None)`

User Python iterators can emit semantic progress events:

```python
from swift_bridge import progress

def my_streaming_inference(prompt):
    progress("tokenising")
    tokens = tokenise(prompt)
    progress(f"loaded {len(tokens)} input tokens")
    for t in generate(tokens):
        yield t
        if t.position % 10 == 0:
            progress(f"emitted {t.position} tokens")
```

Phase 3 ships the wire path (`WorkerResponse.streamProgress`) and a pool-side hook (`WorkerProcess.progressHandler`). Phase C2 (below) surfaces progress events to consumers as a typed event case (`StreamEvent<T>.progress(elapsedMs:, hint:)`) so application code can react without intermixing them with values.

**Important contract**: progress is *semantic*, keepalive is *structural*. A user iterator that emits only `progress()` calls but never yields a value does NOT substitute for keepalive — see `StreamKeepaliveTests.testProgressFramesAloneDoNotSubstituteForKeepalive`. If you need both liveness and progress, you get both for free; if you disable keepalive, progress alone won't keep your stream alive past `receiveTimeout`.

Calling `progress()` outside an active stream is a silent no-op (the active channel ID is `0`, frame is dropped by the pool reader).

## Typed event streams — `StreamEvent<T>` (v0.2.0+ / Phase C2)

`*EventStream` entry points return a unified stream that interleaves decoded values with user-emitted `swift_bridge.progress(...)` events in causal order. Wrapping case is `StreamEvent<T>`:

```swift
public enum StreamEvent<Element: Sendable>: Sendable {
    case value(Element)
    case progress(elapsedMs: UInt64, hint: String?)
}
```

Use it when you need both the iterator's values AND its progress updates without inventing your own protocol on top of pickled data:

```swift
let stream: CancellableStream<StreamEvent<MyToken>> = try await pool.invokeEventStream(
    module: "my_inference", function: "stream_tokens",
    args: [.python(prompt)],
    options: .longRunning(timeout: 1800)
) { data in
    try JSONDecoder().decode(MyToken.self, from: data)
}

for try await event in stream {
    switch event {
    case .value(let token):
        ui.append(token.text)
    case .progress(let ms, let hint):
        ui.statusLine = "\(hint ?? "") (\(ms)ms)"
    default:
        continue   // StreamEvent is non-@frozen; future cases land additively
    }
}
```

### Six entry points

Three verbs × two tiers (custom `decode:` closure + default pickle decoder):

| Verb | Custom decode | Default pickle (`T: PythonConvertible`) |
|------|---------------|------------------------------------------|
| method | `methodEventStream(handle:name:args:kwargs:options:decode:)` | `methodEventStream<T: PythonConvertible>(handle:...:options:)` |
| invoke | `invokeEventStream(module:function:args:kwargs:options:decode:)` | `invokeEventStream<T: PythonConvertible>(module:...:options:)` |
| eval | `evalEventStream(_:bindings:options:decode:)` | `evalEventStream<T: PythonConvertible>(_:bindings:options:)` |

All six default to `options: .default`. **No `*EventStreamRaw` tier exists** — use the existing `*StreamRaw` (no progress) or pass `decode: { $0 }` to the custom-decode tier for an identity decoder.

### Opting out of progress

`StreamOptions.surfaceProgressEvents = false` skips the per-stream progress handler install. The worker still emits progress frames over the wire but the pool reader drops them silently and your `for try await` only sees `.value(...)` cases:

```swift
let opts = StreamOptions(surfaceProgressEvents: false)
let stream: CancellableStream<StreamEvent<Int>> = try await pool.evalEventStream(
    "gen()", options: opts
)
```

This flag has no effect on the legacy value-only `*Stream` overloads — they always drop progress frames silently.

### Wire-order guarantee

`.value` and `.progress` events are delivered in the exact order the worker produced them. A generator emitting `progress("a"), yield 1, progress("b"), yield 2` always reaches the consumer as `.progress("a"), .value(1), .progress("b"), .value(2)` — no reordering across the async pickle decode boundary. Locked by `StreamEventTests.testValuesAndProgressInterleavedDelivery`.

### Coexistence with test progress handlers

If you've installed your own progress handler via `installProgressHandlerForTest` (or any direct write to `worker.progressHandler`), running an `*EventStream` invocation will swap in its per-stream handler at install time and **restore yours on stream completion** (success, error, or consumer cancel). Your handler still receives progress events from any subsequent legacy `*Stream` runs.

## Per-stream channel IDs (v0.2.0+ / Phase 2)

Every stream command and response on the wire carries a `streamChannelID: UInt32` allocated by the pool from a per-worker monotonic counter. Consumers don't see this — it's an internal addressing primitive that lets later phases (keepalive frames, per-stream cancel) target a specific stream without disturbing other in-flight work.

What this means for you:

- The pool always issues stream commands with a non-zero channel ID. Channel `0` is reserved for legacy v1 wire frames.
- Mismatched-channel frames inbound to `sendStreamCommand` are logged and dropped, not raised as errors. This is forward-compat for Phase 3.
- The counter resets on worker respawn — post-respawn streams start at channel 1 again.
- `WorkerCommand.streamCancel(streamChannelID:)` is the new clean-abort wire path. In v0.2.0 / Phase 2 it is a no-op acknowledgement; Phase 4 will wire it through to per-channel abort flags. SIGUSR1 remains the active cancel mechanism today.

## GIL discipline inside the worker (v0.2.0+ / Phase 1)

The worker's `executeStream` releases the GIL between iterations so other GIL consumers (the side-channel daemon, the v0.2.0+ keepalive timer) can acquire it during a long-running stream. This is invisible to consumers but matters when reasoning about `sideEval` latency mid-stream:

```swift
// During a 10-iteration stream that sleeps 100ms per iteration,
// a sideEval lands within ~150ms — not after the stream completes.
let stream: CancellableStream<Int> = try await pool.evalStream("""
    import time
    def gen():
        for i in range(10):
            time.sleep(0.1)
            yield i
    gen()
    """, worker: 0)
try await pool.sideEval("touched_at = time.time()", worker: 0)
for try await _ in stream {}
// touched_at is set to a timestamp well within the stream's runtime
```
