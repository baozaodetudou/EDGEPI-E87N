#!/usr/bin/env python3
"""python3 -B tests/test-doctor.py: stdlib, temporary fixtures, no live probes."""

from contextlib import ExitStack
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "board-support"))
from e87n import doctor as diagnostics


CHECKS = {
    "root_access", "os", "kernel", "device_tree", "memory", "root_filesystem",
    "cmdline", "network", "thermal", "fan", "nv3007", "kernel_config",
    "kernel_modules", "containers", "storage",
}
DT = "sys/firmware/devicetree/base"
NET = "sys/devices/platform/ethernet/net/end0"
ZONE = "sys/devices/virtual/thermal/thermal_zone2"
COOLING = "sys/devices/virtual/thermal/cooling_device4"
HWMON = "sys/devices/platform/fan/hwmon/hwmon3"
FB = "sys/devices/platform/spi/spi0.0/graphics/fb0"
SYMBOLS = (
    "CGROUPS MEMCG CGROUP_PIDS NAMESPACES UTS_NS IPC_NS PID_NS NET_NS USER_NS "
    "SECCOMP SECCOMP_FILTER VETH BRIDGE BRIDGE_NETFILTER NF_TABLES NF_NAT OVERLAY_FS "
    "EXT4_FS MMC MMC_BLOCK MMC_MTK BLK_DEV_NVME SCSI BLK_DEV_SD USB_STORAGE USB_UAS "
    "BTRFS_FS FB_TFT FB_TFT_NV3007 SENSORS_PWM_FAN"
).split()
CONFIG = "# Linux/arm64 6.18.52 Kernel Configuration\n" + "".join(
    f"CONFIG_{symbol}={'m' if symbol in ('VETH', 'OVERLAY_FS', 'FB_TFT_NV3007') else 'y'}\n"
    for symbol in SYMBOLS)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="e87n-doctor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("ascii"))
        return path

    def link(self, relative, target, *, absolute=False):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("/" + target if absolute else os.path.relpath(self.root / target, path.parent))
        return path

    def populate(self):
        self.put("usr/lib/os-release", 'ID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n')
        self.link("etc/os-release", "usr/lib/os-release")
        self.put("proc/sys/kernel/osrelease", "6.18.52-current-edgepi-e87n\n")
        self.put(f"{DT}/model", b"EdgePi E87N\0")
        self.put(f"{DT}/compatible", b"edgepi,e87n\0mediatek,mt7987\0")
        self.put("proc/meminfo", "MemTotal: 1011132 kB\nMemAvailable: 750000 kB\n")
        self.put("proc/123/mountinfo", "12 1 8:2 / / rw,relatime - ext4 /dev/disk/by-uuid/PRIVATE rw\n")
        self.link("proc/self", "proc/123")
        self.put("proc/cmdline", "console=ttyS0 root=UUID=PRIVATE rootwait rw\n")
        for attr, value in (("carrier", "1"), ("addr_assign_type", "0"), ("operstate", "up")):
            self.put(f"{NET}/{attr}", value)
        self.link("sys/class/net/end0", NET)
        self.put("sys/class/net/lo/carrier", "1")
        self.put(f"{ZONE}/type", "cpu-thermal\n")
        self.put(f"{ZONE}/temp", "58000\n")
        self.link("sys/class/thermal/thermal_zone2", ZONE)
        for attr, value in (("type", "pwm-fan"), ("cur_state", "2"), ("max_state", "3")):
            self.put(f"{COOLING}/{attr}", value)
        self.link("sys/class/thermal/cooling_device4", COOLING)
        self.put(f"{HWMON}/name", "pwmfan\n")
        self.put(f"{HWMON}/pwm1", "192\n")
        self.link("sys/class/hwmon/hwmon3", HWMON)
        self.put(f"{FB}/name", "fb_nv3007\n")
        self.put(f"{FB}/virtual_size", "428,142\n")
        self.put(f"{FB}/bits_per_pixel", "16\n")
        self.link("sys/class/graphics/fb0", FB)
        self.put("proc/config.gz", gzip.compress(CONFIG.encode("ascii")))
        self.put("proc/modules", "overlay 20480 0 - Live 0xffff000012345678\nfb_nv3007 16384 1 - Live 0xffff000087654321\n")

    def report(self, root=None):
        """Every os.open/dup must stay in the fixture; reject mutation APIs."""
        opened = set()
        original_open, original_dup, original_close = os.open, os.dup, os.close

        def checked_open(path, flags, mode=0o777, *, dir_fd=None):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if dir_fd is None:
                self.assertEqual(Path(path), self.root)
            else:
                self.assertIn(dir_fd, opened)
                self.assertNotIn("/", os.fspath(path))
                self.assertNotIn(os.fspath(path), (".", ".."))
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            opened.add(fd)
            return fd

        def checked_dup(fd):
            self.assertIn(fd, opened)
            child = original_dup(fd)
            opened.add(child)
            return child

        def checked_close(fd):
            self.assertIn(fd, opened)
            opened.remove(fd)
            return original_close(fd)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(diagnostics.os, "open", side_effect=checked_open))
            stack.enter_context(mock.patch.object(diagnostics.os, "dup", side_effect=checked_dup))
            stack.enter_context(mock.patch.object(diagnostics.os, "close", side_effect=checked_close))
            for name in ("write", "mkdir", "makedirs", "unlink", "remove", "rename", "replace",
                         "chmod", "chown", "symlink", "link", "system", "popen"):
                stack.enter_context(mock.patch.object(diagnostics.os, name, side_effect=AssertionError("mutation or process launch")))
            result = diagnostics.doctor(self.root if root is None else root)
        self.assertEqual(opened, set(), "diagnostic descriptors leaked")
        self.assertEqual(set(result["checks"]), CHECKS)
        self.assertEqual(result["hardware_validation"], "not-performed")
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        self.assertEqual(result["warnings"], [name for name, check in result["checks"].items()
                                            if check["status"] in ("warning", "unknown")])
        self.assertEqual(result["errors"], [name for name, check in result["checks"].items()
                                          if check["status"] == "error"])
        return result

    def check(self, name):
        return self.report()["checks"][name]

    def test_all_observations_ok_is_still_not_hardware_validation(self):
        self.populate()
        report = self.report()
        self.assertEqual((report["status"], report["exit_code"]), ("ok", 0))
        self.assertEqual(report["warnings"], [])
        self.assertTrue(all(item["status"] == "ok" for item in report["checks"].values()))
        self.assertEqual(report["checks"]["memory"]["details"]["mem_total_kib"], 1011132)
        self.assertIsNone(report["checks"]["fan"]["details"]["hwmon"][0]["rpm"])

    def test_missing_data_is_explicit_and_does_not_probe_host(self):
        report = self.report()
        self.assertEqual((report["status"], report["exit_code"]), ("warning", 1))
        self.assertIsNone(report["checks"]["memory"]["details"]["mem_total_kib"])
        self.assertIsNone(report["checks"]["kernel"]["details"]["version"])
        self.assertIsNone(report["checks"]["root_filesystem"]["details"]["root_present"])
        self.assertIsNone(report["checks"]["kernel_modules"]["details"]["loaded"]["overlay"])
        self.assertEqual(report["checks"]["network"]["details"]["interfaces"], [])
        self.assertIsNone(report["checks"]["network"]["details"]["any_carrier"])
        self.assertIsNone(report["checks"]["network"]["details"]["random_address_observed"])
        self.assertIsNone(report["checks"]["cmdline"]["details"]["multiple_root_arguments"])
        self.assertIsNone(report["checks"]["cmdline"]["details"]["conflicting_ro_rw"])

    def test_invalid_roots_return_finite_error_reports(self):
        for root in ("relative", self.root / "..", self.root / "absent", "\0", 42):
            with self.subTest(root=root):
                report = self.report(root=root)
                self.assertEqual((report["status"], report["exit_code"]), ("error", 2))
                self.assertIn("root_access", report["errors"])

    def test_import_has_no_diagnostic_io_or_writes(self):
        source = Path(diagnostics.__file__).read_text()
        code = compile(source, diagnostics.__file__, "exec")
        with mock.patch.object(os, "open", side_effect=AssertionError("import opened a path")), \
                mock.patch.object(os, "write", side_effect=AssertionError("import wrote")), \
                mock.patch.object(Path, "resolve", side_effect=AssertionError("import resolved a path")):
            namespace = {"__name__": "doctor_import_test"}
            exec(code, namespace)
        self.assertTrue(callable(namespace["doctor"]))

    def test_wrong_os_kernel_and_board_are_errors(self):
        self.populate()
        self.put("usr/lib/os-release", "ID=openwrt\nVERSION_ID=24\nVERSION_CODENAME=other\n")
        self.put("proc/sys/kernel/osrelease", "6.18.520-current-edgepi-e87n\n")
        self.put(f"{DT}/compatible", b"unrelated,board\0")
        report = self.report()
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(set(report["errors"]), {"os", "kernel", "device_tree"})

    def test_os_release_point_version_quotes_and_missing_codename(self):
        self.put("etc/os-release", "ID='debian'\nVERSION_ID=\"13.6\" # comment\n")
        check = self.check("os")
        self.assertEqual(check["status"], "ok")
        self.assertIsNone(check["details"]["trixie"])

    def test_ambiguous_or_shell_os_release_is_not_executed_or_accepted(self):
        for data in ('ID=debian\nID=debian\nVERSION_ID=13\n',
                     'ID=debian\nVERSION_ID="13\n',
                     'ID=$(touch /SHOULD_NOT_EXIST)\nVERSION_ID=13\n'):
            with self.subTest(data=data):
                self.put("etc/os-release", data)
                self.assertNotEqual(self.check("os")["status"], "ok")

    def test_bad_kernel_release_does_not_select_boot_config_path(self):
        for release in ("6.18.52/../../etc/shadow", "6.18.52\n6.18.52", "garbage", "6.18.52evil"):
            with self.subTest(release=release):
                self.put("proc/sys/kernel/osrelease", release)
                report = self.report()
                self.assertEqual(report["checks"]["kernel"]["status"], "unknown")
                self.assertIsNone(report["checks"]["kernel_config"]["details"]["source"])

    def test_dt_strings_require_termination_and_exact_identity(self):
        for compatible in (b"edgepi,e87n", b"edgepi,e87n-evil\0", b""):
            with self.subTest(compatible=compatible):
                self.put(f"{DT}/compatible", compatible)
                self.put(f"{DT}/model", b"EdgePi E87N\0")
                self.assertNotEqual(self.check("device_tree")["status"], "ok")

    def test_memory_boundary_and_observed_total_not_reference_total(self):
        for total, expected in ((1, "warning"), (262143, "warning"), (262144, "warning"),
                                (262145, "ok"), (1011132, "ok")):
            with self.subTest(total=total):
                self.put("proc/meminfo", f"MemTotal: {total} kB\n")
                check = self.check("memory")
                self.assertEqual(check["status"], expected)
                self.assertEqual(check["details"]["mem_total_kib"], total)

    def test_invalid_or_duplicate_memory_is_unknown(self):
        for text in ("MemTotal: 0 kB", "MemTotal: -1 kB", "MemTotal: 123 MB",
                     "MemTotal: NaN kB", "MemTotal: 99999999999999999 kB",
                     "MemTotal: 123 kB\nMemTotal: 456 kB", "MemAvailable: 123 kB"):
            with self.subTest(text=text):
                self.put("proc/meminfo", text)
                check = self.check("memory")
                self.assertEqual(check["status"], "unknown")
                self.assertIsNone(check["details"]["mem_total_kib"])

    def test_mounts_fallback_and_read_only_root(self):
        self.put("proc/mounts", "SECRET / ext4 ro,relatime 0 0\n")
        check = self.check("root_filesystem")
        self.assertEqual(check["status"], "warning")
        self.assertEqual(check["details"]["source"], "/proc/mounts")
        self.assertTrue(check["details"]["read_only"])
        self.assertEqual(check["details"]["filesystem"], "ext4")

    def test_no_root_and_stacked_roots_are_not_fabricated(self):
        for text, status, present in (("none /tmp tmpfs rw 0 0", "warning", False),
                                      ("x / ext4 rw 0 0\ny / overlay rw 0 0", "unknown", True)):
            with self.subTest(text=text):
                self.put("proc/mounts", text)
                check = self.check("root_filesystem")
                self.assertEqual(check["status"], status)
                self.assertEqual(check["details"]["root_present"], present)
                self.assertIsNone(check["details"]["filesystem"])

    def test_read_only_superblock_overrides_rw_mount_flag(self):
        self.put("proc/self/mountinfo", "12 1 8:2 / / rw,relatime - ext4 SECRET ro,errors=remount-ro\n")
        check = self.check("root_filesystem")
        self.assertEqual(check["status"], "warning")
        self.assertTrue(check["details"]["read_only"])

    def test_cmdline_presence_and_memory_limit_warning(self):
        for cmdline in ("", "console=ttyS0", "root=", "root=PRIVATE mem=256M",
                        "root=PRIVATE root=OTHER", "root=PRIVATE rw ro"):
            with self.subTest(cmdline=cmdline):
                self.put("proc/cmdline", cmdline)
                self.assertEqual(self.check("cmdline")["status"], "warning")
        self.put("proc/cmdline", 'root="unfinished')
        self.assertEqual(self.check("cmdline")["status"], "unknown")

    def test_network_address_provenance_no_mac(self):
        self.populate()
        for number, meaning, status in ((0, "permanent", "ok"), (1, "random", "warning"),
                                        (2, "inherited", "ok"), (3, "set", "ok")):
            with self.subTest(number=number):
                self.put(f"{NET}/addr_assign_type", str(number))
                check = self.check("network")
                self.assertEqual(check["status"], status)
                self.assertEqual(check["details"]["interfaces"][0]["address_assignment"], meaning)

    def test_unused_network_port_does_not_hide_working_carrier(self):
        self.populate()
        for attr, value in (("carrier", "0"), ("addr_assign_type", "0"), ("operstate", "down")):
            self.put(f"sys/class/net/end1/{attr}", value)
        self.assertEqual(self.check("network")["status"], "ok")
        self.put(f"{NET}/carrier", "0")
        self.assertEqual(self.check("network")["status"], "warning")

    def test_missing_invalid_carrier_and_assignment_stay_null(self):
        self.populate()
        (self.root / NET / "carrier").unlink()
        self.put(f"{NET}/addr_assign_type", "99")
        check = self.check("network")
        self.assertEqual(check["status"], "warning")
        self.assertIsNone(check["details"]["interfaces"][0]["carrier"])
        self.assertIsNone(check["details"]["interfaces"][0]["addr_assign_type"])

    def test_thermal_and_fan_values_do_not_imply_rotation(self):
        self.populate()
        self.put(f"{ZONE}/temp", "-1000")
        self.put(f"{COOLING}/cur_state", "0")
        self.put(f"{HWMON}/pwm1", "0")
        report = self.report()
        self.assertEqual(report["checks"]["thermal"]["details"]["zones"][0]["temp_mc"], -1000)
        self.assertEqual(report["checks"]["fan"]["details"]["hwmon"], [{"pwm": 0, "rpm": None}])
        self.assertNotIn("passed", json.dumps(report))

    def test_invalid_sensors_and_unrelated_fans_are_not_substituted(self):
        self.populate()
        self.put(f"{ZONE}/temp", "200001")
        self.put(f"{COOLING}/cur_state", "4")
        self.put(f"{HWMON}/name", "unrelated")
        self.put(f"{HWMON}/fan1_input", "2000")
        report = self.report()
        self.assertEqual(report["checks"]["thermal"]["status"], "warning")
        self.assertEqual(report["checks"]["fan"]["status"], "warning")
        self.assertIsNone(report["checks"]["fan"]["details"]["cooling_devices"][0]["state"])
        self.assertEqual(report["checks"]["fan"]["details"]["hwmon"], [])

    def test_nv3007_module_and_dt_presence_are_not_framebuffer_availability(self):
        self.populate()
        self.put(f"{FB}/name", "simpledrm")
        self.put(f"{DT}/panel/compatible", b"newvisionu,nv3007\0")
        check = self.check("nv3007")
        self.assertEqual(check["status"], "warning")
        self.assertTrue(check["details"]["module_loaded"])
        self.assertEqual(check["details"]["framebuffers"], [])

    def test_nv3007_wrong_geometry_and_missing_bpp_warn(self):
        self.populate()
        self.put(f"{FB}/virtual_size", "142,428")
        (self.root / FB / "bits_per_pixel").unlink()
        check = self.check("nv3007")
        self.assertEqual(check["status"], "warning")
        self.assertEqual(check["details"]["framebuffers"], [{"size": [142, 428], "bits_per_pixel": None}])

    def test_runtime_config_distinguishes_builtin_module_disabled_and_missing(self):
        self.populate()
        config = CONFIG.replace("CONFIG_MEMCG=y", "# CONFIG_MEMCG is not set").replace("CONFIG_USER_NS=y\n", "")
        self.put("proc/config.gz", gzip.compress(config.encode()))
        check = self.check("containers")
        self.assertEqual(check["status"], "warning")
        self.assertEqual(check["details"]["config"]["CONFIG_MEMCG"], "n")
        self.assertIsNone(check["details"]["config"]["CONFIG_USER_NS"])
        self.assertEqual(check["details"]["config"]["CONFIG_OVERLAY_FS"], "m")
        self.assertEqual(check["details"]["config"]["CONFIG_CGROUPS"], "y")

    def test_duplicate_config_symbol_is_unknown(self):
        for value in ("y", "n", "invalid", "", "y\nCONFIG_CGROUPS=y"):
            with self.subTest(value=value):
                self.put("proc/config", CONFIG + f"CONFIG_CGROUPS={value}\n")
                check = self.check("containers")
                self.assertEqual(check["status"], "unknown")
                self.assertIsNone(check["details"]["config"]["CONFIG_CGROUPS"])

    def test_config_fallback_uses_exact_running_release_and_labels_provenance(self):
        self.put("proc/sys/kernel/osrelease", "6.18.52-current-edgepi-e87n")
        self.put("boot/config-6.18.50-current-edgepi-e87n", CONFIG)
        self.assertEqual(self.check("kernel_config")["status"], "unknown")
        self.put("boot/config-6.18.52-current-edgepi-e87n", CONFIG)
        report = self.report()
        self.assertFalse(report["checks"]["kernel_config"]["details"]["running_kernel_source"])
        self.assertEqual(report["checks"]["containers"]["status"], "warning")
        self.assertEqual(report["checks"]["storage"]["status"], "warning")

    def test_runtime_config_takes_precedence_over_boot_copy(self):
        self.populate()
        self.put("boot/config-6.18.52-current-edgepi-e87n", CONFIG.replace("CONFIG_MMC=y", "CONFIG_MMC=n"))
        self.assertEqual(self.check("storage")["details"]["config"]["CONFIG_MMC"], "y")

    def test_corrupt_or_expanding_gzip_never_uses_partial_config(self):
        for data in (b"not gzip", gzip.compress(CONFIG.encode())[:-5],
                     gzip.compress(CONFIG.encode() + b"#" * (1024 * 1024 + 1))):
            with self.subTest(length=len(data)):
                self.put("proc/config.gz", data)
                self.assertEqual(self.check("kernel_config")["status"], "unknown")
                self.assertIsNone(self.check("containers")["details"]["config"]["CONFIG_CGROUPS"])

    def test_empty_modules_means_none_loaded_not_no_builtin_support(self):
        self.populate()
        self.put("proc/modules", "")
        report = self.report()
        self.assertFalse(report["checks"]["kernel_modules"]["details"]["loaded"]["nvme"])
        self.assertEqual(report["checks"]["storage"]["status"], "ok")
        self.assertEqual(report["checks"]["nv3007"]["status"], "ok")

    def test_malformed_module_list_reports_unknown(self):
        self.put("proc/modules", "overlay\n")
        check = self.check("kernel_modules")
        self.assertEqual(check["status"], "unknown")
        self.assertIsNone(check["details"]["loaded"]["overlay"])

    def test_absolute_symlinks_are_rebased_inside_fixture(self):
        self.populate()
        (self.root / "etc/os-release").unlink()
        self.link("etc/os-release", "usr/lib/os-release", absolute=True)
        (self.root / "sys/class/net/end0").unlink()
        self.link("sys/class/net/end0", NET, absolute=True)
        self.assertEqual(self.report()["exit_code"], 0)

    def test_absolute_fixture_local_links_are_accepted(self):
        self.populate()
        (self.root / "sys/class/net/end0").unlink()
        (self.root / "sys/class/net/end0").symlink_to(self.root / NET)
        self.assertEqual(self.check("network")["status"], "ok")

    def test_parent_directory_symlink_cannot_escape(self):
        path = self.root / "sys"
        path.mkdir()
        (path / "class").symlink_to("../../outside")
        report = self.report()
        self.assertFalse(report["checks"]["network"]["details"]["enumeration_complete"])
        self.assertFalse(report["checks"]["fan"]["details"]["enumeration_complete"])

    def test_too_many_internal_symlinks_are_bounded(self):
        for index in range(17):
            self.link("proc/meminfo" if index == 0 else f"proc/link{index}", f"proc/link{index + 1}")
        self.put("proc/link17", "MemTotal: 1011132 kB")
        self.assertIsNone(self.check("memory")["details"]["mem_total_kib"])

    def test_relative_escape_and_cross_tree_links_are_rejected(self):
        self.put("outside/carrier", "1")
        self.put("outside/addr_assign_type", "0")
        self.link("sys/class/net/end0", "outside")
        self.put("sys/class/net/end1/carrier", "1")
        (self.root / "sys/class/net/end1/addr_assign_type").symlink_to("../../../../../../outside/addr_assign_type")
        check = self.check("network")
        self.assertEqual(check["status"], "warning")
        self.assertIsNone(check["details"]["interfaces"][0]["carrier"])
        self.assertIsNone(check["details"]["interfaces"][1]["addr_assign_type"])

    def test_host_absolute_link_cannot_open_host_pseudo_files(self):
        self.link("proc/meminfo", "proc/meminfo", absolute=True)
        self.link("sys/class/net/end0", "sys/class/net/lo", absolute=True)
        report = self.report()
        self.assertIsNone(report["checks"]["memory"]["details"]["mem_total_kib"])
        self.assertIsNone(report["checks"]["network"]["details"]["interfaces"][0]["carrier"])

    def test_os_release_symlink_cannot_select_arbitrary_fixture_file(self):
        self.put("etc/ssh/private", "ID=debian\nVERSION_ID=13\n")
        self.link("etc/os-release", "etc/ssh/private")
        self.assertEqual(self.check("os")["status"], "unknown")

    def test_symlink_swap_between_stat_and_open_is_not_followed(self):
        path = self.put("proc/meminfo", "MemTotal: 1011132 kB")
        self.put("outside", "MemTotal: 123 kB")
        original_stat = os.stat
        replaced = False

        def swapping_stat(name, *args, **kwargs):
            nonlocal replaced
            info = original_stat(name, *args, **kwargs)
            if name == "meminfo" and kwargs.get("dir_fd") is not None and not replaced:
                replaced = True
                original_unlink(path)
                original_symlink("../outside", path)
            return info

        original_unlink, original_symlink = os.unlink, os.symlink
        with mock.patch.object(os, "stat", side_effect=swapping_stat):
            check = self.check("memory")
        self.assertTrue(replaced)
        self.assertIsNone(check["details"]["mem_total_kib"])

    def test_fifo_and_directory_attributes_are_not_opened_for_read(self):
        path = self.root / "proc/meminfo"
        path.parent.mkdir(parents=True)
        os.mkfifo(path)
        self.assertIsNone(self.check("memory")["details"]["mem_total_kib"])
        path.unlink()
        path.mkdir()
        self.assertIsNone(self.check("memory")["details"]["mem_total_kib"])

    def test_oversized_attributes_do_not_accept_valid_prefixes(self):
        self.put("proc/meminfo", "MemTotal: 1011132 kB\n" + " " * 16384)
        self.put("proc/config", CONFIG + "#" * (1024 * 1024))
        report = self.report()
        self.assertIsNone(report["checks"]["memory"]["details"]["mem_total_kib"])
        self.assertEqual(report["checks"]["kernel_config"]["status"], "unknown")

    def test_enumeration_cap_is_explicit(self):
        for index in range(65):
            self.put(f"sys/class/net/eth{index}/carrier", "1")
            self.put(f"sys/class/net/eth{index}/addr_assign_type", "0")
        check = self.check("network")
        self.assertEqual(check["status"], "warning")
        self.assertFalse(check["details"]["enumeration_complete"])
        self.assertEqual(len(check["details"]["interfaces"]), 64)

    def test_non_ascii_attributes_remain_unknown(self):
        self.put("proc/meminfo", b"MemTotal: \xff kB")
        self.assertIsNone(self.check("memory")["details"]["mem_total_kib"])

    def test_unreadable_attributes_are_unknown_without_exception_details(self):
        self.populate()
        original_read = os.read

        def failed_read(fd, size):
            raise PermissionError("PRIVATE_PATH_OR_SECRET")

        with mock.patch.object(diagnostics.os, "read", side_effect=failed_read):
            report = self.report()
        self.assertIsNone(report["checks"]["memory"]["details"]["mem_total_kib"])
        self.assertEqual(report["exit_code"], 1)
        self.assertNotIn("PRIVATE_PATH_OR_SECRET", json.dumps(report))
        self.assertIs(os.read, original_read)

    def test_no_addresses_keys_unique_ids_paths_or_raw_values_in_json(self):
        self.populate()
        secret = "PRIVATE_UNIQUE_SECRET"
        self.put("usr/lib/os-release", "ID=debian\nVERSION_ID=13\nPRETTY_NAME=" + secret)
        self.put("proc/cmdline", f"root=UUID={secret} ip=192.0.2.42 password={secret} ssh_key={secret} rw")
        self.put("proc/sys/kernel/osrelease", "6.18.52-" + secret)
        self.put(f"{DT}/serial-number", secret)
        self.put(f"{NET}/address", "00:11:22:33:44:55")
        self.link("sys/class/net/enx001122334455", NET)
        self.put(f"{ZONE}/type", "cpu-" + secret)
        encoded = json.dumps(self.report())
        for private in (secret, "00:11:22:33:44:55", "enx001122334455", "192.0.2.42",
                        "ffff000012345678", "PRIVATE", str(self.root)):
            self.assertNotIn(private, encoded)

    def test_call_does_not_change_fixture_files(self):
        self.populate()

        def inventory():
            return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                    for path in self.root.rglob("*") if not path.is_symlink() and path.is_file()}

        before = inventory()
        self.report()
        self.assertEqual(inventory(), before)


if __name__ == "__main__":
    unittest.main()
