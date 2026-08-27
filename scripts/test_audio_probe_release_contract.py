#!/usr/bin/env python3
"""Pure contract tests for the commercial audio-probe manifest audit."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import pathlib
import stat
import tempfile
import unittest
import zipfile

import audio_probe_release_contract as contract


class AudioProbeReleaseContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="swiftpython-audio-probe-contract-tests."
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.repo = self.root / "commercial"
        self.output = self.root / "artifacts"
        self.repo.mkdir()
        self.output.mkdir()
        for relative in sorted(contract.REQUIRED_DISTRIBUTION_FILES):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode("utf-8"))
        for relative in (
            "SwiftPythonWorker",
            contract.PROBE_NAME,
            "scripts/audit_release_surface.sh",
            "scripts/consumer_path_smoke.sh",
        ):
            (self.repo / relative).chmod(0o755)
        self.non_sandbox_template = b"non-sandbox"
        self.sandbox_template = b"sandbox"
        (self.repo / "Entitlements" / contract.NON_SANDBOX_ENTITLEMENTS).write_bytes(
            self.non_sandbox_template
        )
        (self.repo / "Entitlements" / contract.SANDBOX_ENTITLEMENTS).write_bytes(
            self.sandbox_template
        )
        self.version = "0.6.0-duplex.test"
        self.inventory = contract.ProbeInventory(
            architectures=("arm64", "x86_64"),
            platform="macOS",
            minimum_os_version="15.0",
            sdk_version="26.5",
            load_commands=(
                "/System/Library/Frameworks/AVFAudio.framework/Versions/A/AVFAudio",
                "/System/Library/Frameworks/AudioToolbox.framework/Versions/A/AudioToolbox",
                "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/Python",
            ),
            run_paths=("/usr/lib/swift", "@loader_path"),
            python_load_commands=(
                "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/Python",
            ),
            signing_identifier=contract.PROBE_IDENTIFIER,
            signature_kind="developerIDApplication",
            signature_authorities=("Developer ID Application: Test (TEAMID)",),
            team_identifier="TEAMID",
            signed_entitlements=contract.EXPECTED_NON_SANDBOX_ENTITLEMENTS,
            bundle_identifier=contract.PROBE_IDENTIFIER,
            bundle_name="SwiftPython Audio Readiness Probe",
            bundle_version="1",
            microphone_usage_description="Bounded microphone readiness test.",
            schema_version=1,
        )
        self.manifest_path = self.output / "manifest.json"
        self.manifest = self.make_valid_manifest()

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def write_record(
        self,
        role: str,
        name: str,
        relative_path: str,
        data: bytes,
        *,
        swiftpm: bool = False,
    ) -> dict[str, object]:
        path = self.output / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        record: dict[str, object] = {
            "role": role,
            "name": name,
            "path": relative_path,
            "bytes": len(data),
            "sha256": self.digest(data),
        }
        if swiftpm:
            record["swiftPMChecksum"] = self.digest(data)
        return record

    def write_distribution(self, *, include_probe: bool = True) -> pathlib.Path:
        path = self.output / f"SwiftPythonCommercial-{self.version}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            root = f"swiftpython-commercial-{self.version}"
            root_info = zipfile.ZipInfo(f"{root}/")
            root_info.create_system = 3
            root_info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(root_info, b"")
            payloads, directories = contract.distribution_repo_payloads(self.repo)
            for relative, (_, metadata) in sorted(directories.items()):
                info = zipfile.ZipInfo(f"{root}/{relative}/")
                info.create_system = 3
                info.external_attr = metadata.st_mode << 16
                archive.writestr(info, b"")
            for relative, (source, metadata) in sorted(
                payloads.items()
            ):
                if not include_probe and relative == contract.PROBE_NAME:
                    continue
                info = zipfile.ZipInfo(f"{root}/{relative}")
                info.create_system = 3
                info.external_attr = metadata.st_mode << 16
                if stat.S_ISLNK(metadata.st_mode):
                    payload = source.readlink().as_posix().encode("utf-8")
                else:
                    payload = source.read_bytes()
                archive.writestr(info, payload)
        return path

    def rewrite_distribution(
        self,
        *,
        replacement_data: dict[str, bytes] | None = None,
        replacement_modes: dict[str, int] | None = None,
        omitted: set[str] | None = None,
        extra_entries: list[tuple[str, bytes, int]] | None = None,
    ) -> pathlib.Path:
        path = self.write_distribution()
        rewritten = self.output / "rewritten-distribution.zip"
        root = f"swiftpython-commercial-{self.version}"
        replacement_data = replacement_data or {}
        replacement_modes = replacement_modes or {}
        omitted = omitted or set()
        extra_entries = extra_entries or []
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w") as target:
            for original in source.infolist():
                relative = original.filename.removeprefix(root + "/")
                if relative in omitted:
                    continue
                info = zipfile.ZipInfo(original.filename)
                info.create_system = 3
                mode = replacement_modes.get(relative, original.external_attr >> 16)
                info.external_attr = mode << 16
                target.writestr(
                    info,
                    replacement_data.get(relative, source.read(original)),
                )
            for name, payload, mode in extra_entries:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                target.writestr(info, payload)
        rewritten.replace(path)
        return path

    def write_xcframework_archive(self, module: str) -> pathlib.Path:
        tree = self.repo / f"{module}.xcframework"
        path = self.output / f"{module}.xcframework.zip"
        payloads, directories = contract.filesystem_tree_inventory(
            tree,
            f"test {module}",
        )
        with zipfile.ZipFile(path, "w") as archive:
            root_info = zipfile.ZipInfo(f"{tree.name}/")
            root_info.create_system = 3
            root_info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(root_info, b"")
            for relative, (_, metadata) in sorted(directories.items()):
                info = zipfile.ZipInfo(f"{tree.name}/{relative}/")
                info.create_system = 3
                info.external_attr = metadata.st_mode << 16
                archive.writestr(info, b"")
            for relative, (source, metadata) in sorted(payloads.items()):
                info = zipfile.ZipInfo(f"{tree.name}/{relative}")
                info.create_system = 3
                info.external_attr = metadata.st_mode << 16
                payload = (
                    source.readlink().as_posix().encode("utf-8")
                    if stat.S_ISLNK(metadata.st_mode)
                    else source.read_bytes()
                )
                archive.writestr(info, payload)
        return path

    def rewrite_xcframework_archive(
        self,
        module: str,
        *,
        replacement_modes: dict[str, int] | None = None,
        extra_entries: list[tuple[str, bytes, int]] | None = None,
    ) -> pathlib.Path:
        path = self.write_xcframework_archive(module)
        rewritten = self.output / f"{module}.xcframework.rewritten.zip"
        replacement_modes = replacement_modes or {}
        extra_entries = extra_entries or []
        root = f"{module}.xcframework"
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w") as target:
            for original in source.infolist():
                relative = original.filename.removeprefix(root + "/")
                info = zipfile.ZipInfo(original.filename)
                info.create_system = 3
                mode = replacement_modes.get(relative, original.external_attr >> 16)
                info.external_attr = mode << 16
                target.writestr(info, source.read(original))
            for name, payload, mode in extra_entries:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                target.writestr(info, payload)
        rewritten.replace(path)
        return path

    def make_valid_manifest(self) -> dict[str, object]:
        artifacts: list[dict[str, object]] = []
        for role, name in (
            ("binaryTarget", "SwiftPythonRuntime.xcframework.zip"),
            ("privateBinaryDependency", "SwiftPythonEngine.xcframework.zip"),
            ("binaryTarget", "SwiftPythonAudioInterop.xcframework.zip"),
            ("binaryTarget", "SwiftPythonMetalInterop.xcframework.zip"),
        ):
            module = name.removesuffix(".xcframework.zip")
            archive = self.write_xcframework_archive(module)
            artifacts.append(
                self.write_record(
                    role,
                    name,
                    name,
                    archive.read_bytes(),
                    swiftpm=True,
                )
            )
        worker_record = self.write_record(
            "workerExecutable", "SwiftPythonWorker", "SwiftPythonWorker", b"worker"
        )
        (self.output / "SwiftPythonWorker").chmod(0o755)
        artifacts.append(worker_record)
        (self.repo / "SwiftPythonWorker").write_bytes(b"worker")
        probe_bytes = b"probe"
        (self.repo / contract.PROBE_NAME).write_bytes(probe_bytes)
        probe = self.write_record(
            contract.PROBE_ROLE,
            contract.PROBE_NAME,
            contract.PROBE_NAME,
            probe_bytes,
        )
        (self.output / contract.PROBE_NAME).chmod(0o755)
        probe.update(self.inventory.manifest_fields())
        artifacts.append(probe)
        helper_hashes: dict[str, str] = {}
        for helper in contract.VM_HELPER_NAMES:
            data = helper.encode()
            (self.repo / "VMWorker" / helper).write_bytes(data)
            record = self.write_record(
                "vmGuestHelper", helper, f"VMWorker/{helper}", data
            )
            artifacts.append(record)
            helper_hashes[helper] = str(record["sha256"])
        distribution = self.write_distribution()
        artifacts.append(
            {
                "role": "completeDistribution",
                "name": distribution.name,
                "path": distribution.name,
                "bytes": distribution.stat().st_size,
                "sha256": contract.sha256(distribution),
            }
        )
        return {
            "manifestSchemaVersion": 3,
            "version": self.version,
            "date": "2026-08-26T00:00:00Z",
            "swiftToolsVersion": "6.0",
            "platforms": ["macOS 15.0"],
            "sourceRevision": "a" * 40,
            "sourceTreeState": "clean",
            "protocols": copy.deepcopy(contract.EXPECTED_PROTOCOLS),
            "distributionZip": distribution.name,
            "vmImage": {
                "name": "base-ubuntu.img",
                "role": "sameVersionVMImageAttestation",
                "bytes": 8 * 1024 * 1024 * 1024,
                "sha256": "f" * 64,
                "manifest": "base-ubuntu.img.manifest.json",
                "imageVersion": 1,
                "swiftpythonVersion": self.version,
                "supervisorVersion": "3",
                "distro": contract.EXPECTED_VM_DISTRO,
                "builtAt": "2026-08-26T00:00:00Z",
                "guestArtifactSHA256": helper_hashes,
            },
            "artifacts": artifacts,
        }

    def validate(self, manifest: dict[str, object] | None = None) -> None:
        candidate = self.manifest if manifest is None else manifest
        self.manifest_path.write_text(json.dumps(candidate), encoding="utf-8")
        contract.validate_manifest(
            candidate,
            manifest_path=self.manifest_path,
            repo=self.repo,
            expected_version=self.version,
            inventory=self.inventory,
        )

    def probe_record(self, manifest: dict[str, object]) -> dict[str, object]:
        artifacts = manifest["artifacts"]
        assert isinstance(artifacts, list)
        return next(item for item in artifacts if item["role"] == contract.PROBE_ROLE)

    def distribution_record(self, manifest: dict[str, object]) -> dict[str, object]:
        artifacts = manifest["artifacts"]
        assert isinstance(artifacts, list)
        return next(
            item for item in artifacts if item["role"] == "completeDistribution"
        )

    def artifact_record(
        self,
        manifest: dict[str, object],
        role: str,
        name: str,
    ) -> dict[str, object]:
        artifacts = manifest["artifacts"]
        assert isinstance(artifacts, list)
        return next(
            item
            for item in artifacts
            if item["role"] == role and item["name"] == name
        )

    def update_artifact_digest(
        self,
        record: dict[str, object],
        path: pathlib.Path,
    ) -> None:
        digest = contract.sha256(path)
        record["bytes"] = path.stat().st_size
        record["sha256"] = digest
        if "swiftPMChecksum" in record:
            record["swiftPMChecksum"] = digest

    def test_exact_schema_three_candidate_passes(self) -> None:
        self.validate()

    def test_schema_two_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["manifestSchemaVersion"] = 2
        with self.assertRaisesRegex(contract.ContractError, "schema must be exactly 3"):
            self.validate(candidate)

    def test_probe_role_is_mandatory(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["artifacts"] = [
            item
            for item in candidate["artifacts"]
            if item["role"] != contract.PROBE_ROLE
        ]
        with self.assertRaisesRegex(
            contract.ContractError, "artifact inventory mismatch"
        ):
            self.validate(candidate)

    def test_probe_protocol_requires_exact_integer_one(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["protocols"]["audioHardwareProbe"] = True
        with self.assertRaisesRegex(contract.ContractError, "protocol inventory"):
            self.validate(candidate)

    def test_complete_schema_three_protocol_inventory_is_mandatory(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        del candidate["protocols"]["duplexFeatureHelpers"]
        with self.assertRaisesRegex(contract.ContractError, "protocol inventory"):
            self.validate(candidate)

    def test_platform_inventory_is_exact(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["platforms"] = ["macOS 15.0", "Linux"]
        with self.assertRaisesRegex(contract.ContractError, "platforms must be exactly"):
            self.validate(candidate)

    def test_manifest_root_inventory_is_closed(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        del candidate["distributionZip"]
        with self.assertRaisesRegex(contract.ContractError, "root has missing"):
            self.validate(candidate)

    def test_vm_image_attestation_cannot_be_null(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["vmImage"] = None
        with self.assertRaisesRegex(contract.ContractError, "must be one object"):
            self.validate(candidate)

    def test_vm_image_helper_hashes_must_match_artifacts(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["vmImage"]["guestArtifactSHA256"]["swiftpython_worker.py"] = "0" * 64
        with self.assertRaisesRegex(contract.ContractError, "digest mismatch"):
            self.validate(candidate)

    def test_vm_image_version_is_exact(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["vmImage"]["imageVersion"] = 2
        with self.assertRaisesRegex(contract.ContractError, "current exact version"):
            self.validate(candidate)

    def test_vm_image_distro_is_exact(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["vmImage"]["distro"] = "ubuntu-24.04"
        with self.assertRaisesRegex(contract.ContractError, "current exact distro"):
            self.validate(candidate)

    def test_arm64_only_probe_inventory_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        inventory = dataclasses.replace(self.inventory, architectures=("arm64",))
        self.probe_record(candidate)["architectures"] = ["arm64"]
        self.manifest_path.write_text(json.dumps(candidate), encoding="utf-8")
        with self.assertRaisesRegex(contract.ContractError, "non-universal"):
            contract.validate_manifest(
                candidate,
                manifest_path=self.manifest_path,
                repo=self.repo,
                expected_version=self.version,
                inventory=inventory,
            )

    def test_probe_inventory_is_closed(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        self.probe_record(candidate)["inventedField"] = "not allowed"
        with self.assertRaisesRegex(contract.ContractError, "missing or extra fields"):
            self.validate(candidate)

    def test_probe_inventory_must_equal_staged_executable(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        self.probe_record(candidate)["schemaVersion"] = 2
        with self.assertRaisesRegex(contract.ContractError, "schemaVersion"):
            self.validate(candidate)

    def test_python_linked_must_be_boolean_true(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        self.probe_record(candidate)["pythonLinked"] = 1
        with self.assertRaisesRegex(contract.ContractError, "pythonLinked"):
            self.validate(candidate)

    def test_commercial_probe_must_match_manifest_bytes(self) -> None:
        (self.repo / contract.PROBE_NAME).write_bytes(b"different")
        with self.assertRaisesRegex(contract.ContractError, "differs from manifest"):
            self.validate()

    def test_complete_distribution_must_embed_probe(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        path = self.write_distribution(include_probe=False)
        record = self.distribution_record(candidate)
        record["bytes"] = path.stat().st_size
        record["sha256"] = contract.sha256(path)
        with self.assertRaisesRegex(contract.ContractError, "SwiftPythonAudioProbe"):
            self.validate(candidate)

    def test_complete_distribution_probe_bytes_must_match(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        path = self.rewrite_distribution(
            replacement_data={contract.PROBE_NAME: b"different"}
        )
        record = self.distribution_record(candidate)
        record["bytes"] = path.stat().st_size
        record["sha256"] = contract.sha256(path)
        with self.assertRaisesRegex(contract.ContractError, "changed payload"):
            self.validate(candidate)

    def test_complete_distribution_templates_must_match(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        relative = f"Entitlements/{contract.NON_SANDBOX_ENTITLEMENTS}"
        path = self.rewrite_distribution(
            replacement_data={relative: b"different"}
        )
        record = self.distribution_record(candidate)
        record["bytes"] = path.stat().st_size
        record["sha256"] = contract.sha256(path)
        with self.assertRaisesRegex(contract.ContractError, "changed payload"):
            self.validate(candidate)

    def test_complete_distribution_rejects_top_level_payload(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        path = self.rewrite_distribution(
            extra_entries=[("TOP_LEVEL_SECRET.env", b"secret", stat.S_IFREG | 0o600)]
        )
        record = self.distribution_record(candidate)
        record["bytes"] = path.stat().st_size
        record["sha256"] = contract.sha256(path)
        with self.assertRaisesRegex(contract.ContractError, "wrong-root"):
            self.validate(candidate)

    def test_complete_distribution_rejects_extra_rooted_payload(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        root = f"swiftpython-commercial-{self.version}"
        path = self.rewrite_distribution(
            extra_entries=[
                (f"{root}/UNATTESTED.txt", b"extra", stat.S_IFREG | 0o644)
            ]
        )
        record = self.distribution_record(candidate)
        record["bytes"] = path.stat().st_size
        record["sha256"] = contract.sha256(path)
        with self.assertRaisesRegex(contract.ContractError, "payload inventory"):
            self.validate(candidate)

    def test_complete_distribution_rejects_noncanonical_path_alias(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        root = f"swiftpython-commercial-{self.version}"
        readme = self.repo / "README.md"
        path = self.rewrite_distribution(
            omitted={"README.md"},
            extra_entries=[
                (
                    f"{root}//README.md",
                    readme.read_bytes(),
                    readme.stat().st_mode,
                )
            ],
        )
        record = self.distribution_record(candidate)
        record["bytes"] = path.stat().st_size
        record["sha256"] = contract.sha256(path)
        with self.assertRaisesRegex(contract.ContractError, "noncanonical zip path"):
            self.validate(candidate)

    def test_complete_distribution_rejects_symlink_entitlement(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        relative = f"Entitlements/{contract.NON_SANDBOX_ENTITLEMENTS}"
        path = self.rewrite_distribution(
            replacement_modes={relative: stat.S_IFLNK | 0o777}
        )
        record = self.distribution_record(candidate)
        record["bytes"] = path.stat().st_size
        record["sha256"] = contract.sha256(path)
        with self.assertRaisesRegex(contract.ContractError, "changed payload type"):
            self.validate(candidate)

    def test_binary_artifact_rejects_extra_rooted_payload(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        module = "SwiftPythonAudioInterop"
        root = f"{module}.xcframework"
        path = self.rewrite_xcframework_archive(
            module,
            extra_entries=[
                (f"{root}/UNATTESTED", b"extra", stat.S_IFREG | 0o644)
            ],
        )
        record = self.artifact_record(
            candidate,
            "binaryTarget",
            f"{module}.xcframework.zip",
        )
        self.update_artifact_digest(record, path)
        with self.assertRaisesRegex(contract.ContractError, "payload inventory"):
            self.validate(candidate)

    def test_binary_artifact_rejects_symlinked_payload(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        module = "SwiftPythonRuntime"
        path = self.rewrite_xcframework_archive(
            module,
            replacement_modes={"Info.plist": stat.S_IFLNK | 0o777},
        )
        record = self.artifact_record(
            candidate,
            "binaryTarget",
            f"{module}.xcframework.zip",
        )
        self.update_artifact_digest(record, path)
        with self.assertRaisesRegex(contract.ContractError, "changed payload type"):
            self.validate(candidate)

    def test_checkout_rejects_escaping_symlink(self) -> None:
        outside = self.root / "outside"
        outside.write_bytes(b"not distribution data")
        (self.repo / "UNSAFE").symlink_to("../outside")
        with self.assertRaisesRegex(contract.ContractError, "symlink escapes"):
            contract.distribution_repo_payloads(self.repo)

    def test_manifest_artifact_paths_must_not_escape(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        self.probe_record(candidate)["path"] = "../SwiftPythonAudioProbe"
        with self.assertRaisesRegex(contract.ContractError, "path must be exactly"):
            self.validate(candidate)

    def test_missing_probe_is_rejected_before_tooling(self) -> None:
        (self.repo / contract.PROBE_NAME).unlink()
        with self.assertRaisesRegex(contract.ContractError, "missing audio probe"):
            contract.inspect_probe(self.repo)

    def test_symlink_probe_is_rejected_before_tooling(self) -> None:
        probe = self.repo / contract.PROBE_NAME
        probe.unlink()
        target = self.repo / "target"
        target.write_bytes(b"probe")
        probe.symlink_to(target)
        with self.assertRaisesRegex(contract.ContractError, "must not be a symlink"):
            contract.inspect_probe(self.repo)

    def test_nonexecutable_probe_is_rejected_before_tooling(self) -> None:
        probe = self.repo / contract.PROBE_NAME
        probe.chmod(0o600)
        with self.assertRaisesRegex(contract.ContractError, "must be executable"):
            contract.inspect_probe(self.repo)


if __name__ == "__main__":
    unittest.main()
