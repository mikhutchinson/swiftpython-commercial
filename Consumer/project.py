"""Deterministic app-owned XPC and ExtensionFoundation project descriptions."""
from __future__ import annotations

import json
from pathlib import Path
import re

from contract import require
from release_manifest import BINARY_MODULES, CORE_MODULES, host_records, version_tuple


def configuration(payload, host, bundle_id, workers, parent_sandbox, service_sandbox):
    require(host in ("xpc-service", "extension-foundation"), "Expected an Apple worker host")
    require(isinstance(bundle_id, str) and len(bundle_id) <= 180 and
            re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z][A-Za-z0-9-]*){2,}", bundle_id),
            "Use a consumer-owned reverse-DNS bundle identifier with at least three components")
    require(type(workers) is int and 2 <= workers <= 64,
            "The respawn smoke needs a finite inventory of 2 through 64 workers")
    require(type(parent_sandbox) is bool and type(service_sandbox) is bool, "Explicit sandbox policies required")
    require(host != "xpc-service" or not service_sandbox,
            "The XPC recipe currently requires nonsandboxed services; parent sandbox policy is independent")
    require(host != "extension-foundation" or service_sandbox,
            "ExtensionFoundation services require their own sandbox policy")
    record = next(item for item in host_records(payload) if item["kind"] == host)
    targets = [target for values in record["buildTargets"].values() for target in values]
    minimum = max([record["sourceAvailability"]["minimumOS"]] +
                  [item["minimumOS"] for item in targets], key=version_tuple)
    return {"schemaVersion": 1, "host": host, "bundleIdentifier": bundle_id,
            "workerIdentifiers": [f"{bundle_id}.worker{index:02d}" for index in range(workers)],
            "parentSandbox": parent_sandbox, "serviceSandbox": service_sandbox,
            "architectures": sorted({item["architecture"] for item in targets}),
            "minimumOS": minimum}


def entitlements(sandbox):
    return {"com.apple.security.app-sandbox": True,
            "com.apple.security.network.client": True,
            "com.apple.security.network.server": True} if sandbox else {}


def local_package():
    products = {"SwiftPythonRuntime": CORE_MODULES,
                "SwiftPythonWorkerService": ("SwiftPythonWorkerService",) + CORE_MODULES,
                "SwiftPythonAudioInterop": ("SwiftPythonAudioInterop",) + CORE_MODULES,
                "SwiftPythonMetalInterop": ("SwiftPythonMetalInterop",) + CORE_MODULES}
    return '''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "SwiftPythonCommercial", platforms: [.macOS(.v15)],
    products: [
''' + "".join(f'        .library(name: {json.dumps(name)}, targets: {json.dumps(list(modules))}),\n'
              for name, modules in products.items()) + '''    ], targets: [
''' + "".join(f'        .binaryTarget(name: "{name}", path: "{name}.xcframework"),\n'
              for name in BINARY_MODULES) + "    ])\n"


def render(template, substitutions):
    result = template
    for key, value in substitutions.items():
        require(key in result, f"Missing template placeholder: {key}")
        result = result.replace(key, value)
    require(re.search(r"__[A-Z_]+__", result) is None, "Unresolved template placeholder")
    return result


def sources(kit, config):
    kit = Path(kit)
    extension = config["host"] == "extension-foundation"
    app = render((kit / "Common/App.swift.template").read_text(), {
        "__HOST_IMPORTS__": "import ExtensionFoundation" if extension else "",
        "__EXTENSION_POINT__": """extension AppExtensionPoint {
    @Definition static var pythonWorker: AppExtensionPoint { Name("Worker") }
}""" if extension else "",
        "__CONFIGURE_HOST__": "try PythonWorkerExtensions.configure(extensionPoint: .pythonWorker)" if extension
            else 'try PythonWorkerXPCServices.configure(environment: ["PYTHONDONTWRITEBYTECODE": "1"])',
        "__HOST_KIND__": config["host"],
    })
    worker = render((kit / "ExtensionFoundation/Worker.swift.template").read_text(), {
        "__CONSUMER_BUNDLE_ID__": config["bundleIdentifier"],
    }) if extension else (kit / "XPC/Worker.swift").read_text()
    return app, worker


def project(config):
    extension = config["host"] == "extension-foundation"
    worker_info = {"EXAppExtensionAttributes": {
        "EXExtensionPointIdentifier": config["bundleIdentifier"] + ".Worker",
    }} if extension else {"XPCService": {"ServiceType": "Application", "_ProcessType": "Interactive",
                                         "RunLoopType": "NSRunLoop", "JoinExistingSession": True}}
    worker_settings = {"SKIP_INSTALL": True,
                       "LD_RUNPATH_SEARCH_PATHS": "$(inherited) @executable_path/../../../../Frameworks"}
    if extension:
        worker_settings["APPLICATION_EXTENSION_API_ONLY"] = True
    inventory = {"version": 1, "bundleIdentifiers": config["workerIdentifiers"]}
    if extension:
        inventory["extensionPointIdentifier"] = config["bundleIdentifier"] + ".Worker"
    app_settings = {"PRODUCT_BUNDLE_IDENTIFIER": config["bundleIdentifier"],
                    "LD_RUNPATH_SEARCH_PATHS": "$(inherited) @executable_path/../Frameworks"}
    if extension:
        app_settings["EX_ENABLE_EXTENSION_POINT_GENERATION"] = True
    worker_names = [f"PythonWorker{index:02d}" for index in range(len(config["workerIdentifiers"]))]
    targets = {"ConsumerApp": {
        "type": "application", "platform": "macOS", "sources": ["App"],
        "dependencies": [{"package": "SwiftPython", "product": "SwiftPythonRuntime"}] +
                        [{"target": name, "embed": True} for name in worker_names],
        "settings": {"base": app_settings},
        "info": {"path": "Generated/App-Info.plist", "properties": {
            "LSUIElement": True,
            "SwiftPythonWorkerExtensions" if extension else "SwiftPythonWorkerXPCServices": inventory,
        }},
    }}
    for name, identifier in zip(worker_names, config["workerIdentifiers"]):
        targets[name] = {"templates": ["Worker"], "settings": {"base": {"PRODUCT_BUNDLE_IDENTIFIER": identifier}}}
    return {
        "name": "ConsumerApp", "packages": {"SwiftPython": {"path": "Inputs"}},
        "options": {"deploymentTarget": {"macOS": config["minimumOS"]}},
        "settings": {"base": {"SWIFT_VERSION": "6.0", "GENERATE_INFOPLIST_FILE": True,
            "CURRENT_PROJECT_VERSION": "1", "MARKETING_VERSION": "0.0.1",
            "ENABLE_HARDENED_RUNTIME": True, "ENABLE_DEBUG_DYLIB": False,
            # These generated hosts/workers declare no AppIntents. Extension-point
            # generation remains independently enabled for ExtensionFoundation.
            "LM_SKIP_METADATA_EXTRACTION": True,
            "CODE_SIGNING_ALLOWED": False, "ENABLE_USER_SCRIPT_SANDBOXING": True}},
        "targetTemplates": {"Worker": {
            "type": "extensionkit-extension" if extension else "xpc-service", "platform": "macOS",
            "sources": ["Worker"], "dependencies": [{"package": "SwiftPython", "product": "SwiftPythonWorkerService", "embed": False}],
            "settings": {"base": worker_settings},
            "info": {"path": "Generated/${target_name}-Info.plist", "properties": worker_info},
        }}, "targets": targets,
        "schemes": {"ConsumerApp": {"build": {"targets": {"ConsumerApp": "all"}}}},
    }
