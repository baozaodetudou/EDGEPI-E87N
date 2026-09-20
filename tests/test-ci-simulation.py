#!/usr/bin/env python3
"""Synthetic evidence for gate unit tests only; never represents a real boot."""
import copy
import hashlib
from importlib import import_module
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_config import BUILD
gate = import_module("ci-simulation")


def fixture_report(firmware, display, source_commit, run_id, run_attempt):
    """Create explicitly synthetic JSON for isolated CI policy tests."""
    return {
        "schema_version": 1, "status": "PASS",
        "artifacts": {"firmware_tar": "synthetic-fixture.tar",
                      "firmware_tar_sha256": hashlib.sha256(firmware).hexdigest(),
                      "fit_sha256": hashlib.sha256(b"synthetic FIT").hexdigest(),
                      "root_sha256": hashlib.sha256(b"synthetic root").hexdigest(),
                      "display_deb_sha256": hashlib.sha256(display).hexdigest()},
        "guest": {"kernel_release": BUILD["kernel_release"], "debian_version": BUILD["debian_point"],
                  **{key: True for key in ("systemd_pid1", "dhcp", "dns", "timezone", "locale",
                                          "minimal", "persistence", "first_login", "ssh", "apt",
                                          "warm_reboot", "cold_reboot", "host_keys", "display_package")}},
        "hardware_exemptions": [
            {"unit": "e87n-factory-resize.service",
             "reason": "requires physical /dev/mmcblk0p5 and must not run on QEMU virt"},
            {"unit": "e87n-factory-mac.service",
             "reason": "requires E87N DT aliases and factory p2 MAC bytes"}],
        "binding": {"source_commit": source_commit, "run_id": run_id, "run_attempt": run_attempt},
    }


class SimulationGate(unittest.TestCase):
    def setUp(self):
        self.report = fixture_report(b"firmware", b"display", "a" * 40, "123", "1")
        self.args = {"firmware_sha256": hashlib.sha256(b"firmware").hexdigest(),
                     "display_sha256": hashlib.sha256(b"display").hexdigest(),
                     "source_commit": "a" * 40, "run_id": "123", "run_attempt": "1"}

    def test_exact_successful_evidence(self):
        self.assertEqual(gate.verify_report(self.report, **self.args), self.report)

    def test_every_required_guest_check_fails_closed(self):
        for key in self.report["guest"]:
            for value in (None, False, "", 1):
                report = copy.deepcopy(self.report)
                report["guest"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    gate.verify_report(report, **self.args)

    def test_identity_and_payload_mismatches(self):
        for key in self.args:
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.verify_report(self.report, **{**self.args, key: "b" * 64})
        for status in ("FAIL", "SKIP", "not run", None):
            with self.subTest(status=status), self.assertRaises(ValueError):
                gate.verify_report({**self.report, "status": status}, **self.args)

    def test_unreviewed_guest_version_is_rejected(self):
        for key, value in (("kernel_release", "6.18.51-current-filogic"), ("debian_version", "12.0")):
            report = copy.deepcopy(self.report)
            report["guest"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.verify_report(report, **self.args)

    def test_report_loader_rejects_missing_symlink_duplicate_and_oversized(self):
        with tempfile.TemporaryDirectory(prefix="e87n-simulation-unit-") as directory:
            path = Path(directory).resolve() / "result.json"
            with self.assertRaises(ValueError):
                gate.load_report(path)
            path.write_text(json.dumps(self.report))
            self.assertEqual(gate.load_report(path), self.report)
            link = path.with_name("link.json")
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                gate.load_report(link)
            for content in ('{"status":"PASS","status":"FAIL"}', " " * (1024 * 1024 + 1)):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    gate.load_report(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
