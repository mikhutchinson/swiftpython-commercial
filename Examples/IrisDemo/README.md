# IRIS

IRIS is a complete macOS SwiftUI example for the public SwiftPython runtime
binary distribution. It loads sklearn datasets, renders them with Swift Charts,
and trains classifiers through a bundled Python service module.
When scaling is enabled, sklearn's `Pipeline` fits `StandardScaler` inside
each cross-validation fold and on the training split only; reported metrics do
not leak held-out data into preprocessing.

## Run

```bash
cd Examples/IrisDemo
./scripts/build_app.sh --open
```

The script builds the Swift package, creates `build/IRIS.app`, embeds the
private Engine and Python frameworks plus the example's Python resources,
applies a local development signature to the executable, and launches the app
when `--open` is passed. It needs no Homebrew, system Python, or Python path
configuration.

This helper creates a development app-shaped bundle. It does not apply
distribution entitlements, use a Developer ID signature, notarize, or prove App
Sandbox behavior. Follow the root distribution/signing guide for a shipping
application.

## Boundary

Swift owns the UI and view model. Python owns the sklearn data/model work in
`Sources/Python/iris_kernel.py`. The Swift facade in
`Sources/Services/IrisKernel.swift` loads that file as a bundled resource and
exchanges JSON payloads with it through `SwiftPythonRuntime`.
