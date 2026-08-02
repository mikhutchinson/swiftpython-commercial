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

The script builds the Swift package, creates `build/IRIS.app`, copies the
bundled Python resources into the app, and launches the app when `--open` is
passed.

This helper creates a development app-shaped bundle; it does not bundle a
Python framework, apply distribution entitlements, sign nested code, notarize,
or prove App Sandbox behavior. Follow the root distribution/signing guide for a
shipping application.

## Boundary

Swift owns the UI and view model. Python owns the sklearn data/model work in
`Sources/Python/iris_kernel.py`. The Swift facade in
`Sources/Services/IrisKernel.swift` loads that file as a bundled resource and
exchanges JSON payloads with it through `SwiftPythonRuntime`.
