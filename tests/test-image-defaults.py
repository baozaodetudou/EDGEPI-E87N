#!/usr/bin/env python3
"""Portable source-policy regressions; real SSH/APT tests use the smoke script."""
from pathlib import Path
import re
import subprocess
import unittest

REPO = Path(__file__).resolve().parents[1]


class DefaultsPolicy(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ("board-support/image-defaults.sh", "userpatches/customize-image.sh",
                     "tests/smoke-minimal-userspace.sh"):
            subprocess.run(["bash", "-n", str(REPO / name)], check=True)

    def test_build_resolver_is_left_for_framework_finalization(self):
        script = (REPO / "board-support/image-defaults.sh").read_text()
        executable = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("/etc/resolv.conf", executable)
        smoke = (REPO / "tests/smoke-minimal-userspace.sh").read_text()
        self.assertIn("! -L $scratch/root/etc/resolv.conf", smoke)
        self.assertLess(smoke.index("install hello"), smoke.index("ln -sfn /run/systemd/resolve/stub-resolv.conf"))

    def test_ssh_public_factory_profile(self):
        config = (REPO / "board-support/00-e87n-security.conf").read_text()
        self.assertRegex(config, r"(?m)^PermitRootLogin yes$")
        self.assertRegex(config, r"(?m)^PasswordAuthentication yes$")
        self.assertRegex(config, r"(?m)^PermitEmptyPasswords no$")
        dropin = (REPO / "board-support/systemd/e87n-ssh-keygen.conf").read_text()
        self.assertIn("Requires=sshd-keygen.service", dropin)
        self.assertNotIn("ExecCondition=", dropin)

    def test_default_dhcp_without_router_roles(self):
        config = (REPO / "board-support/network/10-e87n-dhcp.yaml").read_text()
        for required in ("renderer: networkd", 'name: "e*"', "dhcp4: true", "dhcp6: true", "optional: true"):
            self.assertIn(required, config)
        for unwanted in ("addresses:", "bridges:", "gateway4:", "dhcp-server"):
            self.assertNotIn(unwanted, config)

    def test_extra_management_tools_not_requested(self):
        script = (REPO / "userpatches/customize-image.sh").read_text()
        for package in ("lvm2", "mdadm", "cryptsetup-bin", "smartmontools", "docker.io", "luci"):
            self.assertIsNone(re.search(r"\b" + re.escape(package) + r"\b", script))
        self.assertIn("--no-install-recommends", script)


if __name__ == "__main__":
    unittest.main()
