# SwiftPython Particle Showcase

**1,048,576 particles. NumPy moves them. Metal draws them.**

A native Mac example with four formations: galaxy, double helix, moving wave,
and **SWIFT / PYTHON** typography. Switching formations changes the targets of
the existing particles. Scatter gives them a velocity impulse; the springs pull
them back. This is a procedural spring simulation.

## Run

From the commercial repository root:

```bash
Examples/ParticleShowcase/run.sh
```

Requires macOS 15+, Metal and Xcode command-line tools. The builder downloads
hash-locked NumPy wheels and the SwiftPM runtime artifacts, then creates
`Examples/ParticleShowcase/build/Particle Showcase.app`. The app bundles Python,
NumPy and the exact matched worker. Running it needs no Python installation,
network service, API key, microphone or camera.

Controls: choose a formation, **Space** to pause/resume, **B** to scatter,
**Auto** to cycle formations, and **⌘S** to save a clip. Reduce Motion starts the
simulation paused and disables automatic cycling; Resume explicitly starts it.
The app is ad hoc signed for local development. See the
[example index](../README.md) for build prerequisites and dependency licenses.

## Record

```bash
Examples/ParticleShowcase/run.sh --export /tmp/swiftpython-particle-clip
```

This writes a **1920×1080, 22-second, 30 fps H.264 MP4**, five PNG stills and
`receipt.json`. Use a new directory for each export. The video uses actual
NumPy/Metal frames from the same engine as the app, on a deterministic timeline.
Encoding runs offline. The video's 30 fps timebase is distinct from the live
window's measured frame rate; the video labels its timings accordingly.

Optional bounds: `--seconds 1...30`, `--fps 20...60`, and `--particles N`
(1024...1048576, in multiples of 1024). Short exports stop at that point in the
timeline. The default uses all four formations.

## What crosses the boundary

[`ParticleEngine.swift`](Sources/ParticleShowcase/ParticleEngine.swift) allocates
one N×4 float32 array with public `createManagedTensor`. Python receives the
tensor through a handle binding and retains it as its output array. Frame
commands carry a formation, time, timestep and impulse number; replies carry
two numeric timing/sequence values. The particle array is never converted to a
Swift `[Float]` or serialized per frame.

[`particles.py`](Sources/ParticleShowcase/Resources/particles.py) contains all
particle motion. Python also retains target formations, velocities and scratch
arrays. **16 MiB** is the shared particle array's size, not total app memory.

The frame's ownership sequence is explicit:

1. Await the Python step so it finishes writing the array.
2. Enter public `withManagedTensor` and validate the actual address and size.
3. On unified-memory Metal devices with page-aligned storage, wrap those pages
   using `makeBuffer(bytesNoCopy:...)`. Verify Metal preserves the address.
4. Submit rendering and a small GPU verification probe; wait for GPU completion
   **inside the tensor scope**. No pointer or Metal view of it escapes.
5. Return to the loop; only then may Python write the next frame.

An unsupported memory layout/device uses an explicit particle upload and reports
its copied byte count. The separate BGRA image readback used by the video
encoder is also a copy. The **zero-copy claim applies to particle payload
handoff on the measured shared route**. NumPy's computation and output-image
encoding still perform ordinary memory work.

The renderer follows Apple's documented
[`makeBuffer(bytesNoCopy:...)` requirements](https://developer.apple.com/documentation/metal/mtldevice/makebuffer(bytesnocopy:length:options:deallocator:)).
The scope and synchronous GPU completion are deliberate example-level ownership
policy; they do not add a general asynchronous GPU lease to the tensor API.

## Verify

```bash
Examples/ParticleShowcase/run.sh --smoke /tmp/swiftpython-particle-smoke
PYTHONDONTWRITEBYTECODE=1 python3.13 Examples/ParticleShowcase/Tests/test_particles.py
```

Use the Python executable configured for this checkout when running the small
kernel checks. The smoke run uses the real worker and Metal device. At each of
four formation boundaries it verifies finite coordinates, distinct worker/host
PIDs, a full Python/Swift SHA-256 match, GPU-read sample words, and a changed
buffer digest. It then shuts down the pool and checks that its worker was reaped.

`receipt.json` records every exported frame's Python, GPU, and complete
step/render durations; median/p95 timings; copy route; actual device; and the
final buffer proof. These are local measurements, not a comparison with another
runtime or an ecosystem-wide reliability claim.

Native controls and motion/accessibility choices follow the
[Apple HIG](https://developer.apple.com/design/human-interface-guidelines/),
[macOS guidance](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos),
and [accessibility guidance](https://developer.apple.com/design/human-interface-guidelines/accessibility).
