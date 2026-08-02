# DuplexSession

This public-package example proves two worker-v6 contracts through the shipped
`SwiftPythonRuntime` and matched `SwiftPythonWorker`:

- a frame can enter Python while output progresses independently; and
- a logical message larger than the negotiated physical-frame ceiling is
  fragmented, reassembled under an explicit bound, and returned as a SHA-256
  digest.

From the commercial repository root:

```bash
swift run --package-path Examples/DuplexSession
```

The local checkout is used by default. To validate the published tag instead:

```bash
SWIFTPYTHON_COMMERCIAL_PACKAGE_URL=https://github.com/mikhutchinson/swiftpython-commercial.git \
SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION=0.6.0-duplex.3 \
swift run --package-path Examples/DuplexSession
```

This is a generic byte-session example. Audio capture/playback and Metal lease
integration are optional products documented in
`docs/api-guide/ch11-apple-interop.md`.
