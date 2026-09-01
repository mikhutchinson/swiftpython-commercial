# Core Runtime Smoke

Minimal macOS CLI that exercises **in-process** `Python.run` against the bundled
SwiftPython runtime. This matches the README “Smoke Test” snippet: read
`sys.version`, then serialize a tiny object with the standard-library `json`
module.

## Run

From this directory:

```bash
swift run
```

The commercial dependency supplies and links its private Python 3.13
framework. No host Python, package manager, linker flag, or environment setup
is required.

## What it proves

- The package links the commercial product's private `Python.framework`.
- The embedded interpreter imports `sys` and `json` successfully from Swift.
