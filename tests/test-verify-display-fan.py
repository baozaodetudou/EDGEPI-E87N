#!/usr/bin/env python3
"""Host-only fixtures: python3 -B tests/test-verify-display-fan.py.

All fixture writes are confined to temporary directories. No Git, devices, VMs,
mounts or target execution. Unit tests inject an Fdt adapter; the optional CLI
integration test compiles a synthetic DTS with host dtc and uses real fdtget.
"""

import sys

sys.dont_write_bytecode = True

import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-display-fan.py"
SPEC = importlib.util.spec_from_file_location("verify_display_fan", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)  # the HOST verifier, never a target module

# Keep the contract independent from implementation constants.
BUILTINS = """SPI SPI_MASTER SPI_MT65XX STAGING FB FB_DEVICE BACKLIGHT_CLASS_DEVICE
BACKLIGHT_PWM THERMAL MTK_LVTS_THERMAL PWM PWM_MEDIATEK HWMON SENSORS_PWM_FAN""".split()
CONFIG = ("# Linux/arm64 6.18.52 Kernel Configuration\n" +
          "".join("CONFIG_%s=y\n" % name for name in BUILTINS) +
          "CONFIG_FB_TFT=m\nCONFIG_FB_TFT_NV3007=m\n" +
          "# CONFIG_FRAMEBUFFER_CONSOLE is not set\n# CONFIG_CPU_FREQ is not set\n")
DEFAULT = ('{"enabled":true,"brightness_percent":20,"screen":"overview",'
           '"refresh_seconds":2,"theme":"dual","rotation_enabled":false,'
           '"rotation_seconds":3,"rotation_screens":["overview","thermal",'
           '"network","storage"]}\n')
SERVICE = """[Unit]
Description=E87N display
[Service]
Type=simple
ExecStartPre=/usr/bin/e87nctl display apply
ExecStart=/usr/bin/python3 -I -m e87n.display --daemon
Restart=on-failure
RestrictAddressFamilies=AF_UNIX AF_INET
[Install]
WantedBy=multi-user.target
"""
PACKAGE = "usr/lib/python3/dist-packages/e87n/"
MODULES = "lib/modules/6.18.52-current-edgepi-e87n/kernel/drivers/staging/fbtft/"
UNIT = "usr/lib/systemd/system/e87n-display.service"
ENABLE = "etc/systemd/system/multi-user.target.wants/e87n-display.service"
PANEL = "/soc/spi@11009800/display@0"
PWM = "/soc/pwm@10048000"
FAN = "/pwm-fan"
LIGHT = "/backlight"
LVTS = "/soc/lvts@1100a000"
ZONE = "/thermal-zones/cpu-thermal"


def cells(*values):
    return b"".join(value.to_bytes(4, "big") for value in values)


def strings(*values):
    return b"".join(value.encode("ascii") + b"\0" for value in values)


def device_tree():
    nodes = {
        "/": {"compatible": strings("edgepi,e87n", "mediatek,mt7987a", "mediatek,mt7987")},
        "/soc": {},
        "/soc/spi@11009800": {"status": strings("okay")},
        PANEL: {"compatible": strings("newvisionu,nv3007"), "status": strings("okay"),
                "reg": cells(0), "width": cells(142), "height": cells(428),
                "rotate": cells(270), "spi-max-frequency": cells(52000000), "fps": cells(30)},
        PWM: {"compatible": strings("mediatek,mt7987-pwm"), "#pwm-cells": cells(2),
              "phandle": cells(1), "status": strings("okay")},
        LIGHT: {"compatible": strings("pwm-backlight"), "status": strings("okay"),
                "pwms": cells(1, 2, 50000), "brightness-levels": cells(*range(0, 251, 10), 255),
                "default-brightness-level": cells(26)},
        FAN: {"compatible": strings("pwm-fan"), "status": strings("okay"),
              "phandle": cells(2), "pwms": cells(1, 1, 50000), "#cooling-cells": cells(2),
              "cooling-levels": cells(0, 128, 192, 255)},
        LVTS: {"compatible": strings("mediatek,mt7987-lvts-ap"), "status": strings("okay"),
               "phandle": cells(3), "#thermal-sensor-cells": cells(1)},
        "/thermal-zones": {},
        ZONE: {"thermal-sensors": cells(3, 0), "polling-delay": cells(1000),
               "polling-delay-passive": cells(1000)},
        ZONE + "/trips": {},
        ZONE + "/cooling-maps": {},
        ZONE + "/trips/crit": {"temperature": cells(125000), "type": strings("critical")},
        ZONE + "/trips/hot": {"temperature": cells(120000), "type": strings("hot")},
    }
    for level, temperature in enumerate((50000, 65000, 75000), 1):
        nodes[ZONE + "/trips/active%d" % level] = {
            "temperature": cells(temperature), "type": strings("active"),
            "phandle": cells(3 + level), "hysteresis": cells(2000)}
        nodes[ZONE + "/cooling-maps/map%d" % level] = {
            "trip": cells(3 + level), "cooling-device": cells(2, level, level)}
    return nodes


class FakeFdt(audit.Fdt):
    """Exercise the real byte/cell/string adapter with injected fdtget output."""

    def __init__(self, nodes=None):
        super().__init__("unused-host-fdtget", "synthetic.dtb")
        self.nodes = device_tree() if nodes is None else copy.deepcopy(nodes)

    def run(self, *args):
        mode = args[0]
        node = args[3] if mode == "-t" else args[2]
        audit.require(node in self.nodes, "host fdtget failed: missing node " + node)
        if mode == "-p":
            return "\n".join(self.nodes[node])
        if mode == "-l":
            return "\n".join(path.rsplit("/", 1)[1] for path in self.nodes
                             if path != "/" and (path.rsplit("/", 1)[0] or "/") == node)
        if mode == "-t" and args[1] == "bx":
            return " ".join("%02x" % byte for byte in self.nodes[node][args[4]])
        raise AssertionError("unexpected fdtget arguments: " + repr(args))


def write(root, name, data, executable=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


def make_rootfs(root):
    for name in ("hardware.py", "display.py", "__main__.py"):
        write(root, PACKAGE + name, '"""Synthetic target module."""\nraise RuntimeError("TARGET EXECUTED")\n')
    write(root, PACKAGE + "__init__.py", "")
    write(root, "usr/bin/e87nctl", "#!/bin/sh\nexit 99\n", executable=True)
    write(root, "etc/e87n/display.json", DEFAULT)
    write(root, UNIT, SERVICE)
    link = root / ENABLE
    link.parent.mkdir(parents=True)
    link.symlink_to("/usr/lib/systemd/system/e87n-display.service")
    write(root, "etc/modules-load.d/e87n-display.conf", "# E87N display\nfb_nv3007\n")
    for name in ("fb_nv3007", "fbtft"):
        write(root, MODULES + name + ".ko", b"SYNTHETIC module presence fixture\n")
    write(root, "usr/lib/python3/dist-packages/PIL/Image.py", 'raise RuntimeError("PIL IMPORTED")\n')
    write(root, "usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", b"SYNTHETIC font presence fixture\n")
    write(root, "usr/share/fonts/truetype/wqy/wqy-microhei.ttc", b"SYNTHETIC CJK font presence fixture\n")


def snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        value = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
        result[str(path.relative_to(root))] = (info.st_mode, info.st_mtime_ns, value)
    return result


class ConfigTests(unittest.TestCase):
    def test_valid_config_and_typed_values(self):
        for version in ("6.18.52", "6.18.52-current-edgepi-e87n"):
            audit.check_config((CONFIG.replace("6.18.52", version) +
                                'CONFIG_NUMBER=-2\nCONFIG_HEX=0xAB\nCONFIG_MT76x02_LIB=m\n'
                                'CONFIG_TEXT="literal $(this_is_not_executed)"\n').encode())

    def test_all_required_config_states_and_missing_symbols(self):
        expected = dict.fromkeys(BUILTINS, "y")
        expected.update(FB_TFT="m", FB_TFT_NV3007="m", FRAMEBUFFER_CONSOLE="n", CPU_FREQ="n")
        for name, value in expected.items():
            line = "# CONFIG_%s is not set\n" % name if value == "n" else "CONFIG_%s=%s\n" % (name, value)
            for replacement in ("", "CONFIG_%s=%s\n" % (name, "m" if value == "y" else "y")):
                with self.subTest(name=name, replacement=replacement):
                    with self.assertRaisesRegex(audit.Invalid, "CONFIG_" + name):
                        audit.check_config(CONFIG.replace(line, replacement).encode())

    def test_wrong_version_architecture_and_duplicate_header(self):
        for text in (CONFIG.replace("6.18.52", "6.18.50"), CONFIG.replace("6.18.52", "6.18.520"),
                     CONFIG.replace("arm64", "x86_64"), CONFIG.replace("6.18.52", "6.18.52-evil"),
                     CONFIG + "# Linux/arm64 6.18.52 Kernel Configuration\n"):
            with self.subTest(text=text.splitlines()[0]):
                with self.assertRaisesRegex(audit.Invalid, "header"):
                    audit.check_config(text.encode())

    def test_duplicates_and_malicious_config_are_rejected_without_execution(self):
        with tempfile.TemporaryDirectory(prefix="e87n-config-tests.") as scratch:
            marker = Path(scratch) / "executed"
            for payload in ("CONFIG_SPI=y", "# CONFIG_SPI is not set", "CONFIG_SPI=banana",
                            "CONFIG_EVIL=$(touch '%s')" % marker, "touch '%s'" % marker,
                            "__import__('pathlib').Path(%r).touch()" % str(marker)):
                with self.subTest(payload=payload), self.assertRaises(audit.Invalid):
                    audit.check_config((CONFIG + payload + "\n").encode())
            self.assertFalse(marker.exists())


class DtbTests(unittest.TestCase):
    def reject(self, nodes, error):
        with self.assertRaisesRegex(audit.Invalid, error):
            audit.check_dtb(FakeFdt(nodes))

    def test_valid_board_and_implicit_enabled_nodes(self):
        audit.check_dtb(FakeFdt())
        nodes = device_tree()
        for props in nodes.values():
            props.pop("status", None)
        audit.check_dtb(FakeFdt(nodes))

    def test_disabled_nodes_and_ancestors(self):
        for node in device_tree():
            # The hot/critical trips are enabled-checked too, but their thermal
            # thresholds remain the independent platform auditor's remit.
            with self.subTest(node=node):
                nodes = device_tree()
                nodes[node]["status"] = strings("disabled")
                self.reject(nodes, "disabled/unavailable")

    def test_wrong_compatibles(self):
        for node in ("/", PANEL, PWM, LIGHT, FAN, LVTS):
            with self.subTest(node=node):
                nodes = device_tree()
                nodes[node]["compatible"] = strings("wrong,device")
                self.reject(nodes, "wrong compatible")

    def test_panel_fields(self):
        for prop in ("reg", "width", "height", "rotate", "spi-max-frequency", "fps"):
            with self.subTest(prop=prop):
                nodes = device_tree()
                nodes[PANEL][prop] = cells(999)
                self.reject(nodes, "display " + prop)

    def test_pwm_provider_channel_period_and_inversion(self):
        for node, expected in ((LIGHT, 2), (FAN, 1)):
            for values in ((1, expected, 50000, 1), (1, expected, 50000, 0),
                           (1, expected, 40000), (1, 0, 50000), (999, expected, 50000),
                           (1, expected), (1, expected, 50000, 1, expected, 50000)):
                with self.subTest(node=node, values=values):
                    nodes = device_tree()
                    nodes[node]["pwms"] = cells(*values)
                    self.reject(nodes, "PWM must")
        nodes = device_tree()
        nodes[PWM]["#pwm-cells"] = cells(3)
        nodes[LIGHT]["pwms"] = cells(1, 2, 50000, 1)
        self.reject(nodes, "#pwm-cells must be 2")

    def test_backlight_and_cooling_levels(self):
        for node, prop, values, error in (
            (LIGHT, "brightness-levels", (0, 128, 255), "brightness-levels"),
            (LIGHT, "default-brightness-level", (20,), "default-brightness-level"),
            (FAN, "cooling-levels", tuple(range(0, 251, 10)), "cooling-levels"),
            (FAN, "#cooling-cells", (1,), "#cooling-cells")):
            with self.subTest(prop=prop):
                nodes = device_tree()
                nodes[node][prop] = cells(*values)
                self.reject(nodes, error)

    def test_thermal_sensor_trips_and_maps(self):
        modifications = (
            (ZONE, "thermal-sensors", cells(999, 0), "LVTS sensor"),
            (ZONE, "polling-delay", cells(0), "positive"),
            (ZONE + "/trips/active2", "temperature", cells(60000), "50/65/75"),
            (ZONE + "/trips/active2", "type", strings("passive"), "50/65/75"),
            (ZONE + "/cooling-maps/map2", "cooling-device", cells(2, 1, 1), "state 2/2"),
            (ZONE + "/cooling-maps/map2", "cooling-device", cells(99, 2, 2), "state 2/2"),
            (ZONE + "/cooling-maps/map2", "trip", cells(4), "duplicate fan cooling"),
            (ZONE + "/cooling-maps/map2", "trip", cells(999), "fan cooling map"),
            (LVTS, "phandle", cells(1), "LVTS sensor"),
        )
        for node, prop, value, error in modifications:
            with self.subTest(node=node, prop=prop, value=value):
                nodes = device_tree()
                nodes[node][prop] = value
                self.reject(nodes, error)
        nodes = device_tree()
        del nodes[ZONE + "/cooling-maps/map3"]
        self.reject(nodes, "missing fan cooling maps")

    def test_missing_nodes_properties_and_malformed_cells(self):
        nodes = device_tree()
        del nodes[PANEL]
        self.reject(nodes, "missing node")
        nodes = device_tree()
        del nodes[PANEL]["width"]
        self.reject(nodes, "missing.*width")
        nodes = device_tree()
        nodes[PANEL]["height"] = b"\x01"
        self.reject(nodes, "non-cell")
        nodes = device_tree()
        nodes[PANEL]["width"] = cells(142, 142)
        self.reject(nodes, "one cell")
        nodes = device_tree()
        nodes[PANEL]["compatible"] = b"newvisionu,nv3007"
        self.reject(nodes, "unterminated")

    def test_fdtget_failure_and_timeout(self):
        dt = audit.Fdt("/host/fdtget", "/some path/board.dtb")
        with mock.patch.object(audit.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "invalid DTB")) as run:
            with self.assertRaisesRegex(audit.Invalid, "host fdtget failed: invalid DTB"):
                dt.properties("/")
            self.assertEqual(run.call_args.args[0], ["/host/fdtget", "-p", "/some path/board.dtb", "/"])
            self.assertNotIn("shell", run.call_args.kwargs)
        with mock.patch.object(audit.subprocess, "run", side_effect=subprocess.TimeoutExpired("fdtget", 10)):
            with self.assertRaises(subprocess.TimeoutExpired):
                dt.properties("/")


class RootfsTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="e87n-audit-tests.")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / "root fs"
        self.root.mkdir()
        make_rootfs(self.root)

    def reject(self, error):
        with self.assertRaisesRegex((audit.Invalid, OSError, SyntaxError, ValueError), error):
            audit.check_rootfs(self.root)

    def test_valid_rootfs_is_read_only_and_never_executes_target(self):
        marker = Path(self.scratch.name) / "target-executed"
        poison = "from pathlib import Path\nPath(%r).write_text('executed')\n" % str(marker)
        for name in ("hardware.py", "display.py", "__main__.py", "__init__.py"):
            write(self.root, PACKAGE + name, poison)
        write(self.root, "usr/lib/python3/dist-packages/PIL/Image.py", poison)
        write(self.root, "usr/bin/e87nctl", "#!/bin/sh\ntouch '%s'\n" % marker, executable=True)
        before = snapshot(self.root)
        with mock.patch.object(audit.subprocess, "run", side_effect=AssertionError("target execution")):
            audit.check_rootfs(self.root)
        self.assertFalse(marker.exists())
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(list(self.root.rglob("__pycache__")))

    def test_missing_required_files(self):
        names = [PACKAGE + name for name in ("hardware.py", "display.py", "__main__.py", "__init__.py")]
        names += ["usr/bin/e87nctl", "etc/e87n/display.json", UNIT, ENABLE,
                  "etc/modules-load.d/e87n-display.conf", MODULES + "fb_nv3007.ko", MODULES + "fbtft.ko",
                  "usr/lib/python3/dist-packages/PIL/Image.py",
                  "usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "usr/share/fonts/truetype/wqy/wqy-microhei.ttc"]
        for name in names:
            with self.subTest(name=name):
                path = self.root / name
                saved = path.with_name(path.name + ".saved")
                path.rename(saved)
                try:
                    with self.assertRaises((audit.Invalid, OSError)):
                        audit.check_rootfs(self.root)
                finally:
                    saved.rename(path)

    def test_json_malicious_duplicates_types_and_nondefaults(self):
        marker = Path(self.scratch.name) / "json-executed"
        bad = ["__import__('pathlib').Path(%r).touch()" % str(marker),
               DEFAULT.replace('"enabled":true', '"enabled":true,"enabled":true'),
               DEFAULT.replace('"enabled":true', '"enabled":1'),
               DEFAULT.replace('"enabled":true', '"enabled":false'),
               DEFAULT.replace(":20", ":20.0"), DEFAULT.replace(":20", ":21"),
               DEFAULT.replace('"rotation_seconds":3', '"rotation_seconds":NaN'),
               DEFAULT.replace('"rotation_seconds":3', '"rotation_seconds":3,"extra":0'),
               DEFAULT.replace('"overview"', '"other"'), "[]", "null"]
        for data in bad:
            with self.subTest(data=data):
                write(self.root, "etc/e87n/display.json", data)
                with self.assertRaises((audit.Invalid, ValueError)):
                    audit.check_rootfs(self.root)
        self.assertFalse(marker.exists())

    def test_python_syntax_and_control_mode(self):
        original = (self.root / (PACKAGE + "display.py")).read_bytes()
        write(self.root, PACKAGE + "display.py", "return\n")
        self.reject("outside function")
        write(self.root, PACKAGE + "display.py", original)
        (self.root / "usr/bin/e87nctl").chmod(0o644)
        self.reject("must be executable")

    def test_service_commands_must_be_exact(self):
        for data in (SERVICE.replace("python3 -I", "python3"),
                     SERVICE.replace("display apply", "display status"),
                     SERVICE.replace("ExecStart=", "#ExecStart="),
                     SERVICE.replace("[Install]", "ExecStartPre=/bin/true\n[Install]"),
                     SERVICE.replace("WantedBy=multi-user.target", "WantedBy=graphical.target")):
            with self.subTest(data=data):
                write(self.root, UNIT, data)
                self.reject("e87n-display.service")

    def test_service_continuations(self):
        continued = "-m " + "\\" + "\n" + "e87n.display"
        write(self.root, UNIT, SERVICE.replace("-m e87n.display", continued))
        audit.check_rootfs(self.root)

    def test_enable_link_relative_wrong_dangling_regular_and_loop(self):
        link = self.root / ENABLE
        link.unlink()
        link.symlink_to("../../../../usr/lib/systemd/system/e87n-display.service")
        audit.check_rootfs(self.root)
        for target in ("/usr/lib/systemd/system/other.service", "/dev/null", "e87n-display.service"):
            with self.subTest(target=target):
                link.unlink()
                link.symlink_to(target)
                self.reject("enable symlink|symlink loop")
        link.unlink()
        write(self.root, ENABLE, SERVICE)
        self.reject("missing enable symlink")

    def test_overrides_and_dropins(self):
        override = write(self.root, "etc/systemd/system/e87n-display.service", SERVICE)
        self.reject("override")
        override.unlink()
        for directory in ("e87n-display.service.d", "e87n-.service.d", "service.d"):
            path = write(self.root, "etc/systemd/system/" + directory + "/override.conf", "[Service]\nUser=nobody\n")
            self.reject("drop-in")
            path.unlink()

    def test_compressed_modules_and_usr_merge(self):
        for name, suffix in (("fb_nv3007", ".zst"), ("fbtft", ".xz")):
            path = self.root / (MODULES + name + ".ko")
            path.rename(path.with_name(path.name + suffix))
        audit.check_rootfs(self.root)
        (self.root / "lib/modules").rename(self.root / "usr/lib/modules")
        (self.root / "lib").rmdir()
        (self.root / "lib").symlink_to("/usr/lib")
        audit.check_rootfs(self.root)

    def test_wrong_module_release_path_duplicates_and_empty(self):
        path = self.root / (MODULES + "fb_nv3007.ko")
        duplicate = write(self.root, MODULES + "fb_nv3007.ko.zst", b"synthetic")
        self.reject("exactly one installed fb_nv3007")
        duplicate.unlink()
        data = path.read_bytes()
        path.write_bytes(b"")
        self.reject("invalid installed module")
        path.unlink()
        write(self.root, MODULES.replace("6.18.52", "6.18.50") + "fb_nv3007.ko", data)
        self.reject("exactly one installed fb_nv3007")
        write(self.root, MODULES.replace("staging/fbtft", "video/fbdev") + "fb_nv3007.ko", data)
        self.reject("exactly one installed fb_nv3007")

    def test_missing_wrong_duplicate_module_load(self):
        for data in ("# fb_nv3007\n", "fbtft\n", "fb_nv3007\nfb_nv3007\n", "fb_nv3007\nfancontrol\n"):
            with self.subTest(data=data):
                write(self.root, "etc/modules-load.d/e87n-display.conf", data)
                self.reject("must load fb_nv3007")

    def test_legacy_programs(self):
        for name in ("usr/bin/display", "usr/sbin/display-control", "usr/bin/fancontrol",
                     "etc/init.d/fancontrol", "etc/config/display", "usr/local/bin/fan-control.sh"):
            with self.subTest(name=name):
                path = write(self.root, name, "legacy program\n")
                self.reject("forbidden OpenWrt")
                path.unlink()

    def test_additional_fan_units_and_writer_commands_in_dropins(self):
        candidates = (
            ("etc/systemd/system/e87n-fan.service", "[Service]\nExecStart=/bin/true\n"),
            ("usr/lib/systemd/system/fancontrol.timer", "[Timer]\nOnBootSec=1\n"),
            ("etc/systemd/system/cooling.service", "[Service]\nExecStart=/usr/bin/fancontrol\n"),
            ("etc/systemd/system/cooling.service", "[Service]\nExecStart=/usr/bin/e87nctl fan set 50\n"),
            ("etc/systemd/system/cooling.service", "[Service]\nExecStart=/bin/sh -c 'echo 128 > /sys/class/hwmon/hwmon0/pwm1'\n"),
            ("etc/systemd/system/other.service.d/10-cooling.conf", "[Service]\nExecStartPost=/bin/sh -c 'echo 3 > /sys/class/thermal/cooling_device0/cur_state'\n"),
        )
        for name, data in candidates:
            with self.subTest(name=name, data=data):
                path = write(self.root, name, data)
                self.reject("additional fan")
                path.unlink()

    def test_symlinked_writer_dropin_directory(self):
        write(self.root, "opt/cooling/drop.conf", "[Service]\nExecStartPost=/usr/bin/fancontrol\n")
        (self.root / "etc/systemd/system/other.service.d").symlink_to("/opt/cooling")
        self.reject("additional fan")

    def test_absolute_symlinks_cannot_read_host_files(self):
        outside = write(Path(self.scratch.name), "host-source.py", "raise RuntimeError('host')\n")
        path = self.root / (PACKAGE + "display.py")
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(FileNotFoundError):
            audit.check_rootfs(self.root)
        self.assertTrue(str(audit.rooted(self.root, "/" + PACKAGE + "display.py")).startswith(str(self.root) + "/"))
        path.unlink()
        path.symlink_to("../../../../../../../../../../escape.py")
        self.reject("escapes rootfs")


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="e87n-cli-tests.")
        self.addCleanup(self.scratch.cleanup)
        self.work = Path(self.scratch.name)
        self.root = self.work / "root fs"
        self.root.mkdir()
        make_rootfs(self.root)
        self.config = write(self.work, "kernel .config", CONFIG)
        self.dtb = write(self.work, "board image.dtb", b"synthetic")

    def main(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = audit.main(["--rootfs", str(self.root), "--config", str(self.config), "--dtb", str(self.dtb)])
        return result, output.getvalue()

    def test_cli_with_injected_fdt_is_read_only(self):
        before = snapshot(self.work)
        with mock.patch.object(audit.shutil, "which", return_value=sys.executable), \
                mock.patch.object(audit, "Fdt", return_value=FakeFdt()):
            result, output = self.main()
        self.assertEqual(result, 0, output)
        self.assertIn("PASS: STATIC", output)
        self.assertIn("no boot, runtime, or hardware validation", output)
        self.assertIn("DTB SHA256:", output)
        self.assertEqual(snapshot(self.work), before)

    def test_missing_host_fdtget_and_rootfs_tool_rejected(self):
        with mock.patch.object(audit.shutil, "which", return_value=None):
            result, output = self.main()
        self.assertEqual(result, 1)
        self.assertIn("host fdtget is required", output)
        tool = write(self.root, "usr/bin/fdtget", "#!/bin/sh\nexit 99\n", executable=True)
        with mock.patch.object(audit.shutil, "which", return_value=str(tool)):
            result, output = self.main()
        self.assertEqual(result, 1)
        self.assertIn("host tool outside --rootfs", output)

    def test_cli_failure_is_not_pass_and_has_no_traceback(self):
        (self.root / ENABLE).unlink()
        result, output = self.main()
        self.assertEqual(result, 1)
        self.assertIn("FAIL: static", output)
        self.assertNotIn("PASS:", output)
        self.assertNotIn("Traceback", output)

    @unittest.skipUnless(shutil.which("dtc") and shutil.which("fdtget"), "host dtc/fdtget not installed")
    def test_real_dtb_cli_and_negative_cases(self):
        def dts(nodes, node="/"):
            body = "\n".join('%s = [%s];' % (key, " ".join("%02x" % byte for byte in value))
                             for key, value in nodes[node].items())
            children = [p for p in nodes if p != "/" and (p.rsplit("/", 1)[0] or "/") == node]
            body += "\n" + "\n".join(dts(nodes, child) for child in children)
            return ("/" if node == "/" else node.rsplit("/", 1)[1]) + " {\n" + body + "\n};"

        variants = [(device_tree(), None)]
        for node, prop, value, error in (
                ("/soc/spi@11009800", "status", strings("disabled"), "disabled/unavailable"),
                (LIGHT, "pwms", cells(1, 2, 50000, 1), "normal polarity")):
            nodes = device_tree()
            nodes[node][prop] = value
            variants.append((nodes, error))
        for nodes, error in variants:
            with self.subTest(error=error):
                source = write(self.work, "synthetic board.dts", "/dts-v1/;\n" + dts(nodes))
                result = subprocess.run([shutil.which("dtc"), "-q", "-I", "dts", "-O", "dtb", "-o",
                                         str(self.dtb), str(source)], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                before = snapshot(self.work)
                result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--rootfs", str(self.root),
                                         "--config", str(self.config), "--dtb", str(self.dtb)],
                                        capture_output=True, text=True, timeout=30)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0 if error is None else 1, output)
                self.assertIn("PASS: STATIC" if error is None else error, output)
                self.assertEqual(snapshot(self.work), before)
        self.dtb.write_bytes(b"not a flattened device tree")
        result, output = self.main()
        self.assertEqual(result, 1)
        self.assertIn("host fdtget failed", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
