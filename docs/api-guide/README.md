# SwiftPython Public API Guide

This guide documents the public binary API shipped by
`swiftpython-commercial` `0.6.0-duplex.8.1`.
It is written for application authors who need to embed Python execution in a
macOS app, worker service, or sandboxed tool without access to the private
SwiftPython source tree.

The private SwiftPython implementation, generator pipeline, and internal test
layout are intentionally not documented here. Everything below is supported
from the public package artifacts: `SwiftPythonRuntime.xcframework`, its
code-only private dependency `SwiftPythonEngine.xcframework`, optional
`SwiftPythonAudioInterop.xcframework` and
`SwiftPythonMetalInterop.xcframework`, the matched `SwiftPythonWorker`,
the five-file `VMWorker/` set, and the entitlement templates.

## Chapters

| Chapter | File | Use it for |
|---------|------|------------|
| 1 - Core Runtime | [ch1-core-runtime.md](ch1-core-runtime.md) | In-process `Python.run`, dynamic Python objects, errors, context managers |
| 2 - Data Interop | [ch2-data-interop.md](ch2-data-interop.md) | Swift/Python conversion, remote arguments, buffers, shared tensors |
| 3 - Concurrency & Handles | [ch3-concurrency-handles.md](ch3-concurrency-handles.md) | GIL rules, `PyHandle`, `OwnedPyHandle`, handle lifetime |
| 4 - ProcessPool | [ch4-process-pool.md](ch4-process-pool.md) | Multi-process workers, lifecycle, telemetry, events, resource limits |
| 5 - Streaming | [ch5-streaming.md](ch5-streaming.md) | Python generators, cancellation, progress events, long-running streams |
| 6 - DAG Orchestration | [ch6-dag.md](ch6-dag.md) | Dependency-aware parallel jobs over a pool |
| 7 - Callbacks | [ch7-callbacks.md](ch7-callbacks.md) | Python calling Swift functions, reentrant work, streaming callbacks |
| 8 - Python Packages | [ch8-generated-modules.md](ch8-generated-modules.md) | Building app-level wrappers over NumPy, Pandas, ML, CLI tools, or your own modules |
| 9 - Sandbox & VM Exec | [ch9-sandbox-vm.md](ch9-sandbox-vm.md) | Ubuntu VM tenants, shell exec, PTY sessions, VM-backed pools |
| 10 - Full-Duplex Sessions | [ch10-full-duplex.md](ch10-full-duplex.md) | Capabilities, frames, logical messages, shared arena, control, lifecycle |
| 11 - Audio & Metal Interop | [ch11-apple-interop.md](ch11-apple-interop.md) | Optional AVAudio adapters, Metal leases, GPU completion, copy evidence |

## Decision Map

| Goal | Public API |
|------|------------|
| Run small Python code in the app process | `try await Python.run { ... }` |
| Convert Swift values to/from Python | `PythonConvertible`, `pyList`, `pyDict`, `PythonBuffer` |
| Retain in-process Python identity across actor/task boundaries | `PyObjectRef` plus executor/GIL-scoped operations, or `PythonObjectRef` |
| Hold a remote worker object with automatic cleanup | `OwnedPyHandle` |
| Run CPU-bound Python in parallel | `PythonProcessPool` |
| Send and receive concurrently on one pinned worker generation | `PythonDuplexSession` |
| Send a bounded application unit larger than one media frame | `DuplexInput.sendMessage` with `.messages` requirements |
| Send local bytes through a runtime-managed handle | `acquireManagedBuffer` with `.managedBuffers` requirements |
| Capture/play PCM without session calls from the realtime callback | `SwiftPythonAudioInterop` |
| Retain Metal storage through GPU completion and record actual copies | `SwiftPythonMetalInterop` |
| Keep work pinned to one worker | `pool.worker(index)` / `StreamOptions.pinned(worker:)` |
| Trace command, callback, stream, side-channel, and respawn lifecycle | `pool.telemetry()` + `ProcessPoolTelemetryContext` |
| Stream a Python generator | `evalStream`, `invokeStream`, `methodStream` |
| Stream values plus progress | `evalEvents`, `invokeEvents`, `methodEvents` |
| Stream from a worker without holding its IPC socket | `startOutputStream` + `ManagedOutputBuffer` |
| Share an opaque managed tensor across host and workers | `createManagedTensor`, `withManagedTensor`, `copyToManagedTensor` |
| Observe worker lifecycle | `pool.events()` |
| Await exact custom-transport release after idle shedding | `shedIdleWorkersAndWait(force:)` |
| Run shell commands inside a Linux VM tenant | `SandboxPool.execShell` |
| Run an interactive terminal in a tenant | `SandboxPool.execShellPTY` |
| Require accelerated isolated startup | `SandboxConfiguration.startup` plus `tenant.startupMode` |
| Let Python call Swift | `registerCallback`, `registerReentrantCallback`, `registerStreamingCallback` |
| Pool callbacks / reentrant / `evalEvents` in one runnable CLI | `Examples/BridgingRing` |
| Managed tensor + output streaming in one demo | `Examples/SharedTensorPipeline` |
| Frame and fragmented-message duplex in one demo | `Examples/DuplexSession` |
| Smoke-test wiring (`Python.run`, process pool CLIs) | `Examples/CoreRuntimeSmoke`, `Examples/ProcessPoolSmoke` |
| Start from a complete macOS app | `Examples/IrisDemo` |

## Public Package Boundary

This public repository is the binary distribution for SwiftPython users on the
AGPL-3.0 path, the free Small Organization Commercial Grant, or a written
commercial-license path. Use the documented runtime APIs to build your app-level
integration. Do not depend on private source paths, private generated bindings,
internal test fixtures, or implementation details from a SwiftPython source
checkout.

When in doubt, treat the public Swift interface inside the XCFramework as the
contract and keep your app code behind your own small facade.
