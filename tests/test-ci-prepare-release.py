#!/usr/bin/env python3
"""Release preparation fixtures: fake bytes, no network, sudo or target execution."""
import hashlib
import copy
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_config import BUILD, TARGET
from factory_firmware import FORMAT
from release_identity import image_filename, release_tag

fixture_report = import_module("test-ci-simulation").fixture_report

COMMIT = "2a60011f98d33b9cc38c005061ec8935e029874c"
VERSION = (REPO / "packaging/e87n-display/VERSION").read_text().strip()
DEB = f"e87n-display_{VERSION}_all.deb"


class ReleasePreparation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="e87n-release-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "release"
        self.put("scripts/ci-prepare-release.py", (REPO / "scripts/ci-prepare-release.py").read_bytes())
        for prefix in ("", "image-job/", "display-job/"):
            for name in ("scripts/build_config.py", "scripts/release_identity.py",
                         "userpatches/config/e87n-build.json",
                         "scripts/ci-simulation.py", "scripts/factory_firmware.py", "testing/validate.py"):
                self.put(prefix + name, (REPO / name).read_bytes())
        self.put("packaging/e87n-display/VERSION", VERSION)
        # Use the real collector to establish its exact current layout, with
        # synthetic build products. Neither collector nor preparer runs them.
        for kind in ("image", "display"):
            job = self.root / (kind + "-job")
            self.put(f"{kind}-job/scripts/ci-collect-artifacts.py", (REPO / "scripts/ci-collect-artifacts.py").read_bytes())
            self.put(f"{kind}-job/packaging/e87n-display/VERSION", VERSION)
            self.put(f"{kind}-job/output/ci/logs/{kind}.log", "fake build log\n")
            self.put(f"{kind}-job/output/ci/logs/{kind}.exit-code", "0\n")
            # The always() evidence step runs on successful builds as well.
            self.put(f"{kind}-job/output/ci/failure-evidence/{kind}/run.txt", "fixture evidence\n")
            if kind == "image":
                self.put("image-job/output/ci/firmware/candidate-uboot-firmware.tar", b"fake firmware, not tar\x00")
                display = b"fake independent package, not a deb"
                self.put(f"image-job/output/ci/simulation-display-debs/{DEB}", display)
                self.put("image-job/output/ci/simulation/result.json", json.dumps(fixture_report(
                    b"fake firmware, not tar\x00", display, COMMIT, "34737922588", "1")))
                self.put("image-job/source/armbian-build/output/debs/kernel.deb", b"fake kernel package")
                self.put("image-job/source/armbian-build/output/debs/extra/trixie-utils/kernel.deb", b"different nested package")
                self.put("image-job/source/armbian-build/output/logs/build.log.xz", b"fake compressed log")
                self.put("image-job/output/ci/logs/image-audit-1.log", "fake static audit passed\n")
                self.put("image-job/output/ci/logs/factory-firmware-audit-1.log", "PASS: static factory firmware audit\n")
                for name in (
                    "E87N-ramdiag-40000000-initrd.itb",
                    "E87N-ramdiag-40080000-initrd.itb",
                    "E87N-ramdiag-40000000-no-initrd.itb",
                    "E87N-ramdiag-40000000-initrd.itb.json",
                    "E87N-ramdiag-40080000-initrd.itb.json",
                    "E87N-ramdiag-40000000-no-initrd.itb.json",
                    "MANIFEST.json",
                    "README.txt",
                ):
                    self.put(f"image-job/output/ci/ramdiag/{name}", "fixture diagnostic\n")
                self.put("image-job/source/armbian-build/output/images/candidate.img.xz", b"internal raw GPT build product")
            else:
                self.put(f"display-job/output/ci/display-debs/{DEB}", b"fake independent package, not a deb")
            result = subprocess.run([sys.executable, str(job / "scripts/ci-collect-artifacts.py"),
                                     "--kind", kind, "--status", "success"],
                                    env={**os.environ, "GITHUB_SHA": COMMIT, "GITHUB_RUN_ID": "34737922588", "GITHUB_RUN_ATTEMPT": "1"},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            shutil.move(str(job / "output/ci/artifacts"), str(self.root / kind))

    def put(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents.encode() if isinstance(contents, str) else contents)
        return path

    def manifest(self, kind):
        root = self.root / kind
        lines = [hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.relative_to(root).as_posix()
                 for p in sorted(root.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
        (root / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    def metadata(self, kind, key, value, remove=False):
        path = self.root / kind / "build-metadata.json"
        metadata = json.loads(path.read_text())
        if remove:
            metadata.pop(key, None)
        else:
            metadata[key] = value
        path.write_text(json.dumps(metadata))
        self.manifest(kind)

    def run_cli(self, kind="image", **overrides):
        tag_kind = kind if kind in ("image", "display") else "image"
        args = {"kind": kind, "artifact": str(self.root / kind),
                "output": str(self.output), "source-commit": COMMIT, "run-id": "34737922588",
                "run-attempt": "1", "tag": release_tag(tag_kind),
                "repository": "baozaodetudou/EDGEPI-E87N"}
        args.update(overrides)
        return subprocess.run([sys.executable, str(self.root / "scripts/ci-prepare-release.py"),
                               *(item for key, value in args.items() for item in ("--" + key, value))],
                              cwd=self.root, capture_output=True, text=True, timeout=15)

    def rejected(self, message=None, kind="image", **overrides):
        result = self.run_cli(kind, **overrides)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("FAIL: release preparation:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        if message:
            self.assertIn(message, result.stderr)
        self.assertFalse(self.output.exists())

    def test_valid_image_staging_retains_qemu_binding_but_one_public_download(self):
        payload = b"fake\x00" * 450000
        self.put("image/images/candidate-uboot-firmware.tar", payload)
        report_path = self.root / "image/validation/qemu/result.json"
        report = json.loads(report_path.read_text())
        report["artifacts"]["firmware_tar_sha256"] = hashlib.sha256(payload).hexdigest()
        report_path.write_text(json.dumps(report))
        self.manifest("image")

        result = self.run_cli("image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in self.output.iterdir()}, {
            image_filename(), DEB, "image-build-metadata.json",
            "kernel-packages.tar.xz", "build-evidence.tar.xz", "SHA256SUMS",
            "RELEASE-NOTES.md", "simulation-result.json"})
        self.assertEqual(payload, (self.output / image_filename()).read_bytes())
        self.assertEqual((self.root / "image/packages/simulation" / DEB).read_bytes(),
                         (self.output / DEB).read_bytes())
        self.assert_manifest(7)
        with tarfile.open(self.output / "kernel-packages.tar.xz") as bundle:
            self.assertEqual(set(bundle.getnames()), {
                p.relative_to(self.root / "image").as_posix()
                for p in (self.root / "image/packages/armbian").rglob("*.deb")})
        with tarfile.open(self.output / "build-evidence.tar.xz") as bundle:
            self.assertTrue(all(name.startswith("image/") for name in bundle.getnames()))
            self.assertIn("image/validation/qemu/result.json", bundle.getnames())
        notes = (self.output / "RELEASE-NOTES.md").read_text()
        base = "/releases/download/" + release_tag("image") + "/"
        self.assertEqual(notes.count(base), 1)
        self.assertIn(base + image_filename(), notes)
        self.assertIn(DEB, notes)
        self.assertIn("not a second Release download", notes)
        self.assertIn(VERSION, notes)
        self.assertNotIn("No board has been validated", notes)

    def test_valid_display_staging_is_independent_and_contains_only_display_evidence(self):
        shutil.rmtree(self.root / "image")
        result = self.run_cli("display")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in self.output.iterdir()}, {
            DEB, "display-build-metadata.json", "build-evidence.tar.xz",
            "RELEASE-NOTES.md", "SHA256SUMS"})
        self.assert_manifest(4)
        with tarfile.open(self.output / "build-evidence.tar.xz") as bundle:
            self.assertTrue(all(name.startswith("display/") for name in bundle.getnames()))
            self.assertNotIn("display/validation/qemu/result.json", bundle.getnames())
        notes = (self.output / "RELEASE-NOTES.md").read_text()
        self.assertEqual(notes.count("/releases/download/"), 1)
        self.assertIn("/" + DEB, notes)
        self.assertNotIn("uboot-firmware.tar", notes)
        self.assertIn("不需要重新构建", notes)
        self.assertIn("实体设备验收记录", notes)

    def assert_manifest(self, expected_entries):
        lines = (self.output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(lines), expected_entries)
        for line in lines:
            checksum, name = line.split("  ", 1)
            self.assertEqual(checksum, hashlib.sha256((self.output / name).read_bytes()).hexdigest())

    def test_image_requires_qemu_evidence_bound_to_internal_display_package(self):
        display = self.root / "image/packages/simulation" / DEB
        display.write_bytes(display.read_bytes() + b"different")
        self.manifest("image")
        self.rejected("display_deb_sha256", kind="image")
        report = self.root / "image/validation/qemu/result.json"
        original = json.loads(report.read_text())
        for section, key, value in (
                ("binding", "source_commit", "a" * 40), ("binding", "run_id", "1"),
                ("binding", "run_attempt", "2"), ("artifacts", "firmware_tar_sha256", "a" * 64),
                ("guest", "display_package", False)):
            data = copy.deepcopy(original)
            data[section][key] = value
            report.write_text(json.dumps(data))
            self.manifest("image")
            with self.subTest(section=section, key=key):
                self.rejected(kind="image")
        report.write_text(json.dumps(original))
        self.manifest("image")

    def test_channel_metadata_identity_and_source_pins_are_strict(self):
        for kind in ("image", "display"):
            for key, good, bad in (
                    ("source_commit", COMMIT, "a" * 40), ("run_id", "34737922588", "9"),
                    ("run_attempt", "1", "2"), ("kind", kind, "display" if kind == "image" else "image"),
                    ("display_version_source", VERSION, "9.9"),
                    ("kernel_commit", BUILD["kernel_commit"], "a" * 40),
                    ("armbian_commit", BUILD["armbian_commit"], "b" * 40)):
                with self.subTest(kind=kind, key=key):
                    self.metadata(kind, key, bad)
                    self.rejected(key, kind=kind)
                    self.metadata(kind, key, good)

    def test_failed_builds_collection_errors_targets_and_exit_codes_are_rejected(self):
        for kind in ("image", "display"):
            self.metadata(kind, "build_step_outcome", "failure")
            self.rejected("build_step_outcome", kind=kind)
            self.metadata(kind, "build_step_outcome", "success")
            self.metadata(kind, "collection_errors", ["missing payload"])
            self.rejected("collection_errors", kind=kind)
            self.metadata(kind, "collection_errors", [])
            self.metadata(kind, "target", {**TARGET, "debian": "12"})
            self.rejected("target", kind=kind)
            self.metadata(kind, "target", TARGET)
            exit_code = self.root / kind / "logs/ci" / f"{kind}.exit-code"
            exit_code.write_text("7\n")
            self.manifest(kind)
            self.rejected("exit-code", kind=kind)
            exit_code.write_text("0\n")
            self.manifest(kind)

    def test_image_specific_audits_ramdiag_and_simulation_are_mandatory(self):
        for key in ("image_static_audit", "factory_static_audit", "simulation_validation"):
            self.metadata("image", key, "failed")
            self.rejected(key, kind="image")
            self.metadata("image", key, "passed")
        audit = self.root / "image/logs/ci/factory-firmware-audit-1.log"
        audit.write_text("FAIL\n")
        self.manifest("image")
        self.rejected("audit log", kind="image")
        audit.write_text("PASS: restored\n")
        tested_display = self.root / "image/packages/simulation" / DEB
        tested_display.write_bytes(b"")
        self.manifest("image")
        self.rejected("empty simulation display package", kind="image")
        tested_display.write_bytes(b"restored display package")
        diagnostic = self.root / "image/diagnostics/README.txt"
        diagnostic.unlink()
        self.manifest("image")
        self.rejected("RAM diagnostic", kind="image")

    def test_payload_shape_is_kind_specific(self):
        extra = self.put("image/images/second-uboot-firmware.tar", "extra")
        self.manifest("image")
        self.rejected("exactly one", kind="image")
        extra.unlink()
        self.manifest("image")
        wrong = self.root / "display/packages/display" / DEB
        renamed = wrong.with_name("e87n-display_9.9_all.deb")
        wrong.rename(renamed)
        self.manifest("display")
        self.rejected("standalone", kind="display")
        renamed.rename(wrong)
        self.manifest("display")
        self.put("display/images/unexpected-uboot-firmware.tar", "firmware")
        self.manifest("display")
        self.rejected("unexpected", kind="display")

    def test_every_manifest_entry_is_hashed_and_unexpected_files_are_rejected(self):
        for kind in ("image", "display"):
            root = self.root / kind
            target = next(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
            target.write_bytes(target.read_bytes() + b"tampered")
            self.rejected("SHA256 mismatch", kind=kind)
            self.manifest(kind)
            unexpected = self.put(f"{kind}/private.key", "secret")
            self.manifest(kind)
            self.rejected("unexpected", kind=kind)
            unexpected.unlink()
            self.manifest(kind)

    def test_manifest_paths_duplicates_and_coverage_fail_closed(self):
        for kind in ("image", "display"):
            path = self.root / kind / "SHA256SUMS"
            original = path.read_text()
            path.write_text("0" * 64 + "  ../outside\n" + original)
            self.rejected("unsafe artifact filename", kind=kind)
            path.write_text(original + original.splitlines()[0] + "\n")
            self.rejected("duplicate manifest", kind=kind)
            path.write_text("\n".join(original.splitlines()[1:]) + "\n")
            self.rejected("coverage", kind=kind)
            path.write_text(original)

    def test_symlinks_special_files_existing_output_and_overlap_are_rejected(self):
        target = self.root / "display/build-metadata.json"
        saved = self.root / "saved-metadata"
        target.rename(saved)
        target.symlink_to(saved)
        self.rejected("symlink", kind="display")
        target.unlink()
        saved.rename(target)
        self.manifest("display")
        os.mkfifo(self.root / "display/logs/ci/pipe.log")
        self.rejected("not a regular file", kind="display")
        (self.root / "display/logs/ci/pipe.log").unlink()
        self.put("release/keep.txt", "preserve")
        result = self.run_cli("display")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.output / "keep.txt").read_text(), "preserve")
        shutil.rmtree(self.output)
        self.rejected("overlaps", kind="display", output=str(self.root / "display/new-release"))

    def test_invalid_cli_arguments_fail_without_tracebacks(self):
        cases = {
            "source-commit": ["abc", "x" * 40], "run-id": ["1;id", ""],
            "run-attempt": ["-1", "1\n"], "tag": ["../bad", "v1/escape", "v1..2", "v1.lock"],
            "repository": ["owner", "owner/repo/extra", "owner/repo?token=secret"],
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.rejected(kind="display", **{key: value})
        result = self.run_cli("invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
