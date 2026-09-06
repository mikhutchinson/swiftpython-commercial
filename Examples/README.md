# Examples

Two native Mac apps built against the public SwiftPython runtime:

| Demo | What it does | Run |
|---|---|---|
| [Particle Showcase](ParticleShowcase/) | NumPy moves 1,048,576 particles; Metal renders their shared positions. Includes a 1080p video exporter. | `Examples/ParticleShowcase/run.sh` |
| [Iris](IrisDemo/) | Explore three datasets, train three scikit-learn classifiers, and inspect validation scores and learning curves. | `Examples/IrisDemo/scripts/build_app.sh --open` |

Requires macOS 15+ and Xcode command-line tools, including their Python 3 build tool.
SwiftPM fetches the exact runtime binaries. The builders download CPython 3.13
wheels pinned by version and SHA-256 in
[`python-wheels.lock.json`](../scripts/python-wheels.lock.json), then embed them
alongside the runtime. Both Apple Silicon and Intel wheels are specified; each
app build targets the Mac building it.

The resulting apps run offline and need no installed Python or Python packages.
They are ad hoc signed development apps. Distribution signing and notarization
follow the [runtime deployment guide](../README.md). Wheel license notices and
metadata remain in each app's `Contents/Resources/PythonPackages` directory.

Packages use the containing commercial checkout by default. To validate an
exact hosted tag, set both `SWIFTPYTHON_COMMERCIAL_PACKAGE_URL` and
`SWIFTPYTHON_COMMERCIAL_PACKAGE_VERSION` before building. Each
builder takes its sidecar from that same resolved checkout.

The independent [`consumer_path_smoke.sh`](../scripts/consumer_path_smoke.sh)
fixture covers deployment, ordinary calls, streams, duplex and optional adapters.
