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

## Bounded macOS hardware readiness

`DuplexAudioHardwareProbeLauncher` is macOS-only. It launches one fresh helper
engine at the fixed path
`Bundle.main.bundleURL/Contents/MacOS/SwiftPythonAudioProbe`; it never searches
`PATH`, discovers a worker or build product, autobuilds, accepts a caller path,
uses XPC, or falls back to an in-process engine. Published
`0.6.0-duplex.7` predates this helper. A newer candidate exposing the launcher
is complete only when the exact matching raw helper is embedded, re-signed,
and verified with it.

The parent application owns microphone privacy policy:

- put a nonempty `NSMicrophoneUsageDescription` in the parent `Info.plist`;
- put `com.apple.security.device.audio-input` on a sandboxed parent;
- request the first microphone authorization through the app's UI; and
- run the launcher only when
  `DuplexAudioHardwareProbeLauncher.permissionState == .granted`.

The helper requires already-granted permission and never prompts. Sign it
before the outer app with the same team and the exact signing identifier
`<parent signing identifier>.SwiftPythonAudioProbe`. Use
`SwiftPythonAudioProbe.entitlements` for a non-sandbox parent or
`SwiftPythonAudioProbe-sandbox.entitlements` for sandbox inheritance. A
URL-based SwiftPM dependency does not auto-embed this raw executable.

```swift
enum ReadinessError: Error {
    case microphonePermissionNotGranted
}

let wire = try DuplexAudioFormat(
    sampleRate: 24_000,
    channels: 1,
    sampleType: .signedInteger16,
    interleaving: .interleaved
)
let request = try DuplexAudioHardwareProbeConfiguration(
    wireFormat: wire,
    durationSeconds: 2,
    timeoutSeconds: 30,
    requiresNonIdentityCaptureConversion: true
)

guard DuplexAudioHardwareProbeLauncher.permissionState == .granted else {
    throw ReadinessError.microphonePermissionNotGranted
}
switch try await DuplexAudioHardwareProbeLauncher.run(
    configuration: request
) {
case let .ready(report):
    precondition(report.engineScope == .isolatedChildProcess)
    precondition(report.metrics.captureHostTimestampFallbackCount == 0)
    precondition(report.metrics.captureClockResetCount == 0)
    precondition(report.metrics.captureHostClockResetCount == 0)
case let .notReady(failure):
    throw failure
@unknown default:
    fatalError("Update SwiftPython before interpreting a new probe result")
}
```

The launcher validates canonical path, static and suspended-process identity,
sandbox inheritance, strict schema-v1 I/O, and bounded TERM-then-KILL cleanup.
A `.ready` report proves only overlapping capture/playback on one fresh,
muted, helper-owned engine at that instant. It does not certify an existing
caller engine, reserve the route, or predict a future route. Production
capture/playback must still handle typed route and configuration changes.

Ready requires zero capture host-timestamp fallbacks, capture device-clock
resets, and capture host-clock resets. Keep
`playbackInvalidSampleTimeCount` as a separate diagnostic: downstream
sample-rate conversion can legitimately invalidate HAL callback sample time,
so that counter alone is not loss and is not a zero-required gate.

The commercial fixture exposes `SWIFTPYTHON_AUDIO_PROBE_GATE=off`,
`containment`, and `ready`. `containment` may observe a strict receipt or
establish bounded launch/cleanup around a typed device failure, but the fixture
never promotes that mode to release-gate evidence. Only `ready` satisfies the
notarized release device gate. The final non-sandbox and inherited-sandbox
fixtures are launched as quarantined stapled `.app` bundles through
LaunchServices, not by directly executing `Contents/MacOS`. The gate observes
the exact transient bundle identifier, relies on bundle worker discovery, and
requires one fresh nonce-bound success receipt before the app exits.

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
registered GPU read keeps the managed buffer unavailable for reuse. For GPU-write or
bidirectional access, `sendMessage` waits until Metal access and registered
commands complete successfully.

A registered GPU writer that fails makes that exact backing allocation
unavailable. Neither a later successful command nor a ledger entry revives it.
Cancellation or worker death with live GPU ownership also prevents unsafe
reuse. The underlying recovery counters and topology are private.

## Standalone Metal pool

`DuplexMetalRegionPool` is a bounded pool for adapter storage outside a
session-owned ingress pool. It records the actual fallback or copy path for
adapter-owned storage.

Use a session-owned `ManagedBuffer` when its mapped bytes must be sent through
managed local ingress. Use a standalone region pool when the
application needs bounded Metal staging but the transport route has different
copy semantics.
