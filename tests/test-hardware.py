#!/usr/bin/env python3
"""Run with python3 -B tests/test-hardware.py; fixtures only, no devices/root needed."""

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "board-support"))

import e87n
from e87n import hardware
from e87n.__main__ import _main


DEFAULT = {"enabled": True, "brightness_percent": 20, "screen": "overview", "refresh_seconds": 2}
DT = "sys/firmware/devicetree/base"
BL = "sys/devices/platform/backlight/backlight/backlight"
PLATFORM = "sys/devices/platform/backlight"


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="e87n-hardware-")
        self.addCleanup(self.temporary.cleanup)
        # macOS /var is a symlink; injection begins at the canonical fixture root.
        self.root = Path(self.temporary.name).resolve()
        self.hw = hardware._Hardware(_root=self.root, _owner_uid=os.getuid(), _euid=lambda: 0)
        self.config = self.root / "etc/e87n/display.json"
        self.config.parent.mkdir(parents=True)
        self.put(f"{DT}/compatible", b"edgepi,e87n\0mediatek,mt7987\0")
        self.put(f"{DT}/backlight/compatible", b"pwm-backlight\0")
        self.cells(f"{DT}/backlight/pwms", 7, 2, 50000)
        self.cells(f"{DT}/soc/pwm@10048000/phandle", 7)
        self.cells(f"{DT}/soc/pwm@10048000/#pwm-cells", 2)
        self.put(f"{BL}/max_brightness", "26\n")
        self.put(f"{BL}/brightness", "26\n")
        self.put(f"{BL}/bl_power", "0\n")
        (self.root / "sys/bus/platform/drivers/pwm-backlight").mkdir(parents=True)
        self.link("sys/class/backlight/backlight", BL)
        self.link(f"{BL}/device", PLATFORM)
        self.link(f"{PLATFORM}/driver", "sys/bus/platform/drivers/pwm-backlight")
        self.link(f"{PLATFORM}/of_node", f"{DT}/backlight")

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("ascii"))
        return path

    def cells(self, relative, *values):
        return self.put(relative, struct.pack(f">{len(values)}I", *values))

    def link(self, relative, target):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(os.path.relpath(self.root / target, path.parent))
        return path

    def config_data(self, **changes):
        config = dict(DEFAULT, **changes)
        self.put("etc/e87n/display.json", json.dumps(config))
        return config

    def cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = _main(list(args), _hardware=self.hw)
        return result, output.getvalue(), errors.getvalue()

    def brightness(self):
        return int((self.root / BL / "brightness").read_text().strip())

    def assert_rejected(self, *args):
        before = (self.root / BL / "brightness").read_bytes()
        with mock.patch.object(hardware.os, "write", wraps=os.write) as writer:
            result, output, errors = self.cli(*(args or ("display", "apply")))
        self.assertEqual(result, 1, (output, errors))
        self.assertTrue(errors)
        writer.assert_not_called()
        self.assertEqual((self.root / BL / "brightness").read_bytes(), before)

    def populate_samples(self):
        for index, kind, temperature in ((2, "cpu-thermal", 58000), (8, "SOC_thermal", 62000),
                                         (0, "gpu-thermal", 99000)):
            base = f"sys/devices/virtual/thermal/thermal_zone{index}"
            self.put(f"{base}/type", kind)
            self.put(f"{base}/temp", str(temperature))
            self.put(f"{base}/policy", "step_wise\n")
            self.link(f"sys/class/thermal/thermal_zone{index}", base)
        cooling = "sys/devices/virtual/thermal/cooling_device9"
        for attr, value in (("type", "pwm-fan"), ("cur_state", "2"), ("max_state", "3")):
            self.put(f"{cooling}/{attr}", value)
        self.link("sys/class/thermal/cooling_device9", cooling)
        self.link("sys/devices/virtual/thermal/thermal_zone2/cdev0", cooling)
        self.put("sys/class/thermal/cooling_device0/type", "Processor")
        self.put("sys/class/thermal/cooling_device0/cur_state", "99")
        for index, kind, attributes in (
                (3, "mdio_bus:03", {"temp1_input": "43500"}),
                (4, "pwmfan", {"pwm1": "192", "pwm1_enable": "1"}),
                (0, "unrelated", {"temp1_input": "99999", "pwm1": "255", "fan1_input": "9999"})):
            path = f"sys/devices/platform/sample{index}/hwmon/hwmon{index}"
            self.put(f"{path}/name", kind)
            for attr, value in attributes.items():
                self.put(f"{path}/{attr}", value)
            self.link(f"sys/class/hwmon/hwmon{index}", path)
        net = "sys/devices/platform/ethernet/net/end0"
        self.put(f"{net}/statistics/rx_bytes", "123456")
        self.put(f"{net}/statistics/tx_bytes", "987654")
        self.put(f"{net}/carrier", "1")
        self.link("sys/class/net/end0", net)
        self.put("sys/class/net/lo/carrier", "1")
        nvme = "sys/devices/pci0000:00/0000:00:00.0/nvme/nvme0"
        mon = f"{nvme}/hwmon/hwmon7"
        self.put(f"{mon}/name", "nvme")
        self.put(f"{mon}/temp1_input", "37000")
        self.put(f"{mon}/temp1_label", "Composite")
        self.put(f"{mon}/temp2_input", "89000")
        self.put(f"{mon}/temp2_label", "Sensor 1")
        self.link(f"{mon}/device", nvme)
        self.link("sys/class/hwmon/hwmon7", mon)
        self.link("sys/class/nvme/nvme0", nvme)
        self.put("proc/loadavg", "0.50 1.25 2.75 1/123 100\n")
        self.put("proc/meminfo", "MemTotal: 1011132 kB\nMemAvailable: 750000 kB\n")
        self.put("proc/uptime", "123.75 400.00\n")

    def test_snapshot_exact_worker_contract(self):
        self.populate_samples()
        self.assertEqual(self.hw.snapshot(), {
            "cpu_usage_percent": None, "cpu_temp_mc": 62000, "phy_temp_mc": 43500,
            "fan": {"state": 2, "max_state": 3, "pwm": 192, "rpm": None, "policy": "step_wise"},
            "loadavg": [0.5, 1.25, 2.75], "mem_total_kib": 1011132,
            "mem_available_kib": 750000, "uptime_seconds": 123.75,
            "network": [{"name": "end0", "ipv4": None, "ipv6": [],
                         "rx_bytes": 123456, "tx_bytes": 987654, "carrier": 1}],
            "local_ipv4": [],
            "storage": [{"name": "nvme0", "temp_mc": 37000}],
        })

    def test_empty_and_missing_sensors(self):
        result = self.hw.snapshot()
        self.assertEqual(set(result), {"cpu_usage_percent", "cpu_temp_mc", "phy_temp_mc", "fan", "loadavg", "mem_total_kib",
                                       "local_ipv4",
                                       "mem_available_kib", "uptime_seconds", "network", "storage"})
        self.assertEqual(result["loadavg"], [None, None, None])
        self.assertTrue(all(value is None for value in result["fan"].values()))
        self.assertIsNone(result["cpu_temp_mc"])
        self.assertIsNone(result["phy_temp_mc"])
        self.assertEqual(result["network"], [])
        self.assertEqual(result["storage"], [])

    def test_cpu_interval_excludes_guest_and_counts_iowait_as_idle(self):
        self.put("proc/stat", "cpu 100 20 30 700 50 10 20 5 10 2\ncpu0 50 0 0 300\n")
        self.assertIsNone(self.hw.snapshot()["cpu_usage_percent"])
        self.put("proc/stat", "cpu 120 25 45 740 60 15 25 5 30 5\n")
        self.assertEqual(self.hw.snapshot()["cpu_usage_percent"], 50.0)
        self.put("proc/stat", "cpu 120 25 45 840 60 15 25 5 30 5\n")
        self.assertEqual(self.hw.snapshot()["cpu_usage_percent"], 0.0)
        self.put("proc/stat", "cpu 220 25 45 840 60 15 25 5 30 5\n")
        self.assertEqual(self.hw.snapshot()["cpu_usage_percent"], 100.0)
        self.assertIsNone(self.hw.snapshot()["cpu_usage_percent"])

    def test_cpu_bad_missing_and_reset_counters_discard_baseline(self):
        for bad in ("", "cpu0 1 2 3 4", "cpu 1 2 3", "cpu -1 2 3 4",
                    "cpu nan 2 3 4", "cpu 18446744073709551616 2 3 4",
                    "cpu 1 2 3 4 " + "0 " * 7, "cpu 1 2 3 4\n" + "x" * hardware._MAX_BYTES):
            with self.subTest(bad=bad[:80]):
                self.put("proc/stat", "cpu 10 20 30 400\n")
                self.hw.snapshot()
                self.put("proc/stat", bad)
                self.assertIsNone(self.hw.snapshot()["cpu_usage_percent"])
                self.put("proc/stat", "cpu 20 20 30 410\n")
                self.assertIsNone(self.hw.snapshot()["cpu_usage_percent"])
        self.put("proc/stat", "cpu 1 2 3 4\n")
        self.assertIsNone(self.hw.snapshot()["cpu_usage_percent"])
        self.put("proc/stat", "cpu 2 2 3 5\n")
        self.assertEqual(self.hw.snapshot()["cpu_usage_percent"], 50.0)
        (self.root / "proc/stat").unlink()
        self.assertIsNone(self.hw.snapshot()["cpu_usage_percent"])

    def test_public_snapshot_preserves_cpu_baseline_between_frames(self):
        with mock.patch.object(hardware, "_snapshot_backend", None), \
                mock.patch.object(hardware, "_Hardware", return_value=self.hw) as factory:
            self.put("proc/stat", "cpu 10 0 0 10\n")
            self.assertIsNone(hardware.snapshot()["cpu_usage_percent"])
            self.put("proc/stat", "cpu 15 0 0 15\n")
            self.assertEqual(hardware.snapshot()["cpu_usage_percent"], 50.0)
            factory.assert_called_once_with()

    def test_ipv4_ioctl_is_local_read_only_and_closes_socket(self):
        reader = hardware._Hardware()
        reply = bytearray(256)
        struct.pack_into("H", reply, 16, socket.AF_INET)
        reply[20:24] = bytes((192, 0, 2, 87))
        with mock.patch.object(hardware.socket, "socket") as factory, \
                mock.patch.object(hardware.fcntl, "ioctl", return_value=bytes(reply)) as ioctl:
            channel = factory.return_value.__enter__.return_value
            channel.fileno.return_value = 87
            self.assertEqual(reader._ipv4_address("end0"), "192.0.2.87")
            factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
            ioctl.assert_called_once_with(87, 0x8915, struct.pack("256s", b"end0"))
            factory.return_value.__exit__.assert_called_once()
            for action in (channel.bind, channel.connect, channel.send, channel.sendto):
                action.assert_not_called()
            for address in ((127, 0, 0, 1), (0, 0, 0, 0), (224, 0, 0, 1)):
                reply[20:24] = bytes(address)
                ioctl.return_value = bytes(reply)
                self.assertIsNone(reader._ipv4_address("end0"))
            ioctl.return_value = b"short"
            self.assertIsNone(reader._ipv4_address("end0"))
            ioctl.side_effect = OSError("no IPv4 assigned")
            self.assertIsNone(reader._ipv4_address("end0"))
        with mock.patch.object(hardware.socket, "socket", side_effect=OSError("AF_INET blocked")):
            self.assertIsNone(reader._ipv4_address("end0"))
        with mock.patch.object(hardware.socket, "socket", side_effect=AssertionError("host query")):
            self.assertIsNone(self.hw._ipv4_address("end0"))
            self.assertIsNone(reader._ipv4_address("end0;bad"))

    def test_production_proc_net_uses_current_pid_without_following_symlinks(self):
        reader = hardware._Hardware()
        with mock.patch.object(hardware.os, "getpid", return_value=123), \
                mock.patch.object(reader, "_directory", return_value=contextlib.nullcontext(87)) as directory, \
                mock.patch.object(reader, "_read_at", return_value=b"fixture") as read:
            self.assertEqual(reader._text("proc/net/fib_trie"), "fixture")
            directory.assert_called_once_with(Path("proc/123/net"))
            read.assert_called_once_with(87, "fib_trie")
        self.put("outside/fib_trie", " |-- 192.0.2.87\n /32 host LOCAL\n")
        self.link("proc/net", "outside")
        self.assertEqual(self.hw._local_ipv4_addresses(), [])

    def test_ipv6_validated_bounded_and_resampled_with_interfaces(self):
        self.populate_samples()
        self.put("proc/net/if_inet6", "\n".join((
            "20010db8000000000000000000000087 02 40 00 80 end0",
            "fe800000000000000000000000000087 02 40 20 80 end0",
            "20010db8000000000000000000000087 02 40 00 80 end0",  # duplicate
            "20010db8000000000000000000000001 02 40 00 40 end0",  # tentative
            "20010db8000000000000000000000002 02 40 00 08 end0",  # DAD failed
            "20010db8000000000000000000000003 02 40 00 20 end0",  # deprecated
            "20010db8000000000000000000000004 02 ff 00 80 end0",  # invalid prefix
            "00000000000000000000000000000001 01 80 10 80 lo",
            "00000000000000000000000000000000 02 40 00 80 end0",
            "ff020000000000000000000000000001 02 40 00 80 end0", "bad")))
        with mock.patch.object(self.hw, "_ipv4_address", return_value="192.0.2.87"):
            net = self.hw.snapshot()["network"][0]
        self.assertEqual(net["ipv4"], "192.0.2.87")
        self.assertEqual(net["ipv6"], ["2001:db8::87", "fe80::87"])
        self.put("proc/net/if_inet6", "\n".join(
            f"20010db800000000000000000000{index:04x} 02 40 00 80 end0" for index in range(20)))
        self.assertEqual(len(self.hw.snapshot()["network"][0]["ipv6"]), 8)
        self.put("proc/net/if_inet6", "x" * (hardware._MAX_BYTES + 1))
        self.assertEqual(self.hw.snapshot()["network"][0]["ipv6"], [])
        self.assertIsNone(self.hw.snapshot()["network"][0]["ipv4"])

    def test_local_ipv4_fallback_only_host_routes_no_socket_or_writes(self):
        self.populate_samples()
        self.put("proc/net/fib_trie", """Main:
  +-- 0.0.0.0/0 2 0 2
     |-- 0.0.0.0
        /0 universe UNICAST
     |-- 127.0.0.1
        /32 host LOCAL
     |-- 192.0.2.87
        /32 host LOCAL
     |-- 192.0.2.255
        /32 link BROADCAST
     |-- 198.51.100.1
        /32 universe UNICAST
Local:
     |-- 192.0.2.87
        /32 host LOCAL
     |-- 999.0.0.1
        /32 host LOCAL
     |-- 169.254.1.2
        /32 host LOCAL
     |-- 0.0.0.0
        /32 host LOCAL
""")
        with mock.patch.object(hardware.socket, "socket", side_effect=AssertionError("host socket")), \
                mock.patch.object(hardware.os, "write", side_effect=AssertionError("device write")):
            self.assertEqual(self.hw.snapshot()["local_ipv4"], ["192.0.2.87", "169.254.1.2"])
        self.put("proc/net/fib_trie", "\n".join(
            f" |-- 192.0.2.{index}\n /32 host LOCAL" for index in range(1, 65)))
        self.assertEqual(len(self.hw.snapshot()["local_ipv4"]), 32)
        self.put("proc/net/fib_trie", " |-- 192.0.2.87\n /32 host LOCAL\n" + "x" * hardware._MAX_BYTES)
        self.assertEqual(self.hw.snapshot()["local_ipv4"], [])

    def test_only_real_rpm_and_measured_pwm(self):
        self.populate_samples()
        self.assertIsNone(self.hw.snapshot()["fan"]["rpm"])
        self.put("sys/devices/platform/sample4/hwmon/hwmon4/fan1_input", "1234")
        self.assertEqual(self.hw.snapshot()["fan"]["rpm"], 1234)
        (self.root / "sys/devices/platform/sample4/hwmon/hwmon4/pwm1").unlink()
        self.assertIsNone(self.hw.snapshot()["fan"]["pwm"])

    def test_policy_only_from_zone_bound_to_fan(self):
        self.populate_samples()
        (self.root / "sys/devices/virtual/thermal/thermal_zone2/cdev0").unlink()
        self.assertIsNone(self.hw.snapshot()["fan"]["policy"])

    def test_invalid_samples_and_nonfinite_values_are_none(self):
        self.populate_samples()
        self.put("sys/devices/virtual/thermal/thermal_zone2/temp", "bad")
        self.put("sys/devices/virtual/thermal/thermal_zone8/temp", "999999999")
        self.put("sys/devices/platform/sample3/hwmon/hwmon3/temp1_input", "nan")
        self.put("sys/devices/platform/sample4/hwmon/hwmon4/fan1_input", "-1")
        self.put("sys/devices/platform/sample4/hwmon/hwmon4/pwm1", "256")
        self.put("proc/loadavg", "nan inf -2 1/123 4")
        self.put("proc/uptime", "Infinity 4")
        self.put("proc/meminfo", "MemTotal: -1 kB\nMemAvailable: 7 MB\n")
        sample = self.hw.snapshot()
        for key in ("cpu_temp_mc", "phy_temp_mc", "mem_total_kib", "mem_available_kib", "uptime_seconds"):
            self.assertIsNone(sample[key])
        self.assertEqual(sample["loadavg"], [None, None, None])
        self.assertIsNone(sample["fan"]["pwm"])
        self.assertIsNone(sample["fan"]["rpm"])
        json.dumps(sample, allow_nan=False)

    def test_network_is_sampled_again_and_vanished_metric_is_none(self):
        self.populate_samples()
        self.hw.snapshot()
        self.put("sys/devices/platform/ethernet/net/end0/statistics/rx_bytes", "77")
        (self.root / "sys/devices/platform/ethernet/net/end0/carrier").unlink()
        net = self.hw.snapshot()["network"][0]
        self.assertEqual(net["rx_bytes"], 77)
        self.assertIsNone(net["carrier"])

    def test_nvme_composite_only_and_controller_without_hwmon(self):
        self.populate_samples()
        self.put("sys/devices/pci0000:00/0000:00:00.0/nvme/nvme0/hwmon/hwmon7/temp1_label", "Sensor 2")
        (self.root / "sys/class/nvme/nvme1").mkdir()
        self.assertEqual(self.hw.snapshot()["storage"], [
            {"name": "nvme0", "temp_mc": None}, {"name": "nvme1", "temp_mc": None}])

    def test_bounded_samples(self):
        for index in range(80):
            self.put(f"sys/class/net/en{index}/carrier", "1")
        self.put("proc/loadavg", "1" * (hardware._MAX_BYTES + 1))
        self.assertEqual(len(self.hw.snapshot()["network"]), 32)
        self.assertEqual(self.hw.snapshot()["loadavg"], [None, None, None])
        self.assertLessEqual(len(self.hw._entries("sys/class/net", r".*", limit=1000)), hardware._MAX_ENTRIES)

    def test_sensor_symlinks_cannot_escape_selected_sysfs(self):
        self.populate_samples()
        self.put("outside/type", "cpu-thermal")
        self.put("outside/temp", "130000")
        self.link("sys/class/thermal/thermal_zone99", "outside")
        self.link("sys/class/net/evil0", "outside")
        temperature = self.root / "sys/devices/virtual/thermal/thermal_zone8/temp"
        temperature.unlink()
        temperature.symlink_to(self.root / "outside/temp")
        result = self.hw.snapshot()
        self.assertEqual(result["cpu_temp_mc"], 58000)
        self.assertEqual([item["name"] for item in result["network"]], ["end0"])

    def test_status_and_fan_are_read_only_without_board_or_root(self):
        self.populate_samples()
        self.hw.euid = lambda: 1000
        self.put(f"{DT}/compatible", b"other,board\0")
        self.put("etc/e87n/display.json", "invalid json")
        with mock.patch.object(hardware.os, "write", side_effect=AssertionError("unexpected write")), \
                mock.patch.object(hardware.os, "mkdir", side_effect=AssertionError("unexpected mkdir")), \
                mock.patch.object(hardware.os, "replace", side_effect=AssertionError("unexpected replace")):
            for args in (("status",), ("fan", "status")):
                code, output, errors = self.cli(*args)
                self.assertEqual(code, 0, errors)
                self.assertIsInstance(json.loads(output), dict)

    def test_public_api_uses_shared_backend_without_import_io(self):
        with mock.patch.object(hardware, "_snapshot_backend", None), \
                mock.patch.object(hardware, "_Hardware", return_value=self.hw):
            self.assertEqual(e87n.snapshot(), self.hw.snapshot())
            self.assertEqual(e87n.load_display_config(), DEFAULT)

    def test_default_is_independent_and_not_written_by_load(self):
        config = self.hw.load_display_config()
        self.assertEqual(config, DEFAULT)
        config["enabled"] = False
        self.assertEqual(self.hw.load_display_config(), DEFAULT)
        self.assertFalse(self.config.exists())

    def test_default_apply_is_one_shot_without_persisting(self):
        self.config.parent.rmdir()
        code, output, errors = self.cli("display", "apply")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output), DEFAULT)
        self.assertEqual(self.brightness(), 21)
        self.assertFalse(self.config.parent.exists())
        self.assertEqual((self.root / BL / "bl_power").read_text(), "0\n")

    def test_normal_polarity_and_active_low_mapping(self):
        for percent, raw in ((0, 26), (1, 26), (20, 21), (50, 13), (75, 7), (99, 0), (100, 0)):
            with self.subTest(percent=percent):
                code, _, errors = self.cli("display", "brightness", str(percent))
                self.assertEqual(code, 0, errors)
                self.assertEqual(self.brightness(), raw)
                self.assertEqual(self.hw.load_display_config()["brightness_percent"], percent)

    def test_explicit_normal_pwm_flags_supported(self):
        self.cells(f"{DT}/soc/pwm@10048000/#pwm-cells", 3)
        self.cells(f"{DT}/backlight/pwms", 7, 2, 50000, 0)
        self.assertEqual(self.cli("display", "apply")[0], 0)
        self.assertEqual(self.brightness(), 21)

    def test_off_preserves_brightness_and_setters_preserve_off(self):
        self.config_data(brightness_percent=35)
        for args in (("off",), ("brightness", "80"), ("screen", "network"), ("refresh", "5")):
            self.assertEqual(self.cli("display", *args)[0], 0)
            self.assertEqual(self.brightness(), 26)
            self.assertFalse(self.hw.load_display_config()["enabled"])
            self.assertEqual((self.root / BL / "bl_power").read_text(), "0\n")
        self.assertEqual(self.hw.load_display_config()["brightness_percent"], 80)
        self.assertEqual(self.cli("display", "on")[0], 0)
        self.assertEqual(self.brightness(), 5)

    def test_apply_disabled_and_zero_percent_never_power_down(self):
        for config in ({"enabled": False, "brightness_percent": 100}, {"brightness_percent": 0}):
            self.config_data(**config)
            self.put(f"{BL}/bl_power", "4\n")
            self.assertEqual(self.cli("display", "apply")[0], 0)
            self.assertEqual(self.brightness(), 26)
            self.assertEqual((self.root / BL / "bl_power").read_text(), "0\n")

    def test_all_screens_persist(self):
        for screen in ("overview", "thermal", "network", "storage"):
            self.assertEqual(self.cli("display", "screen", screen)[0], 0)
            self.assertEqual(self.hw.load_display_config()["screen"], screen)

    def test_invalid_cli_values_and_no_fan_setters(self):
        cases = [("display", "brightness", value) for value in
                 ("-1", "101", "1.0", "true", "nan", "1e2", "20;touch nope", "$(id)", " 20", "２０")]
        cases += [("fan", "set", "2"), ("fan", "off"), ("fan", "speed", "50"),
                  ("display", "screen", "shell"), ("--root", str(self.root), "status")]
        cases += [("display", "refresh", value) for value in
                  ("0", "1", "61", "-2", "2.0", "nan", "true", "２", " 2", "2;id", "1e1")]
        with mock.patch.object(self.hw, "display") as setter:
            for args in cases:
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        _main(list(args), _hardware=self.hw)
                    self.assertEqual(raised.exception.code, 2)
            setter.assert_not_called()

    def test_display_config_cli_is_read_only_without_root_or_board(self):
        self.hw.euid = lambda: 1000
        self.put(f"{DT}/compatible", b"other,board\0")
        with mock.patch.object(self.hw, "display", side_effect=AssertionError("apply")), \
                mock.patch.object(hardware.os, "write", side_effect=AssertionError("write")), \
                mock.patch.object(hardware.os, "mkdir", side_effect=AssertionError("mkdir")), \
                mock.patch.object(hardware.os, "replace", side_effect=AssertionError("replace")):
            code, output, errors = self.cli("display", "config")
            self.assertEqual(code, 0, errors)
            self.assertEqual(json.loads(output), DEFAULT)
        self.assertFalse(self.config.exists())
        saved = self.config_data(enabled=False, brightness_percent=73, screen="network", refresh_seconds=60)
        self.assertEqual(json.loads(self.cli("display", "config")[1]), saved)
        self.put("etc/e87n/display.json", "invalid")
        code, output, errors = self.cli("display", "config")
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("invalid display config", errors)

    def test_refresh_cli_persists_and_preserves_other_settings(self):
        saved = self.config_data(enabled=False, brightness_percent=73, screen="network")
        for seconds in (2, 5, 60):
            code, output, errors = self.cli("display", "refresh", str(seconds))
            self.assertEqual(code, 0, errors)
            saved["refresh_seconds"] = seconds
            self.assertEqual(json.loads(output), saved)
            self.assertEqual(self.hw.load_display_config(), saved)
            self.assertEqual(self.brightness(), 26)

    def test_display_help_documents_settings_without_hardware(self):
        with mock.patch.object(self.hw, "display", side_effect=AssertionError("apply")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                _main(["display", "--help"], _hardware=self.hw)
        self.assertEqual(raised.exception.code, 0)
        for command in ("config", "refresh", "brightness", "screen", "on", "off", "apply"):
            self.assertIn(command, output.getvalue())

    def test_shipped_config_matches_overview_defaults(self):
        shipped = Path(__file__).resolve().parents[1] / "board-support/display.json"
        self.assertEqual(json.loads(shipped.read_text()), DEFAULT)

    def test_invalid_config_fails_closed(self):
        invalid = ["null", "[]", "{}", "invalid", '{"enabled":true,"enabled":false}',
                   json.dumps(dict(DEFAULT, unknown=1)), "[" * 1500, " " * (hardware._MAX_BYTES + 1)]
        for key, values in {"enabled": [0, 1, "false", None],
                            "brightness_percent": [-1, 101, True, 20.0, "20", None],
                            "screen": ["invalid", 1, [], None],
                            "refresh_seconds": [0, 1, -1, 61, True, 2.0, "2", None, float("nan")]}.items():
            invalid.extend(json.dumps(dict(DEFAULT, **{key: value})) for value in values)
        for data in invalid:
            with self.subTest(data=data[:100]):
                self.put("etc/e87n/display.json", data)
                with self.assertRaises((hardware.HardwareError, OSError)):
                    self.hw.load_display_config()
                self.assert_rejected()
                self.assert_rejected("display", "on")
                self.assertEqual(self.config.read_text(), data)

    def test_config_symlink_dangling_link_and_hardlink_rejected(self):
        target = self.put("outside/config", json.dumps(DEFAULT))
        for destination in (target, self.root / "outside/missing"):
            with self.subTest(destination=destination):
                self.config.symlink_to(destination)
                self.assert_rejected("display", "off")
                self.assertTrue(self.config.is_symlink())
                self.config.unlink()
        os.link(target, self.config)
        self.assert_rejected()
        self.assertEqual(target.read_text(), json.dumps(DEFAULT))

    def test_config_parent_symlinks_rejected(self):
        self.config.parent.rmdir()
        (self.root / "elsewhere").mkdir()
        self.config.parent.symlink_to(self.root / "elsewhere")
        self.assert_rejected("display", "off")
        self.assertEqual(list((self.root / "elsewhere").iterdir()), [])

    def test_config_ancestor_symlink_rejected(self):
        self.config.parent.rmdir()
        (self.root / "etc").rmdir()
        (self.root / "elsewhere/e87n").mkdir(parents=True)
        (self.root / "etc").symlink_to(self.root / "elsewhere")
        self.assert_rejected("display", "on")

    def test_special_config_files_rejected_without_blocking(self):
        os.mkfifo(self.config)
        self.assert_rejected()
        self.config.unlink()
        self.config.mkdir()
        self.assert_rejected()

    def test_config_owner_and_permissions_rejected(self):
        self.config_data()
        self.config.chmod(0o666)
        self.assert_rejected()
        self.config.chmod(0o600)
        self.config.parent.chmod(0o777)
        self.assert_rejected()
        self.config.parent.chmod(0o755)
        self.hw.owner_uid = os.getuid() + 1
        self.assert_rejected()

    def test_non_root_never_writes_config_or_hardware(self):
        self.hw.euid = lambda: 1000
        for command in (("apply",), ("on",), ("off",), ("brightness", "20"), ("screen", "thermal")):
            self.assert_rejected("display", *command)
        self.assertFalse(self.config.exists())

    def test_wrong_or_missing_board_never_writes(self):
        for value in (b"other,board\0", b"edgepi,e87n-other\0", b"edgepi,e87n", b""):
            self.put(f"{DT}/compatible", value)
            self.assert_rejected("display", "brightness", "30")
        (self.root / DT / "compatible").unlink()
        self.assert_rejected("display", "off")
        self.assertFalse(self.config.exists())

    def test_missing_backlight_rejected(self):
        (self.root / "sys/class/backlight/backlight").unlink()
        self.assert_rejected("display", "off")
        self.assertFalse(self.config.exists())

    def test_wrong_backlight_maximum_rejected(self):
        for value in ("255", "0", "25", "27", "bad"):
            self.put(f"{BL}/max_brightness", value)
            self.assert_rejected("display", "on")

    def test_wrong_pwm_channel_period_provider_and_inverted_polarity_rejected(self):
        for spec, count in (((7, 1, 50000), 2), ((7, 2, 40000), 2), ((8, 2, 50000), 2),
                            ((7, 2, 50000, 1), 3), ((7, 2, 50000, 2), 3), ((7, 2), 2)):
            with self.subTest(spec=spec):
                self.cells(f"{DT}/backlight/pwms", *spec)
                self.cells(f"{DT}/soc/pwm@10048000/#pwm-cells", count)
                self.assert_rejected("display", "off")
        self.assertFalse(self.config.exists())

    def test_wrong_device_driver_or_of_node_link_rejected(self):
        for relative in ("sys/class/backlight/backlight", f"{BL}/device",
                         f"{PLATFORM}/driver", f"{PLATFORM}/of_node"):
            path = self.root / relative
            original = os.readlink(path)
            path.unlink()
            (self.root / "wrong-device").mkdir(exist_ok=True)
            path.symlink_to(self.root / "wrong-device")
            self.assert_rejected("display", "off")
            path.unlink()
            path.symlink_to(original)

    def test_wrong_device_tree_compatible_rejected(self):
        self.put(f"{DT}/backlight/compatible", b"other-backlight\0")
        self.assert_rejected()

    def test_backlight_attribute_symlinks_never_followed(self):
        target = self.put("outside/victim", "26\n")
        for name in ("brightness", "bl_power", "max_brightness"):
            path = self.root / BL / name
            old = path.read_bytes()
            path.unlink()
            path.symlink_to(target)
            self.assert_rejected("display", "off")
            self.assertEqual(target.read_bytes(), b"26\n")
            path.unlink()
            path.write_bytes(old)

    def test_device_tree_leaf_symlink_rejected(self):
        path = self.root / DT / "compatible"
        victim = self.put("outside/compatible", path.read_bytes())
        path.unlink()
        path.symlink_to(victim)
        self.assert_rejected()

    def test_persist_flush_rename_directory_flush_then_apply(self):
        self.config_data()
        old_inode = self.config.stat().st_ino
        events = []
        real_fsync, real_replace = os.fsync, os.replace
        real_apply = self.hw._apply_config

        def fsync(fd):
            events.append("dir-sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file-sync")
            real_fsync(fd)

        def replace(*args, **kwargs):
            events.append("replace")
            return real_replace(*args, **kwargs)

        def apply(config, descriptors):
            events.append("apply")
            self.assertEqual(self.hw.load_display_config(), config)
            return real_apply(config, descriptors)

        with mock.patch.object(hardware.os, "fsync", side_effect=fsync), \
                mock.patch.object(hardware.os, "replace", side_effect=replace), \
                mock.patch.object(self.hw, "_apply_config", side_effect=apply):
            self.assertEqual(self.cli("display", "brightness", "40")[0], 0)
        self.assertEqual(events, ["file-sync", "replace", "dir-sync", "apply"])
        self.assertNotEqual(self.config.stat().st_ino, old_inode)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(self.config.stat().st_uid, os.getuid())
        self.assertEqual([p.name for p in self.config.parent.iterdir()], ["display.json"])

    def test_persist_failure_does_not_apply_and_cleans_temporary(self):
        original = self.config_data()
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation), \
                    mock.patch.object(hardware.os, operation, side_effect=OSError("disk failure")):
                self.assert_rejected("display", "off")
            self.assertEqual(self.hw.load_display_config(), original)
            self.assertEqual([p.name for p in self.config.parent.iterdir()], ["display.json"])

    def test_apply_failure_keeps_persisted_intent_and_exits_nonzero(self):
        with mock.patch.object(self.hw, "_apply_config", side_effect=OSError("device vanished")):
            code, _, errors = self.cli("display", "off")
        self.assertEqual(code, 1)
        self.assertIn("device vanished", errors)
        self.assertFalse(self.hw.load_display_config()["enabled"])
        self.assertEqual(self.brightness(), 26)

    def test_apply_enoent_does_not_retry_with_defaults(self):
        self.config_data(enabled=False)
        with mock.patch.object(self.hw, "_apply_config", side_effect=FileNotFoundError("gone")) as apply:
            self.assertEqual(self.cli("display", "apply")[0], 1)
        apply.assert_called_once()
        self.assertFalse(apply.call_args.args[0]["enabled"])

    def test_refresh_interval_inclusive_boundaries(self):
        for refresh in (2, 60):
            self.config_data(refresh_seconds=refresh)
            self.assertEqual(self.hw.load_display_config()["refresh_seconds"], refresh)
            self.assertEqual(self.cli("display", "apply")[0], 0)

    def test_apply_with_read_only_config_never_persists(self):
        saved = self.config_data(enabled=False, brightness_percent=73, refresh_seconds=60)
        before = self.config.read_bytes()
        metadata = self.config.stat()
        self.config.chmod(0o444)
        self.config.parent.chmod(0o555)
        self.addCleanup(self.config.parent.chmod, 0o755)
        with mock.patch.object(hardware.os, "replace", side_effect=AssertionError("config replace")), \
                mock.patch.object(hardware.os, "mkdir", side_effect=AssertionError("config mkdir")), \
                mock.patch.object(hardware.os, "fsync", side_effect=AssertionError("config fsync")), \
                mock.patch.object(self.hw, "_save_config_at", side_effect=AssertionError("config save")):
            code, output, errors = self.cli("display", "apply")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output), saved)
        self.assertEqual(self.brightness(), 26)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.config.stat().st_ino, metadata.st_ino)
        self.assertEqual(self.config.stat().st_mtime_ns, metadata.st_mtime_ns)

    def test_short_hardware_write_is_failure(self):
        with mock.patch.object(hardware.os, "write", return_value=0):
            self.assertEqual(self.cli("display", "off")[0], 1)
        self.assertFalse(self.hw.load_display_config()["enabled"])

    def test_no_fan_writes_from_any_command(self):
        self.populate_samples()
        writes = []
        real_write = os.write
        allowed = {(self.root / BL / name).stat().st_ino: name for name in ("brightness", "bl_power")}
        fan_files = [self.root / "sys/devices/platform/sample4/hwmon/hwmon4" / name
                     for name in ("pwm1", "pwm1_enable")]
        fan_files.append(self.root / "sys/devices/virtual/thermal/cooling_device9/cur_state")
        before = {path: path.read_bytes() for path in fan_files}

        def write(fd, data):
            name = allowed[os.fstat(fd).st_ino]
            writes.append((name, data))
            return real_write(fd, data)

        with mock.patch.object(hardware.os, "write", side_effect=write):
            for args in (("status",), ("fan", "status"), ("display", "off"),
                         ("display", "brightness", "20"), ("display", "screen", "thermal"),
                         ("display", "config"), ("display", "refresh", "5"),
                         ("display", "on"), ("display", "apply")):
                self.assertEqual(self.cli(*args)[0], 0)
        self.assertTrue(writes)
        self.assertTrue(all(data == b"0\n" for name, data in writes if name == "bl_power"))
        self.assertEqual({path: path.read_bytes() for path in fan_files}, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
