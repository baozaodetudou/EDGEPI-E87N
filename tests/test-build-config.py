#!/usr/bin/env python3
"""Central configuration contracts; no build, network or target execution."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_config import BUILD, KERNEL_RELEASE, KERNEL_VERSION, TARGET, load_config
from release_identity import (candidate_artifact, display_filename, image_filename,
                              release_tag, release_title, tag_version)


class BuildConfiguration(unittest.TestCase):
    def test_reviewed_source_identity(self):
        self.assertEqual(BUILD["kernel_version"], "6.18.52")
        self.assertEqual(BUILD["kernel_commit"], "a638fabe36f293e58ab6be002af04b866959c546")
        self.assertEqual(BUILD["kernel_source"].lower(), "https://github.com/frank-w/bpi-router-linux.git")
        self.assertEqual(BUILD["armbian_commit"], "7c1bb29eb0e7bd75b0703d86fe654b2680e646da")
        self.assertEqual(BUILD["firmware_version"], "2026.09.1")
        self.assertEqual(KERNEL_RELEASE, KERNEL_VERSION + "-current-edgepi-e87n")
        self.assertEqual(TARGET, {"debian": "13", "release": "trixie",
                                  "kernel": KERNEL_VERSION, "extra_storage": "no"})

    def test_invalid_pins_and_profiles_are_rejected(self):
        invalid = (("schema", 2), ("kernel_commit", "main"), ("kernel_commit", "a" * 39),
                   ("armbian_commit", "HEAD"), ("kernel_version", "6.19.1"),
                   ("kernel_release", KERNEL_VERSION + "-current-filogic"),
                   ("release", "bookworm"), ("architecture", "amd64"),
                   ("firmware_version", "2026.9.1"), ("firmware_version", "2026.09.0"))
        with tempfile.TemporaryDirectory(prefix="e87n-build-config-") as directory:
            path = Path(directory) / "config.json"
            for key, value in invalid:
                with self.subTest(key=key, value=value):
                    path.write_text(json.dumps({**BUILD, key: value}))
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_cli_reads_from_its_source_tree_from_any_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="e87n-config-cli-") as directory:
            for key in ("kernel_version", "kernel_release", "kernel_commit", "armbian_commit",
                        "release", "board", "extra_storage", "firmware_version"):
                result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/build_config.py"), key],
                                        cwd=directory, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, str(BUILD[key]) + "\n")
            result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/build_config.py")],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), BUILD)

    def test_unknown_key_fails_without_output(self):
        result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/build_config.py"), "missing"],
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_release_identity_is_stable_and_channel_specific(self):
        self.assertEqual(release_tag("image"), "e87n-image-v2026.09.1")
        self.assertEqual(release_tag("display", version="1.2.3+git~rc1"),
                         "e87n-display-v1.2.3.plus.git.tilde.rc1")
        self.assertEqual(candidate_artifact("image", "123", "2"),
                         "e87n-image-v2026.09.1-candidate-123-2")
        self.assertEqual(release_title("image"),
                         "E87N Image | 2026.09.1 | Debian 13.7 | Linux 6.18.52")
        self.assertEqual(release_title("display", version="1.2.0-1"),
                         "E87N Display | 1.2.0-1")
        self.assertEqual(image_filename(),
                         "edgepi-e87n-debian_2026.09.1_arm64-uboot-firmware.tar")
        self.assertEqual(display_filename(version="1.2.0-1"),
                         "e87n-display_1.2.0-1_all.deb")
        for invalid in ("", "bad/value", "1" + "a" * 60):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                tag_version(invalid)
        with self.assertRaises(ValueError):
            release_tag("display", version="")


if __name__ == "__main__":
    unittest.main(verbosity=2)
