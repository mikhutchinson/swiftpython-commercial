#!/usr/bin/env python3
"""Generate, build, sign and preflight a minimal app from verified commercial inputs.

Requires Python 3.9+, Xcode with the required SDK, and XcodeGen. Neither the
generated app nor its workers discover a Python installation on the user's Mac.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

# This recipe is an immutable input, including when invoked without Python -B.
sys.dont_write_bytecode = True

from contract import ContractError, exact_value, load_json, relative_path, require, sha256, tree_inventory, verify_inventory
from native import command, finalize_runtime, native_closure, sign, verify_signatures
from project import configuration, entitlements, local_package, project, sources
from release_manifest import PAYLOAD_NAME, PAYLOAD_ROOTS, verified_payload


GENERATED_ROOTS = ["App", "Worker", "Config", "project.json", "Inputs/Package.swift"]
KIT = Path(__file__).resolve().parent


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def verify_recipe(inputs):
    require(exact_value(tree_inventory(KIT.parent, [KIT.name]), tree_inventory(inputs, ["Consumer"])),
            "The executing consumer kit differs from the attested recipe; use the candidate's matching kit")


def generate(args):
    distribution = args.distribution.resolve(strict=True)
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(distribution),
            "Output must be a new directory outside the verified distribution")
    payload = verified_payload(distribution, manifest_path=args.manifest,
                               allow_development=args.allow_development, requested_host=args.host)
    config = configuration(payload, args.host, args.bundle_id, args.worker_count,
                           args.parent_sandbox == "yes", args.service_sandbox == "yes")
    # Use only the attested recipe, even if this tool was invoked from elsewhere.
    verify_recipe(distribution)
    output.mkdir(parents=True)
    inputs = output / "Inputs"
    inputs.mkdir()
    for name in PAYLOAD_ROOTS + [PAYLOAD_NAME]:
        original = distribution / name
        if original.is_dir():
            shutil.copytree(original, inputs / name, symlinks=True)
        else:
            shutil.copy2(original, inputs / name)
    if args.manifest:
        shutil.copy2(args.manifest, output / "publisher-manifest.json")
    verified_payload(inputs, manifest_path=output / "publisher-manifest.json" if args.manifest else None,
                     allow_development=args.allow_development, requested_host=args.host)
    app, worker = sources(inputs / "Consumer", config)
    for directory, contents in (("App", app), ("Worker", worker)):
        (output / directory).mkdir()
        (output / directory / "Main.swift").write_text(contents)
    (output / "Config").mkdir()
    for name, sandbox in (("App", config["parentSandbox"]), ("Worker", config["serviceSandbox"])):
        (output / "Config" / (name + ".entitlements")).write_bytes(plistlib.dumps(entitlements(sandbox)))
    (inputs / "Package.swift").write_text(local_package())
    write_json(output / "project.json", project(config))
    record = {"schemaVersion": 1, "configuration": config,
              "provenance": "release" if args.manifest else "development",
              "payloadSHA256": sha256(inputs / PAYLOAD_NAME),
              "publisherManifestSHA256": sha256(output / "publisher-manifest.json") if args.manifest else None,
              "generated": {"roots": GENERATED_ROOTS, "entries": tree_inventory(output, GENERATED_ROOTS)}}
    write_json(output / "assembly.json", record)
    return {"stage": "generate", "project": str(output), "provenance": record["provenance"], "configuration": config}


def load_project(root):
    root = root.resolve(strict=True)
    record = load_json(root / "assembly.json")
    require(type(record) is dict and set(record) == {"schemaVersion", "configuration", "provenance",
            "payloadSHA256", "publisherManifestSHA256", "generated"} and
            type(record["schemaVersion"]) is int and record["schemaVersion"] == 1,
            "Unsupported assembly receipt")
    require(record["provenance"] in ("release", "development"), "Unknown assembly provenance")
    release = record["provenance"] == "release"
    inputs = root / "Inputs"
    require(sha256(inputs / PAYLOAD_NAME) == record["payloadSHA256"], "Changed input payload")
    if release:
        require(sha256(root / "publisher-manifest.json") == record["publisherManifestSHA256"],
                "Changed publisher manifest")
    else:
        require(record["publisherManifestSHA256"] is None, "Development assembly cannot claim a publisher manifest")
    config = record["configuration"]
    payload = verified_payload(inputs, manifest_path=root / "publisher-manifest.json" if release else None,
                               allow_development=not release, requested_host=config["host"])
    verify_recipe(inputs)
    expected = configuration(payload, config["host"], config["bundleIdentifier"], len(config["workerIdentifiers"]),
                             config["parentSandbox"], config["serviceSandbox"])
    require(exact_value(config, expected), "Invalid assembly configuration")
    require(record["generated"]["roots"] == GENERATED_ROOTS, "Incomplete generated input inventory")
    verify_inventory(root, record["generated"])
    require(exact_value(load_json(root / "project.json"), project(config)), "Altered project dependency closure")
    require((inputs / "Package.swift").read_text() == local_package(), "Altered local binary package closure")
    app, worker = sources(inputs / "Consumer", config)
    require((root / "App/Main.swift").read_text() == app and (root / "Worker/Main.swift").read_text() == worker,
            "Altered host bootstrap or worker entry point")
    for name, sandbox in (("App", config["parentSandbox"]), ("Worker", config["serviceSandbox"])):
        require(exact_value(plistlib.loads((root / "Config" / (name + ".entitlements")).read_bytes()), entitlements(sandbox)),
                f"Altered target policy: {name}")
    return root, record


def static_linkage(root, config):
    """Bind each target/architecture's linker inputs to the attested archives."""
    root = root.resolve(strict=True)
    products = root / "DerivedData/Build/Products/Release"
    intermediate = root / "DerivedData/Build/Intermediates.noindex/ConsumerApp.build/Release"
    targets = ["ConsumerApp"] + [f"PythonWorker{index:02d}" for index in range(len(config["workerIdentifiers"]))]
    records = {}
    for target in targets:
        modules = ["SwiftPythonRuntime"] + (["SwiftPythonWorkerService"] if target != "ConsumerApp" else [])
        for arch in config["architectures"]:
            map_path = intermediate / f"{target}.build/{target}-LinkMap-normal-{arch}.txt"
            content = map_path.read_text()
            require(re.search(r"(?m)^# Arch: " + re.escape(arch) + r"$", content),
                    f"Wrong linker-map architecture: {map_path}")
            require("# Object files:\n" in content and "# Sections:\n" in content, f"Incomplete linker map: {map_path}")
            objects = content.split("# Object files:\n", 1)[1].split("# Sections:\n", 1)[0]
            archives = {Path(value).resolve() for value in re.findall(r"(?m)^\[\s*\d+\]\s+(.+\.a)\([^\n]+\)$", objects)}
            bound = {}
            for module in modules:
                framework = root / "Inputs" / (module + ".xcframework")
                info = plistlib.loads((framework / "Info.plist").read_bytes())
                slices = [item for item in info["AvailableLibraries"]
                          if item["SupportedPlatform"] == "macos" and arch in item["SupportedArchitectures"]]
                require(len(slices) == 1, f"Ambiguous static input for {module}/{arch}")
                selected = slices[0]
                source = framework / relative_path(selected["LibraryIdentifier"]) / relative_path(selected["LibraryPath"])
                linked = (products / source.name).resolve(strict=True)
                require(linked.is_relative_to(products.resolve()) and linked in archives,
                        f"No linked {module} archive in {target}/{arch}")
                digest = sha256(source)
                require(sha256(linked) == digest, f"Linker consumed an unattested {module} archive: {target}/{arch}")
                bound[module] = {"input": str(source.relative_to(root)), "sha256": digest}
            records[f"{target}/{arch}"] = {"map": str(map_path.relative_to(root)),
                                          "sha256": sha256(map_path), "archives": bound}
    return records


def build(args):
    root, record = load_project(args.project)
    config = record["configuration"]
    for name in ("xcodegen", "xcodebuild", "codesign", "xcrun"):
        require(shutil.which(name), f"Missing build prerequisite: {name}")
    require(args.identity != "-", "A real Apple team identity is required")
    require(args.signing_mode != "distribution" or record["provenance"] == "release",
            "Distribution signing requires publisher-attested release inputs")
    def toolchain():
        return {"xcode": command("xcodebuild", "-version").strip(),
                "swift": command("xcrun", "swift", "--version").strip(),
                "macOSSDK": command("xcrun", "--sdk", "macosx", "--show-sdk-version").strip(),
                "xcodegen": command("xcodegen", "--version").strip()}
    build_tools = toolchain()
    receipt_path = root / "signing-receipt.json"
    receipt_path.unlink(missing_ok=True)
    log_path = root / "build.log"
    with log_path.open("w") as log:
        for arguments in (
            ["xcodegen", "generate", "--spec", "project.json"],
            ["xcodebuild", "-project", "ConsumerApp.xcodeproj", "-scheme", "ConsumerApp",
             "-configuration", "Release", "-derivedDataPath", str(root / "DerivedData"),
             "-destination", "generic/platform=macOS", "build",
             "CODE_SIGNING_ALLOWED=NO", "ONLY_ACTIVE_ARCH=NO", "ARCHS=" + " ".join(config["architectures"]),
             "LD_GENERATE_MAP_FILE=YES"],
        ):
            result = subprocess.run(arguments, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=False)
            require(result.returncode == 0, f"Build command failed; see {log_path}: {arguments[0]}")
    # Recheck the exact unsigned inputs after Xcode has consumed them.
    load_project(root)
    require(toolchain() == build_tools, "Selected build toolchain changed during assembly")
    linkage = static_linkage(root, config)
    app = root / "DerivedData/Build/Products/Release/ConsumerApp.app"
    finalize_runtime(app, config)
    signatures = sign(app, config, args.identity, args.signing_mode, root / "Config")
    native_closure(app, config)
    receipt = {"schemaVersion": 1, "stage": "signed-assembly", "signingMode": args.signing_mode,
               "assemblySHA256": sha256(root / "assembly.json"), "payloadSHA256": record["payloadSHA256"],
               "appRelativePath": str(app.relative_to(root)), "configuration": config, "signatures": signatures,
               "staticLinkage": linkage,
               "toolchain": build_tools,
               "output": {"roots": [app.name], "entries": tree_inventory(app.parent, [app.name])}}
    write_json(receipt_path, receipt)
    return preflight(root, receipt_path)



def signed_app_inventory(app, inventory, signing_mode):
    """Preserve sealed inputs while independently checking an added Apple ticket."""
    require(type(inventory) is dict and set(inventory) == {"roots", "entries"} and
            inventory["roots"] == [app.name] and type(inventory["entries"]) is dict,
            "Unsupported signed app inventory")
    ticket_path = app.name + "/Contents/CodeResources"
    observed = tree_inventory(app.parent, [app.name])
    ticket = observed.get(ticket_path)
    expected = inventory
    if ticket is not None:
        require(signing_mode == "distribution", "Stapled ticket requires distribution signing")
        require(ticket.get("kind") == "file" and type(ticket.get("mode")) is int and
                ticket["mode"] & 0o111 == 0 and ticket.get("size", 0) > 0,
                "Stapled ticket must be a nonempty non-executable regular file")
        if ticket_path not in inventory["entries"]:
            expected = {"roots": inventory["roots"],
                        "entries": {**inventory["entries"], ticket_path: ticket}}
    # Validate the entire tree before invoking Apple's ticket validator. Nothing
    # except the one additive ticket may differ from the original signing receipt.
    verify_inventory(app.parent, expected)
    if ticket is None:
        return None
    command("xcrun", "stapler", "validate", str(app))
    # Catch ticket or signed-output mutation during the external validation call.
    verify_inventory(app.parent, expected)
    return {"path": ticket_path, **ticket, "validation": "stapler-validate"}


def preflight(root, receipt_path):
    root, record = load_project(root)
    receipt = load_json(receipt_path)
    require(type(receipt) is dict and set(receipt) == {"schemaVersion", "stage", "signingMode", "assemblySHA256",
            "payloadSHA256", "appRelativePath", "configuration", "signatures", "staticLinkage", "toolchain", "output"} and
            type(receipt["schemaVersion"]) is int and receipt["schemaVersion"] == 1 and
            receipt["stage"] == "signed-assembly", "Unsupported signing receipt")
    require(receipt["assemblySHA256"] == sha256(root / "assembly.json") and
            receipt["payloadSHA256"] == record["payloadSHA256"] and
            exact_value(receipt["configuration"], record["configuration"]), "Signed output/input provenance mismatch")
    require(type(receipt["toolchain"]) is dict and set(receipt["toolchain"]) == {
        "xcode", "swift", "macOSSDK", "xcodegen"} and all(type(value) is str and value
        for value in receipt["toolchain"].values()), "Incomplete build toolchain receipt")
    require(receipt["signingMode"] in ("local-development", "distribution"), "Invalid signing mode")
    require(receipt["signingMode"] != "distribution" or record["provenance"] == "release",
            "Distribution signature requires release provenance")
    require(receipt["appRelativePath"] == "DerivedData/Build/Products/Release/ConsumerApp.app", "Unexpected app output path")
    app = root / receipt["appRelativePath"]
    require(receipt["output"]["roots"] == [app.name], "Incomplete signed app inventory")
    stapled_ticket = signed_app_inventory(app, receipt["output"], receipt["signingMode"])
    require(exact_value(static_linkage(root, record["configuration"]), receipt["staticLinkage"]),
            "Static linkage evidence changed since assembly")
    native_closure(app, record["configuration"])
    signatures = verify_signatures(app, record["configuration"], receipt["signingMode"])
    require(signatures == receipt["signatures"], "Signed identities changed since assembly")
    result = {"stage": "preflight", "passed": True, "app": str(app), "provenance": record["provenance"],
            "signingMode": receipt["signingMode"], "signingReceiptSHA256": sha256(receipt_path),
            "execution": "not-run", "notarization": "not-run"}
    if stapled_ticket is not None:
        result["stapledTicket"] = stapled_ticket
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate_parser = commands.add_parser("generate")
    generate_parser.add_argument("--distribution", type=Path, required=True)
    provenance = generate_parser.add_mutually_exclusive_group(required=True)
    provenance.add_argument("--manifest", type=Path)
    provenance.add_argument("--allow-development", action="store_true")
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--host", choices=("xpc-service", "extension-foundation"), required=True)
    generate_parser.add_argument("--bundle-id", required=True)
    generate_parser.add_argument("--worker-count", type=int, default=2)
    generate_parser.add_argument("--parent-sandbox", choices=("yes", "no"), required=True)
    generate_parser.add_argument("--service-sandbox", choices=("yes", "no"), required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--project", type=Path, required=True)
    build_parser.add_argument("--identity", required=True)
    build_parser.add_argument("--signing-mode", choices=("local-development", "distribution"), required=True)
    check_parser = commands.add_parser("preflight")
    check_parser.add_argument("--project", type=Path, required=True)
    check_parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate":
        result = generate(args)
    elif args.command == "build":
        result = build(args)
    else:
        result = preflight(args.project, args.receipt)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ContractError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"passed": False, "error": str(error)}), file=sys.stderr)
        sys.exit(1)
