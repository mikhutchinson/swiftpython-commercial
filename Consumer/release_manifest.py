#!/usr/bin/env python3
"""Versioned commercial host metadata and publisher input provenance.

Build targets come from actual Mach-O load commands. They never constitute
execution qualification. The publisher and the standalone consumer use this
same validator; historical schema 3 cannot declare an Apple worker host.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys

sys.dont_write_bytecode = True

from contract import ContractError, exact_value, load_json, relative_path, require, sha256, tree_inventory, verify_inventory


BINARY_MODULES = (
    "SwiftPythonRuntime", "SwiftPythonEngine", "Python",
    "SwiftPythonAudioInterop", "SwiftPythonMetalInterop", "SwiftPythonWorkerService",
)
CORE_MODULES = ("SwiftPythonRuntime", "SwiftPythonEngine", "Python")
HOST_KINDS = ("sidecar", "xpc-service", "extension-foundation")
PAYLOAD_NAME = "payload.json"
PAYLOAD_ROOTS = [name + ".xcframework" for name in BINARY_MODULES] + [
    "SwiftPythonWorker", "SwiftPythonAudioProbe", "VMWorker", "Entitlements", "Consumer",
]
REQUIRED_KIT_FILES = (
    "README.md", "contract.py", "release_manifest.py", "project.py", "native.py", "assemble.py", "execute.py", "sealed_stdlib.py",
    "Common/App.swift.template", "XPC/Worker.swift", "ExtensionFoundation/Worker.swift.template",
)


def run(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    require(result.returncode == 0,
            f"Command failed ({result.returncode}): {arguments!r}\n{result.stderr[-4000:]}")
    return result.stdout


def version_tuple(value):
    require(isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+){1,2}", value),
            f"Invalid Mach-O version: {value!r}")
    return tuple(int(v) for v in value.split("."))


def build_target_from_otool(text, architecture):
    """Aggregate deployment requirements of every object in a static archive."""
    minimums, sdks = set(), set()
    for block in re.split(r"(?m)^Load command \d+\s*$", text):
        fields = dict(re.findall(r"(?m)^\s*(cmd|platform|minos|version|sdk)\s+(\S+)\s*$", block))
        if fields.get("cmd") == "LC_BUILD_VERSION":
            require(fields.get("platform") in ("1", "MACOS", "macos"),
                    "Commercial binary contains a non-macOS build target")
            minimum = fields.get("minos")
        elif fields.get("cmd") == "LC_VERSION_MIN_MACOSX":
            minimum = fields.get("version")
        else:
            continue
        version_tuple(minimum)
        version_tuple(fields.get("sdk"))
        minimums.add(minimum)
        sdks.add(fields["sdk"])
    require(minimums, "Mach-O has no macOS deployment requirement")
    return {"platform": "macos", "architecture": architecture,
            "minimumOS": max(minimums, key=version_tuple),
            "sdks": sorted(sdks, key=version_tuple)}


def inspect_binary(path, architectures):
    actual = run(["xcrun", "lipo", "-archs", str(path)]).split()
    require(len(actual) == len(set(actual)) and set(actual) == set(architectures),
            f"Mach-O architecture mismatch: {path}: {actual}, expected {architectures}")
    return [build_target_from_otool(
        run(["xcrun", "otool", "-arch", arch, "-l", str(path)]), arch
    ) for arch in sorted(architectures)]


def inspect_xcframework(root, module, architectures):
    framework = root / (module + ".xcframework")
    info = plistlib.loads((framework / "Info.plist").read_bytes())
    libraries = info.get("AvailableLibraries")
    require(type(libraries) is list and libraries, f"Missing slices: {module}")
    targets, found = [], set()
    for library in libraries:
        require(library.get("SupportedPlatform") == "macos" and
                "SupportedPlatformVariant" not in library, f"Non-macOS slice: {module}")
        selected = library.get("SupportedArchitectures")
        require(type(selected) is list and selected and len(selected) == len(set(selected)) and
                set(selected).isdisjoint(found), f"Invalid slice architecture inventory: {module}")
        found.update(selected)
        identifier = relative_path(library["LibraryIdentifier"])
        # xcodebuild records BinaryPath for both static libraries and frameworks.
        binary_path = library.get("BinaryPath")
        if binary_path is None:
            binary_path = library["LibraryPath"]
            if binary_path.endswith(".framework"):
                binary_path += "/" + module
        relative = relative_path(binary_path)
        binary = (framework / identifier / relative).resolve(strict=True)
        require(binary.is_relative_to(framework.resolve(strict=True)) and binary.is_file(),
                f"Missing or escaping library: {module}")
        targets.extend(inspect_binary(binary, selected))
    require(found == set(architectures), f"XCFramework architecture mismatch: {module}")
    return sorted(targets, key=lambda item: item["architecture"])


def validate_payload(payload):
    require(type(payload) is dict and set(payload) == {
        "schemaVersion", "sourceRevision", "sourceTreeState", "buildTargets", "inventory",
    }, "Unsupported payload shape")
    require(type(payload["schemaVersion"]) is int and payload["schemaVersion"] == 1,
            "Unsupported payload schema")
    require(type(payload["sourceRevision"]) is str and
            re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", payload["sourceRevision"]),
            "Payload needs a full source revision")
    require(payload["sourceTreeState"] in ("clean", "dirty"), "Invalid source tree state")
    require(type(payload["inventory"]) is dict and
            payload["inventory"].get("roots") == PAYLOAD_ROOTS, "Incomplete payload roots")
    entries = payload["inventory"].get("entries")
    require(type(entries) is dict and all(type(entries.get("Consumer/" + name)) is dict and
            entries["Consumer/" + name].get("kind") == "file" for name in REQUIRED_KIT_FILES),
            "Incomplete consumer recipe inventory")
    expected = set(BINARY_MODULES) | {"SwiftPythonWorker", "SwiftPythonAudioProbe"}
    require(type(payload["buildTargets"]) is dict and set(payload["buildTargets"]) == expected,
            "Incomplete payload build targets")
    shared_architectures = None
    for name, targets in payload["buildTargets"].items():
        require(type(targets) is list and targets, f"Missing build targets: {name}")
        architectures = set()
        for target in targets:
            require(type(target) is dict and set(target) == {
                "platform", "architecture", "minimumOS", "sdks",
            }, f"Invalid build target: {name}")
            require(target["platform"] == "macos" and
                    target["architecture"] in ("arm64", "x86_64") and
                    target["architecture"] not in architectures, f"Invalid architecture: {name}")
            architectures.add(target["architecture"])
            version_tuple(target["minimumOS"])
            require(type(target["sdks"]) is list and target["sdks"], f"Missing SDK: {name}")
            for sdk in target["sdks"]:
                version_tuple(sdk)
        if shared_architectures is None:
            shared_architectures = architectures
        require(architectures == shared_architectures, f"Mixed payload architectures: {name}")
    return payload


def create_payload(root, revision, tree_state, architectures):
    root = Path(root).resolve(strict=True)
    require(set(architectures) and set(architectures) <= {"arm64", "x86_64"}, "Invalid architectures")
    targets = {name: inspect_xcframework(root, name, architectures) for name in BINARY_MODULES}
    for name in ("SwiftPythonWorker", "SwiftPythonAudioProbe"):
        targets[name] = inspect_binary(root / name, architectures)
    payload = {"schemaVersion": 1, "sourceRevision": revision, "sourceTreeState": tree_state,
               "buildTargets": targets,
               "inventory": {"roots": PAYLOAD_ROOTS, "entries": tree_inventory(root, PAYLOAD_ROOTS)}}
    validate_payload(payload)
    (root / PAYLOAD_NAME).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def host_records(payload):
    validate_payload(payload)
    result = []
    for kind in HOST_KINDS:
        apple = kind != "sidecar"
        modules = CORE_MODULES + (("SwiftPythonWorkerService",) if apple else ())
        artifacts = [module + ".xcframework.zip" for module in modules]
        if not apple:
            artifacts.append("SwiftPythonWorker")
        recipe = {"sidecar": "Entitlements", "xpc-service": "Consumer/XPC",
                  "extension-foundation": "Consumer/ExtensionFoundation"}[kind]
        result.append({
            "kind": kind, "artifacts": artifacts,
            "recipe": {"path": recipe, "schemaVersion": 1, "inventory": PAYLOAD_NAME},
            "maturity": "preview" if apple else "supported",
            "sourceAvailability": {"platform": "macos", "minimumOS": "26.0" if kind == "extension-foundation" else "15.0"},
            "buildTargets": {module: payload["buildTargets"][module] for module in
                             modules + (() if apple else ("SwiftPythonWorker",))},
            "qualification": {"status": "not-run", "evidence": []},
        })
    return result


def validate_host_manifest(manifest, payload=None, requested_host=None):
    require(type(manifest) is dict, "Manifest must be an object")
    schema = manifest.get("manifestSchemaVersion")
    require(type(schema) is int and schema in (3, 4), "Unsupported commercial manifest schema")
    if schema == 3:
        require("hosts" not in manifest, "Schema 3 cannot declare new worker hosts")
        require(requested_host in (None, "sidecar"), "Schema 3 has no Apple worker host contract")
        return
    require(payload is not None, "Schema 4 requires its attested payload inventory")
    validate_payload(payload)
    require(manifest.get("sourceRevision") == payload["sourceRevision"] and
            manifest.get("sourceTreeState") == payload["sourceTreeState"], "Manifest/payload source mismatch")
    expected = host_records(payload)
    require(exact_value(manifest.get("hosts"), expected),
            "Invalid host contract or unsupported execution qualification; build metadata is not execution evidence")
    if requested_host is not None:
        require(requested_host in HOST_KINDS, "Unknown requested host")
    records = manifest.get("artifacts")
    require(type(records) is list, "Missing manifest artifacts")
    names = [item.get("name") for item in records if type(item) is dict]
    require(len(names) == len(records) and len(names) == len(set(names)), "Duplicate or invalid artifact records")
    require({name + ".xcframework.zip" for name in BINARY_MODULES} <= set(names),
            "Incomplete commercial binary artifacts")
    require(PAYLOAD_NAME in names and "SwiftPythonWorker" in names, "Missing host input artifacts")
    for record in records:
        name = record["name"]
        if name == PAYLOAD_NAME:
            require(record.get("role") == "payloadInventory" and record.get("path") == PAYLOAD_NAME,
                    "Invalid payload inventory artifact")


def verified_payload(root, *, manifest_path=None, allow_development=False, requested_host=None):
    root = Path(root).resolve(strict=True)
    path = root / PAYLOAD_NAME
    payload = validate_payload(load_json(path))
    if manifest_path is None:
        require(allow_development, "Release provenance requires an external manifest; local development must be explicit")
    else:
        manifest = load_json(manifest_path)
        validate_host_manifest(manifest, payload, requested_host)
        require(manifest["manifestSchemaVersion"] == 4, "This consumer kit requires schema 4")
        record = next(item for item in manifest["artifacts"] if item["name"] == PAYLOAD_NAME)
        require(record.get("sha256") == sha256(path) and type(record.get("bytes")) is int and
                record["bytes"] == path.stat().st_size,
                "Publisher payload inventory hash/size mismatch")
        require(payload["sourceTreeState"] == "clean", "Release inputs must have clean source provenance")
    verify_inventory(root, payload["inventory"])
    return payload


def verify_staged_payload(root, *, expected_payload, source_revision, require_clean=False):
    """Bind every copied input to the builder's independently supplied inventory."""
    root = Path(root).resolve(strict=True)
    require(sha256(root / PAYLOAD_NAME) == sha256(expected_payload),
            "Staged payload inventory differs from the build output")
    payload = verified_payload(root, allow_development=True)
    require(payload["sourceRevision"] == source_revision, "Staged source revision mismatch")
    require(not require_clean or payload["sourceTreeState"] == "clean",
            "Release inputs must have clean source provenance")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-payload")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--source-revision", required=True)
    create.add_argument("--source-tree-state", choices=("clean", "dirty"), required=True)
    create.add_argument("--architectures", nargs="+", required=True)
    hosts = commands.add_parser("hosts")
    hosts.add_argument("--payload", type=Path, required=True)
    verify = commands.add_parser("verify-staged")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--expected-payload", type=Path, required=True)
    verify.add_argument("--source-revision", required=True)
    verify.add_argument("--require-clean", action="store_true")
    release = commands.add_parser("validate-release")
    release.add_argument("--root", type=Path, required=True)
    release.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create-payload":
        create_payload(args.root, args.source_revision, args.source_tree_state, args.architectures)
        print(json.dumps({"payload": str(args.root / PAYLOAD_NAME), "sha256": sha256(args.root / PAYLOAD_NAME)}))
    elif args.command == "hosts":
        print(json.dumps(host_records(load_json(args.payload)), sort_keys=True))
    elif args.command == "verify-staged":
        verify_staged_payload(args.root, expected_payload=args.expected_payload,
                              source_revision=args.source_revision, require_clean=args.require_clean)
        print(json.dumps({"verified": True, "root": str(args.root)}))
    else:
        verified_payload(args.root, manifest_path=args.manifest)
        print(json.dumps({"verified": True, "schemaVersion": 4}))


if __name__ == "__main__":
    try:
        main()
    except (ContractError, OSError, ValueError, KeyError) as error:
        print(f"commercial host contract: {error}", file=sys.stderr)
        sys.exit(1)
