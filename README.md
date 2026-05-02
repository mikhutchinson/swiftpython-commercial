# SwiftPython Commercial Runtime

Binary distribution of the SwiftPython runtime for macOS. This package provides a pre-built XCFramework, worker binary, and VM guest scripts for consuming SwiftPython functionality via Swift Package Manager.

**Latest release: `v0.4.0` — SandboxPool + protocol v4 VM supervisor runtime.** Ships the matched `SwiftPythonRuntime.xcframework`, `SwiftPythonWorker`, and `VMWorker/` Python guest scripts required by Ubuntu/Alpine VM provisioning. See [What's new in v0.4.0](#whats-new-in-v040) below.

**[API Guide →](docs/api-guide/)** — comprehensive reference covering the core runtime, type conversion, concurrency, ProcessPool, streaming, DAG orchestration, callbacks, and generated modules.

## Requirements

- macOS 15.0+
- Swift 6.0+
- Python 3.13 (Homebrew recommended)

## Installation

Add this package to your `Package.swift` dependencies:

```swift
dependencies: [
    .package(url: "https://github.com/mikhutchinson/swiftpython-commercial.git", from: "0.4.0")
]
```

Or use environment variables for dynamic resolution:

```bash
export SWIFTPYTHON_COMMERCIAL_PACKAGE_URL=https://github.com/mikhutchinson/swiftpython-commercial.git
export SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION=0.4.0
```

## What's new in v0.4.0

v0.4.0 is the VM sandbox release. It moves the VM backend from a candidate surface to a release artifact with the runtime, sidecar, and guest Python scripts built from the same SwiftPython commit.

**Wire-protocol compatibility**: v0.4.0 uses worker/supervisor protocol v4. Ship the v0.4.0 `SwiftPythonRuntime.xcframework`, `SwiftPythonWorker`, and `VMWorker/` directory together. Older sidecars should be treated as stale and replaced, not mixed with this runtime.

### New surface

- **`SandboxPool`** — actor-owned tenant pool with per-tenant secrets, lock files, drain-and-replace, crash diagnostics, quotas, and event reporting.
- **Ubuntu 24.04 ARM64 image builder** — two-phase build path: QEMU cloud-init first boot, then Virtualization.framework serial provisioning with Python/scientific packages, `uv`, supervisor, worker, and systemd service installation.
- **VM supervisor exec protocol** — `execShell`, `execShellStream`, and `execShellPTY` with separate stdout/stderr, stdin frames, resize frames, signal frames, output caps, and typed error mapping.
- **Supervisor policy enforcement** — defaults to sudo disabled, worker/exec children run as `user`, open-file limits use `RLIMIT_NOFILE`, CPU quota uses Linux cgroup v2 when constrained, and policy is configured after authenticated supervisor handshake.
- **Commercial VM script payload** — `VMWorker/swiftpython_supervisor.py` and `VMWorker/swiftpython_worker.py` are now shipped beside the XCFramework so binary consumers can provision VM images without SwiftPython source checkouts.

### Adoption

To adopt v0.4.0, pin `swiftpython-commercial` to `0.4.0`, re-resolve the package, copy the new `SwiftPythonWorker` into the app bundle, and leave the `VMWorker/` directory available in the package checkout or set `SWIFTPYTHON_VM_WORKER_DIR` to an explicit deployed copy.

## What's new in v0.2.1

A focused additive release that ships the public manual-respawn verb the v0.2.0 surface was missing, together with a fast-kill mode for the "agent stuck mid-bash, just kill it" case that cooperative cancel (SIGUSR1 + `swift_bridge.check_cancel()`) cannot solve.

**Wire-protocol compatibility**: v0.2.1 ships the same wire format as v0.2.0; the protocol-version handshake floor stays at `2`. A v0.2.0 framework spawning a v0.2.1 worker (or vice versa) handshakes successfully — no migration is forced on consumers staying on v0.2.0.

### New surface

- **`public func respawnWorker(_:reason:force:)`** on `PythonProcessPool` (was `internal` in v0.2.0). Default reason is `.userInitiated`; `force: false` preserves the v0.2.0 graceful path (SIGTERM → 500ms grace → SIGKILL).
- **`force: true`** parameter that skips the SIGTERM grace and SIGKILLs the existing process immediately (~50ms vs the graceful path's ~1s). Use when the worker is wedged inside a syscall (`subprocess.run`, `time.sleep`) where polite shutdown is pointless waiting.
- **`PoolEvent.RespawnReason.userInitiated`** — the default reason on the public verb. Distinct from `.explicit` (which is reserved for internal/test programmatic respawns) so subscribers can filter end-user actions from telemetry-driven recycles.
- **`PythonWorkerError.workerForciblyRespawned(workerID:)`** — surfaced to any in-flight caller whose worker was killed by a concurrent `respawnWorker(_:force: true)`. Categorically distinct from `.workerCrashed` so hosts can surface a "stopped" banner instead of a "crashed" banner and skip crash-report / blacklisting paths. **Excluded from the pool's transparent-retry path** so the runtime does not silently re-execute work the user just told us to abandon.

### Adoption

```swift
// User-facing menu command:
try? await pool.respawnWorker(workerIndex)  // graceful, default reason: .userInitiated

// Emergency stop button (escalate from cooperative cancel):
try? await pool.respawnWorker(workerIndex, force: true)
```

```swift
// Distinguish user-driven cancellation from real crashes:
catch let error as PythonWorkerError {
    switch error {
    case .workerForciblyRespawned(let id):
        showStoppedBanner(workerID: id)   // not a crash — user requested it
    case .workerCrashed(let id, _):
        showCrashedBanner(workerID: id)   // real crash — escalate
    default: throw error
    }
}
```

See the Migration v0.2.0 → v0.2.1 section above for the recommended host pattern (cooperative-cancel → 2s deadline → force-respawn escalation) and the honest scope of what `force: true` does NOT solve (worker child processes survive SIGKILL; respawn budget still applies).

## What's new in v0.2.0

The v0.2.0 release closes seven structural gaps the v0.1.x streaming primitive had — across nine independently-gated phases organised in three parallel tracks plus a convergence step.

**v0.2.0 is wire-compatible** with v0.1.x consumers when `IPCConfiguration.requiredProtocolVersion = 1` is set (emergency rollback), and **source-compatible** with v0.1.x consumers using the legacy 18 stream overloads (which stay shimmed unchanged in v0.2.0 and are deprecated-and-removed in a single v0.3.0 release).

| Track | What landed | New consumer-facing API |
|-------|-------------|--------------------------|
| **Wire/Protocol** | Versioned wire handshake (`requiredProtocolVersion`), per-stream channel IDs, worker-emitted `streamKeepalive` (default 5s), user-emitted `streamProgress` (`swift_bridge.progress(...)`), per-stream `streamCancel`, cooperative `swift_bridge.check_cancel()`, opt-in `PyErr_SetInterrupt` injection, stream-scoped respawn on `respawnOnTimeout`. | `IPCConfiguration.streamKeepaliveInterval`, `.allowInterruptInjection`, `.requiredProtocolVersion` |
| **Ergonomics** | `StreamOptions` collapses 18 stream overloads → 9 modern entry points; `OwnedPyHandle` eliminates `defer { Task { try? await pool.release(h) } }` boilerplate via ARC-driven release. | `StreamOptions(timeout:workerAffinity:surfaceProgressEvents:)`, `pool.evalOwned()` / `invokeOwned()` / `methodOwned()` returning `OwnedPyHandle` |
| **Observability** | `pool.events()` multi-subscriber lifecycle stream; `StreamEvent<T>` typed value+progress streams (wire-order preserved); `PoolEvent.callbackOrphaned` for in-flight callbacks lost to worker death. | `pool.events()` → `AsyncStream<PoolEvent>`, `*EventStream<T>` returning `CancellableStream<StreamEvent<T>>` |
| **Hardening** | Worker `sendLock` write-mutex; `executeStream` releases the GIL between iterations (enables side-channel daemon + keepalive timer thread); cross-track integration tests + chaos scenario verifying all phases compose under simultaneous failure. | (transparent to consumers — fewer wedges, cleaner cleanup on failure) |

### What's deletable in your v0.1.x consumer code

If your code includes any of the following workarounds, v0.2.0 lets you delete them. See the sections above for before/after recipes.

| v0.1.x scaffolding | v0.2.0 primitive that replaces it |
|--------------------|------------------------------------|
| Per-chunk timeout watchdogs (`Task.sleep` then check stream progress) | `IPCConfiguration.streamKeepaliveInterval` (worker emits liveness on a clock) |
| Manual ticker plumbing emitting "still alive" frames from Python | `IPCConfiguration.streamKeepaliveInterval` (same job, no user code) |
| Disambiguating progress markers from real values in pickled data | `StreamEvent<T>` with `.value(T)` / `.progress(elapsedMs:hint:)` cases |
| `defer { Task { try? await pool.release(handle) } }` everywhere | `pool.evalOwned(...)` + `OwnedPyHandle` — release on scope exit |
| Polling `pool.respawnCount(for:)` to detect crashes | Subscribe to `pool.events()` for `.workerRespawned` / `.workerDied` |
| Custom orphan-callback bookkeeping when workers crash mid-callback | `PoolEvent.callbackOrphaned(workerID:callID:callbackName:kind:)` |

### Wire protocol compatibility

| Pool config | Worker version | Outcome |
|-------------|----------------|---------|
| Default v0.2.0 | v2 worker (this release) | Full v0.2.0 surface, all features active |
| `requiredProtocolVersion: 1` | v2 worker (this release) | Degraded mode — v0.2.0 features emit on the wire but consumers see legacy semantics |
| Default v0.2.0 | v1 worker (legacy v0.1.x binary) | **Spawn fails fast** with `PythonWorkerError.protocolError` |
| `requiredProtocolVersion: 1` | v1 worker (legacy v0.1.x binary) | Legacy v1 wire only |

For an emergency rollback to a v0.1.x worker binary while still using a v0.2.0 framework:

```swift
let ipc = IPCConfiguration(requiredProtocolVersion: 1)
let pool = try await PythonProcessPool(workers: 4, ipc: ipc)
```

This disables every v0.2.0 feature (channel IDs, keepalive, progress, multiplex cancel) for that pool but keeps the spawn loop alive. New code should never need this; it exists to catch misconfiguration silently rather than wedging.

### Wire-protocol reference

For the full v0.2.0 wire format, see the [API Guide](docs/api-guide/) shipped with this package.

## Usage

The package provides three runtime artifacts:

- `SwiftPythonRuntime.xcframework` — The runtime library (link against this target)
- `SwiftPythonWorker` — Sidecar process for Python execution
- `VMWorker/` — Python supervisor/worker scripts installed into Linux VM images

Your app bundle should include the `SwiftPythonWorker` binary alongside your main executable. Consumers that build VM images outside an SPM checkout should also deploy `VMWorker/` and set `SWIFTPYTHON_VM_WORKER_DIR` to that directory.

## Linker Configuration

The XCFramework links against Python 3.13. Ensure your `Package.swift` includes the appropriate linker flags:

```swift
linkerSettings: [
    .unsafeFlags([
        "-L/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib",
        "-lpython3.13"
    ])
]
```

## App Bundle Structure

When building a `.app` bundle, place resource bundles at the app root and include the worker in `Contents/MacOS/`:

```
YourApp.app/
├── Contents/
│   ├── MacOS/
│   │   ├── YourApp              ← main binary
│   │   └── SwiftPythonWorker    ← sidecar (required)
│   └── Info.plist
└── [SPM resource bundles]
```

A wrapper launcher script is recommended to set `PYTHONHOME` for Finder/Dock launches:

```bash
#!/bin/bash
export PYTHONHOME="/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13"
export PATH="$PYTHONHOME/bin:$PATH"
exec "$(dirname "$0")/YourApp.bin"
```

## Entitlements & Code Signing

The `SwiftPythonWorker` and your consumer app both need specific entitlements to work under macOS [Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime) (required for notarization) and optionally [App Sandbox](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app).

### Why entitlements are needed

Python loads ad-hoc signed `.so` extension modules (NumPy, Torch, etc.) and uses `mmap(PROT_WRITE|PROT_EXEC)` internally. Under Hardened Runtime, macOS blocks both behaviors unless the executable carries the appropriate code signing exceptions. Since the worker is a separate process (spawned via `posix_spawn`), it needs its own entitlements — it does not inherit from your app.

### Worker (pre-signed)

The `SwiftPythonWorker` ships pre-signed with hardened runtime, an embedded `CFBundleIdentifier`, and 3 code signing exceptions:

| Entitlement | Purpose |
|-------------|---------|
| `cs.allow-unsigned-executable-memory` | Python/NumPy/SciPy use `mmap(PROT_EXEC)` without `MAP_JIT` |
| `cs.disable-library-validation` | Load ad-hoc signed `.so` modules and Homebrew's `libpython` |
| `cs.allow-dyld-environment-variables` | Safety net for non-Homebrew Python installs using `DYLD_LIBRARY_PATH` |

**After copying the worker into your `.app` bundle, you must re-sign it** — copying invalidates the signature:

```bash
codesign --force --sign - --options runtime \
  --entitlements Entitlements/SwiftPythonWorker.entitlements \
  "YourApp.app/Contents/MacOS/SwiftPythonWorker"
```

### Consumer app

Your app binary links `libpython3.13` (via the XCFramework static lib) and needs its own entitlements. Use the `Entitlements/ConsumerApp.entitlements` template:

```bash
codesign --force --sign - --options runtime \
  --entitlements Entitlements/ConsumerApp.entitlements \
  "YourApp.app/Contents/MacOS/YourApp"
```

**Sign innermost first** (worker, then app).

### Signing identity: ad-hoc vs Apple Development

| Identity | Command | Hardened runtime CS exceptions | Keychain access |
|----------|---------|-------------------------------|-----------------|
| Ad-hoc | `--sign -` | Works | Login keychain works, but macOS prompts on every rebuild (binary hash changes → new ACL entry) |
| Apple Development | `--sign "Apple Development: Name (ID)"` | Works | Login keychain works with **no prompts** across rebuilds (same identity = stable ACL) |
| Developer ID | `--sign "Developer ID Application: Name (ID)"` | Works | Required for notarization |

**Ad-hoc signing is sufficient** for the hardened runtime CS exceptions that SwiftPython needs. However, if your app uses the macOS **login keychain** (`SecItemAdd`/`SecItemCopyMatching`), ad-hoc signing causes a password prompt on every rebuild because each build produces a different binary hash, and the legacy keychain's ACL is per-hash.

**Recommended**: Sign with an Apple Development identity during development. Auto-detect it in your build script:

```bash
IDENTITY=$(security find-identity -v -p codesigning | grep "Apple Development" | head -1 | awk -F'"' '{print $2}')
SIGN_ID="${IDENTITY:--}"  # falls back to ad-hoc if no identity found
codesign --force --sign "$SIGN_ID" --options runtime \
  --entitlements YourApp.entitlements \
  "YourApp.app/Contents/MacOS/YourApp"
```

> **Note on data protection keychain**: `kSecUseDataProtectionKeychain: true` requires a provisioning profile embedded in the app bundle — signing with entitlements alone (even `keychain-access-groups`) returns `errSecMissingEntitlement (-34018)`. Use the standard login keychain for SPM-based apps without Xcode projects.

### App Sandbox (Mac App Store)

A separate `SwiftPythonWorker-sandbox.entitlements` is provided for Mac App Store distribution where the parent app is sandboxed. It adds `app-sandbox` + `inherit` so the worker inherits the parent's sandbox profile.

> **Warning**: Do NOT use the sandbox entitlements if the parent app is not sandboxed. `app-sandbox: true` always activates sandboxing — when the parent isn't sandboxed, the worker gets a default restrictive sandbox that blocks Python file access (`/opt/homebrew/`, site-packages, etc.), causing it to hang on initialization.

To use:
1. Sign the worker with `SwiftPythonWorker-sandbox.entitlements` instead of `SwiftPythonWorker.entitlements`
2. Enable App Sandbox on your consumer app (uncomment sandbox keys in `ConsumerApp.entitlements`)
3. Ensure Python framework + site-packages are accessible within the sandbox (bundled in the app or in a sandbox-readable location)

> **Caveat**: `com.apple.security.get-task-allow` (injected by Xcode in debug builds) is incompatible with `com.apple.security.inherit`. If the worker crashes on launch during development, ensure "Code Sign On Copy" is checked in your Xcode build phase, or set `CODE_SIGN_INJECT_BASE_ENTITLEMENTS = NO`.

## Troubleshooting

| Issue | Resolution |
|-------|------------|
| `Library not loaded: libpython3.13.dylib` | Ensure `PYTHONHOME` and `DYLD_LIBRARY_PATH` are set correctly |
| `compiled module was created by a different version of the compiler` | Rebuild your project with the same Swift version used to build this XCFramework |
| SPM fingerprint mismatch | Delete `~/Library/org.swift.swiftpm/security/fingerprints`, `.build/`, and `Package.resolved`, then re-resolve |
| `VMWorker scripts not found` from an image builder | Use the v0.4.0 commercial package checkout with `VMWorker/` present, copy `VMWorker/` beside the consuming tool, or set `SWIFTPYTHON_VM_WORKER_DIR=/path/to/VMWorker` |
| `PythonWorkerError.protocolError(...)` mentioning protocol v4 | Runtime and sidecar are not the matched v0.4.0 pair. Re-resolve `swiftpython-commercial`, copy the v0.4.0 `SwiftPythonWorker`, and re-sign it in the app bundle. |
| `PythonWorkerError.protocolError("Worker N speaks protocol v1; pool requires v2 or higher")` (v0.2.0+) | Your sidecar `SwiftPythonWorker` binary is from v0.1.x but the framework is v0.2.0. Update the worker binary to the v0.2.0 release (re-resolve SPM, copy the new worker into your `.app` bundle, re-sign). For an emergency rollback to keep using a v0.1.x worker binary, set `IPCConfiguration(requiredProtocolVersion: 1)` — this disables every v0.2.0 feature for that pool. See [What's new in v0.2.0 § Wire protocol compatibility](#whats-new-in-v020) above. |
| `from swift_bridge import progress` raises `ImportError` (v0.2.0+) | The `progress()` Python helper is installed lazily on first stream invocation. Defer the `import` to runtime inside the generator function: `def gen(): from swift_bridge import progress; ...`. See the v0.2.0 section above. |

## Version History

| Build | Date | Notes |
|-------|------|-------|
| 0.4.0 | 2026-04-27 | **Current. SandboxPool + protocol v4 VM supervisor runtime** — real Ubuntu 24.04 image builder, VM-backed tenant pool, authenticated supervisor configure command, exec capture/stream/PTY, stdin/resize/signal frames, sudo/RLIMIT/cgroup policy enforcement, crash diagnostics, and packaged `VMWorker/` scripts for commercial binary consumers. |
| 0.3.0 | 2026-04-23 | Multi-stream worker protocol v3 — same-worker stream multiplexing, protocol v3 handshake, public surface pruning, and matched sidecar rebuild. |
| 0.2.1 | 2026-04-23 | Public respawn surface + force-kill fast-path — promotes `PythonProcessPool.respawnWorker(_:reason:force:)` from internal to public; adds `force: true` SIGKILL fast-path (~50ms vs graceful ~1s) for the "agent stuck mid-bash" case where cooperative cancel cannot break a worker out of a blocking syscall; adds `PoolEvent.RespawnReason.userInitiated` (default reason on the public verb) and `PythonWorkerError.workerForciblyRespawned(workerID:)` so hosts can distinguish user-driven kills from real crashes. **Wire-compatible with v0.2.0 in either direction** — no protocol-level changes; the handshake floor stays at `2`.  |
| 0.2.0 | 2026-04-20 | Streaming overhaul — versioned wire protocol (handshake floor v2), per-stream channel IDs, `streamKeepalive` + `streamProgress` frames, `StreamOptions` (collapses 18 stream overloads → 9 modern entry points), `OwnedPyHandle` (ARC-driven release), `pool.events()` lifecycle observability, `StreamEvent<T>` typed value+progress streams, `PoolEvent.callbackOrphaned` for in-flight callbacks, cooperative `swift_bridge.check_cancel()` + opt-in `PyErr_SetInterrupt` injection, stream-scoped respawn. Wire-compatible with v0.1.x consumers via `requiredProtocolVersion: 1`. Legacy 18 stream overloads stay shimmed; deprecated-and-removed in a single v0.3.0 release. See the v0.2.0 section above. |
| 0.1.26 | 2026-04-17 | Fix XCFramework layout for xcodebuild consumers — `EmitSwiftModule` previously failed with `cannot find type 'PyObjectRef'` because Xcode 15+ explicit-modules only registers the slice via `Info.plist`'s `HeadersPath` and does not probe `Headers/` for nested `.swiftmodule` directories. The xcframework now ships the Swift module in both `<slice>/SwiftPythonRuntime.swiftmodule/` (Apple-canonical, xcodebuild) and `<slice>/Headers/SwiftPythonRuntime.swiftmodule/` (SPM back-compat). Bump consumer's `SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION` to `0.1.26` and re-resolve. Also includes Linux CI fixes: `SHUT_RDWR` Int32 cast and `PythonVMWorkerTests` `canImport(Darwin)` gate. |
| 0.1.25 | 2026-04-15 | Semver tag for SPM pinning; same binaries as v0.1.24. Restored fingerprint-safe versioning after a force-push incident on v0.1.24. |
| 0.1.24 | 2026-04-15 | API gap fixes (16 items) — `PyObjectRef.isNone` (C-level Py_IsNone check, zero overhead), `PyObjectRef.typeName`, `PyObjectRef: CustomStringConvertible/CustomDebugStringConvertible`, `PyObjectRef.pyEquals(_:)` (Python-level `==`), `PyObjectRef.count` + `getItem(_:)` throwing variants, dict `subscript(pyKey:)` + `setItem(pyKey:value:)`, `Python.str/repr/type` static convenience, `Float: PythonConvertible` round-trip, all 8 fixed-width integer types as `PythonConvertible`, `Bool` round-trip, 4-arg typed callback overload. 163 new `APIGapTests`. Also: pyList double-wrap fix; build is now warning-free workspace-wide. |
| 0.1.23 | 2026-03-28 | Semver tag for SPM pinning; same binaries as v0.1.22. |
| 0.1.22 | 2026-03-28 | macOS 26: fix `EXC_BAD_ACCESS` in Swift concurrency (`swift_task_isMainExecutorImpl`) — remove `SerialExecutor` conformance from `PythonThreadExecutor`. |
| 0.1.21 | 2026-03-26 | Rebuild SwiftPythonWorker (stderr/EPIPE hardening) and XCFramework. |
| 0.1.20 | 2026-03-26 | Worker IPC recv serialization and nested stream routing. |
| 0.1.19 | 2026-03-26 | Iteration on macOS 26 executor crashes; superseded by v0.1.22. |
| 0.1.18 | 2026-03-26 | Fix `PythonThreadExecutor` dangling `unowned` reference crash (macOS 26). |
| 0.1.17 | 2026-03-15 | Socket paths for App Sandbox — `NSTemporaryDirectory()` + short filenames (`sun_path` limit). |
| 0.1.16 | 2026-03-15 | Socket directory: use `/tmp` directly, no subdirectory (sandbox cannot `mkdir`). |
| 0.1.15 | 2026-03-15 | AF_UNIX path length — prefer `/tmp` over long temp paths. |
| 0.1.14 | 2026-03-14 | XCFramework via `xcodebuild -create-xcframework`; library evolution; Developer ID signing. |
| 0.1.13 | 2026-03-04 | Rebuild with CI hardening improvements. |
| 0.1.12 | 2026-03-03 | `SharedRingBuffer` atomicity; eager persistent namespace init; `_oob_writer` liveness. |
| 0.1.11 | 2026-03-03 | `SharedRingBuffer` out-of-band streaming; `DispatchSourceMemoryPressure` fix (Darwin). |
| 0.1.10 | 2026-03-02 | Semver tag; same revision as the v0.1.9.20260302 OOB streaming drop. |
| 0.1.9 / 0.1.9.20260302 | 2026-03-02 | Broadcast hardening; ggml-metal atexit SIGABRT fix; `SharedRingBuffer` OOB streaming. |
| 0.1.8 | 2026-03-01 | Side channel (`sideEval`): fire-and-forget Python eval via dedicated UDS socket per worker — safe to call during active streams; stream queue (`enqueue`/`dequeue`): ring-buffer data feed from Swift into Python generators with backpressure; proper `SideCommand` codec in `MessageFrame`; daemon thread shutdown hardened (stop flag + semaphore + GIL fence); 412 tests passing |
| 0.1.7+build.20260228 | 2026-02-28 | Fix: `sendResponse` write race — `sendLock: NSLock` serializes concurrent `send(2)` calls from main stream thread and Python daemon threads; regression test `testConcurrentDaemonThreadCallbackDuringStream`; warning fixes in test files; macOS/Linux build configuration split; 957 tests passing |
| 0.1.7+build.20260227 | 2026-02-27 | Memory-pressure-aware worker lifecycle; stream timeout socket drain; unified post-spawn setup (`configureSpawnedWorker`); dispatch_source_memorypressure actor isolation fix; MLX SIGBUS fix; worker `MSG_NOSIGNAL` on Linux; `persistentNamespace` cleanup on shutdown |
| 0.1.7 | — | See [GitHub Releases](https://github.com/mikhutchinson/swiftpython-commercial/releases) for prior release details |

## License

Commercial license. See LICENSE file for terms.
