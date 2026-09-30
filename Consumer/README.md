# Commercial Apple worker consumer kit

**Unshipped implementation in progress.** The published 8.4 package does not
contain this kit or WorkerService. Passing preflight proves assembly and signing;
it does not certify execution, foreign-team reproducibility or notarization.

The kit creates a minimal macOS application using the commercial Runtime and
WorkerService products. Its app-owned XPC services or ExtensionFoundation
extensions embed the matched private Python runtime through the binary package.
The installed app does not need a separate Python installation.

Build prerequisites are Python 3.9 or later for these scripts, Xcode with the
SDK required by the selected host, XcodeGen, and a real Apple signing identity.
The deployment target is the greater of the API availability and the actual
requirements recorded in the candidate's Mach-O binaries. It is not an OS
execution-support claim. The local signing mode uses no secure timestamp;
distribution mode requires publisher-attested inputs and timestamped Developer
ID signatures. Personal Team entitlement availability must be tested separately.
The producer's sealed-bytecode audit additionally requires its matching CPython
3.13 build interpreter. It recompiles packaged standard-library source without
executing it and checks every compiled code field against the sealed cache,
including hash invalidation, optimization and public traceback filenames. The
payload inventory binds the final serialized bytes. This is a producer
gate; normal consumer assembly and app execution do not need an installed
Python 3.13 runtime.

Obtain the complete distribution plus its external `manifest.json` from the
same release. The manifest attests `payload.json`, which inventories the six
XCFrameworks, worker, audio probe, VM helpers, entitlements and this recipe.
Keep that external manifest as the trusted publisher input. A development
inventory alone detects changes but cannot establish release provenance.

```sh
python3 Consumer/assemble.py generate \
  --distribution /path/to/extracted-distribution \
  --manifest /path/to/external/manifest.json \
  --output /path/to/new-consumer-project \
  --host xpc-service --bundle-id com.example.MyApplication \
  --worker-count 2 --parent-sandbox no --service-sandbox no

python3 Consumer/assemble.py build \
  --project /path/to/new-consumer-project \
  --identity '<Apple-signing-identity>' --signing-mode local-development

python3 Consumer/assemble.py preflight \
  --project /path/to/new-consumer-project \
  --receipt /path/to/new-consumer-project/signing-receipt.json

python3 Consumer/execute.py \
  --project /path/to/new-consumer-project \
  --receipt /path/to/new-consumer-project/signing-receipt.json \
  --output /path/to/new-execution-evidence
```

For a local developer candidate, replace `--manifest` with the explicit
`--allow-development` option. This path records development provenance and
refuses distribution signing. For ExtensionFoundation, use
`--host extension-foundation --service-sandbox yes`. The app and service sandbox
policies are independent. These templates do not assert App Store eligibility.
The XPC recipe requires nonsandboxed services under either parent policy.
Sandboxed XPC services are not qualified: their current bootstrap needs access
to the containing app's signed inventory, which the service sandbox can deny.
The generator rejects that policy before building. ExtensionFoundation is the
recipe for the independently sandboxed worker proof in this kit.

The worker count declares a finite inventory; the minimal respawn smoke requires
2 through 64 identities. Every identity derives from your bundle identifier.
Use a unique consumer prefix for each installed candidate. This minimal kit has
no extra wheels or app-owned Python command; it does not discover PATH Python.
The execution verifier rejects another registered app with the same bundle ID
before launch. Superseded development copies must be explicitly unregistered or
given distinct identities; refreshing the newest copy alone is insufficient.

`build` reruns the same finalizer for clean and incremental Xcode builds. All
copying and loader-route fixes precede signing native images, frameworks, worker
bundles and finally the app. Preflight never repairs an app. It verifies input
provenance, exact generated inputs, worker inventory, linker maps bound to the
attested Runtime/WorkerService archives for every target and architecture, bundle-contained native
dependencies, actual signatures and sandbox entitlements, then checks the sealed
output against the separate signing receipt. Publisher hashes describe the
inputs before consumer signing; output hashes describe the resulting app.

For distribution-signed apps, preflight also supports Apple's stapled ticket at
`Contents/CodeResources`. It validates the ticket with `stapler validate` and
records its digest without rewriting the original signing receipt. Every
pre-existing file must remain identical; other additions, removed or changed
files and invalid tickets are rejected. Execution rechecks the same ticket and
sealed output afterward. This ticket check does not submit an app for
notarization or replace the separate Gatekeeper and release qualification gates.
The generated fixtures declare no App Intents and disable only that optional
metadata extraction task; ExtensionFoundation point generation remains enabled.

The generated application performs Python work, requests a public respawn and
shuts down. Manual launches write to the bundle identifier's cache directory.
Automated verification uses LaunchServices' explicit stdout/stderr files in the
new evidence directory and does not read another app's protected container.
The execution verifier requires fresh results, samples exact executable paths,
matches acquisitions to kernel retirement receipts, checks owned descendants
have exited, and verifies the app seal remains unchanged. It records the exact
OS version/build and CPU architecture. Full execution qualification additionally
requires the release corpus and source-inaccessible execution. Those gates and
notarization remain distinct from this minimal smoke.

For an enforced private-source read-denial run, generate an ExtensionFoundation project with both
`--parent-sandbox yes` and `--service-sandbox yes`. Add one or more
`--deny-source /absolute/path/to/private/source/file` arguments to `execute.py`.
The runner verifies that each file exists and is readable outside the app, then
requires both the native host and Python worker to receive `EACCES` or `EPERM`.
A missing file is a failed probe. The receipt records this sandbox-specific
result separately; it does not claim a clean-machine test of a nonsandboxed host.
