# Distribution verification

The complete distribution includes the worker, frameworks, consumer kit and
verification scripts. The scripts check packaged consumers using the same
runtime bytes that applications install.

The framework set is `SwiftPythonRuntime.xcframework`,
`SwiftPythonWorkerService.xcframework`, `SwiftPythonEngine.xcframework`,
`Python.xcframework`, `SwiftPythonAudioInterop.xcframework` and
`SwiftPythonMetalInterop.xcframework`. The `payload.json` inventory binds the
consumer kit to these files.

The guest helpers include `_swiftpython_wire.py`, `_swiftpython_duplex.py`,
`swiftpython_protocol.py`, `swiftpython_supervisor.py` and `swiftpython_worker.py`.
Worker wire v6 carries DuplexSession traffic. The VM helper inventory and hashes
are recorded in the distribution manifest.

The consumer smoke script exercises three app-shaped modes: Developer ID,
App Sandbox and virtualization. The VM gate requires 20 consecutive positive warm restores.
Audio consumers need `NSMicrophoneUsageDescription` and microphone permission.

Run the packaged checks with these settings when the corresponding hardware,
image and signing configuration are available:

```sh
export SWIFTPYTHON_AUDIO_PROBE_GATE=ready
export SWIFTPYTHON_VM_RELEASE_GATE=1
export SWIFTPYTHON_NOTARY_PROFILE="<notarytool-keychain-profile>"
```

A successful source build does not replace these packaged checks. Keep the
receipts for the exact distribution being tested.

Notary `ready` mode requires an explicit stable application identity. Set
`SWIFTPYTHON_SMOKE_ID_SUFFIX=releasegate`. Bootstrap the two grants once with
`SWIFTPYTHON_AUDIO_TCC_BOOTSTRAP=1`, opening the apps through LaunchServices.
Launching their executables directly does not provision these grants. Normal
`ready` runs never open a TCC prompt.
