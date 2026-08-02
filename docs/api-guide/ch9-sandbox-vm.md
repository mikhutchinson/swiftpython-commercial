# Chapter 9 - Isolated Sandboxes

`SandboxProvider` creates strongly isolated Python execution pools without
exposing the provider's boot mechanism, transport, kernel assets, or storage
layout. Use a sandbox for tenant-specific dependencies, shell tools, untrusted
jobs, per-tenant secrets, or Linux-only packages.

The `0.6.0-duplex.3` commercial release keeps these matched assets together:

- `SwiftPythonRuntime.xcframework` and its private
  `SwiftPythonEngine.xcframework` dependency;
- the local `SwiftPythonWorker` sidecar;
- the complete five-file `VMWorker/` helper set;
- the attested runtime asset and optional accelerated-start checkpoint.

Treat the release as one unit. Do not mix frameworks, workers, helpers,
runtime assets, or checkpoints from different versions.

## Configure a Sandbox

Obtain the runtime asset and any accelerated-start checkpoint through the
release tooling or your deployment pipeline. Application code supplies only a
high-level policy:

```swift
import SwiftPythonRuntime

let support = FileManager.default.urls(
    for: .applicationSupportDirectory,
    in: .userDomainMask
)[0]

let configuration = SandboxConfiguration(
    runtimeAsset: support.appending(path: "SwiftPython/runtime.asset"),
    storageDirectory: support.appending(path: "SwiftPython/Sandboxes"),
    compute: .balanced,
    startup: .standard,
    network: .denied,
    workersPerSandbox: 1,
    minimumRuntimeVersion: "0.6.0-duplex.3",
    integrity: .strict
)

let sandbox = try await SandboxProviders.system.makePool(
    configuration: configuration
)
```

The compute presets are `.efficiency`, `.balanced`, and `.performance`.
Provider-specific CPU counts, memory thresholds, boot commands, and paths are
intentionally private. Put `storageDirectory` in app-owned storage.

## Accelerated Startup

Use an attested checkpoint and its sealed credential when the deployment
provides them:

```swift
let credentialBytes = try Data(contentsOf: credentialURL)
let configuration = SandboxConfiguration(
    runtimeAsset: runtimeAssetURL,
    storageDirectory: sandboxStorageURL,
    compute: .performance,
    startup: .accelerated(
        checkpoint: checkpointURL,
        credential: SandboxCredential(sealedBytes: credentialBytes)
    ),
    network: .denied,
    workersPerSandbox: 1,
    minimumRuntimeVersion: "0.6.0-duplex.3",
    integrity: .strict
)
```

Keep the credential outside the checkpoint and restrict it to the application.
For a release gate, require `tenant.startupMode == .accelerated`; do not accept
standard startup as evidence that an accelerated checkpoint restored.

## Acquire and Release a Tenant

```swift
let tenantID = SandboxTenantID(rawValue: "customer-a")
let tenant = try await sandbox.acquire(tenantID: tenantID)

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

Release tenants when the user or session is done. Use `force: true` for broken
or untrusted tenants that should stop immediately.

## Captured Shell Exec

`execShell` waits for completion and returns captured stdout and stderr:

```swift
let result = try await sandbox.execShell(
    tenantID: tenantID,
    "python3 -c 'import platform; print(platform.python_version())'",
    options: ExecStreamOptions(timeout: 60, maxOutputBytes: 4 * 1024 * 1024)
)

print(result.exitCode)
print(String(decoding: result.stdout, as: UTF8.self))
print(String(decoding: result.stderr, as: UTF8.self))
```

Use captured exec for bounded commands such as package inspection, one-shot
transforms, small scripts, tests, and setup operations.

## Streaming Shell Exec

```swift
let session = try await sandbox.execShellStream(
    tenantID: tenantID,
    "python3 train.py",
    options: ExecStreamOptions(timeout: 3_600, traceID: UUID())
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

## PTY Sessions

```swift
let pty = try await sandbox.execShellPTY(
    tenantID: tenantID,
    "bash",
    options: ExecPTYOptions(
        timeout: 3_600,
        initialSize: TerminalSize(columns: 120, rows: 32)
    )
)

try await pty.sendStdin(Data("python3 --version\n".utf8))
try await pty.resize(to: TerminalSize(columns: 100, rows: 30))
try await pty.signal(.interrupt)
try await pty.finishStdin()
_ = try await pty.result.value
```

Use `cancel()` when the user closes the terminal or abandons the session.

## Python Inside a Tenant

Each acquired `SandboxTenant` includes a `processPool`:

```swift
let tenant = try await sandbox.acquire(tenantID: tenantID)

let value: Int = try await tenant.processPool.evalResult("""
import os
len(os.listdir("/"))
""")

try await sandbox.release(tenant)
```

The tenant process pool also exposes frame and logical-message duplex. The
provider reports supported features at runtime. A managed local ingress-buffer
requirement may be unavailable in isolated environments; use bounded inline
logical-message chunks when it is not negotiated.

## Activity

`activities()` reports mechanism-neutral lifecycle and operation state:

```swift
Task {
    for await activity in await sandbox.activities() {
        switch activity {
        case .ready(let id, let milliseconds):
            logger.info("tenant \(id.rawValue) ready in \(milliseconds)ms")
        case .operationFinished(let id, let succeeded, _):
            logger.info("tenant \(id.rawValue) succeeded: \(succeeded)")
        case .unavailable(let id):
            logger.error("tenant \(id.rawValue) unavailable")
        case .eventsDropped(let count):
            logger.warning("missed \(count) sandbox activities")
        default:
            break
        }
    }
}
```

## Operations

| API | Use |
|-----|-----|
| `activeTenantIDs()` | Inspect active tenants |
| `evictIdle(maxAge:)` | Stop tenants idle beyond the policy |
| `forceStop(tenantID:)` | Stop one tenant immediately |
| `shutdown()` | Stop all tenants and finish activity streams |

## Security Notes

- Treat tenant IDs as application identifiers, not shell text.
- Keep sandbox storage and credentials in app-owned, access-controlled paths.
- Keep network access denied unless the tenant explicitly needs it.
- Enforce output caps with `ExecStreamOptions.maxOutputBytes`.
- Require strict integrity for shipped runtime assets and checkpoints.
- Keep Runtime, private Engine, worker, helpers, runtime asset, and checkpoint
  aligned to the exact release.
- Do not import or redistribute `SwiftPythonEngine` as a product. SwiftPM links
  it privately through the public Runtime product, and app bundles embed the
  supplied signed framework once under `Contents/Frameworks`.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| `VMWorker` helpers not found | Deploy the complete matched helper directory or set `SWIFTPYTHON_VM_WORKER_DIR` |
| Runtime asset or checkpoint rejected | Rebuild or download all assets for the exact release |
| Accelerated gate reports `.standard` | Fail the gate; the checkpoint was not used |
| Managed ingress buffers are unavailable | Use bounded inline logical-message chunks |
| Command floods output | Lower `maxOutputBytes` and consume chunks promptly |
| Tenant keeps stale application state | Use per-user IDs or force-stop on logout |
