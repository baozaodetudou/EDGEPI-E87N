#!/usr/bin/env python3
"""No-device regressions for offline adaptation and runtime authorization gates."""
import copy
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = load("prepare_factory", "scripts/prepare-factory-rootfs.py")
runtime = load("factory_boot", "board-support/factory-boot/factory_boot.py")
UUID = "ee0d711d-5cce-4fa5-9425-50d0bcb26096"


def snapshot():
    return {"machine": "aarch64", "uid": 0, "model": b"EdgePi E87N\0",
            "compatible": b"edgepi,e87n\0mediatek,mt7987a\0", "removable": 0,
            "card_type": "MMC", "sectors": 15268864,
            "parts": {i: dict(name=n, start=s, size=z, number=i, parent_ok=True, dev=f"179:{i}")
                      for i, (n, s, z) in runtime.LAYOUT.items()},
            "mounts": [dict(mount="/", root="/", type="ext4", source="/dev/mmcblk0p5",
                            dev="179:5", options=["rw", "relatime"])]}


class RuntimeTests(unittest.TestCase):
    def test_accept_exact_layout(self):
        runtime.validate_live(snapshot())

    def test_readonly_mac_namespace_but_not_resize(self):
        state = snapshot()
        state["mounts"][0]["options"] = ["ro"]
        runtime.validate_live(state, writable_required=False)
        with self.assertRaises(ValueError):
            runtime.validate_live(state)

    def test_every_partition_field_fails_closed(self):
        for number in runtime.LAYOUT:
            for field in ("name", "start", "size", "number", "parent_ok", "dev"):
                state = snapshot()
                state["parts"][number][field] = {"name": "other", "start": 0, "size": 1,
                                               "number": 9, "parent_ok": False, "dev": "bad"}[field]
                with self.subTest(number=number, field=field), self.assertRaises(ValueError):
                    runtime.validate_live(state)

    def test_wrong_board_disk_and_root(self):
        for field, value in (("machine", "x86_64"), ("uid", 1000), ("model", b"E87N\0"),
                             ("compatible", b"mediatek,mt7987\0"), ("removable", 1),
                             ("card_type", "SD"), ("sectors", 100)):
            state = snapshot()
            state[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.validate_live(state)
        for field, value in (("root", "/subdir"), ("type", "overlay"), ("source", "/dev/sda5"),
                             ("dev", "8:5"), ("options", ["ro"])):
            state = snapshot()
            state["mounts"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.validate_live(state)

    def test_extra_missing_duplicate_partitions(self):
        for action in ("extra", "missing", "duplicate"):
            state = snapshot()
            if action == "extra":
                state["parts"][6] = copy.deepcopy(state["parts"][5])
            elif action == "missing":
                del state["parts"][1]
            else:
                state["parts"][2]["dev"] = state["parts"][1]["dev"]
            with self.subTest(action=action), self.assertRaises(ValueError):
                runtime.validate_live(state)

    def test_bad_snapshot_cannot_reach_device_or_command(self):
        with mock.patch.object(runtime, "live_snapshot", return_value={"machine": "x86_64"}), \
             mock.patch.object(runtime, "open_partition") as opened, \
             mock.patch.object(runtime.subprocess, "run") as command:
            with self.assertRaises(ValueError):
                runtime.resize_rootfs()
            opened.assert_not_called()
            command.assert_not_called()

    def test_ext4_geometry(self):
        data = bytearray(1024)
        data[56:58] = b"\x53\xef"
        struct.pack_into("<I", data, 24, 2)
        struct.pack_into("<I", data, 4, 216832)
        self.assertEqual(runtime.ext4_geometry(data), (216832, 1897723))
        for offset, value in ((24, 6), (4, 0), (4, 1897724)):
            bad = bytearray(data)
            struct.pack_into("<I", bad, offset, value)
            with self.assertRaises(ValueError):
                runtime.ext4_geometry(bad)

    def test_mac_validation(self):
        good = bytes.fromhex("001122334455001122334456")
        self.assertEqual(runtime.factory_macs(good), ("00:11:22:33:44:55", "00:11:22:33:44:56"))
        for bad in (b"", good[:11], b"\0" * 6 + good[6:], b"\xff" * 6 + good[6:],
                    b"\x01" + good[1:], good[:6] * 2):
            with self.subTest(data=bad.hex()), self.assertRaises(ValueError):
                runtime.factory_macs(bad)

    def test_interface_identity_and_admin_state(self):
        interfaces = {f"eth{i}": (runtime.ALIASES[i], 0x1002) for i in range(2)}
        runtime.validate_interfaces(interfaces)
        for bad in ({"eth0": interfaces["eth0"]},
                    {"eth0": interfaces["eth1"], "eth1": interfaces["eth0"]},
                    {"eth0": (runtime.ALIASES[0], 0x1003), "eth1": interfaces["eth1"]}):
            with self.assertRaises(ValueError):
                runtime.validate_interfaces(bad)

    def test_network_keeps_netplan_dhcp(self):
        base = "[Match]\nName=e*\n\n[Link]\nRequiredForOnline=no\n\n[Network]\nDHCP=yes\nIPv6AcceptRA=yes\n"
        for index, result in enumerate(runtime.port_networks(base, ("00:11:22:33:44:55", "00:11:22:33:44:56"))):
            self.assertIn(f"Property=ID_NET_NAME_ONBOARD=end{index}", result)
            self.assertIn("DHCP=yes\nIPv6AcceptRA=yes", result)
            self.assertIn("RequiredForOnline=no", result)
        for bad in (base.replace("DHCP=yes", "DHCP=no"), base.replace("Name=e*", "Name=*"),
                    base + "[Match]\nName=e*\n", base.replace("[Link]", "[Link]\nMACAddress=aa")):
            with self.assertRaises(ValueError):
                runtime.port_networks(bad, ("00:11:22:33:44:55", "00:11:22:33:44:56"))

    def test_unit_ordering_and_nonblocking_failure(self):
        unit = (prep.ASSETS / "e87n-factory-mac.service").read_text()
        self.assertIn("Before=systemd-networkd.service", unit)
        self.assertIn("udevadm settle --timeout=5", unit)
        self.assertIn("/usr/lib/e87n/factory-boot.py mac", unit)
        dropin = (prep.ASSETS / "20-e87n-factory-mac.conf").read_text()
        self.assertIn("Wants=e87n-factory-mac.service", dropin)
        self.assertNotIn("Requires=", dropin)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="e87n-factory-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root, self.boot = self.base / "root", self.base / "boot"
        self.root.mkdir()
        self.boot.mkdir()
        for directory in ("etc", "var/lib/dpkg", "var/lib/apt/lists/partial", "usr/lib/modules/keep",
                          "etc/systemd/system/multi-user.target.wants"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.fstab = f"# keep\nUUID={UUID} / ext4 defaults,commit=120 0 1\nUUID=old /boot ext4 defaults 0 2\ntmpfs /tmp tmpfs defaults 0 0\n"
        (self.root / "etc/fstab").write_text(self.fstab)
        (self.root / "var/lib/dpkg/status").write_text("\n\n".join(
            f"Package: {name}\nStatus: hold ok installed\nVersion: 26.11\n"
            for name in ("linux-image-current-filogic", "linux-dtb-current-filogic")))
        (self.root / "var/lib/apt/lists/index").write_bytes(b"apt-cache")
        (self.root / "var/lib/apt/lists/partial/temp").write_bytes(b"partial")
        (self.root / "usr/lib/modules/keep/module").write_bytes(b"essential")
        (self.root / "etc/systemd/system/multi-user.target.wants/armbian-resize-filesystem.service").symlink_to(
            "/usr/lib/systemd/system/armbian-resize-filesystem.service")
        (self.boot / "vmlinuz-test").write_bytes(b"kernel fixture")
        (self.boot / "vmlinuz-test").chmod(0o640)
        (self.boot / "Image").symlink_to("vmlinuz-test")
        (self.boot / "initrd.img-test").write_bytes(b"initrd fixture")
        (self.boot / "lost+found").mkdir()
        (self.boot / "lost+found/ignored").write_text("ignore")

    def test_adapt_verify_repeat_and_preserve(self):
        prep.adapt(self.root, self.boot, UUID)
        first = prep.tree(self.root)
        self.assertEqual(prep.check_offline(self.root, self.boot, UUID)["helper"], "/usr/lib/e87n/factory-boot.py")
        prep.adapt(self.root, self.boot, UUID)
        self.assertEqual(first, prep.tree(self.root))
        self.assertEqual(os.readlink(self.root / "boot/Image"), "vmlinuz-test")
        self.assertEqual(stat.S_IMODE((self.root / "boot/vmlinuz-test").stat().st_mode), 0o640)
        self.assertFalse((self.root / "boot/lost+found").exists())
        self.assertEqual((self.root / "usr/lib/modules/keep/module").read_bytes(), b"essential")
        self.assertIn(f"UUID={UUID} / ext4", (self.root / "etc/fstab").read_text())

    def test_root_and_symlink_root_refused(self):
        with self.assertRaises(ValueError):
            prep.directory("/")
        link = self.base / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            prep.adapt(link, self.boot, UUID)

    def test_target_ancestor_symlink_no_external_write(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "usr/lib/e87n").symlink_to(outside)
        with self.assertRaises(ValueError):
            prep.adapt(self.root, self.boot, UUID)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.root / "etc/fstab").read_text(), self.fstab)

    def test_boot_symlink_refused_and_source_links_not_followed(self):
        (self.root / "boot").symlink_to(self.base)
        with self.assertRaises(ValueError):
            prep.adapt(self.root, self.boot, UUID)
        (self.root / "boot").unlink()
        (self.boot / "outside-link").symlink_to("/etc/not-a-real-host-file")
        prep.adapt(self.root, self.boot, UUID)
        self.assertEqual(os.readlink(self.root / "boot/outside-link"), "/etc/not-a-real-host-file")

    def test_invalid_uuid_holds_and_fstab_reject_before_writes(self):
        for bad in ("bad", "00000000-0000-0000-0000-000000000000"):
            with self.assertRaises(ValueError):
                prep.adapt(self.root, self.boot, bad)
        status = self.root / "var/lib/dpkg/status"
        status.write_text(status.read_text().replace("hold ok installed", "install ok installed"))
        with self.assertRaises(ValueError):
            prep.adapt(self.root, self.boot, UUID)
        self.assertFalse((self.root / "boot").exists())

    def test_fstab_duplicate_and_nested_mounts(self):
        for extra in (f"UUID={UUID} / ext4 defaults 0 1\n", "UUID=x /boot ext4 defaults 0 2\n",
                      "UUID=x /boot/efi vfat defaults 0 2\n"):
            with self.assertRaises(ValueError):
                prep.fstab(self.fstab + extra, UUID)

    def test_cache_symlink_refused_without_deleting_target(self):
        outside = self.base / "keep"
        outside.write_text("keep")
        (self.root / "var/lib/apt/lists/redirect").symlink_to(outside)
        with self.assertRaises(ValueError):
            prep.adapt(self.root, self.boot, UUID)
        self.assertEqual(outside.read_text(), "keep")
        self.assertFalse((self.root / "boot").exists())

    def test_special_boot_file_refused(self):
        os.mkfifo(self.boot / "fifo")
        with self.assertRaises(ValueError):
            prep.adapt(self.root, self.boot, UUID)

    def test_verify_detects_tamper_without_repair(self):
        prep.adapt(self.root, self.boot, UUID)
        helper = self.root / "usr/lib/e87n/factory-boot.py"
        helper.write_text("tampered")
        with self.assertRaises(ValueError):
            prep.check_offline(self.root, self.boot, UUID)
        with self.assertRaises(ValueError):
            prep.adapt(self.root, self.boot, UUID)
        self.assertEqual(helper.read_text(), "tampered")

    def test_growroot_source_hook_rejected(self):
        path = self.root / prep.FORBIDDEN[0]
        path.parent.mkdir(parents=True)
        path.write_text("fixture")
        with self.assertRaises(ValueError):
            prep.adapt(self.root, self.boot, UUID)

    def test_initrd_list_gate(self):
        with mock.patch.object(prep.shutil, "which", return_value="lsinitramfs"), \
             mock.patch.object(prep.subprocess, "run") as command:
            command.return_value.stdout = "init\nusr/lib/modules/a.ko\n"
            prep.verify_initrds(self.boot)
            for name in ("growroot", "growpart", "resize2fs"):
                command.return_value.stdout = f"init\nusr/sbin/{name}\n"
                with self.assertRaises(ValueError):
                    prep.verify_initrds(self.boot)

    def test_cli_root_refused(self):
        result = subprocess.run([sys.executable, "-B", str(REPO / "scripts/prepare-factory-rootfs.py"),
                                 "--root", "/", "--boot", str(self.boot), "--uuid", UUID],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing /", result.stderr)


class PatchTests(unittest.TestCase):
    def test_board_patch_applies_after_901(self):
        helper = load("network_fixture", "tests/test-network-policy.py")
        source = helper.added_source((helper.PATCH_DIR / "0000-add-mt7987-e87n-dts.patch").read_text(), helper.DTS_PATH)
        with tempfile.TemporaryDirectory(prefix="e87n-memory-test-") as directory:
            target = Path(directory) / helper.DTS_PATH
            target.parent.mkdir(parents=True)
            target.write_text(source)
            for name in ("901-e87n-ethernet-aliases.patch", "902-e87n-memory-1g.patch"):
                result = subprocess.run([helper.find_gnu_patch(), "--batch", "--forward", "--fuzz=0", "-p1",
                                         "-i", str(helper.PATCH_DIR / name)], cwd=directory,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotRegex(result.stdout + result.stderr, "offset|fuzz")
            board = target.read_text()
            self.assertIn("reg = <0 0x40000000 0 0x40000000>", board)
            self.assertIn("reg = <0 0x7ff70000 0 0x10000>", board)
            self.assertIn("reg = <0 0x7ff80000 0 0x80000>;\n\t\tno-map;", board)
            self.assertIn("ethernet0 = &gmac0;", board)
            self.assertIn('#include "mt7987a.dtsi"', board)


if __name__ == "__main__":
    unittest.main()
