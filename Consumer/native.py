"""Native bundle closure and explicit inside-out signing for the consumer kit."""
from __future__ import annotations

from pathlib import Path
import plistlib
import re
import shutil
import subprocess

from contract import exact_value, require, tree_inventory
from project import entitlements
from release_manifest import inspect_binary, version_tuple


MACHO_MAGIC = {bytes.fromhex(value) for value in (
    "cffaedfe", "cefaedfe", "feedfacf", "feedface", "cafebabe", "bebafeca", "cafebabf", "bfbafeca",
)}


def command(*arguments, stderr=False):
    result = subprocess.run([str(arg) for arg in arguments], capture_output=True, text=True, check=False)
    require(result.returncode == 0,
            f"Command failed ({result.returncode}): {arguments!r}\n{result.stderr[-4000:]}\n{result.stdout[-2000:]}")
    return result.stderr if stderr else result.stdout


def macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in MACHO_MAGIC


def info(bundle):
    result = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    executable = result.get("CFBundleExecutable")
    require(isinstance(executable, str) and re.fullmatch(r"[A-Za-z0-9_-]+", executable),
            f"Unsafe or missing bundle executable: {bundle}")
    require(macho(bundle / "Contents/MacOS" / executable), f"Missing native bundle executable: {bundle}")
    return result


def worker_bundles(app, config):
    require(app.suffix == ".app" and not app.is_symlink(), "Expected a regular application bundle")
    parent = info(app)
    require(parent.get("CFBundleIdentifier") == config["bundleIdentifier"], "Parent bundle identifier mismatch")
    extension = config["host"] == "extension-foundation"
    selected_key = "SwiftPythonWorkerExtensions" if extension else "SwiftPythonWorkerXPCServices"
    other_key = "SwiftPythonWorkerXPCServices" if extension else "SwiftPythonWorkerExtensions"
    expected = {"version": 1, "bundleIdentifiers": config["workerIdentifiers"]}
    if extension:
        expected["extensionPointIdentifier"] = config["bundleIdentifier"] + ".Worker"
    require(exact_value(parent.get(selected_key), expected) and other_key not in parent, "Worker inventory mismatch or dual bootstrap")
    folder, suffix = ("Extensions", ".appex") if extension else ("XPCServices", ".xpc")
    other_folder = "XPCServices" if extension else "Extensions"
    require(not (app / "Contents" / other_folder).exists(), "Unexpected second worker host directory")
    directory = app / "Contents" / folder
    entries = sorted(directory.iterdir())
    if extension:
        # @AppExtensionPoint.Definition creates an app-owned descriptor beside
        # the extensions. Validate its exact contract; it is not a worker.
        descriptor = directory / (parent["CFBundleExecutable"] + ".appexpt")
        require(descriptor in entries and descriptor.is_file() and not descriptor.is_symlink(),
                "Missing app-owned extension-point descriptor")
        declaration = plistlib.loads(descriptor.read_bytes())
        require(exact_value(declaration, {"EXVersion": 2, config["bundleIdentifier"] + ".Worker": {
            "EXExtensionPointName": "Worker"}}), "Wrong extension-point descriptor identity or schema")
        entries.remove(descriptor)
    workers = entries
    require(len(workers) == len(config["workerIdentifiers"]) and
            all(item.suffix == suffix and item.is_dir() and not item.is_symlink() for item in workers),
            "Worker bundle count/type mismatch")
    identifiers = []
    for worker in workers:
        properties = info(worker)
        identifiers.append(properties.get("CFBundleIdentifier"))
        if extension:
            require(properties.get("EXAppExtensionAttributes", {}).get("EXExtensionPointIdentifier") ==
                    config["bundleIdentifier"] + ".Worker", f"Wrong extension point: {worker}")
        else:
            require(exact_value(properties.get("XPCService"), {"ServiceType": "Application", "_ProcessType": "Interactive",
                    "RunLoopType": "NSRunLoop", "JoinExistingSession": True}), f"Incorrect XPC service policy: {worker}")
    require(len(set(identifiers)) == len(identifiers) and set(identifiers) == set(config["workerIdentifiers"]),
            "Missing, duplicate or foreign worker bundle identifier")
    return workers


def load_commands(binary):
    text = command("xcrun", "otool", "-l", binary)
    runpaths, dependencies = set(), set()
    for block in re.split(r"(?m)^Load command \d+\s*$", text):
        kind = re.search(r"(?m)^\s*cmd (\S+)\s*$", block)
        if kind is None:
            continue
        if kind[1] == "LC_RPATH":
            value = re.search(r"(?m)^\s*path (.+) \(offset \d+\)\s*$", block)
            require(value is not None, f"Malformed runpath: {binary}")
            runpaths.add(value[1])
        elif kind[1] in ("LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB", "LC_LOAD_UPWARD_DYLIB"):
            value = re.search(r"(?m)^\s*name (.+) \(offset \d+\)\s*$", block)
            require(value is not None, f"Malformed native dependency: {binary}")
            dependencies.add(value[1])
    return runpaths, dependencies


def system_dependency(path):
    if not path.startswith(("/usr/lib/", "/System/Library/")):
        return False
    require(all(part not in ("", ".", "..") for part in path.split("/")[1:]),
            f"Noncanonical system dependency: {path}")
    return True


def native_closure(app, config):
    # This inventory also rejects escaping symlinks, cycles and special files.
    tree_inventory(app.parent, [app.name])
    workers = worker_bundles(app, config)
    require(not any(path.name in ("SwiftPythonWorker", "PythonCommand") for path in app.rglob("*")),
            "The minimal Apple-host recipe contains an undeclared executable")
    for worker in workers:
        require(not any((worker / "Contents/Frameworks").glob("*.framework")), "Duplicated worker runtime")
    for name in ("Python", "SwiftPythonEngine"):
        require((app / f"Contents/Frameworks/{name}.framework/{name}").is_file(), f"Missing shared runtime: {name}")
    require((app / "Contents/Frameworks/Python.framework/Versions/3.13/lib/python3.13/encodings/__init__.py").is_file(),
            "Missing packaged Python standard library")
    binaries = sorted(path for path in app.rglob("*") if macho(path))
    owners = [app] + workers
    owner_executables = {owner: owner / "Contents/MacOS" / info(owner)["CFBundleExecutable"] for owner in owners}
    commands = {binary: load_commands(binary) for binary in binaries}
    for binary in binaries:
        for target in inspect_binary(binary, config["architectures"]):
            require(version_tuple(target["minimumOS"]) <= version_tuple(config["minimumOS"]),
                    f"Native image exceeds app deployment target: {binary}")
        runpaths, dependencies = commands[binary]
        # Libraries may be loaded by either the app or any service. Prove each
        # owner's loader route; paths merely starting with @ are insufficient.
        possible_owners = [owner for owner in workers if binary.is_relative_to(owner)] or owners
        if binary == owner_executables[app]:
            possible_owners = [app]
        for owner in possible_owners:
            executable = owner_executables[owner]
            def expand(value, loader):
                for token, directory in (("@loader_path", loader.parent), ("@executable_path", executable.parent)):
                    if value == token or value.startswith(token + "/"):
                        candidate = (directory / value[len(token):].lstrip("/")).resolve()
                        require(candidate.is_relative_to(app), f"Escaping loader path: {binary}: {value}")
                        return candidate
                require(value in ("/usr/lib/swift",), f"External runpath: {binary}: {value}")
                return Path(value)
            search = [expand(value, binary) for value in runpaths]
            search += [expand(value, executable) for value in commands[executable][0]]
            for dependency in dependencies:
                if system_dependency(dependency):
                    continue  # System dylibs can reside only in the dyld shared cache.
                if re.fullmatch(r"@rpath/libswift[A-Za-z0-9_]+\.dylib", dependency):
                    # Swift's OS runtime is also supplied by the shared cache.
                    continue
                if dependency.startswith("@rpath/"):
                    found = [(directory / dependency[7:]).resolve() for directory in search]
                else:
                    found = [expand(dependency, binary)]
                require(any(candidate.is_relative_to(app) and candidate.is_file() for candidate in found),
                        f"Unresolved bundle dependency: {binary}: {dependency} for {owner.name}")
    return workers, binaries


def finalize_runtime(app, config):
    """Only remove proven Xcode duplicates and its temporary build runpaths."""
    workers = worker_bundles(app, config)
    for worker in workers:
        executable = worker / "Contents/MacOS" / info(worker)["CFBundleExecutable"]
        runpaths, _ = load_commands(executable)
        require("@executable_path/../../../../Frameworks" in runpaths, "Worker lacks containing-app runtime route")
        for name in ("Python", "SwiftPythonEngine"):
            nested = worker / f"Contents/Frameworks/{name}.framework"
            if not nested.exists():
                continue
            shared = app / f"Contents/Frameworks/{name}.framework"
            def identities(framework):
                return set(re.findall(r"UUID: ([A-Fa-f0-9-]+) \(([^)]+)\)",
                                      command("xcrun", "dwarfdump", "--uuid", framework / name)))
            expected, actual = identities(shared), identities(nested)
            require(expected and actual == expected, f"Nested runtime differs from shared input: {nested}")
            shutil.rmtree(nested)
    for binary in (path for path in app.rglob("*") if macho(path)):
        for value in load_commands(binary)[0]:
            if Path(value).is_absolute() and Path(value).resolve() == (app.parent / "PackageFrameworks").resolve():
                command("install_name_tool", "-delete_rpath", value, binary)
    return native_closure(app, config)


def sign(app, config, identity, mode, entitlement_directory):
    require(identity and identity != "-", "Apple worker peers require a real team signing identity")
    require(mode in ("local-development", "distribution"), "Explicit signing mode required")
    workers, binaries = native_closure(app, config)
    def seal(path, policy=None):
        arguments = ["codesign", "--force", "--sign", identity, "--options", "runtime",
                     "--timestamp" if mode == "distribution" else "--timestamp=none"]
        if policy is not None:
            arguments += ["--entitlements", entitlement_directory / (policy + ".entitlements")]
        command(*arguments, path)
    frameworks = sorted(app.rglob("*.framework"), key=lambda p: len(p.parts), reverse=True)
    # codesign treats a bundle's main executable as the bundle itself. Signing
    # it in the raw-image pass would seal its owner before nested code exists.
    bundle_executables = {(bundle / "Contents/MacOS" / info(bundle)["CFBundleExecutable"]).resolve()
                          for bundle in [app] + workers}
    bundle_executables.update((framework / framework.stem).resolve() for framework in frameworks)
    for binary in binaries:
        if binary.resolve() not in bundle_executables:
            seal(binary)
    for framework in frameworks:
        seal(framework)
    for worker in workers:
        seal(worker, "Worker")
    seal(app, "App")
    return verify_signatures(app, config, mode)


def verify_signatures(app, config, mode):
    workers = worker_bundles(app, config)
    command("codesign", "--verify", "--deep", "--strict", app)
    receipts, team = [], None
    for bundle, sandbox in [(app, config["parentSandbox"])] + [(worker, config["serviceSandbox"]) for worker in workers]:
        text = command("codesign", "-d", "--verbose=4", bundle, stderr=True)
        fields = dict(re.findall(r"(?m)^(Identifier|TeamIdentifier|CDHash|Timestamp)=(.+)$", text))
        require(fields.get("Identifier") == info(bundle)["CFBundleIdentifier"], f"Signed identifier mismatch: {bundle}")
        actual = fields.get("TeamIdentifier")
        require(actual and actual != "not set", f"Ad-hoc or teamless peer: {bundle}")
        if team is None:
            team = actual
        require(team == actual, f"Foreign-team worker: {bundle}")
        require(re.search(r"flags=.+\(.*runtime.*\)", text), f"Hardened runtime missing: {bundle}")
        if mode == "distribution":
            require("Authority=Developer ID Application:" in text and fields.get("Timestamp"),
                    f"Distribution signature requires Developer ID and secure timestamp: {bundle}")
        signed = command("codesign", "-d", "--entitlements", ":-", bundle)
        observed = plistlib.loads(signed.encode()) if signed.strip() else {}
        require(exact_value(observed, entitlements(sandbox)), f"Unexpected signed entitlements: {bundle}: {observed}")
        receipts.append({"bundle": str(bundle.relative_to(app.parent)), "team": actual,
                         "identifier": fields["Identifier"], "cdhash": fields.get("CDHash"),
                         "entitlements": observed})
    return receipts
