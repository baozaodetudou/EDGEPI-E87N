#!/usr/bin/env python3
"""Temporary-directory fixtures only; use root in a disposable Linux builder."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import subprocess

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("system_audit", REPO / "scripts/verify-system.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@unittest.skipUnless(os.geteuid() == 0, "root-owned fixture checks require disposable Linux builder root")
class SystemAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        self.boot = Path(self.temp.name) / "boot"
        self.boot.mkdir()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(module.shutil, "which", return_value="/fixture/fdtget").start()
        def fdtget(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "/soc/ethernet@15100000/mac@" + argv[-1][-1] + "\n", "")
        mock.patch.object(module.subprocess, "run", side_effect=fdtget).start()
        for source, dest in module.PAIRS.items():
            self.put(dest, (REPO / "board-support" / source).read_bytes())
        self.put("/etc/systemd/system/sshd@.service.d/e87n.conf", self.file("/etc/systemd/system/ssh.service.d/e87n.conf").read_bytes())
        self.put("/etc/machine-id", b"")
        import ctypes
        import ctypes.util
        crypt = ctypes.CDLL(ctypes.util.find_library("crypt")).crypt
        crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        crypt.restype = ctypes.c_char_p
        encoded = crypt(b"doumao", b"$6$fixtureonly$").decode()
        self.put("/etc/shadow", f"root:{encoded}:20000:0:99999:7:::\n".encode())
        self.put("/etc/passwd", b"root:x:0:0:root:/root:/bin/bash\n")
        self.put("/etc/default/armbian-firstrun", b"OPENSSHD_REGENERATE_HOST_KEYS=false\n")
        self.put("/usr/lib/systemd/system/sshd-keygen.service", b"[Service]\nExecStart=/usr/bin/ssh-keygen -A\n")
        self.put("/etc/timezone", b"Asia/Shanghai\n")
        self.put("/usr/share/zoneinfo/Asia/Shanghai", b"fixture-timezone")
        self.file("/etc/localtime").symlink_to("/usr/share/zoneinfo/Asia/Shanghai")
        self.file("/etc/resolv.conf").symlink_to("/run/systemd/resolve/stub-resolv.conf")
        self.put("/etc/default/locale", b'LANG=zh_CN.UTF-8\nLANGUAGE=zh_CN:zh\n')
        self.put("/etc/locale.gen", b"zh_CN.UTF-8 UTF-8\n")
        self.put("/usr/lib/locale/locale-archive", b"fixture-locale")
        self.put("/etc/apt/sources.list", b"deb https://deb.debian.org/debian trixie main\ndeb https://security.debian.org/debian-security trixie-security main\n")
        for name in ("e87n-display", "ssh", "systemd-networkd", "systemd-resolved", "systemd-timesyncd"):
            if name != "e87n-display":
                self.put("/usr/lib/systemd/system/" + name + ".service", b"[Service]\n")
            link = self.file("/etc/systemd/system/multi-user.target.wants/" + name + ".service")
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to("/usr/lib/systemd/system/" + name + ".service")
        self.file("/etc/systemd/system/ssh.socket").symlink_to("/dev/null")
        paragraphs = [f"Package: {name}\nStatus: install ok installed" for name in module.PACKAGES]
        version = (REPO / "packaging/e87n-display/VERSION").read_text().strip()
        paragraphs = [entry + ("\nVersion: " + version if entry.startswith("Package: e87n-display\n") else "")
                      for entry in paragraphs]
        paragraphs += [f"Package: {name}\nStatus: hold ok installed" for name in ("linux-image-current-filogic", "linux-dtb-current-filogic")]
        self.put("/var/lib/dpkg/status", "\n\n".join(paragraphs).encode())
        self.put("/var/lib/dpkg/info/e87n-display.list", ("\n".join(module.PAIRS.values()) +
                 "\n/etc/e87n/display.json\n/etc/modules-load.d/e87n-display.conf\n").encode())
        self.put("/var/lib/dpkg/info/e87n-display.conffiles", b"/etc/e87n/display.json\n/etc/modules-load.d/e87n-display.conf\n")

    def file(self, path):
        return self.root / path.lstrip("/")

    def put(self, path, data):
        target = self.file(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)

    def check(self):
        module.check(self.root, self.boot)

    def test_good(self):
        self.check()

    def test_shared_keys_rejected(self):
        self.put("/etc/ssh/ssh_host_ed25519_key", b"FAKE-NOT-A-KEY")
        with self.assertRaisesRegex(ValueError, "contains SSH host keys"):
            self.check()

    def test_wrong_password_rejected_without_echo(self):
        self.put("/etc/shadow", b"root:!retained-secret:20000:0:99999:7:::\n")
        with self.assertRaisesRegex(ValueError, "factory password") as caught:
            self.check()
        self.assertNotIn("retained-secret", str(caught.exception))

    def test_personal_seed_rejected(self):
        (self.boot / "e87n-provision.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "personal provisioning"):
            self.check()

    def test_machine_id_rejected(self):
        self.put("/etc/machine-id", b"0" * 32)
        with self.assertRaisesRegex(ValueError, "machine-id"):
            self.check()

    def test_source_mismatch_rejected(self):
        self.put("/etc/ssh/sshd_config.d/00-e87n-security.conf", b"PermitRootLogin no\n")
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            self.check()

    def test_missing_package_rejected(self):
        path = self.file("/var/lib/dpkg/status")
        path.write_text(path.read_text().replace("Package: e87n-display", "Package: unrelated"))
        with self.assertRaisesRegex(ValueError, "e87n-display"):
            self.check()

    def test_root_autologin_rejected(self):
        self.put("/etc/systemd/system/serial-getty@.service.d/override.conf", b"ExecStart=agetty --autologin root\n")
        with self.assertRaisesRegex(ValueError, "auto-logs"):
            self.check()

    def test_kernel_unhold_rejected(self):
        path = self.file("/var/lib/dpkg/status")
        path.write_text(path.read_text().replace("hold ok installed", "install ok installed"))
        with self.assertRaisesRegex(ValueError, "not held"):
            self.check()

    def test_obsolete_gate_rejected(self):
        self.put("/usr/lib/systemd/system/e87n-provision-seed.service", b"[Unit]\n")
        with self.assertRaisesRegex(ValueError, "provisioning gate"):
            self.check()

    def test_broken_unit_enable(self):
        self.file("/etc/systemd/system/multi-user.target.wants/ssh.service").unlink()
        with self.assertRaisesRegex(ValueError, "enabled unit"):
            self.check()

    def test_missing_alias(self):
        with mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            with self.assertRaisesRegex(ValueError, "GMAC alias"):
                self.check()

    def test_timezone(self):
        self.put("/etc/timezone", b"Etc/UTC\n")
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.check()

    def test_locale(self):
        self.put("/etc/default/locale", b"LANG=C.UTF-8\n")
        with self.assertRaisesRegex(ValueError, "locale"):
            self.check()

    def test_apt_signatures(self):
        self.put("/etc/apt/sources.list", b"deb [trusted=yes] https://example.invalid trixie trixie-security\n")
        with self.assertRaisesRegex(ValueError, "signature"):
            self.check()

    def test_password_not_expired(self):
        file = self.file("/etc/shadow")
        file.write_text(file.read_text().replace(":20000:", ":0:"))
        with self.assertRaisesRegex(ValueError, "expired"):
            self.check()

    def test_masked_keygen(self):
        self.file("/etc/systemd/system/sshd-keygen.service").symlink_to("/dev/null")
        with self.assertRaisesRegex(ValueError, "masked or overridden"):
            self.check()

    def test_display_version(self):
        file = self.file("/var/lib/dpkg/status")
        file.write_text(file.read_text().replace("Version: ", "Version: 999"))
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            self.check()

    def test_display_package_ownership(self):
        self.put("/var/lib/dpkg/info/e87n-display.list", b"/fixture\n")
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.check()

    def test_conffiles(self):
        self.put("/var/lib/dpkg/info/e87n-display.conffiles", b"/fixture\n")
        with self.assertRaisesRegex(ValueError, "conffiles"):
            self.check()


if __name__ == "__main__":
    unittest.main()
