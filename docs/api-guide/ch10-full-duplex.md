# Chapter 10 - Full-Duplex Sessions

`PythonDuplexSession` is the long-lived ProcessPool primitive for input,
output, application control, and interruption that must progress independently
on one pinned worker generation. Core duplex carries bounded opaque bytes. It is
not an audio model and does not select an accelerator stack.

Use duplex when a Python handler must receive new input while it is still
producing output. Keep `evalStream`, `invokeStream`, and `methodStream`
for finite output-only iterators.

## Capability truth

Duplex requires worker wire v6 plus live feature, transport, authentication,
helper-schema, payload-route, and limit declarations. Lowering the ordinary
worker protocol floor for a legacy call never emulates duplex.

`duplexSupport(for:)` is a high-level observation for advisory UI or routing.
Put requirements on the open itself because a worker can be replaced after
inspection:

```swift
let support = await pool.duplexSupport(for: 0)
precondition(support?.supportsMessages == true)

let requirements = DuplexSessionRequirements.messages
var options = DuplexOptions.default
options.requirements = requirements

let session = try await pool.openDuplexSession(
    handler: handler,
    options: options
)
```

The standard requirement sets are:

| Requirement | Contract |
|---|---|
| `.frames` | worker-v6 frame duplex |
| `.messages` | frames plus bounded logical-message fragmentation |
| `.managedBuffers` | messages plus runtime-managed local buffer support |

The pool rechecks requirements against the exact generation it reserves. An
unsupported feature or helper schema fails locally before handler setup.

## Complete frame session

Output and event sequences each admit one iterator. Consume both concurrently
when both matter; each sequence preserves its own order, not a combined order.

```swift
let session = try await pool.openDuplexSession(
    handler: .eval(
        code: """
        from swift_duplex import InputFrame
        def run(session):
            session.ready()
            for event in session.iter_input():
                if isinstance(event, InputFrame):
                    session.output.send(
                        bytes(event.buffer),
                        processed_input_through=event.sequence,
                    )
            session.output.finish()
        """,
        entrypoint: "run"
    )
)

do {
    async let outputCount: Int = {
        var count = 0
        for try await frame in session.output {
            consume(frame.buffer.withUnsafeBytes { Data($0) })
            try await session.acknowledgeOutput(
                consumedThrough: DuplexPosition(
                    sequence: frame.position.sequence,
                    byteOffset: frame.buffer.count
                )
            )
            count += 1
        }
        return count
    }()

    try await session.input.send(
        DuplexInputFrame(
            payload: Data("hello".utf8),
            flags: [.independent]
        )
    )
    try await session.input.finish()
    let result = try await session.result()
    _ = try await outputCount
    precondition(result.terminal == .completed)
    await session.close()
} catch {
    await session.cancel(reason: .user)
    await session.close()
    throw error
}
```

`finish()` idempotently half-closes input; it does not truncate output.
`close()` is deterministic cancel-and-cleanup and is also safe after a
terminal result.

## Credit, leases, and acknowledgement

`send` suspends until both byte and frame credit are available.
`trySend` reports that it would block without queuing. Capture adapters must
record deliberate discontinuities rather than hiding dropped time.

`DuplexBuffer` is lease backed. Copying the Swift value shares its lease, and
peer output credit returns only when the final copy releases storage.
`acknowledgeOutput(consumedThrough:)` is a different cursor: it says what the
application or playback device actually consumed, including a partial-frame
byte offset.

## Logical messages

`maximumFrameBytes` is a physical envelope ceiling, not the largest
application unit. A session opened with `.messages` can fragment a larger
bounded message:

```swift
var options = DuplexOptions.default
options.requirements = .messages
options.limits.maximumFrameBytes = 256 * 1_024
options.limits.maximumLogicalMessageBytes = 64 * 1_024 * 1_024
options.limits.preferredMessageChunkBytes = 256 * 1_024

let session = try await pool.openDuplexSession(
    handler: handler,
    options: options
)
try await session.input.sendMessage(
    encodedKeyframe,
    format: DuplexFormat(
        "video/hevc",
        metadata: ["profile": "main10"]
    ),
    flags: [.independent]
)
```

`sendMessage(Data, ...)` fragments automatically. A streamed producer can use
`beginMessage(byteCount:...)`, `write`, and `finish`, or `abort` after
a source failure. Message identity, total length, chunk offset/index,
timestamp, format metadata, and flags remain bounded and validated.

Python consumes without forced reassembly:

```python
message = session.receive_message()
for chunk in message.chunks():
    consume(memoryview(chunk.buffer))

# Explicit owned allocation, still bounded:
payload = message.read(max_bytes=64 * 1024 * 1024)
```

Retaining a Python chunk intentionally withholds ingress credit. Duplicate,
missing, overlapping, out-of-order, incomplete, and over-limit messages fail
without allocating an unbounded full-message buffer.

## Managed local ingress buffers

Transport and payload storage are independent. A local UDS session can carry
inline bytes or an opaque runtime-managed buffer reference.
VM/vsock carries inline bytes and does not advertise this route.

```swift
var options = DuplexOptions.default
options.requirements = .managedBuffers
options.managedBuffers = ManagedBufferConfiguration(
    preset: .throughput,
    maximumBufferBytes: 16 * 1_024 * 1_024,
    maximumBufferedBytes: 32 * 1_024 * 1_024
)

let session = try await pool.openDuplexSession(
    handler: handler,
    options: options
)
let buffer = try await session.input.acquireManagedBuffer(
    byteCount: encodedKeyframe.count,
    alignment: .page
)
try buffer.withUnsafeMutableBytes { destination in
    encodedKeyframe.copyBytes(to: destination)
}
try await session.input.sendMessage(
    buffer,
    format: DuplexFormat("video/hevc"),
    flags: [.independent]
)
```

The opaque handle belongs to one session. Sending ends CPU-write ownership.
Stale, cross-session, revoked, and double-send attempts fail locally. Python
sees a borrowed read-only view. Pool topology, generational counters, backing
paths, offsets, and quarantine state remain private Engine details.

Capacity becomes available only after the final peer, native, and GPU use has
ended. `session.managedBufferStatus` exposes only coarse capacity, bytes in use,
and availability. A caller-controlled path or naked public memory region is
never duplex send authority.

## Control, interruption, and terminal truth

`sendControl` transfers an application-control value to a bounded Python
inbox. Priority interruption uses a distinct reserve and carries the
application's consumed-output cursor:

```swift
let interruptionID = try await session.interrupt(
    reason: .inputActivity,
    consumedOutputThrough: playbackCursor
)
```

An interruption result is correlated by that ID. It may report truncation,
generation stopped without state truncation, already finished, or unsupported;
it is not a promise that arbitrary Python or GPU work is preemptible between
safe points.

`result()` waits for terminal truth and throws a typed `DuplexFailure` for a
failed terminal. Sessions remain pinned, never migrate or replay, and resolve
once with final accepted, processed, produced, and acknowledged watermarks.

## Native and VM transport limits

The live capability snapshot owns all limits. In this release, local UDS can
advertise a larger physical-frame ceiling than VM/vsock; logical-message limits
are negotiated separately and may exceed both. Do not hard-code the source
maximum as an application guarantee. State the minimum in
`DuplexSessionRequirements` and use
`session.negotiatedConfiguration` as truth.

The [DuplexSession example](../../Examples/DuplexSession/) executes frame
loopback and a fragmented message above its physical-frame ceiling.
