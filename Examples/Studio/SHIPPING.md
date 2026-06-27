# Shipping SwiftPython Studio

How to turn `SwiftPython Studio.app` into a notarized, stapled DMG that anyone can
download and run with **no Python installed**. This is a standard macOS Developer ID
notarization flow with the extra steps a bundled-Python app needs.

## What ships

- A native macOS app built on the **commercial `SwiftPythonRuntime.xcframework`**
  (v0.5.14) — the exact artifact a customer consumes.
- The bundled **`SwiftPythonWorker`** sidecar (auto-discovered by `PythonProcessPool`).
- A **bundled, relocatable Python 3.13 + numpy/pandas/scikit-learn/scipy** so the
  app is self-contained (the IRIS sample, by contrast, requires Homebrew Python).
- The canonical V2 `pool.<module>` bindings, vendored as `SwiftPythonKit`.
- The `studio-cli` terminal demo (in `Contents/MacOS/`, also droppable in the DMG).

## Prerequisites (release machine)

- A `Developer ID Application` identity in the login Keychain
  (`security find-identity -v -p codesigning`).
- A notary keychain profile created once with:
  ```bash
  xcrun notarytool store-credentials "<YourNotaryProfile>" \
    --apple-id "<APPLE_ID>" --team-id "<TEAM_ID>" --password "<APP_SPECIFIC_PASSWORD>"
  ```
  The commands below use `"$NOTARY_PROFILE"`; export it to your profile name.
- Homebrew `python@3.13` (source for the relocatable bundle) with the four wheels.

## Build → sign → notarize → DMG

```bash
cd Examples/Studio
export NOTARY_PROFILE="<YourNotaryProfile>"

# 1. Assemble, bundle Python, and sign (Developer ID, hardened runtime, inside-out).
STUDIO_APP_SHORT_VERSION=0.1.0 STUDIO_APP_BUILD_VERSION=1 \
  ./scripts/build_app.sh --bundle-python --sign

# 2. Notarize the app, then staple.
APP="build/SwiftPython Studio.app"
ditto -c -k --keepParent "$APP" build/Studio.zip
xcrun notarytool submit build/Studio.zip --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"
spctl -a -vv --type execute "$APP"        # → "Notarized Developer ID"

# 3. Build a DMG (with an Applications drop-target), sign + notarize + staple it.
rm -rf build/dmg && mkdir build/dmg
cp -R "$APP" build/dmg/ && ln -s /Applications build/dmg/Applications
hdiutil create -volname "SwiftPython Studio" -srcfolder build/dmg -ov -format UDZO build/SwiftPythonStudio.dmg
codesign --force --sign "Developer ID Application" build/SwiftPythonStudio.dmg
xcrun notarytool submit build/SwiftPythonStudio.dmg --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple build/SwiftPythonStudio.dmg
```

## Signing order (load-bearing)

`build_app.sh --sign` signs **inside-out**, which is mandatory or notarization
returns `Invalid`:

1. Every nested Mach-O (wheel `.so`/`.dylib`, framework dylibs, the Python binary)
   under `Contents/Frameworks` + `Contents/Resources/Python`
   (Developer ID + `--options runtime` + `--timestamp`).
2. Non-Mach-O Python payloads with executable bits are stripped (`chmod -x`) or a
   quarantined launch shows **"App is damaged"** even when `spctl` passes.
3. The `Python.framework` bundle (re-sealed).
4. The `SwiftPythonWorker` sidecar + `studio-cli`.
5. The outer `.app` last (no `--deep`), with `scripts/studio.entitlements`
   (`disable-library-validation`, `allow-dyld-environment-variables`).

## Wiring the worker to the bundled interpreter

The shipped `SwiftPythonWorker` must use the **bundled** Python, not a system one.
Two pieces, both implemented:

- **Relink** the three binaries (`Studio`, `studio-cli`, `SwiftPythonWorker`) from
  the absolute Homebrew Python load path to `@rpath/Python.framework/...` with an
  added rpath of `@executable_path/../Frameworks` (done in `build_app.sh --bundle-python`).
  Re-sign after this (`install_name_tool` invalidates the signature).
- **Env**: `StudioRuntime.configureBundledPythonIfPresent()` sets, relative to the
  running executable, `PYTHONHOME = …/Contents/Frameworks/Python.framework/Versions/3.13`
  and `PYTHONPATH = …/Contents/Resources/Python/site-packages` before the pool spawns
  the worker (which inherits them). `studio.entitlements` enables
  `allow-dyld-environment-variables` so this is honored under the hardened runtime.

Verify on a Mac with **no Homebrew / no Python** (or simulate locally with
`env -i HOME="$HOME" "$APP/Contents/MacOS/studio-cli"`):

```bash
xcrun stapler validate "build/SwiftPython Studio.app"
# also launch from Finder (not Terminal) — the GUI launchd env has no Homebrew PATH,
# so this proves the bundled interpreter + env wiring are what make it work for strangers.
```

## Hosting

- Upload `SwiftPythonStudio.dmg` to a **public GitHub release** (a dedicated
  releases repo, or this repo's Releases).
- Link to it from your download/marketing page.
- Sparkle auto-update is **deferred** for v1 (static DMG). To add it later, wire a
  standard Sparkle appcast under a `studio/` feed path; nothing in the build here
  changes.

## Gotchas hit while producing the first notarized DMG (do not repeat)

1. **Relinked binaries must be re-signed.** `install_name_tool` (used to point the
   3 binaries at `@rpath/Python.framework`) invalidates the signature; on Apple
   Silicon the binary then fails to launch with `Bad executable`. The `--sign`
   step runs *after* relink for this reason.
2. **Do NOT sign the app's main executable separately.** Signing
   `Contents/MacOS/Studio` directly *and* sealing the `.app` yields "The signature
   of the binary is invalid" at notarization. Sign only the `.app` (it signs the
   main executable + applies entitlements). Helper executables (`SwiftPythonWorker`,
   `studio-cli`) DO need their own `codesign`.
3. **Sign the framework as a bundle**, not its `Versions/3.13` directory:
   `codesign … Contents/Frameworks/Python.framework`.
4. **Strip symlinks that escape the framework.** Homebrew's framework links
   `lib/python3.13/site-packages` up into the Cellar; Gatekeeper rejects it with
   "invalid destination for symbolic link in bundle." `bundle_python.sh` removes
   escaping/absolute symlinks (we use `Contents/Resources/Python/site-packages`
   via `PYTHONPATH` anyway).

## Status — DONE (first release built)

The full path is implemented, run, and verified on the build machine:

- `numpy 2.5 / pandas 3.0 / scikit-learn 1.9 / scipy 1.18` bundled (lean tier).
- The bake-off runs in a **fully sanitized environment** (`env -i`, no Homebrew,
  no inherited `PYTHONHOME`) — proof it works on a Python-less Mac.
- App: notarized (Accepted) + stapled + `spctl … → "Notarized Developer ID"`.
- **`build/SwiftPythonStudio.dmg`** (~182 MB): notarized + stapled + Gatekeeper
  accepted (`source=Notarized Developer ID`).

Remaining before public download: host the DMG on a public GitHub release + link it
from the download page, and one real download-and-run smoke on a *different* Mac.
