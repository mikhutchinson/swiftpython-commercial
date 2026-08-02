# Chapter 11 - Audio and Metal Interop

The generic duplex API lives in `SwiftPythonRuntime`. Apple-native integration
is opt-in:

```swift
import SwiftPythonAudioInterop
import SwiftPythonMetalInterop
```

These are separate binary products, so a core consumer does not acquire their
AVFAudio or Metal linkage.

## Platform and release scope

The executed commercial gate is macOS 15 on Apple Silicon. Source availability
also permits the adapters on supported iOS 18 and tvOS 18 builds, with watchOS
unavailable. That annotation is not a claim that the ProcessPool sidecar or
every adapter path is distributed and executed on every Apple platform.

## PCM format

`DuplexAudioFormat` validates sample rate, channel count, signed-int16 or
float32 samples, interleaving, bytes per frame, and payload alignment.

```swift
let pcm = try DuplexAudioFormat(
    sampleRate: 24_000,
    channels: 1,
    sampleType: .signedInteger16,
    interleaving: .interleaved
)

var options = DuplexOptions.default
options.inputFormat = pcm.duplexFormat
options.outputFormat = pcm.duplexFormat
```

The generic media protocol treats the result as an opaque `DuplexFormat`;
the audio adapter owns PCM interpretation.

`DuplexAudioClockMapper` correlates device sample time and host time with the
continuous clock and duplex position. Device time, media timestamps, and the
playback-consumed cursor remain distinct observations.

## Realtime boundary

AVAudio callbacks touch only preallocated SPSC storage and atomics. They do not
await, acquire the GIL, call a session endpoint, log, allocate per frame, or
export telemetry. Async pumps outside the callback thread interact with
`DuplexInput` and `DuplexOutput`.

`DuplexAudioCapture` records a discontinuity when its fixed capture ring
overflows. `DuplexAudioPlayback` copies lease-backed output into a fixed
playback ring and acknowledges only the samples the device callback consumed.
`stopAndAcknowledge(session:)` requests callback-owned silence, waits a
bounded render quantum, and publishes the final consumed cursor.

Lifecycle notifications are observations. The application decides whether an
audio interruption should finish input, interrupt model output, cancel, or
reopen a later session.

## Route-specific Metal evidence

`DuplexMetalRoute` distinguishes:

- `.arenaSharedNoCopy`;
- `.ownedSharedCopy(reason:)`; and
- `.socketDirectKernelCopy`.

`DuplexCopyLedger` records logical bytes, copied bytes, route, status, and a
bounded segment name. A zero-copy entry proves only the named segment and
route; it is not a pipeline-wide claim.

```swift
let ledger = DuplexCopyLedger()
let metalLease = try sharedLease.makeMetalBufferLease(
    device: device,
    access: .cpuWritesGPUReads,
    ledger: ledger
)
```

A page-aligned session-owned arena lease can map the same pages into an
`MTLBuffer`. This proves arena-to-Metal pointer identity. It does not prove
IOSurface-to-arena, capture-source-to-Python, socket, or VM zero copy.

## Ownership and GPU completion

`DuplexMetalAccess` declares CPU-write/GPU-read, GPU-write/CPU-read, or
bidirectional ownership. Register every command that accesses a lease:

```swift
try metalLease.retainUntilCompleted(by: commandBuffer)
try metalLease.finishMetalAccess()
commandBuffer.commit()
```

For CPU-write/GPU-read, send may proceed after CPU write ownership ends while a
registered GPU read keeps the slot unavailable for reuse. For GPU-write or
bidirectional access, `sendMessage` waits until Metal access and registered
commands complete successfully.

A registered GPU writer that fails poisons that exact arena generation.
Neither a later successful command nor a ledger entry revives it; the slot is
quarantined. Cancellation or worker death with live GPU ownership also prevents
unsafe reuse.

## Standalone Metal pool

`DuplexMetalRegionPool` is a bounded pool for adapter storage outside a
session-owned ingress pool. It can use a `SharedMemoryArena`, owned shared
storage, or a VM route and records the actual fallback/copy path. Its snapshot
reports fixed region count, available/leased/quarantined regions, backing
bytes, and reuse count.

Use a session-owned `DuplexSharedBufferLease` when the exact mapped pages must
be sent through local arena ingress. Use a standalone region pool when the
application needs bounded Metal staging but the transport route has different
copy semantics.
