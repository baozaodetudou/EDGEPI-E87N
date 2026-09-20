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


class BuildConfiguration(unittest.TestCase):
    def test_reviewed_source_identity(self):
        self.assertEqual(BUILD["kernel_version"], "6.18.52")
        self.assertEqual(BUILD["kernel_commit"], "a638fabe36f293e58ab6be002af04b866959c546")
        self.assertEqual(BUILD["kernel_source"].lower(), "https://github.com/frank-w/bpi-router-linux.git")
        self.assertEqual(BUILD["armbian_commit"], "7c1bb29eb0e7bd75b0703d86fe654b2680e646da")
        self.assertEqual(KERNEL_RELEASE, KERNEL_VERSION + "-current-edgepi-e87n")
        self.assertEqual(TARGET, {"debian": "13", "release": "trixie",
                                  "kernel": KERNEL_VERSION, "extra_storage": "no"})

    def test_invalid_pins_and_profiles_are_rejected(self):
        invalid = (("schema", 2), ("kernel_commit", "main"), ("kernel_commit", "a" * 39),
                   ("armbian_commit", "HEAD"), ("kernel_version", "6.19.1"),
                   ("kernel_release", KERNEL_VERSION + "-current-filogic"),
                   ("release", "bookworm"), ("architecture", "amd64"))
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
                        "release", "board", "extra_storage"):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
