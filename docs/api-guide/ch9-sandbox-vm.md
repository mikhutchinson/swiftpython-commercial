# Chapter 9 - Sandbox and VM Exec

`SandboxPool` manages Linux VM tenants backed by SwiftPython's VM worker
runtime. Use it when you need stronger isolation than a local process pool:
tenant-specific dependencies, shell tools, untrusted jobs, per-tenant secrets,
or Linux-only packages.

The `0.6.0-duplex.2` commercial release certifies this surface with one
same-version source revision. Keep all of these pieces together:

- `SwiftPythonRuntime.xcframework` and the matched local sidecar
- `VMWorker/_swiftpython_wire.py`
- `VMWorker/_swiftpython_duplex.py`
- `VMWorker/swiftpython_protocol.py`
- `VMWorker/swiftpython_supervisor.py`
- `VMWorker/swiftpython_worker.py`
- a base image whose manifest attests the exact five helper hashes
- a verified warm snapshot created from that image

The worker protocol (v6), supervisor protocol (v3), duplex media protocol, and
feature-helper schemas version independently. The live guest `describe`
response owns supervisor command vocabulary; do not infer it from a host-side
list or source text.

## Build or Locate a Base Image

`SandboxPool` starts from a prepared Ubuntu disk image and creates tenant clones.
You can build an image programmatically:

```swift
let imagesDir = "\(NSHomeDirectory())/Library/Application Support/MyApp/Images"

let builder = UbuntuImageBuilder(
    outputDir: imagesDir,
    swiftpythonVersion: "0.6.0-duplex.2",
    cpuCount: 2,
    memoryMB: 2048,
    diskSizeMB: 8192
)

builder.onEvent = { event in
    print(event)
}

let baseImagePath = try await builder.build(force: false)
```

If your app ships a prebuilt image, verify it before use:

```swift
let manifest = try SandboxImageVerifier.verify(
    diskPath: baseImagePath,
    minimumSwiftPythonVersion: "0.6.0-duplex.2"
)

print(manifest.sha256)
```

## Create a Sandbox Pool

```swift
let cloneDir = "\(NSHomeDirectory())/Library/Application Support/MyApp/Sandboxes"

let sandbox = try await SandboxPool(
    baseImagePath: baseImagePath,
    cloneDir: cloneDir,
    config: SandboxPoolConfig(
        minimumSwiftPythonVersion: "0.6.0-duplex.2",
        idleEvictionTimeout: 300,
        workersPerTenant: 1,
        verifyImageManifest: true,
        snapshotValidationMode: .cryptographic
    )
)
```

`baseImagePath` is the immutable base image. `cloneDir` is where tenant-specific
disk clones, locks, and runtime state live. Put `cloneDir` in app-owned storage,
not in a shared temporary directory.

## Acquire and Release a Tenant

```swift
let tenantID = SandboxTenantID(rawValue: "customer-a")

let tenant = try await sandbox.acquire(
    tenantID: tenantID,
    vmConfig: VMConfiguration(
        memoryMB: 4096,
        diskMB: 8192,
        allowNetworkEgress: false
    )
)

do {
    let result = try await sandbox.execShell(
        tenantID: tenant.id,
        "python3 --version"
    )
    print(String(decoding: result.stdout, as: UTF8.self))

    try await sandbox.release(tenant)
} catch {
    try? await sandbox.release(tenant, force: true)
    throw error
}
```

Release tenants when the user/session is done. Use `force: true` for broken or
untrusted tenants that should be stopped immediately.

## Captured Shell Exec

`execShell` waits for completion and returns captured stdout/stderr.

```swift
let result = try await sandbox.execShell(
    tenantID: tenantID,
    """
    python3 - <<'PY'
    import json, platform
    print(json.dumps({"python": platform.python_version()}))
    PY
    """,
    options: ExecStreamOptions(timeout: 60, maxOutputBytes: 4 * 1024 * 1024)
)

let stdout = String(decoding: result.stdout, as: UTF8.self)
let stderr = String(decoding: result.stderr, as: UTF8.self)
print(result.exitCode, stdout, stderr)
```

Use captured exec for bounded commands: package inspection, one-shot transforms,
small scripts, tests, and setup operations.

## Streaming Shell Exec

`execShellStream` returns chunks as the command runs.

```swift
let session = try await sandbox.execShellStream(
    tenantID: tenantID,
    "python3 train.py",
    options: ExecStreamOptions(timeout: 3600, traceID: UUID())
)

for try await chunk in session.chunks {
    let text = String(decoding: chunk.bytes, as: UTF8.self)
    switch chunk.stream {
    case .stdout:
        print(text, terminator: "")
    case .stderr:
        print("ERR: \(text)", terminator: "")
    }
}

let result = try await session.result.value
print("exit:", result.exitCode)
```

Use streaming exec for long commands where the UI should show logs or progress.

## PTY Sessions

Use `execShellPTY` for terminal-like sessions that need stdin, resize, or
signals.

```swift
let pty = try await sandbox.execShellPTY(
    tenantID: tenantID,
    "bash",
    options: ExecPTYOptions(
        timeout: 3600,
        initialSize: TerminalSize(columns: 120, rows: 32)
    )
)

try await pty.sendStdin(Data("python3 --version\n".utf8))
try await pty.resize(to: TerminalSize(columns: 100, rows: 30))
try await pty.signal(.interrupt)
try await pty.finishStdin()

let result = try await pty.result.value
```

Use `cancel()` when the user closes the terminal or you are abandoning the
session.

## Python ProcessPool Inside a Tenant

Each acquired `SandboxTenant` includes a `processPool` connected to the tenant's
worker runtime.

```swift
let tenant = try await sandbox.acquire(tenantID: tenantID)

let value: Int = try await tenant.processPool.evalResult("""
import os
len(os.listdir("/"))
""")

try await sandbox.release(tenant)
```

Use this when you want the normal `PythonProcessPool` API but with VM isolation.

The tenant ProcessPool also exposes frame and logical-message duplex over
authenticated vsock. It does not advertise the local
`DuplexPayloadRoute.sharedArena`; an `.arenaIngress` requirement therefore
fails before handler setup. Use bounded inline logical-message chunks for VM
duplex and inspect the live capability snapshot for the physical-frame and
logical-message limits.

## Warm snapshots

A snapshot bundle contains its cloned disk, NVRAM, machine identifier, saved
machine state, copied base-image manifest, and snapshot manifest. The manifest
binds release/supervisor/worker versions, every asset hash, topology and policy
fingerprints, host OS, and a salted restore-secret identifier. The restore
secret itself is external and must remain mode `0600`.

Create and verify snapshots with the signed release tool:

```bash
swift run swift-python-build sandbox snapshot create \
  --image /path/to/base-ubuntu.img \
  --output /path/to/release.swiftpython-snapshot \
  --generate-restore-secret

swift run swift-python-build sandbox snapshot verify \
  --snapshot /path/to/release.swiftpython-snapshot \
  --image /path/to/base-ubuntu.img \
  --restore-secret-file /path/to/release.restore-secret
```

Select restore explicitly for a tenant:

```swift
let tenant = try await sandbox.acquire(
    tenantID: tenantID,
    vmConfig: VMConfiguration(
        bootStrategy: .snapshotRestore(
            snapshotPath: "/path/to/release.swiftpython-snapshot"
        ),
        snapshotRestoreAuthSecret: restoreSecret
    )
)
```

The general runtime can report a failed restore and fall back to cold boot.
Release certification is stricter: repeated warm gates require
`snapshotRestoreSucceeded` and `VMManager.bootMode == .warm`; any restore
failure, dropped event, timeout, or cold fallback fails the gate. Post-restore
proof includes worker calls, callbacks, shared memory, shell capture/streaming,
PTY input/resize, and the same fragmented duplex workload used on cold vsock.
Active/opening duplex sessions are not snapshot-resumable and must make
snapshot preparation fail.

## Events

```swift
Task {
    for await event in sandbox.events() {
        switch event {
        case .tenantSpawned(let id, let bootMs, _):
            logger.info("tenant \(id.rawValue) booted in \(bootMs)ms")
        case .execCompleted(_, let id, let exit, let elapsed, _, _, _):
            logger.info("tenant \(id.rawValue) exec exit \(exit) in \(elapsed)ms")
        case .tenantCrashed(let id, let tail, _):
            logger.error("tenant \(id.rawValue) crashed: \(tail)")
        case .eventsDropped(let count):
            logger.warning("missed \(count) sandbox events")
        default:
            break
        }
    }
}
```

Events cover tenant spawn/eviction/crash, exec start/completion/timeout, kernel
panic reports, quota events, and dropped subscriber events.

## VM Configuration

```swift
let config = VMConfiguration(
    guestOS: .ubuntu24,
    cpuCount: 4,
    cpuQuotaPercent: 80,
    memoryMB: 4096,
    diskMB: 16384,
    maxOpenFilesPerProcess: 2048,
    fileSystemMounts: [
        VMFileSystemMount(hostPath: "/Users/me/shared-data", guestTag: "shared", readOnly: true)
    ],
    allowNetworkEgress: false,
    guestSudoMode: .none
)
```

Default posture is conservative: no sudo and no network egress. Enable broader
access only for tenants that require it.

## Operations

| API | Use |
|-----|-----|
| `activeTenantIDs()` | Inspect currently active tenants |
| `evictIdle(maxAge:)` | Stop tenants that have been idle long enough |
| `forceStop(tenantID:)` | Kill one tenant |
| `drainAndReplace(newConfig:)` | Rotate pool policy without reusing old tenants |
| `shutdown()` | Stop all tenants |
| `cleanupStaleLockFiles(in:)` | Clean lock files after an app crash |
| `recordGuestKernelPanic` | Attach a panic tail to diagnostics |

## Security Notes

- Treat tenant IDs as application identifiers, not user-provided shell text.
- Keep clone directories in app-owned storage.
- Disable network egress unless the tenant explicitly needs it.
- Keep `guestSudoMode` at `.none` for untrusted work.
- Use read-only mounts for host data by default.
- Enforce output caps with `ExecStreamOptions.maxOutputBytes`.
- Keep runtime, local worker, all five VM helpers, base image, and snapshot
  aligned to the exact release.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| `VMWorker` scripts not found | Deploy `VMWorker/` with the app or set `SWIFTPYTHON_VM_WORKER_DIR` |
| Image verification fails | Rebuild the base image with all five helpers from the current commercial tag |
| Warm restore reports success after cold fallback | Treat it as a failed release gate; require the positive warm event and `bootMode == .warm` |
| `.arenaIngress` fails in a VM session | Expected: the owned shared-arena payload route is local UDS only |
| Command floods output | Lower `maxOutputBytes` and consume stream chunks promptly |
| Tenant keeps stale state | Use per-user tenant IDs or `forceStop`/`drainAndReplace` on logout |
| Need a normal Python API inside isolation | Use `tenant.processPool` |
