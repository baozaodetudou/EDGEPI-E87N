#!/usr/bin/env python3
"""Pure host tests for the reusable RAM-only FIT diagnostic helpers."""

from __future__ import annotations

import importlib.util
import lzma
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts/ramdiag/fit_common.py"
INIT = ROOT / "scripts/ramdiag/assets/init-network-first"
SERVICES = ROOT / "scripts/ramdiag/assets/services"
BEACON = ROOT / "scripts/ramdiag/assets/beacon.py"
WATCHDOG = ROOT / "scripts/ramdiag/assets/stop-watchdog.py"
BUILD_INITRD = ROOT / "scripts/ramdiag/build-initrd.py"
SPEC = importlib.util.spec_from_file_location("e87n_ramdiag_fit_common", MODULE)
assert SPEC and SPEC.loader
fit_common = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fit_common
SPEC.loader.exec_module(fit_common)


class RamdiagHelpersTest(unittest.TestCase):
    def test_shell_assets_parse_on_host(self):
        for asset in (INIT, SERVICES):
            result = subprocess.run(["/bin/sh", "-n", str(asset)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{asset}: {result.stderr}")

    def test_initrd_supervisor_keeps_pid_one_alive(self):
        init = INIT.read_text()
        services = SERVICES.read_text()
        self.assertIn("service_pid=$!", init)
        self.assertIn('wait "$service_pid"', init)
        self.assertIn("while :; do", init)
        self.assertNotIn('[ "$$" = 1 ] || exit 1', services)
        self.assertIn("sshd -D -e", services)
        self.assertIn('wait "$pid"', services)

    def test_runtime_mounts_are_idempotent_but_not_block_backed(self):
        init = INIT.read_text()
        services = SERVICES.read_text()
        self.assertIn("mounted_type()", init)
        self.assertIn("mount_or_existing()", init)
        for filesystem in ("proc", "sysfs", "devtmpfs", "tmpfs"):
            self.assertIn(f"mount_or_existing {filesystem}", init)
        self.assertNotIn("/dev/mmc", init)
        self.assertNotIn("mount /dev/", init)
        for forbidden in ("mkfs", "blkdiscard", "fdisk", "parted", "saveenv", "mmc write"):
            self.assertNotIn(forbidden, init + services)
        self.assertNotRegex(init + services, r"(?m)(^|[;&|() \t])dd(?:[ \t]|$)")

    def test_mmc_absence_is_checked_for_all_block_names(self):
        for asset in (INIT, SERVICES):
            source = asset.read_text()
            self.assertIn("/sys/class/block/mmcblk*", source)
            self.assertIn("unexpected MMC block device", source)
        self.assertIn("mmc@11230000/status", INIT.read_text())

    def test_udp_and_watchdog_assets_are_ram_only(self):
        beacon = BEACON.read_text()
        watchdog = WATCHDOG.read_text()
        self.assertIn("socket.AF_INET", beacon)
        self.assertIn("192.168.1.2", beacon)
        self.assertIn("/dev/watchdog", watchdog)
        self.assertNotIn("/dev/mmc", beacon + watchdog)

    def test_initrd_builder_handles_modern_python_and_static_ldd(self):
        source = BUILD_INITRD.read_text()
        self.assertIn("import crypt as crypt_module", source)
        self.assertIn('["openssl", "passwd", "-6", "-stdin"]', source)
        self.assertIn("check=False", source)
        self.assertIn("not a dynamic executable", source)
        self.assertIn('"usr/sbin/ip", "usr/bin/ip"', source)

    def test_variant_matrix_is_exactly_three_experiments(self):
        self.assertEqual(
            list(fit_common.VARIANTS),
            ["40000000-initrd", "40080000-initrd", "40000000-no-initrd"],
        )
        self.assertEqual(fit_common.VARIANTS["40000000-initrd"].kernel_load, 0x40000000)
        self.assertEqual(fit_common.VARIANTS["40080000-initrd"].kernel_load, 0x40080000)
        self.assertFalse(fit_common.VARIANTS["40000000-no-initrd"].with_initrd)

    def test_no_initrd_its_has_no_ramdisk_reference(self):
        its = fit_common.render_its(
            kernel_file="kernel.payload",
            dtb_file="board.dtb",
            kernel_load=0x40000000,
            kernel_compression="lzma",
            with_initrd=False,
            initrd_file=None,
            ramdisk_load=fit_common.RAMDISK_LOAD,
            fdt_load=fit_common.FDT_LOAD,
        )
        self.assertNotIn("ramdisk-1", its)
        self.assertIn('kernel = "kernel-1";', its)
        self.assertIn('fdt = "fdt-1";', its)

    def test_initrd_its_contains_all_three_image_references(self):
        its = fit_common.render_its(
            kernel_file="kernel.payload",
            dtb_file="board.dtb",
            kernel_load=0x40080000,
            kernel_compression="lzma",
            with_initrd=True,
            initrd_file="ramdisk.payload",
            ramdisk_load=fit_common.RAMDISK_LOAD,
            fdt_load=fit_common.FDT_LOAD,
        )
        self.assertIn("ramdisk-1", its)
        self.assertIn('ramdisk = "ramdisk-1";', its)
        self.assertIn("load = <0x40080000>; entry = <0x40080000>;", its)

    def test_raw_image_is_compressed_with_vendor_profile(self):
        raw = bytearray(4096)
        struct.pack_into("<Q", raw, 8, 0)
        struct.pack_into("<Q", raw, 16, len(raw))
        raw[56:60] = b"ARM\x64"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Image"
            source.write_bytes(raw)
            payload, compression, header = fit_common.normalize_kernel(source, "auto")
        self.assertEqual(compression, "lzma")
        self.assertEqual(header["image_size"], len(raw))
        self.assertEqual(lzma.decompress(payload, format=lzma.FORMAT_ALONE), bytes(raw))

    def test_ram_only_bootargs_reject_disk_root(self):
        self.assertNotIn("root=", fit_common.BOOTARGS_DEFAULT.split())
        self.assertNotIn("rw", fit_common.BOOTARGS_DEFAULT.split())
        self.assertIn("panic=0", fit_common.BOOTARGS_DEFAULT.split())

    def test_init_keeps_pid1_outside_sshd(self):
        init = (ROOT / "scripts/ramdiag/assets/init-network-first").read_text()
        self.assertIn("RAMDIAG SUPERVISOR: PID 1 retained", init)
        self.assertIn("service_pid=$!", init)
        self.assertNotIn("exec /bin/sh /usr/lib/ramdiag/services", init)

    def test_watchdog_helper_uses_disable_ioctl(self):
        helper = (ROOT / "scripts/ramdiag/assets/stop-watchdog.py").read_text()
        self.assertIn("WDIOC_SETOPTIONS", helper)
        self.assertIn("WDIOS_DISABLECARD", helper)
        self.assertIn("/dev/watchdog0", helper)

    def test_diagnostic_password_hash_is_sha512_crypt(self):
        build = ROOT / "scripts/ramdiag/build-initrd.py"
        spec = importlib.util.spec_from_file_location("e87n_ramdiag_build_initrd", build)
        self.assertIsNotNone(spec)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertTrue(module.password_hash("doumao").startswith("$6$"))


if __name__ == "__main__":
    unittest.main()
