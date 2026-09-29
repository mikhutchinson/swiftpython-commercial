# Host Python workers in your Mac app

The Preview 1 consumer kit builds a minimal macOS app with app-owned XPC services
or ExtensionFoundation workers. It uses the `SwiftPythonRuntime` and
`SwiftPythonWorkerService` products and bundles Python with the app.

## Before you start

Install Xcode, XcodeGen and Python 3.9 or later for the build scripts, and choose
your Apple signing identity. Your finished app does not need an installed Python.

| Worker host | Requirements |
|---|---|
| XPC service | macOS 15 or later; the service is nonsandboxed, with either parent sandbox policy |
| ExtensionFoundation | macOS 26 or later and an SDK with ExtensionFoundation support; sandboxed worker extension |

The generated deployment target also accounts for the supplied binary's minimum
OS requirements. These recipes do not establish App Store eligibility.

Download `SwiftPythonCommercial-0.7.0-preview.1.zip` and `manifest.json` from the
[same release](https://github.com/mikhutchinson/swiftpython-commercial/releases/tag/v0.7.0-preview.1).
Extract the archive into a new directory and keep it intact. Run the commands
below from that directory. The release's inventory validates the complete kit,
including its documentation; use the release archive for assembly even when
reading newer documentation on GitHub.

## Generate an XPC application

```sh
python3 Consumer/assemble.py generate \
  --distribution /path/to/extracted-distribution \
  --manifest /path/to/external/manifest.json \
  --output /path/to/new-consumer-project \
  --host xpc-service --bundle-id com.example.MyApplication \
  --worker-count 2 --parent-sandbox no --service-sandbox no
```

Use your own bundle identifier. The example requires 2 through 64 worker
identities so it can demonstrate worker replacement. Use a distinct bundle
identifier for each installed development app.

For an ExtensionFoundation application, select
`--host extension-foundation --service-sandbox yes`. The parent application's
sandbox policy is independent of the worker's policy. Sandboxed XPC services
are not supported by this recipe.

## Build and sign

```sh
python3 Consumer/assemble.py build \
  --project /path/to/new-consumer-project \
  --identity '<Apple-signing-identity>' --signing-mode local-development
```

The builder embeds the matched runtime and signs nested code before the app.
It handles clean and incremental builds. Local development signing does not
request a secure timestamp. Distribution signing requires the release manifest
and a Developer ID identity; select `--signing-mode distribution`.

## Check and run

```sh
python3 Consumer/assemble.py preflight \
  --project /path/to/new-consumer-project \
  --receipt /path/to/new-consumer-project/signing-receipt.json

python3 Consumer/execute.py \
  --project /path/to/new-consumer-project \
  --receipt /path/to/new-consumer-project/signing-receipt.json \
  --output /path/to/new-execution-evidence
```

Preflight checks the generated app against its inputs and signing receipt; it
does not modify or repair the app. The example performs Python work, replaces a
worker and shuts down. The runner checks fresh results, process cleanup and the
app's signature, and writes the results into the chosen output directory.

The runner rejects duplicate registered applications with the same bundle ID.
Remove the obsolete development registration or give the new app a distinct ID.

## Distribute your application

Keep the release manifest with your build inputs. After building and signing,
notarize your application, staple Apple's ticket and verify Gatekeeper acceptance.
Preflight supports a valid stapled ticket without changing the original signing
receipt. Running preflight by itself does not notarize an app.

The minimal example includes the Python standard library. Add your application's
Python modules and compatible third-party packages as part of your integration.

[Runtime API guide](../docs/api-guide/) ·
[App packaging](https://swiftpython.dev/docs/packaging/)
