#!/usr/bin/env python3
"""Release preparation fixtures: fake bytes, no network, sudo or target execution."""
import hashlib
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
        self.put("packaging/e87n-display/VERSION", VERSION)
        # Use the real collector to establish its exact current layout, with
        # synthetic build products. Neither collector nor preparer runs them.
        for kind in ("image", "display"):
            job = self.root / (kind + "-job")
            self.put(f"{kind}-job/scripts/ci-collect-artifacts.py", (REPO / "scripts/ci-collect-artifacts.py").read_bytes())
            self.put(f"{kind}-job/packaging/e87n-display/VERSION", VERSION)
            self.put(f"{kind}-job/output/ci/logs/{kind}.log", "fake build log\n")
            self.put(f"{kind}-job/output/ci/logs/{kind}.exit-code", "0\n")
            if kind == "image":
                self.put("image-job/output/ci/firmware/candidate-uboot-firmware.tar", b"fake firmware, not tar\x00")
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

    def run_cli(self, **overrides):
        args = {"image-artifact": str(self.root / "image"), "display-artifact": str(self.root / "display"),
                "output": str(self.output), "source-commit": COMMIT, "run-id": "34737922588",
                "run-attempt": "1", "tag": "e87n-test-6.18.51", "repository": "baozaodetudou/EDGEPI-E87N"}
        args.update(overrides)
        return subprocess.run([sys.executable, str(self.root / "scripts/ci-prepare-release.py"),
                               *(item for key, value in args.items() for item in ("--" + key, value))],
                              cwd=self.root, capture_output=True, text=True, timeout=15)

    def rejected(self, message=None, **overrides):
        result = self.run_cli(**overrides)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("FAIL: release preparation:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        if message:
            self.assertIn(message, result.stderr)
        self.assertFalse(self.output.exists())

    def test_valid_exact_assets_hashes_evidence_and_recursive_packages(self):
        # Cross multiple read chunks without allocating or unpacking a real image.
        self.put("image/images/candidate-uboot-firmware.tar", b"fake\x00" * 450000)
        self.manifest("image")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in self.output.iterdir()}, {
            "candidate-uboot-firmware.tar", DEB, "image-build-metadata.json", "display-build-metadata.json",
            "kernel-packages.tar.xz", "build-evidence.tar.xz", "SHA256SUMS", "RELEASE-NOTES.md"})
        for kind, source, target in (("image", "images/candidate-uboot-firmware.tar", "candidate-uboot-firmware.tar"),
                                     ("display", "packages/display/" + DEB, DEB),
                                     ("image", "build-metadata.json", "image-build-metadata.json"),
                                     ("display", "build-metadata.json", "display-build-metadata.json")):
            self.assertEqual((self.root / kind / source).read_bytes(), (self.output / target).read_bytes())
        sums = (self.output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 7)
        metadata = json.loads((self.output / "image-build-metadata.json").read_text())
        self.assertEqual(metadata["factory_format"], "e87n-uboot-firmware-tar-v1")
        self.assertEqual(metadata["factory_static_audit"], "passed")
        for line in sums:
            checksum, name = line.split("  ", 1)
            self.assertEqual(checksum, hashlib.sha256((self.output / name).read_bytes()).hexdigest())
        for archive, expected in (("kernel-packages.tar.xz", {
                p.relative_to(self.root / "image").as_posix(): p
                for p in (self.root / "image/packages/armbian").rglob("*.deb")}),
                ("build-evidence.tar.xz", {
                    f"{kind}/{p.relative_to(self.root / kind).as_posix()}": p
                    for kind in ("image", "display") for p in (self.root / kind).rglob("*")
                    if p.is_file() and (p.name in ("SHA256SUMS", "build-metadata.json")
                                       or "logs" in p.relative_to(self.root / kind).parts)})):
            with tarfile.open(self.output / archive) as bundle:
                self.assertEqual(set(bundle.getnames()), set(expected))
                for member in bundle:
                    self.assertTrue(member.isfile())
                    self.assertEqual((member.uid, member.gid, member.mode), (0, 0, 0o644))
                    self.assertEqual(bundle.extractfile(member).read(), expected[member.name].read_bytes())
        notes = (self.output / "RELEASE-NOTES.md").read_text()
        self.assertIn("/releases/download/e87n-test-6.18.51/candidate-uboot-firmware.tar", notes)
        self.assertIn("/releases/download/e87n-test-6.18.51/" + DEB, notes)
        self.assertIn("仅有以上两个二进制附件", notes)
        self.assertIn(hashlib.sha256((self.output / "candidate-uboot-firmware.tar").read_bytes()).hexdigest(), notes)
        self.assertIn(hashlib.sha256((self.output / DEB).read_bytes()).hexdigest(), notes)
        self.assertNotIn("sha256sum -c SHA256SUMS", notes)
        self.assertNotIn(".img.xz", notes)
        for required in ("experimental", "Debian 13", "trixie", "6.18.51", "extra_storage=no", "root / doumao",
                         "SSH port 22", "LAN", "passwd", "DHCP", "zh_CN.UTF-8", "Asia/Shanghai", "static checks only",
                         "No board has been validated", "Do not flash", "eMMC", "NOT hardware validated",
                         "software validated", "uncompressed USTAR", "e87n-uboot-firmware-tar-v1",
                         "sysupgrade-edgepi-e87n/kernel", "(FIT)", "sysupgrade-edgepi-e87n/root", "(ext4)",
                         "only in the original U-Boot recovery page's `firmware` field",
                         "Never use the SIMG, GPT or FIP fields", "never use LuCI sysupgrade",
                         "verified recovery backup", "successful hardware RAM test boot", "before any flash", "/commit/" + COMMIT,
                         "/actions/runs/34737922588", "/attempts/1"):
            self.assertIn(required, notes)

    def test_debian_prerelease_package_filename(self):
        package = self.root / "image/packages/armbian/kernel.deb"
        package.rename(package.with_name("kernel_1.0~rc1.deb"))
        self.manifest("image")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(self.output / "kernel-packages.tar.xz") as bundle:
            self.assertIn("packages/armbian/kernel_1.0~rc1.deb", bundle.getnames())

    def test_incomplete_ramdiag_matrix_is_rejected(self):
        readme = self.root / "image/diagnostics/README.txt"
        readme.unlink()
        self.manifest("image")
        self.rejected("incomplete RAM diagnostic matrix")

    def test_empty_ramdiag_payload_is_rejected(self):
        payload = self.root / "image/diagnostics/E87N-ramdiag-40000000-initrd.itb"
        payload.write_bytes(b"")
        self.manifest("image")
        self.rejected("empty RAM diagnostic payload")

    def test_failed_or_absent_success(self):
        for kind in ("image", "display"):
            for outcome in ("failure", "cancelled", "skipped", "", None):
                with self.subTest(kind=kind, outcome=outcome):
                    self.metadata(kind, "build_step_outcome", outcome, remove=outcome is None)
                    self.rejected("build_step_outcome")
            self.metadata(kind, "build_step_outcome", "success")

    def test_source_run_attempt_kind_and_version_mismatch(self):
        for kind in ("image", "display"):
            for key, good, bad in (("source_commit", COMMIT, "a" * 40), ("run_id", "34737922588", "9"),
                                   ("run_attempt", "1", "2"), ("kind", kind, "display" if kind == "image" else "image"),
                                   ("display_version_source", VERSION, "9.9")):
                with self.subTest(kind=kind, key=key):
                    self.metadata(kind, key, bad)
                    self.rejected(key)
                    self.metadata(kind, key, good)

    def test_target_mismatch(self):
        target = {"debian": "13", "release": "trixie", "kernel": "6.18.51", "extra_storage": "no"}
        for kind in ("image", "display"):
            for key, bad in (("debian", "12"), ("release", "bookworm"), ("kernel", "6.12"), ("extra_storage", "yes")):
                with self.subTest(kind=kind, key=key):
                    self.metadata(kind, "target", {**target, key: bad})
                    self.rejected("target")
            self.metadata(kind, "target", target)

    def test_missing_or_mismatched_framework_and_kernel_pins(self):
        for kind in ("image", "display"):
            for key, pin in (("armbian_commit", "7c1bb29eb0e7bd75b0703d86fe654b2680e646da"),
                             ("kernel_commit", "f6388029ea9e2c9e807d73827658738ea131faee")):
                for value in ("a" * 40, None):
                    with self.subTest(kind=kind, key=key, value=value):
                        self.metadata(kind, key, value, remove=value is None)
                        self.rejected(key)
                self.metadata(kind, key, pin)

    def test_audit_and_collection_errors(self):
        for audit in ("not proven", "failed", None):
            self.metadata("image", "image_static_audit", audit, remove=audit is None)
            self.rejected("image_static_audit")
        self.metadata("image", "image_static_audit", "passed")
        for kind in ("image", "display"):
            for errors in (["missing build product"], "", None):
                self.metadata(kind, "collection_errors", errors, remove=errors is None)
                self.rejected("collection_errors")
            self.metadata(kind, "collection_errors", [])

    def test_factory_format_and_static_audit_are_required(self):
        for key, expected, invalid in (
                ("factory_format", "e87n-uboot-firmware-tar-v1", ["raw-gpt", "e87n-uboot-firmware-tar-v2", "", None]),
                ("factory_static_audit", "passed", ["not proven", "failed", "not applicable", True, None])):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    self.metadata("image", key, value, remove=value is None)
                    self.rejected(key)
            self.metadata("image", key, expected)

    def test_factory_audit_requires_hashed_pass_log(self):
        path = self.root / "image/logs/ci/factory-firmware-audit-1.log"
        for content in ("", "FAIL: invalid FIT\n", "not PASS\n", "PASSING is not a verdict\n"):
            with self.subTest(content=content):
                path.write_text(content)
                self.manifest("image")
                self.rejected("factory firmware audit log")
        path.unlink()
        self.manifest("image")
        self.rejected("required factory firmware audit log")

    def test_legacy_system_image_is_rejected_even_when_only_payload_and_hashed(self):
        path = self.root / "image/images/candidate-uboot-firmware.tar"
        for name in ("candidate.img.xz", "candidate.img", "candidate-uboot-firmware.tar.xz"):
            with self.subTest(name=name):
                wrong = path.with_name(name)
                path.rename(wrong)
                self.manifest("image")
                self.rejected("unexpected artifact file")
                wrong.rename(path)
        self.manifest("image")

    def test_nonzero_and_malformed_exit_codes(self):
        for kind in ("image", "display"):
            for value in ("23\n", "", "0\n1\n", "success\n"):
                self.put(f"{kind}/logs/ci/{kind}.exit-code", value)
                self.manifest(kind)
                self.rejected("exit-code")
            self.put(f"{kind}/logs/ci/{kind}.exit-code", "0\n")
            self.manifest(kind)
        self.put("image/logs/ci/runner.exit-code", "3\n")
        self.manifest("image")
        self.rejected("exit-code")

    def test_hash_tampering_every_manifest_entry(self):
        for kind in ("image", "display"):
            for path in (self.root / kind).rglob("*"):
                if path.is_file() and path.name != "SHA256SUMS":
                    with self.subTest(path=str(path.relative_to(self.root))):
                        original = path.read_bytes()
                        path.write_bytes(original + b"tampered")
                        self.rejected("SHA256 mismatch")
                        path.write_bytes(original)

    def test_manifest_unsafe_paths_and_duplicates(self):
        path = self.root / "image/SHA256SUMS"
        original = path.read_text()
        for name in ("../outside", "/absolute.log", "images/../escape-uboot-firmware.tar", "images//x-uboot-firmware.tar",
                     "./build-metadata.json", "C:/absolute.log", "images\\x-uboot-firmware.tar", "logs/ci/a\t.log",
                     "logs/ci/a\r.log", "logs/ci/a\x00.log", "logs/ci/a\x7f.log", "logs/ci/a\u0085.log",
                     "images/asset#label-uboot-firmware.tar", "images/asset with space-uboot-firmware.tar"):
            with self.subTest(name=repr(name)):
                path.write_text("0" * 64 + "  " + name + "\n" + original)
                self.rejected("unsafe artifact filename")
        path.write_text(original + original.splitlines()[0] + "\n")
        self.rejected("duplicate manifest")

    def test_manifest_coverage_and_malformed_records(self):
        path = self.root / "image/SHA256SUMS"
        original = path.read_text()
        for contents in ("", "\n".join(original.splitlines()[1:]) + "\n", original + "0" * 64 + "  logs/ci/absent.log\n"):
            path.write_text(contents)
            self.rejected("coverage")
        for contents in ("not a checksum\n", "z" * 64 + "  build-metadata.json\n"):
            path.write_text(contents)
            self.rejected("malformed SHA256SUMS")

    def test_symlinks_in_files_directories_root_and_parent(self):
        for relative in ("image/images/candidate-uboot-firmware.tar", "display/build-metadata.json", "image/SHA256SUMS",
                         "image/packages/armbian", "display"):
            with self.subTest(path=relative):
                path = self.root / relative
                saved = self.root / "saved"
                path.rename(saved)
                path.symlink_to(saved, target_is_directory=saved.is_dir())
                self.rejected("symlink")
                path.unlink()
                saved.rename(path)
        (self.root / "alias").symlink_to(self.root, target_is_directory=True)
        self.rejected("symlink", **{"image-artifact": str(self.root / "alias/image")})
        self.rejected("symlink", output=str(self.root / "alias/new-release"))
        (self.root / "image/logs/ci/dangling.log").symlink_to(self.root / "absent")
        self.rejected("symlink")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX special file fixture")
    def test_nonregular_file(self):
        os.mkfifo(self.root / "image/logs/ci/pipe.log")
        self.rejected("not a regular file")

    def test_image_collision_and_extra_display_package(self):
        path = self.put("image/images/nested/candidate-uboot-firmware.tar", "another image")
        self.manifest("image")
        self.rejected("exactly one")
        path.unlink()
        self.manifest("image")
        self.put("display/packages/display/extra.deb", "unexpected package")
        self.manifest("display")
        self.rejected("standalone")

    def test_existing_output_is_preserved(self):
        for is_directory in (False, True):
            if is_directory:
                sentinel = self.put("release/keep.txt", "preserve me")
            else:
                sentinel = self.put("release", "preserve me")
            result = self.run_cli()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists", result.stderr)
            self.assertEqual(sentinel.read_text(), "preserve me")
            sentinel.unlink()
            if is_directory:
                self.output.rmdir()
        self.output.symlink_to(self.root / "nonexistent")
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.output.is_symlink())

    def test_missing_or_empty_payload_and_evidence(self):
        for relative in ("image/images/candidate-uboot-firmware.tar", "display/packages/display/" + DEB,
                         "image/build-metadata.json", "display/SHA256SUMS", "image/logs/ci/image.exit-code",
                         "image/logs/ci/factory-firmware-audit-1.log",
                         "display/logs/ci/display.log", "image/packages/armbian"):
            with self.subTest(path=relative):
                path = self.root / relative
                saved = self.root / "saved"
                path.rename(saved)
                if path.name != "SHA256SUMS":
                    self.manifest(relative.split("/")[0])
                self.rejected()
                saved.rename(path)
                self.manifest(relative.split("/")[0])
        self.put("image/images/candidate-uboot-firmware.tar", b"")
        self.manifest("image")
        self.rejected("empty payload")

    def test_wrong_display_filename_or_repository_version(self):
        path = self.root / "display/packages/display" / DEB
        wrong = path.with_name("e87n-display_9.9_all.deb")
        path.rename(wrong)
        self.manifest("display")
        self.rejected("standalone")
        wrong.rename(path)
        self.manifest("display")
        self.put("packaging/e87n-display/VERSION", "9.9")
        self.rejected("display_version_source")

    def test_unexpected_extras_even_when_hashed(self):
        for relative in ("image/private.key", "image/logs/ci/secret.sh", "image/images/extra.img",
                         "image/images/extra.img.xz", "image/images/extra-uboot-firmware.tar.xz",
                         "image/packages/armbian/private.pem", "image/packages/display/extra.deb",
                         "display/packages/armbian/extra.deb", "display/logs/armbian/build.log",
                         "image/logs/ci/.secret.log", "display/secrets/private.log"):
            with self.subTest(path=relative):
                path = self.put(relative, "must never be released")
                kind = relative.split("/")[0]
                self.manifest(kind)
                self.rejected("unexpected" if "/.secret" not in relative else "unsafe")
                path.unlink()
                while path.parent != self.root / kind and not any(path.parent.iterdir()):
                    path.parent.rmdir()
                    path = path.parent
                self.manifest(kind)
        self.put("image/logs/ci/unlisted.log", "not in manifest")
        self.rejected("coverage")

    def test_invalid_cli_arguments_and_overlapping_output(self):
        for key, values in {"source-commit": ["abc", "x" * 40], "run-id": ["1;id", ""],
                            "run-attempt": ["-1", "1\n"], "tag": ["../bad", "v1/escape", "v1\n", "v1..2", "v1.lock", "v" * 97],
                            "repository": ["owner", "owner/repo/extra", "owner/repo?token=secret"]}.items():
            for value in values:
                with self.subTest(key=key, value=repr(value)):
                    self.rejected(**{key: value})
        self.rejected("overlaps", output=str(self.root / "image/new-release"))

    def test_oversized_control_file_and_invalid_json(self):
        path = self.root / "image/build-metadata.json"
        path.write_bytes(b" " * 65537)
        self.manifest("image")
        self.rejected("oversized control")
        for contents in ("[]", "null", "{", "\ud800"):
            path.write_bytes(contents.encode("utf-8", errors="surrogatepass"))
            self.manifest("image")
            self.rejected()

    def test_duplicate_metadata_keys(self):
        for kind in ("image", "display"):
            path = self.root / kind / "build-metadata.json"
            original = path.read_text()
            path.write_text('{"build_step_outcome":"failure",' + original.lstrip()[1:])
            self.manifest(kind)
            self.rejected("duplicate metadata key")
            path.write_text(original)
            self.manifest(kind)


if __name__ == "__main__":
    unittest.main(verbosity=2)
