#!/usr/bin/env python3
"""Audit the raw SwiftPython audio-probe release artifact and manifest.

The helper is intentionally not a SwiftPM binary target.  It is a separately
staged executable which consumers copy to one fixed app-bundle path and re-sign
as nested code.  This audit keeps that raw-artifact contract independent from
the Audio XCFramework contract.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import plistlib
import re
import stat
import subprocess
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

PROBE_NAME = "SwiftPythonAudioProbe"
PROBE_ROLE = "audioHardwareProbeExecutable"
PROBE_IDENTIFIER = "com.swiftpython.audio-readiness-probe"
PROBE_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 3
NON_SANDBOX_ENTITLEMENTS = "SwiftPythonAudioProbe.entitlements"
SANDBOX_ENTITLEMENTS = "SwiftPythonAudioProbe-sandbox.entitlements"
EXPECTED_PUBLIC_ARCHITECTURES = ("arm64", "x86_64")
PYTHON_LOAD_COMMAND = "@rpath/Python.framework/Versions/3.13/Python"
EMBEDDED_PYTHON_FRAMEWORK_RUN_PATH = "@executable_path/../Frameworks"
EXPECTED_VM_IMAGE_VERSION = 1
EXPECTED_VM_DISTRO = "ubuntu-24.04-arm64"
EXPECTED_PROTOCOLS = {
    "workerWire": 6,
    "supervisorControl": "3",
    "duplexHelperSchema": 1,
    "duplexMedia": 1,
    "duplexFeatureHelpers": {
        "duplex.messages.v1": 1,
        "duplex.arena-ingress.v1": 2,
    },
    "audioHardwareProbe": PROBE_SCHEMA_VERSION,
}
VM_HELPER_NAMES = (
    "_swiftpython_wire.py",
    "_swiftpython_duplex.py",
    "swiftpython_protocol.py",
    "swiftpython_supervisor.py",
    "swiftpython_worker.py",
)
REQUIRED_MANIFEST_KEYS = {
    "manifestSchemaVersion",
    "version",
    "date",
    "swiftToolsVersion",
    "platforms",
    "sourceRevision",
    "sourceTreeState",
    "protocols",
    "artifacts",
    "distributionZip",
    "vmImage",
}
REQUIRED_DISTRIBUTION_FILES = {
    "Package.swift",
    "VERSION",
    "LICENSE",
    "README.md",
    "SwiftPythonWorker",
    PROBE_NAME,
    "SwiftPythonRuntime.xcframework/Info.plist",
    "SwiftPythonEngine.xcframework/Info.plist",
    "SwiftPythonAudioInterop.xcframework/Info.plist",
    "SwiftPythonMetalInterop.xcframework/Info.plist",
    *(f"VMWorker/{name}" for name in VM_HELPER_NAMES),
    f"Entitlements/{NON_SANDBOX_ENTITLEMENTS}",
    f"Entitlements/{SANDBOX_ENTITLEMENTS}",
    "Entitlements/ConsumerApp.entitlements",
    "Entitlements/SwiftPythonVM.entitlements",
    "Entitlements/SwiftPythonWorker.entitlements",
    "Entitlements/SwiftPythonWorker-sandbox.entitlements",
    "Entitlements/WorkerInfo.plist",
    "scripts/audio_probe_release_contract.py",
    "scripts/test_audio_probe_release_contract.py",
    "scripts/audit_release_surface.sh",
    "scripts/consumer_path_smoke.sh",
    "docs/api-guide/README.md",
    "docs/api-guide/ch10-full-duplex.md",
    "docs/api-guide/ch11-apple-interop.md",
    "Sources/SwiftPythonSmoke/main.swift",
    "Tests/SwiftPythonSmokeTests/PublicDocumentationAPITests.swift",
    "Tests/SwiftPythonSmokeTests/SwiftPythonSmokeTests.swift",
}
REQUIRED_EXECUTABLE_DISTRIBUTION_FILES = {
    "SwiftPythonWorker",
    PROBE_NAME,
    "scripts/audit_release_surface.sh",
    "scripts/consumer_path_smoke.sh",
}
FORBIDDEN_DISTRIBUTION_COMPONENTS = {
    ".git",
    ".build",
    ".swiftpm",
    ".plan",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "Artifacts",
    "DerivedData",
    "__pycache__",
}

EXPECTED_NON_SANDBOX_ENTITLEMENTS = {
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.cs.allow-dyld-environment-variables": True,
}
EXPECTED_SANDBOX_ENTITLEMENTS = {
    "com.apple.security.app-sandbox": True,
    "com.apple.security.inherit": True,
}
ALLOWED_RUN_PATHS = {
    "/usr/lib/swift",
    "@loader_path",
    EMBEDDED_PYTHON_FRAMEWORK_RUN_PATH,
}


class ContractError(RuntimeError):
    """A fail-closed release-contract violation."""


@dataclass(frozen=True)
class PythonRuntimeInventory:
    architectures: tuple[str, ...]
    load_commands: tuple[str, ...]
    run_paths: tuple[str, ...]
    python_load_commands: tuple[str, ...]


@dataclass(frozen=True)
class ProbeInventory:
    architectures: tuple[str, ...]
    platform: str
    minimum_os_version: str
    sdk_version: str
    load_commands: tuple[str, ...]
    run_paths: tuple[str, ...]
    python_load_commands: tuple[str, ...]
    signing_identifier: str
    signature_kind: str
    signature_authorities: tuple[str, ...]
    team_identifier: str
    signed_entitlements: Mapping[str, bool]
    bundle_identifier: str
    bundle_name: str
    bundle_version: str
    microphone_usage_description: str
    schema_version: int

    def manifest_fields(self) -> dict[str, Any]:
        return {
            "architectures": list(self.architectures),
            "platform": self.platform,
            "minimumOSVersion": self.minimum_os_version,
            "sdkVersion": self.sdk_version,
            "loadCommands": list(self.load_commands),
            "runPaths": list(self.run_paths),
            "pythonLoadCommands": list(self.python_load_commands),
            "pythonLinked": True,
            "signingIdentifier": self.signing_identifier,
            "signatureKind": self.signature_kind,
            "signatureAuthorities": list(self.signature_authorities),
            "teamIdentifier": self.team_identifier,
            "hardenedRuntime": True,
            "signedEntitlements": dict(self.signed_entitlements),
            "bundleIdentifier": self.bundle_identifier,
            "bundleName": self.bundle_name,
            "bundleVersion": self.bundle_version,
            "microphoneUsageDescription": self.microphone_usage_description,
            "schemaVersion": self.schema_version,
        }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(arguments),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = ""
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or error.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise ContractError(f"command failed: {' '.join(arguments)}{suffix}") from error


def exact_boolean_dictionary(path: pathlib.Path, description: str) -> dict[str, bool]:
    try:
        payload = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise ContractError(f"could not read {description}: {path}") from error
    require(type(payload) is dict, f"{description} must be one dictionary")
    require(
        all(type(key) is str and type(value) is bool for key, value in payload.items()),
        f"{description} must contain only Boolean entitlement values",
    )
    return payload


def verify_entitlement_templates(repo: pathlib.Path) -> None:
    entitlement_root = repo / "Entitlements"
    observed_non_sandbox = exact_boolean_dictionary(
        entitlement_root / NON_SANDBOX_ENTITLEMENTS,
        "non-sandbox audio-probe entitlements",
    )
    observed_sandbox = exact_boolean_dictionary(
        entitlement_root / SANDBOX_ENTITLEMENTS,
        "sandbox audio-probe entitlements",
    )
    require(
        observed_non_sandbox == EXPECTED_NON_SANDBOX_ENTITLEMENTS,
        "non-sandbox audio-probe entitlements must contain exactly the three "
        "current Python-linked hardened-runtime exceptions",
    )
    require(
        observed_sandbox == EXPECTED_SANDBOX_ENTITLEMENTS,
        "sandbox audio-probe entitlements must contain exactly app-sandbox + inherit",
    )


def executable_architectures(
    executable: pathlib.Path, artifact_name: str
) -> tuple[str, ...]:
    values = tuple(sorted(run(["lipo", "-archs", str(executable)]).stdout.split()))
    require(values, f"{artifact_name} has an empty architecture inventory")
    require(
        len(values) == len(set(values)),
        f"{artifact_name} architecture inventory is duplicated",
    )
    return values


def otool_lines(executable: pathlib.Path, architecture: str) -> list[str]:
    return run(
        ["otool", "-arch", architecture, "-l", str(executable)]
    ).stdout.splitlines()


def build_version(lines: Sequence[str], architecture: str) -> tuple[str, str, str]:
    versions: list[tuple[str, str, str]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != "cmd LC_BUILD_VERSION":
            index += 1
            continue
        platform = minimum = sdk = None
        index += 1
        while index < len(lines):
            field = lines[index].strip()
            if field.startswith("cmd "):
                break
            if field.startswith("platform "):
                value = field.removeprefix("platform ")
                platform = "macOS" if value == "1" else f"platform-{value}"
            elif field.startswith("minos "):
                minimum = field.removeprefix("minos ")
            elif field.startswith("sdk "):
                sdk = field.removeprefix("sdk ")
            index += 1
        require(
            all(type(value) is str and value for value in (platform, minimum, sdk)),
            f"audio probe {architecture} has an unreadable LC_BUILD_VERSION",
        )
        versions.append((platform, minimum, sdk))  # type: ignore[arg-type]
    require(
        len(versions) == 1,
        f"audio probe {architecture} must contain exactly one LC_BUILD_VERSION",
    )
    return versions[0]


def run_paths(
    lines: Sequence[str], architecture: str, artifact_name: str = "audio probe"
) -> tuple[str, ...]:
    values: list[str] = []
    expects_path = False
    for line in lines:
        field = line.strip()
        if field.startswith("cmd "):
            expects_path = field == "cmd LC_RPATH"
            continue
        if expects_path and field.startswith("path "):
            value = field.removeprefix("path ").split(" (offset ", 1)[0]
            require(
                value != "",
                f"{artifact_name} {architecture} has an unreadable LC_RPATH",
            )
            values.append(value)
            expects_path = False
    require(
        len(values) == len(set(values)),
        f"{artifact_name} contains duplicate LC_RPATH entries",
    )
    return tuple(sorted(values))


def load_commands(
    executable: pathlib.Path, architecture: str, artifact_name: str = "audio probe"
) -> tuple[str, ...]:
    output = run(
        ["otool", "-arch", architecture, "-L", str(executable)]
    ).stdout
    values = []
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if stripped:
            values.append(stripped.split(" (", 1)[0])
    values.sort()
    require(values, f"{artifact_name} has an empty dynamic-load inventory")
    return tuple(values)


def is_python_load_command(command: str) -> bool:
    lowercased = command.lower()
    if "python.framework" in lowercased:
        return True
    basename = pathlib.PurePosixPath(command).name.lower()
    return basename == "python" or basename.startswith("libpython")


def validate_python_runtime_contract(
    artifact_name: str,
    load_inventory: Sequence[str],
    run_path_inventory: Sequence[str],
) -> tuple[str, ...]:
    python_loads = tuple(
        command for command in load_inventory if is_python_load_command(command)
    )
    require(
        len(python_loads) == 1,
        f"{artifact_name} must contain exactly one Python load command; "
        f"found {list(python_loads)}",
    )
    observed = python_loads[0]
    if observed != PYTHON_LOAD_COMMAND:
        classification = (
            "absolute Python load command"
            if pathlib.PurePosixPath(observed).is_absolute()
            else "noncanonical Python load command"
        )
        raise ContractError(
            f"{artifact_name} contains an {classification}: {observed}; "
            f"expected {PYTHON_LOAD_COMMAND}"
        )
    require(
        len(run_path_inventory) == len(set(run_path_inventory)),
        f"{artifact_name} contains duplicate LC_RPATH entries",
    )
    require(
        EMBEDDED_PYTHON_FRAMEWORK_RUN_PATH in run_path_inventory,
        f"{artifact_name} is missing LC_RPATH "
        f"{EMBEDDED_PYTHON_FRAMEWORK_RUN_PATH}",
    )
    unexpected = set(run_path_inventory) - ALLOWED_RUN_PATHS
    require(
        not unexpected,
        f"{artifact_name} contains nonportable or unapproved LC_RPATH entries: "
        f"{sorted(unexpected)}",
    )
    return python_loads


def inspect_python_runtime(
    executable: pathlib.Path, artifact_name: str
) -> PythonRuntimeInventory:
    try:
        metadata = executable.lstat()
    except OSError as error:
        raise ContractError(f"missing {artifact_name}: {executable}") from error
    require(not stat.S_ISLNK(metadata.st_mode), f"{artifact_name} must not be a symlink")
    require(stat.S_ISREG(metadata.st_mode), f"{artifact_name} must be a regular file")
    require(os.access(executable, os.X_OK), f"{artifact_name} must be executable")

    architectures = executable_architectures(executable, artifact_name)
    per_arch_loads = tuple(
        load_commands(executable, architecture, artifact_name)
        for architecture in architectures
    )
    per_arch_run_paths = tuple(
        run_paths(otool_lines(executable, architecture), architecture, artifact_name)
        for architecture in architectures
    )
    require(
        len(set(per_arch_loads)) == 1,
        f"{artifact_name} slices have different load commands",
    )
    require(
        len(set(per_arch_run_paths)) == 1,
        f"{artifact_name} slices have different run paths",
    )
    loads = per_arch_loads[0]
    paths = per_arch_run_paths[0]
    python_loads = validate_python_runtime_contract(artifact_name, loads, paths)
    return PythonRuntimeInventory(
        architectures=architectures,
        load_commands=loads,
        run_paths=paths,
        python_load_commands=python_loads,
    )


def embedded_info(probe: pathlib.Path, architecture: str) -> dict[str, Any]:
    output = run(
        [
            "otool",
            "-arch",
            architecture,
            "-v",
            "-s",
            "__TEXT",
            "__info_plist",
            str(probe),
        ]
    ).stdout
    start = output.find("<?xml")
    require(start >= 0, "audio probe has no readable embedded __TEXT,__info_plist")
    try:
        payload = plistlib.loads(output[start:].encode("utf-8"))
    except plistlib.InvalidFileException as error:
        raise ContractError("audio probe embedded Info.plist is malformed") from error
    require(
        type(payload) is dict, "audio probe embedded Info.plist must be one dictionary"
    )
    return payload


def signed_entitlements(probe: pathlib.Path) -> dict[str, bool]:
    completed = run(["codesign", "-d", "--entitlements", "-", "--xml", str(probe)])
    data = (completed.stdout or completed.stderr).encode("utf-8")
    try:
        payload = plistlib.loads(data)
    except plistlib.InvalidFileException as error:
        raise ContractError("audio probe signed entitlements are unreadable") from error
    require(
        type(payload) is dict, "audio probe signed entitlements must be one dictionary"
    )
    require(
        all(type(key) is str and type(value) is bool for key, value in payload.items()),
        "audio probe signed entitlements must contain only Boolean values",
    )
    return payload


def inspect_probe(repo: pathlib.Path) -> ProbeInventory:
    probe = repo / PROBE_NAME
    runtime_inventory = inspect_python_runtime(probe, "audio probe")
    architectures = runtime_inventory.architectures
    require(
        architectures == EXPECTED_PUBLIC_ARCHITECTURES,
        "public audio probe architecture inventory must be exactly "
        f"{list(EXPECTED_PUBLIC_ARCHITECTURES)}, found {list(architectures)}",
    )
    versions = []
    per_arch_info = []
    for architecture in architectures:
        lines = otool_lines(probe, architecture)
        versions.append(build_version(lines, architecture))
        per_arch_info.append(embedded_info(probe, architecture))
    require(len(set(versions)) == 1, "audio probe slices have different build versions")
    encoded_info = [plistlib.dumps(value, sort_keys=True) for value in per_arch_info]
    require(
        len(set(encoded_info)) == 1,
        "audio probe slices have different embedded metadata",
    )

    platform, minimum_os_version, sdk_version = versions[0]
    require(
        platform == "macOS", f"audio probe platform must be macOS, found {platform}"
    )
    require(
        minimum_os_version == "15.0", "audio probe minimum macOS version must be 15.0"
    )
    loads = runtime_inventory.load_commands
    for framework in ("AVFAudio.framework", "AudioToolbox.framework"):
        require(
            any(framework in command for command in loads),
            f"audio probe is missing required {framework} load command",
        )
    python_loads = runtime_inventory.python_load_commands
    for command in loads:
        require(
            "/Users/" not in command
            and "/.build/" not in command
            and "CascadeProjects/" not in command,
            f"audio probe contains a private build-machine load command: {command}",
        )
        is_system = command.startswith(("/System/Library/", "/usr/lib/"))
        require(
            is_system or command == PYTHON_LOAD_COMMAND,
            f"audio probe contains an unapproved dynamic-load command: {command}",
        )

    info = per_arch_info[0]
    require(
        info.get("CFBundleIdentifier") == PROBE_IDENTIFIER,
        "audio probe bundle identifier is wrong",
    )
    require(
        info.get("CFBundleName") == "SwiftPython Audio Readiness Probe",
        "audio probe bundle name is wrong",
    )
    bundle_version = info.get("CFBundleVersion")
    require(
        type(bundle_version) is str and 0 < len(bundle_version.encode("utf-8")) <= 64,
        "audio probe bundle version is missing or invalid",
    )
    purpose = info.get("NSMicrophoneUsageDescription")
    require(
        type(purpose) is str and bool(purpose.strip()),
        "audio probe embedded microphone purpose string is missing",
    )
    schema = info.get("SwiftPythonAudioProbeSchemaVersion")
    require(
        type(schema) is int and schema == PROBE_SCHEMA_VERSION,
        "audio probe schema is missing or stale",
    )

    run(["codesign", "--verify", "--strict", str(probe)])
    signature = run(["codesign", "-d", "--verbose=4", str(probe)])
    details = f"{signature.stdout}\n{signature.stderr}"
    identifier_match = re.search(r"^Identifier=(.+)$", details, re.MULTILINE)
    require(identifier_match is not None, "audio probe signing identifier is missing")
    signing_identifier = identifier_match.group(1)
    require(
        signing_identifier == PROBE_IDENTIFIER,
        "audio probe signing identifier is wrong",
    )
    require(
        re.search(r"^CodeDirectory .*flags=0x.*runtime", details, re.MULTILINE)
        is not None,
        "audio probe signature is missing hardened runtime",
    )
    authorities = tuple(re.findall(r"^Authority=(.+)$", details, re.MULTILINE))
    require(
        any(value.startswith("Developer ID Application:") for value in authorities),
        "audio probe is not signed by a Developer ID Application identity",
    )
    team_match = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    require(team_match is not None, "audio probe team identifier is missing")
    team_identifier = team_match.group(1)
    require(
        team_identifier != "not set" and team_identifier != "",
        "audio probe team identifier is unset",
    )

    entitlements = signed_entitlements(probe)
    require(
        entitlements == EXPECTED_NON_SANDBOX_ENTITLEMENTS,
        "audio probe signed entitlements do not match its non-sandbox template",
    )

    return ProbeInventory(
        architectures=architectures,
        platform=platform,
        minimum_os_version=minimum_os_version,
        sdk_version=sdk_version,
        load_commands=loads,
        run_paths=runtime_inventory.run_paths,
        python_load_commands=python_loads,
        signing_identifier=signing_identifier,
        signature_kind="developerIDApplication",
        signature_authorities=authorities,
        team_identifier=team_identifier,
        signed_entitlements=entitlements,
        bundle_identifier=info["CFBundleIdentifier"],
        bundle_name=info["CFBundleName"],
        bundle_version=bundle_version,
        microphone_usage_description=purpose,
        schema_version=schema,
    )


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ContractError(f"could not hash release artifact: {path}") from error
    return digest.hexdigest()


def expected_artifact_keys(version: str) -> set[tuple[str, str]]:
    return {
        ("binaryTarget", "SwiftPythonRuntime.xcframework.zip"),
        ("privateBinaryDependency", "SwiftPythonEngine.xcframework.zip"),
        ("binaryTarget", "SwiftPythonAudioInterop.xcframework.zip"),
        ("binaryTarget", "SwiftPythonMetalInterop.xcframework.zip"),
        ("workerExecutable", "SwiftPythonWorker"),
        (PROBE_ROLE, PROBE_NAME),
        ("completeDistribution", f"SwiftPythonCommercial-{version}.zip"),
        *(("vmGuestHelper", name) for name in VM_HELPER_NAMES),
    }


def expected_artifact_path(key: tuple[str, str]) -> str:
    role, name = key
    if role == "vmGuestHelper":
        return f"VMWorker/{name}"
    return name


def require_exact_base_record(
    key: tuple[str, str], record: Mapping[str, Any]
) -> None:
    expected_keys = {"name", "path", "role", "bytes", "sha256"}
    if key[0] in {"binaryTarget", "privateBinaryDependency"}:
        expected_keys.add("swiftPMChecksum")
    require(
        set(record) == expected_keys,
        f"manifest artifact {key} has missing or extra fields",
    )
    require(record.get("role") == key[0], f"manifest artifact role is wrong: {key}")
    require(record.get("name") == key[1], f"manifest artifact name is wrong: {key}")
    require(
        record.get("path") == expected_artifact_path(key),
        f"manifest artifact path is not canonical: {key}",
    )


def require_iso8601(value: Any, description: str) -> None:
    require(
        type(value) is str
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
        is not None,
        f"{description} is invalid",
    )
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError as error:
        raise ContractError(f"{description} is invalid") from error
    require(parsed.tzinfo is not None, f"{description} is invalid")


def require_sha256(value: Any, description: str) -> None:
    require(
        type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"{description} must be one lowercase SHA-256",
    )


def exact_typed_value(observed: Any, expected: Any) -> bool:
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            exact_typed_value(observed[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            exact_typed_value(left, right)
            for left, right in zip(observed, expected, strict=True)
        )
    return observed == expected


def require_exact_probe_record(
    record: Mapping[str, Any], inventory: ProbeInventory
) -> None:
    expected_fields = inventory.manifest_fields()
    expected_keys = {
        "name",
        "path",
        "role",
        "bytes",
        "sha256",
        *expected_fields.keys(),
    }
    require(
        set(record) == expected_keys,
        "audio-probe manifest inventory has missing or extra fields",
    )
    require(record.get("name") == PROBE_NAME, "audio-probe manifest name is wrong")
    require(
        record.get("path") == PROBE_NAME,
        "audio-probe manifest path must be exactly SwiftPythonAudioProbe",
    )
    require(record.get("role") == PROBE_ROLE, "audio-probe manifest role is wrong")
    for key, expected in expected_fields.items():
        require(
            record.get(key) == expected and type(record.get(key)) is type(expected),
            f"audio-probe manifest field {key} does not match the staged executable",
        )


def is_forbidden_distribution_relative(relative: pathlib.PurePosixPath) -> bool:
    if any(component in FORBIDDEN_DISTRIBUTION_COMPONENTS for component in relative.parts):
        return True
    if any(component.endswith(".dSYM") for component in relative.parts):
        return True
    name = relative.name
    return (
        name == ".DS_Store"
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith(".pyc")
    )


def contained_symlink_target(
    source: pathlib.Path,
    root: pathlib.Path,
    description: str,
) -> bytes:
    try:
        target = os.readlink(source)
        encoded = target.encode("utf-8")
        resolved_root = root.resolve(strict=True)
        resolved_target = (source.parent / target).resolve(strict=True)
    except (OSError, UnicodeError) as error:
        raise ContractError(f"{description} has an invalid symlink: {source}") from error
    require(
        not pathlib.PurePosixPath(target).is_absolute()
        and os.path.commonpath((str(resolved_root), str(resolved_target)))
        == str(resolved_root),
        f"{description} symlink escapes its tree: {source} -> {target}",
    )
    return encoded


def distribution_repo_payloads(
    repo: pathlib.Path,
) -> tuple[
    dict[str, tuple[pathlib.Path, os.stat_result]],
    dict[str, tuple[pathlib.Path, os.stat_result]],
]:
    payloads: dict[str, tuple[pathlib.Path, os.stat_result]] = {}
    directory_payloads: dict[str, tuple[pathlib.Path, os.stat_result]] = {}
    for directory, directories, files in os.walk(repo, followlinks=False):
        directory_path = pathlib.Path(directory)
        kept_directories: list[str] = []
        for name in sorted(directories):
            candidate = directory_path / name
            relative = pathlib.PurePosixPath(candidate.relative_to(repo).as_posix())
            if relative.parts[0] == ".git":
                continue
            require(
                not is_forbidden_distribution_relative(relative),
                f"forbidden generated directory in commercial checkout: {relative}",
            )
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                contained_symlink_target(
                    candidate,
                    repo,
                    "commercial checkout",
                )
                payloads[relative.as_posix()] = (candidate, metadata)
            else:
                require(
                    stat.S_ISDIR(metadata.st_mode),
                    f"unsupported commercial checkout entry type: {relative}",
                )
                directory_payloads[relative.as_posix()] = (candidate, metadata)
                kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(files):
            candidate = directory_path / name
            relative = pathlib.PurePosixPath(candidate.relative_to(repo).as_posix())
            if relative.parts[0] == ".git":
                continue
            require(
                not is_forbidden_distribution_relative(relative),
                f"forbidden generated file in commercial checkout: {relative}",
            )
            metadata = candidate.lstat()
            require(
                stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode),
                f"unsupported commercial checkout entry type: {relative}",
            )
            if stat.S_ISLNK(metadata.st_mode):
                contained_symlink_target(
                    candidate,
                    repo,
                    "commercial checkout",
                )
            payloads[relative.as_posix()] = (candidate, metadata)
    missing = REQUIRED_DISTRIBUTION_FILES - set(payloads)
    require(
        not missing,
        f"commercial checkout lacks required distribution files: {sorted(missing)}",
    )
    for relative in REQUIRED_DISTRIBUTION_FILES:
        _, metadata = payloads[relative]
        require(
            stat.S_ISREG(metadata.st_mode),
            f"required distribution file must be regular, not a symlink: {relative}",
        )
    for relative in REQUIRED_EXECUTABLE_DISTRIBUTION_FILES:
        source, _ = payloads[relative]
        require(
            os.access(source, os.X_OK),
            f"required distribution executable lacks execute permission: {relative}",
        )
    return payloads, directory_payloads


def archive_member_sha256(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filesystem_tree_inventory(
    root: pathlib.Path,
    description: str,
) -> tuple[
    dict[str, tuple[pathlib.Path, os.stat_result]],
    dict[str, tuple[pathlib.Path, os.stat_result]],
]:
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise ContractError(f"missing {description}: {root}") from error
    require(
        stat.S_ISDIR(root_metadata.st_mode) and not stat.S_ISLNK(root_metadata.st_mode),
        f"{description} must be one real directory: {root}",
    )
    payloads: dict[str, tuple[pathlib.Path, os.stat_result]] = {}
    directories: dict[str, tuple[pathlib.Path, os.stat_result]] = {}
    for directory, child_directories, files in os.walk(root, followlinks=False):
        directory_path = pathlib.Path(directory)
        kept_directories: list[str] = []
        for name in sorted(child_directories):
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                contained_symlink_target(candidate, root, description)
                payloads[relative] = (candidate, metadata)
            else:
                require(
                    stat.S_ISDIR(metadata.st_mode),
                    f"unsupported {description} entry type: {relative}",
                )
                directories[relative] = (candidate, metadata)
                kept_directories.append(name)
        child_directories[:] = kept_directories
        for name in sorted(files):
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            metadata = candidate.lstat()
            require(
                stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode),
                f"unsupported {description} entry type: {relative}",
            )
            if stat.S_ISLNK(metadata.st_mode):
                contained_symlink_target(candidate, root, description)
            payloads[relative] = (candidate, metadata)
    require(payloads, f"{description} is empty: {root}")
    return payloads, directories


def validate_xcframework_zip(path: pathlib.Path, xcframework: pathlib.Path) -> None:
    description = f"binary artifact {xcframework.name}"
    expected_payloads, expected_directories = filesystem_tree_inventory(
        xcframework,
        description,
    )
    observed_payloads: dict[str, zipfile.ZipInfo] = {}
    observed_directories: dict[str, zipfile.ZipInfo] = {}
    canonical_names: set[str] = set()
    saw_root = False
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            require(infos, f"{description} zip is empty")
            for info in infos:
                name = info.filename
                require(
                    "\\" not in name and "\x00" not in name,
                    f"{description} contains an unsafe zip entry: {name!r}",
                )
                require(
                    info.flag_bits & 0x1 == 0,
                    f"{description} contains an encrypted zip entry: {name}",
                )
                canonical = unicodedata.normalize("NFC", name.rstrip("/")).casefold()
                require(
                    canonical not in canonical_names,
                    f"{description} contains a duplicate/colliding zip entry: {name}",
                )
                canonical_names.add(canonical)
                entry = pathlib.PurePosixPath(name.rstrip("/"))
                require(
                    not entry.is_absolute()
                    and entry.parts
                    and "." not in entry.parts
                    and ".." not in entry.parts
                    and entry.parts[0] == xcframework.name,
                    f"{description} contains an unsafe or wrong-root entry: {name}",
                )
                canonical_spelling = entry.as_posix() + ("/" if info.is_dir() else "")
                require(
                    name == canonical_spelling,
                    f"{description} contains a noncanonical zip path: {name}",
                )
                mode = info.external_attr >> 16
                if len(entry.parts) == 1:
                    require(
                        not saw_root
                        and info.is_dir()
                        and stat.S_ISDIR(mode)
                        and info.file_size == 0,
                        f"{description} root entry is invalid",
                    )
                    saw_root = True
                    continue
                relative = pathlib.PurePosixPath(*entry.parts[1:]).as_posix()
                if info.is_dir():
                    require(
                        stat.S_ISDIR(mode) and info.file_size == 0,
                        f"{description} contains an invalid directory: {relative}",
                    )
                    observed_directories[relative] = info
                else:
                    observed_payloads[relative] = info

            require(saw_root, f"{description} zip lacks its exact root directory")
            require(
                set(observed_payloads) == set(expected_payloads),
                f"{description} payload inventory differs from the checkout: "
                f"missing={sorted(set(expected_payloads) - set(observed_payloads))} "
                f"extra={sorted(set(observed_payloads) - set(expected_payloads))}",
            )
            require(
                set(observed_directories) == set(expected_directories),
                f"{description} directory inventory differs from the checkout: "
                f"missing={sorted(set(expected_directories) - set(observed_directories))} "
                f"extra={sorted(set(observed_directories) - set(expected_directories))}",
            )
            for relative, info in observed_directories.items():
                _, metadata = expected_directories[relative]
                require(
                    stat.S_IMODE(info.external_attr >> 16)
                    == stat.S_IMODE(metadata.st_mode),
                    f"{description} changed directory mode: {relative}",
                )
            for relative, info in observed_payloads.items():
                source, metadata = expected_payloads[relative]
                mode = info.external_attr >> 16
                require(
                    stat.S_IFMT(mode) == stat.S_IFMT(metadata.st_mode),
                    f"{description} changed payload type: {relative}",
                )
                require(
                    stat.S_IMODE(mode) == stat.S_IMODE(metadata.st_mode),
                    f"{description} changed payload mode: {relative}",
                )
                if stat.S_ISLNK(metadata.st_mode):
                    target = contained_symlink_target(source, xcframework, description)
                    require(
                        info.file_size == len(target) and archive.read(info) == target,
                        f"{description} changed symlink target: {relative}",
                    )
                else:
                    require(
                        info.file_size == metadata.st_size,
                        f"{description} changed payload size: {relative}",
                    )
                    require(
                        archive_member_sha256(archive, info) == sha256(source),
                        f"{description} changed payload bytes: {relative}",
                    )
    except ContractError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ContractError(f"{description} is not a readable exact tree zip: {path}") from error


def validate_distribution_zip(
    path: pathlib.Path,
    version: str,
    *,
    repo: pathlib.Path,
    expected_probe_sha256: str,
) -> None:
    expected_payloads, expected_directories = distribution_repo_payloads(repo)
    root = f"swiftpython-commercial-{version}"
    observed_payloads: dict[str, zipfile.ZipInfo] = {}
    observed_directories: dict[str, zipfile.ZipInfo] = {}
    canonical_names: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            require(
                len(names) == len(set(names)),
                "complete distribution contains duplicate zip entries",
            )
            for info in infos:
                name = info.filename
                require(
                    "\\" not in name and "\x00" not in name,
                    f"complete distribution contains an unsafe zip entry: {name!r}",
                )
                require(
                    info.flag_bits & 0x1 == 0,
                    f"complete distribution contains an encrypted zip entry: {name}",
                )
                canonical = unicodedata.normalize("NFC", name.rstrip("/")).casefold()
                require(
                    canonical not in canonical_names,
                    "complete distribution contains a case/Unicode-colliding entry: "
                    f"{name}",
                )
                canonical_names.add(canonical)
                entry = pathlib.PurePosixPath(name.rstrip("/"))
                require(
                    not entry.is_absolute()
                    and ".." not in entry.parts
                    and "." not in entry.parts
                    and entry.parts
                    and entry.parts[0] == root,
                    f"complete distribution contains an unsafe or wrong-root entry: {name}",
                )
                canonical_spelling = entry.as_posix() + ("/" if info.is_dir() else "")
                require(
                    name == canonical_spelling,
                    f"complete distribution contains a noncanonical zip path: {name}",
                )
                if len(entry.parts) == 1:
                    mode = info.external_attr >> 16
                    require(
                        info.is_dir()
                        and stat.S_ISDIR(mode)
                        and info.file_size == 0,
                        "complete distribution contains a slashless top-level payload",
                    )
                    continue
                relative = pathlib.PurePosixPath(*entry.parts[1:])
                require(
                    not is_forbidden_distribution_relative(relative),
                    f"complete distribution contains forbidden payload: {relative}",
                )
                mode = info.external_attr >> 16
                if info.is_dir():
                    require(
                        stat.S_ISDIR(mode) and info.file_size == 0,
                        f"complete distribution contains an invalid directory: {relative}",
                    )
                    observed_directories[relative.as_posix()] = info
                    continue
                require(
                    relative.as_posix() not in observed_payloads,
                    f"complete distribution contains duplicate payload: {relative}",
                )
                observed_payloads[relative.as_posix()] = info

            require(
                set(observed_payloads) == set(expected_payloads),
                "complete distribution payload inventory differs from the commercial "
                "checkout: "
                f"missing={sorted(set(expected_payloads) - set(observed_payloads))} "
                f"extra={sorted(set(observed_payloads) - set(expected_payloads))}",
            )
            require(
                set(observed_directories) == set(expected_directories),
                "complete distribution directory inventory differs from the commercial "
                "checkout: "
                f"missing={sorted(set(expected_directories) - set(observed_directories))} "
                f"extra={sorted(set(observed_directories) - set(expected_directories))}",
            )
            for relative, info in observed_directories.items():
                _, metadata = expected_directories[relative]
                require(
                    stat.S_IMODE(info.external_attr >> 16)
                    == stat.S_IMODE(metadata.st_mode),
                    f"complete distribution changed directory mode: {relative}",
                )
            for relative, info in observed_payloads.items():
                source, metadata = expected_payloads[relative]
                mode = info.external_attr >> 16
                require(
                    stat.S_IFMT(mode) == stat.S_IFMT(metadata.st_mode),
                    f"complete distribution changed payload type: {relative}",
                )
                require(
                    stat.S_IMODE(mode) == stat.S_IMODE(metadata.st_mode),
                    f"complete distribution changed payload mode: {relative}",
                )
                if stat.S_ISLNK(metadata.st_mode):
                    target = contained_symlink_target(
                        source,
                        repo,
                        "commercial checkout",
                    )
                    require(
                        info.file_size == len(target) and archive.read(info) == target,
                        f"complete distribution changed symlink target: {relative}",
                    )
                else:
                    require(
                        info.file_size == metadata.st_size,
                        f"complete distribution changed payload size: {relative}",
                    )
                    require(
                        archive_member_sha256(archive, info) == sha256(source),
                        f"complete distribution changed payload bytes: {relative}",
                    )
            probe_info = observed_payloads[PROBE_NAME]
            require(
                stat.S_ISREG(probe_info.external_attr >> 16)
                and probe_info.external_attr >> 16 & 0o111 != 0,
                "complete-distribution audio probe must be a regular executable",
            )
            require(
                archive_member_sha256(archive, probe_info) == expected_probe_sha256,
                "complete-distribution audio probe differs from the manifest artifact",
            )
    except ContractError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ContractError(
            f"complete distribution is not a readable exact checkout zip: {path}"
        ) from error


def validate_vm_image_attestation(
    value: Any,
    *,
    expected_version: str,
    helper_records: Mapping[str, Mapping[str, Any]],
) -> None:
    require(type(value) is dict, "release VM image attestation must be one object")
    expected_keys = {
        "name",
        "role",
        "bytes",
        "sha256",
        "manifest",
        "imageVersion",
        "swiftpythonVersion",
        "supervisorVersion",
        "distro",
        "builtAt",
        "guestArtifactSHA256",
    }
    require(
        set(value) == expected_keys,
        "release VM image attestation has missing or extra fields",
    )
    name = value.get("name")
    require(
        type(name) is str
        and name != ""
        and pathlib.PurePosixPath(name).name == name,
        "release VM image name is invalid",
    )
    require(
        value.get("role") == "sameVersionVMImageAttestation",
        "release VM image role is invalid",
    )
    require(
        type(value.get("bytes")) is int and value.get("bytes") > 0,
        "release VM image byte count is invalid",
    )
    require_sha256(value.get("sha256"), "release VM image digest")
    manifest_name = value.get("manifest")
    require(
        type(manifest_name) is str
        and manifest_name == f"{name}.manifest.json"
        and pathlib.PurePosixPath(manifest_name).name == manifest_name,
        "release VM image manifest name is invalid",
    )
    require(
        type(value.get("imageVersion")) is int
        and value.get("imageVersion") == EXPECTED_VM_IMAGE_VERSION,
        "release VM image protocol version is not the current exact version",
    )
    require(
        value.get("swiftpythonVersion") == expected_version,
        "release VM image SwiftPython version mismatch",
    )
    require(
        value.get("supervisorVersion") == EXPECTED_PROTOCOLS["supervisorControl"],
        "release VM image supervisor version mismatch",
    )
    require(
        value.get("distro") == EXPECTED_VM_DISTRO,
        "release VM image distro is not the current exact distro",
    )
    require_iso8601(value.get("builtAt"), "release VM image build date")
    hashes = value.get("guestArtifactSHA256")
    require(
        type(hashes) is dict and set(hashes) == set(VM_HELPER_NAMES),
        "release VM image helper digest inventory is not the exact five-file set",
    )
    for helper in VM_HELPER_NAMES:
        expected = helper_records[helper].get("sha256")
        require(
            hashes.get(helper) == expected,
            f"release VM image helper digest mismatch for {helper}",
        )


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: pathlib.Path,
    repo: pathlib.Path,
    expected_version: str,
    inventory: ProbeInventory,
) -> None:
    require(
        inventory.architectures == EXPECTED_PUBLIC_ARCHITECTURES,
        "public release manifest cannot attest a non-universal audio probe",
    )
    require(
        set(manifest) == REQUIRED_MANIFEST_KEYS,
        "release manifest schema-3 root has missing or extra fields: "
        f"missing={sorted(REQUIRED_MANIFEST_KEYS - set(manifest))} "
        f"extra={sorted(set(manifest) - REQUIRED_MANIFEST_KEYS)}",
    )
    require(
        type(manifest.get("manifestSchemaVersion")) is int
        and manifest.get("manifestSchemaVersion") == MANIFEST_SCHEMA_VERSION,
        f"release manifest schema must be exactly {MANIFEST_SCHEMA_VERSION}",
    )
    require(
        manifest.get("version") == expected_version, "release manifest version mismatch"
    )
    require_iso8601(manifest.get("date"), "release manifest date")
    require(
        manifest.get("swiftToolsVersion") == "6.0",
        "release manifest Swift tools version must be exactly 6.0",
    )
    require(
        exact_typed_value(manifest.get("platforms"), ["macOS 15.0"]),
        "release manifest platforms must be exactly ['macOS 15.0']",
    )
    require(
        type(manifest.get("sourceRevision")) is str
        and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", manifest["sourceRevision"])
        is not None,
        "release manifest source revision must be one full lowercase Git object ID",
    )
    require(
        manifest.get("sourceTreeState") == "clean",
        "release manifest source tree is not clean",
    )
    protocols = manifest.get("protocols")
    require(
        type(protocols) is dict, "release manifest protocols must be one dictionary"
    )
    require(
        exact_typed_value(protocols, EXPECTED_PROTOCOLS),
        "release manifest protocol inventory is not the exact schema-3 "
        f"worker/supervisor/duplex/audio set: {protocols!r}",
    )
    distribution_name = f"SwiftPythonCommercial-{expected_version}.zip"
    require(
        manifest.get("distributionZip") == distribution_name,
        "release manifest distributionZip is missing or noncanonical",
    )

    artifacts = manifest.get("artifacts")
    require(type(artifacts) is list, "release manifest artifacts must be one array")
    records: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, candidate in enumerate(artifacts):
        require(
            type(candidate) is dict, f"release artifact {index} must be one dictionary"
        )
        key = (candidate.get("role"), candidate.get("name"))
        require(
            all(type(value) is str and value for value in key),
            f"release artifact {index} has no role/name",
        )
        require(key not in records, f"duplicate release artifact record: {key}")
        records[key] = candidate
    expected = expected_artifact_keys(expected_version)
    require(
        set(records) == expected,
        "manifest artifact inventory mismatch: "
        f"missing={sorted(expected - set(records))} extra={sorted(set(records) - expected)}",
    )

    probe_record = records[(PROBE_ROLE, PROBE_NAME)]
    require_exact_probe_record(probe_record, inventory)
    for key, record in records.items():
        if key != (PROBE_ROLE, PROBE_NAME):
            require_exact_base_record(key, record)

    artifact_root = manifest_path.parent.resolve()
    for key, record in records.items():
        relative = record.get("path")
        require(
            type(relative) is str and relative != "",
            f"manifest artifact has no path: {key}",
        )
        relative_path = pathlib.PurePosixPath(relative)
        require(
            not relative_path.is_absolute() and ".." not in relative_path.parts,
            f"manifest artifact path escapes output directory: {key}",
        )
        path = manifest_path.parent / relative_path
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ContractError(
                f"manifest artifact does not exist for {key}: {path}"
            ) from error
        require(
            not stat.S_ISLNK(metadata.st_mode) and stat.S_ISREG(metadata.st_mode),
            f"manifest artifact must be one regular non-symlink file: {key}",
        )
        require(
            os.path.commonpath((str(artifact_root), str(resolved)))
            == str(artifact_root),
            f"manifest artifact resolves outside output directory: {key}",
        )
        digest = sha256(path)
        require(record.get("sha256") == digest, f"manifest SHA-256 mismatch for {key}")
        require(
            type(record.get("bytes")) is int
            and record.get("bytes") == path.stat().st_size,
            f"manifest byte count mismatch for {key}",
        )
        if key[0] in {"binaryTarget", "privateBinaryDependency"}:
            require(
                record.get("swiftPMChecksum") == digest,
                f"SwiftPM checksum mismatch for {key}",
            )
        if key[0] in {"workerExecutable", PROBE_ROLE}:
            require(
                os.access(path, os.X_OK),
                f"manifest executable artifact lacks execute permission: {key}",
            )

    for role, module in (
        ("binaryTarget", "SwiftPythonRuntime"),
        ("privateBinaryDependency", "SwiftPythonEngine"),
        ("binaryTarget", "SwiftPythonAudioInterop"),
        ("binaryTarget", "SwiftPythonMetalInterop"),
    ):
        name = f"{module}.xcframework.zip"
        validate_xcframework_zip(
            manifest_path.parent / records[(role, name)]["path"],
            repo / f"{module}.xcframework",
        )

    output_probe = manifest_path.parent / PROBE_NAME
    staged_probe = repo / PROBE_NAME
    require(
        sha256(output_probe) == sha256(staged_probe),
        "commercial audio probe differs from manifest artifact bytes",
    )

    require(
        sha256(repo / "SwiftPythonWorker")
        == records[("workerExecutable", "SwiftPythonWorker")].get("sha256"),
        "manifest worker hash differs from the commercial checkout",
    )

    helper_records: dict[str, Mapping[str, Any]] = {}
    for helper in VM_HELPER_NAMES:
        record = records[("vmGuestHelper", helper)]
        helper_records[helper] = record
        require(
            sha256(repo / "VMWorker" / helper) == record.get("sha256"),
            f"manifest VM helper hash mismatch for {helper}",
        )

    validate_vm_image_attestation(
        manifest.get("vmImage"),
        expected_version=expected_version,
        helper_records=helper_records,
    )

    complete = records[
        ("completeDistribution", distribution_name)
    ]
    validate_distribution_zip(
        manifest_path.parent / complete["path"],
        expected_version,
        repo=repo,
        expected_probe_sha256=probe_record["sha256"],
    )


def load_manifest(path: pathlib.Path) -> Mapping[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"release manifest contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except ContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"release manifest is unreadable: {path}") from error
    require(type(payload) is dict, "release manifest must be one JSON object")
    return payload


def parse_arguments(arguments: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=pathlib.Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--manifest", type=pathlib.Path)
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    repo = options.repo.resolve()
    try:
        verify_entitlement_templates(repo)
        inspect_python_runtime(repo / "SwiftPythonWorker", "SwiftPythonWorker")
        inventory = inspect_probe(repo)
        if options.manifest is not None:
            manifest_path = options.manifest.resolve()
            validate_manifest(
                load_manifest(manifest_path),
                manifest_path=manifest_path,
                repo=repo,
                expected_version=options.expected_version,
                inventory=inventory,
            )
    except ContractError as error:
        print(f"audio-probe release audit failed: {error}", file=sys.stderr)
        return 1
    if options.manifest is None:
        print(
            "pre-manifest audio-probe audit passed "
            "(NOT manifest or release evidence)"
        )
    else:
        print("manifest-backed audio-probe release audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
