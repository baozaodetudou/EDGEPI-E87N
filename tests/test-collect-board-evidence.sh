#!/usr/bin/env bash
# Synthetic evidence only: never execute the collector against this host or a board.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 - "$repo_dir" <<'PY'
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPT = Path(sys.argv[1]) / "scripts/collect-board-evidence.sh"
# Load definitions without the production entry point. FakeHost is injected into
# main; there is no fixture-root CLI switch to accidentally use on a real board.
payload = SCRIPT.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
collector = {"__name__": "evidence_mock"}
exec(compile(payload, str(SCRIPT), "exec"), collector)
NotCollected = collector["NotCollected"]

# Independent command/path fixtures: an expanded collector scope must fail here.
UNAME = ("uname", "-a")
LSBLK = ("lsblk", "--json", "--output", "NAME,TYPE,SIZE,RO,FSTYPE,MOUNTPOINTS")
FINDMNT = ("findmnt", "--json", "--output", "TARGET,SOURCE,FSTYPE")
LINK = ("ip", "-json", "link", "show")
ADDR = ("ip", "-json", "address", "show")
JOURNAL = ("journalctl", "--kernel", "--boot", "--lines=2000", "--no-pager",
           "--output=short-monotonic", "--quiet")
DMESG = ("dmesg", "--color=never")
PHY = "/sys/bus/mdio_bus/devices/mdio-bus:01"
ZONE = "/sys/class/thermal/thermal_zone0"
COOLING = "/sys/class/thermal/cooling_device0"
HWMON = "/sys/class/hwmon/hwmon0"
PWM = "/sys/class/pwm/pwmchip0"


class FakeHost:
    def __init__(self):
        self.platform = "Linux"
        self.calls = []
        self.failures = {}
        self.files = {
            "/etc/os-release": b'ID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n',
            "/proc/cmdline": b"root=UUID=synthetic ro console=ttyS0 api_token=mock-token\n",
            "/proc/cpuinfo": b"processor : 0\nmodel name : SYNTHETIC ARM64\n",
            "/proc/meminfo": b"MemTotal: 262144 kB\nMemFree: 131072 kB\n",
            "/sys/firmware/devicetree/base/model": b"SYNTHETIC EdgePi E87N\0",
            "/sys/firmware/devicetree/base/compatible": b"edgepi,e87n\0mediatek,mt7987\0",
        }
        self.dirs = {
            "/sys/bus/mdio_bus/devices": ["mdio-bus:01"],
            "/sys/class/thermal": ["thermal_zone0", "cooling_device0", "uevent"],
            "/sys/class/hwmon": ["hwmon0", "uevent"],
            "/sys/class/pwm": ["pwmchip0", "uevent"],
        }
        for path, attributes in (
            (PHY, {"phy_id": "0x00339c11", "modalias": "mdio:synthetic"}),
            (ZONE, {"type": "soc-thermal", "temp": "43000", "mode": "enabled",
                    "policy": "step_wise", "trip_point_0_temp": "85000",
                    "trip_point_0_type": "passive", "trip_point_0_hyst": "2000"}),
            (COOLING, {"type": "pwm-fan", "cur_state": "1", "max_state": "4"}),
            (HWMON, {"name": "pwmfan", "fan1_input": "1800", "pwm1": "100",
                     "pwm1_enable": "2", "temp1_input": "44000"}),
            (PWM, {"npwm": "1"}),
            (PWM + "/pwm0", {"period": "40000", "duty_cycle": "18000",
                             "enable": "1", "polarity": "normal"}),
        ):
            self.dirs[path] = list(attributes) + ["export", "unexport", "uevent", "bind",
                                                 "environ", "statistics", "reset"]
            for name, value in attributes.items():
                self.files[path + "/" + name] = (value + "\n").encode()
        self.dirs[PWM].append("pwm0")
        self.links = {PHY + "/driver": "../../../../bus/mdio_bus/drivers/MediaTek MT7987 2.5GbE PHY"}
        log = b"[1.0] SYNTHETIC MT7987 PHY firmware loaded\n[2.0] SYNTHETIC pwm-fan ready\n"
        self.commands = {
            UNAME: b"Linux mock-board 6.18.52-current-edgepi-e87n SYNTHETIC aarch64 GNU/Linux\n",
            LSBLK: b'{"blockdevices":[{"name":"mmcblk0","type":"disk","size":"8G","ro":false}]}\n',
            FINDMNT: b'{"filesystems":[{"target":"/","source":"/dev/mmcblk0p1","fstype":"ext4"}]}\n',
            LINK: b'[{"ifindex":2,"ifname":"eth0","operstate":"UP"}]\n',
            ADDR: b'[{"ifindex":2,"ifname":"eth0","addr_info":[]}]\n',
            JOURNAL: log, DMESG: log,
        }

    def system(self):
        self.calls.append(("system",))
        return self.platform

    def value(self, operation, key, mapping):
        self.calls.append((operation, key))
        if (operation, key) in self.failures:
            raise NotCollected(self.failures[(operation, key)])
        if key not in mapping:
            raise AssertionError("unexpected host access: %s %s" % (operation, key))
        return mapping[key]

    def read(self, path):
        return self.value("read", path, self.files)

    def names(self, path):
        return self.value("names", path, self.dirs)

    def readlink(self, path):
        return self.value("readlink", path, self.links)

    def run(self, command):
        return self.value("run", command, self.commands)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="e87n-evidence-mock-")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.output = self.parent / "new report"
        self.host = FakeHost()
        self.before = copy.deepcopy((self.host.files, self.host.dirs, self.host.links))

    def run_collector(self, args=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        real_open = os.open

        def exclusive_report_write(path, flags, mode=0o777, **kwargs):
            target = Path(path)
            # Every file opened by main must be an exclusive write immediately
            # within one NEW report. No host input file or device is opened.
            self.assertEqual(target.parent.parent, self.parent)
            self.assertEqual(flags & os.O_ACCMODE, os.O_WRONLY)
            self.assertTrue(flags & os.O_CREAT)
            self.assertTrue(flags & os.O_EXCL)
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertFalse(flags & (os.O_TRUNC | os.O_APPEND))
            self.assertEqual(mode, 0o600)
            return real_open(path, flags, mode, **kwargs)

        with redirect_stdout(stdout), redirect_stderr(stderr), \
                mock.patch.dict(os.environ, {"TMPDIR": str(self.parent), "API_TOKEN": "env-secret-never-read"}), \
                mock.patch.object(os, "open", side_effect=exclusive_report_write), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("real command forbidden")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("real process forbidden")):
            code = collector["main"](["--output", str(self.output)] if args is None else args, host=self.host)
        return code, stdout.getvalue(), stderr.getvalue()

    def manifest(self, output=None):
        return json.loads(((output or self.output) / "manifest.json").read_text())

    def assert_incomplete(self, name, expected_warning):
        code, stdout, stderr = self.run_collector()
        self.assertEqual(code, 1)
        self.assertIn("HARDWARE=NOT-VALIDATED", stdout)
        self.assertIn("NOT-COLLECTED", stderr)
        self.assertIn(expected_warning, stderr)
        report = self.manifest()
        self.assertEqual(report["collection_status"], "INCOMPLETE")
        self.assertEqual(report["hardware_status"], "NOT-VALIDATED")
        self.assertEqual(next(entry["status"] for entry in report["entries"] if entry["name"] == name),
                         "NOT-COLLECTED")
        self.assertIn("STATUS=NOT-COLLECTED", (self.output / (name + ".txt")).read_text())

    def test_complete_mock_is_collection_not_hardware_acceptance(self):
        code, stdout, stderr = self.run_collector()
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("COLLECTION=COLLECTED; HARDWARE=NOT-VALIDATED", stdout)
        report = self.manifest()
        self.assertEqual(report["hardware_status"], "NOT-VALIDATED")
        self.assertEqual(report["collection_status"], "COLLECTED")
        self.assertEqual(len(report["entries"]), 17)
        self.assertTrue(all(entry["status"] == "COLLECTED" for entry in report["entries"]))
        self.assertEqual(report["warnings"], [])
        self.assertIn("No throughput", (self.output / "README.txt").read_text())
        self.assertIn("flash-safety", (self.output / "README.txt").read_text())
        self.assertEqual(self.before, (self.host.files, self.host.dirs, self.host.links))
        self.assertEqual({key for kind, *rest in self.host.calls if kind == "read" for key in rest},
                         set(self.host.files))
        self.assertEqual([call[1] for call in self.host.calls if call[0] == "run"],
                         [UNAME, LSBLK, FINDMNT, LINK, ADDR, JOURNAL])

    def test_default_mktemp_is_new_private_directory_each_time(self):
        for _ in range(2):
            self.assertEqual(self.run_collector([])[0], 0)
        outputs = list(self.parent.iterdir())
        self.assertEqual(len(outputs), 2)
        for output in outputs:
            self.assertTrue(output.name.startswith("e87n-board-evidence-"))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(self.manifest(output)["hardware_status"], "NOT-VALIDATED")

    def test_report_files_are_private_and_not_links(self):
        self.assertEqual(self.run_collector()[0], 0)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertTrue(path.is_file())
            self.assertFalse(path.is_symlink())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_directory_cannot_be_reused(self):
        self.output.mkdir()
        sentinel = self.output / "sentinel"
        sentinel.write_bytes(b"leave unchanged")
        self.assertEqual(self.run_collector()[0], 2)
        self.assertEqual(sentinel.read_bytes(), b"leave unchanged")
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(self.host.calls, [("system",)])

    def test_existing_file_cannot_be_overwritten(self):
        self.output.write_bytes(b"leave unchanged")
        self.assertEqual(self.run_collector()[0], 2)
        self.assertEqual(self.output.read_bytes(), b"leave unchanged")
        self.assertEqual(self.host.calls, [("system",)])

    def test_existing_symlink_cannot_be_followed(self):
        target = self.parent / "existing"
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.run_collector()[0], 2)
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(list(target.iterdir()), [])

    def test_dangling_symlink_cannot_be_replaced(self):
        target = self.parent / "absent"
        self.output.symlink_to(target)
        self.assertEqual(self.run_collector()[0], 2)
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())

    def test_missing_parent_is_not_created(self):
        code, _, stderr = self.run_collector(["--output", str(self.parent / "absent" / "report")])
        self.assertEqual(code, 2)
        self.assertIn("NOT-COLLECTED", stderr)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_report_mkdir_permission_denied(self):
        with mock.patch.object(Path, "mkdir", side_effect=PermissionError("report parent denied")):
            code, _, stderr = self.run_collector()
        self.assertEqual(code, 2)
        self.assertIn("report parent denied", stderr)
        self.assertEqual(self.host.calls, [("system",)])

    def test_pseudo_filesystem_report_parent_rejected_without_access(self):
        for path in ("/sys", "/sys/class/pwm", "/proc", "/proc/1", "/dev", "/dev/shm"):
            with self.subTest(path=path), \
                    mock.patch.object(Path, "resolve", return_value=Path(path)), \
                    mock.patch.object(Path, "is_dir", return_value=True):
                with self.assertRaisesRegex(ValueError, "cannot be inside"):
                    collector["safe_parent"]("synthetic-parent")

    def test_non_linux_refused_before_report_or_reads(self):
        self.host.platform = "Darwin"
        code, _, stderr = self.run_collector()
        self.assertEqual(code, 2)
        self.assertIn("requires Linux", stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.host.calls, [("system",)])

    def test_help_has_boundary_without_collecting(self):
        stream = io.StringIO()
        with redirect_stdout(stream), self.assertRaises(SystemExit) as result:
            collector["main"](["--help"], host=self.host)
        self.assertEqual(result.exception.code, 0)
        self.assertIn("HARDWARE=NOT-VALIDATED", stream.getvalue())
        self.assertIn("NEW_DIRECTORY", stream.getvalue())
        self.assertEqual(self.host.calls, [])

    def test_unapproved_cli_options_fail_without_collecting(self):
        for option in ("--sudo", "--scan", "--root", "--force", "--device"):
            with self.subTest(option=option), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as result:
                collector["main"]([option], host=self.host)
            self.assertEqual(result.exception.code, 2)
        self.assertEqual(self.host.calls, [])

    def test_unreadable_input_is_not_collected(self):
        self.host.failures[("read", "/proc/cpuinfo")] = "Permission denied"
        self.assert_incomplete("cpuinfo", "Permission denied")

    def test_missing_dt_is_not_collected(self):
        self.host.failures[("read", "/sys/firmware/devicetree/base/model")] = "No such file"
        self.assert_incomplete("dt-model", "No such file")

    def test_dt_nuls_become_lines(self):
        self.assertEqual(self.run_collector()[0], 0)
        self.assertIn("edgepi,e87n\nmediatek,mt7987\n", (self.output / "dt-compatible.txt").read_text())
        self.assertNotIn("\0", (self.output / "dt-model.txt").read_text())

    def test_missing_ip_is_not_collected(self):
        self.host.failures[("run", LINK)] = "missing command: ip"
        self.host.failures[("run", ADDR)] = "missing command: ip"
        self.assert_incomplete("ip-link", "missing command: ip")
        self.assertIn("STATUS=NOT-COLLECTED", (self.output / "ip-addr.txt").read_text())

    def test_missing_phy_driver_is_not_collected(self):
        self.host.failures[("readlink", PHY + "/driver")] = "driver link missing"
        self.assert_incomplete("phy-drivers", "driver link missing")

    def test_sysfs_read_failure_keeps_partial_data_not_success(self):
        self.host.failures[("read", ZONE + "/temp")] = "temperature read denied"
        self.assert_incomplete("thermal-sysfs", "temperature read denied")
        self.assertIn("soc-thermal", (self.output / "thermal-sysfs.txt").read_text())

    def test_sysfs_directory_denied_is_not_collected(self):
        self.host.failures[("names", "/sys/class/hwmon")] = "hwmon directory denied"
        self.assert_incomplete("hwmon-sysfs", "hwmon directory denied")

    def test_missing_required_sysfs_attribute_is_not_collected(self):
        self.host.dirs[HWMON].remove("name")
        self.assert_incomplete("hwmon-sysfs", "required evidence attribute unavailable")

    def test_no_pwm_channels_never_exports_one(self):
        self.host.dirs[PWM].remove("pwm0")
        self.assert_incomplete("pwm-sysfs", "none will be exported")
        self.assertFalse(any(call[0] == "read" and str(call[1]).startswith(PWM + "/pwm0/")
                             for call in self.host.calls))

    def test_no_pwm_chips_is_not_collected(self):
        self.host.dirs["/sys/class/pwm"] = ["uevent"]
        self.assert_incomplete("pwm-sysfs", "no PWM chips available")

    def test_journal_denied_fallback_does_not_hide_warning(self):
        self.host.failures[("run", JOURNAL)] = "journal Permission denied"
        self.assert_incomplete("journal-kernel-attempt", "journal Permission denied")
        self.assertIn(("run", DMESG), self.host.calls)
        self.assertIn("STATUS=COLLECTED", (self.output / "kernel-log-recent.txt").read_text())

    def test_both_log_sources_denied(self):
        self.host.failures[("run", JOURNAL)] = "missing command: journalctl"
        self.host.failures[("run", DMESG)] = "dmesg Operation not permitted"
        self.assert_incomplete("kernel-log-recent", "no readable kernel log source")
        self.assertIn("STATUS=NOT-COLLECTED", (self.output / "phy-firmware-log.txt").read_text())

    def test_absent_firmware_messages_not_a_pass(self):
        self.host.commands[JOURNAL] = b"[2.0] pwm-fan ready\n"
        self.assert_incomplete("phy-firmware-log", "no PHY/firmware matches")

    def test_kernel_logs_bounded_and_packet_records_excluded(self):
        lines = ["[%d] SYNTHETIC PHY firmware event" % value for value in range(350)]
        lines += ["FIREWALL IN=eth0 OUT= SRC=192.0.2.1 DST=192.0.2.2 PROTO=TCP",
                  "driver packet payload: synthetic-private-payload",
                  "driver hex dump: synthetic-private-bytes"]
        self.host.commands[JOURNAL] = ("\n".join(lines) + "\n").encode()
        self.assertEqual(self.run_collector()[0], 0)
        for name in ("kernel-log-recent", "phy-firmware-log"):
            output = (self.output / (name + ".txt")).read_text()
            self.assertEqual(output.count("SYNTHETIC PHY firmware event"), 300)
            self.assertNotIn("[49]", output)
            self.assertIn("[50]", output)
            self.assertIn("[349]", output)
            self.assertNotIn("192.0.2.", output)
            self.assertNotIn("synthetic-private-", output)

    def test_packet_only_log_is_not_collected(self):
        self.host.commands[JOURNAL] = b"IN=eth0 SRC=192.0.2.1 DST=192.0.2.2\n"
        self.assert_incomplete("kernel-log-recent", "no non-traffic kernel messages")

    def test_secrets_and_terminal_controls_are_not_persisted(self):
        self.host.commands[JOURNAL] += (
            b'PHY firmware password="mock password" token=mock-log-token '
            b"url=https://user:mock-uri-password@example.invalid/ \x1b[31m\n")
        self.assertEqual(self.run_collector()[0], 0)
        text = "\n".join(path.read_text() for path in self.output.iterdir())
        for secret in ("mock-token", "mock password", "mock-log-token", "mock-uri-password",
                       "env-secret-never-read", "\x1b"):
            self.assertNotIn(secret, text)
        self.assertIn("[REDACTED]", text)

    def test_warnings_redact_secret_values(self):
        self.host.failures[("read", "/proc/cpuinfo")] = "denied credential=mock-warning-secret"
        code, _, stderr = self.run_collector()
        self.assertEqual(code, 1)
        self.assertNotIn("mock-warning-secret", stderr)
        self.assertIn("[REDACTED]", stderr)
        self.assertNotIn("mock-warning-secret", (self.output / "manifest.json").read_text())

    def test_report_files_cannot_be_overwritten(self):
        self.output.mkdir()
        report = collector["Report"](self.output, self.host)
        original = (self.output / "README.txt").read_bytes()
        with self.assertRaises(FileExistsError):
            report.write("README.txt", "replace existing report")
        self.assertEqual((self.output / "README.txt").read_bytes(), original)

    def test_write_failure_returns_error_and_keeps_partial_report(self):
        with mock.patch.object(collector["Report"], "write", side_effect=OSError("No space left")):
            code, _, stderr = self.run_collector()
        self.assertEqual(code, 2)
        self.assertIn("NOT-COLLECTED", stderr)
        self.assertIn("No space left", stderr)
        self.assertTrue(self.output.is_dir())
        self.assertFalse((self.output / "manifest.json").exists())


class HostAdapterTests(unittest.TestCase):
    def setUp(self):
        self.host = collector["Host"]()

    def test_command_allowlist_is_exact(self):
        self.assertEqual(collector["ALLOWED_COMMANDS"], {UNAME, LSBLK, FINDMNT, LINK, ADDR, JOURNAL, DMESG})

    def test_commands_use_no_shell_stdin_or_secret_environment(self):
        result = SimpleNamespace(returncode=0, stdout=b"synthetic\n", stderr=b"")
        for command in (UNAME, LSBLK, FINDMNT, LINK, ADDR, JOURNAL, DMESG):
            with self.subTest(command=command), \
                    mock.patch.object(collector["shutil"], "which", return_value="/mock/" + command[0]), \
                    mock.patch.object(subprocess, "run", return_value=result) as run, \
                    mock.patch.dict(os.environ, {"PATH": "/mock/bin", "API_TOKEN": "do-not-inherit",
                                                 "SYSTEMD_PAGER": "do-not-run"}):
                self.assertEqual(self.host.run(command), b"synthetic\n")
                args, options = run.call_args
                self.assertEqual(args, (("/mock/" + command[0],) + command[1:],))
                self.assertEqual(options, {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                                           "stderr": subprocess.PIPE, "timeout": 15, "check": False,
                                           "env": {"PATH": "/mock/bin", "LC_ALL": "C", "SYSTEMD_COLORS": "0"}})

    def test_mutating_scanning_and_other_commands_never_execute(self):
        for command in (("sudo", "dmesg"), ("dmesg", "--read-clear"), ("dmesg", "--clear"),
                        ("ip", "link", "set", "eth0", "up"), ("ip", "neigh", "show"),
                        ("tcpdump", "-i", "eth0"), ("ethtool", "eth0"), ("ping", "192.0.2.1"),
                        ("dd", "if=/dev/zero", "of=/dev/mmcblk0"), ("sh", "-c", "env")):
            with self.subTest(command=command), \
                    mock.patch.object(subprocess, "run") as run, \
                    mock.patch.object(collector["shutil"], "which") as which:
                with self.assertRaisesRegex(NotCollected, "allowlist"):
                    self.host.run(command)
                run.assert_not_called()
                which.assert_not_called()

    def test_missing_command_never_executes(self):
        with mock.patch.object(collector["shutil"], "which", return_value=None), \
                mock.patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(NotCollected, "missing command: uname"):
                self.host.run(UNAME)
            run.assert_not_called()

    def test_failed_empty_large_and_warning_outputs_are_not_collected(self):
        for result, reason in (
            (SimpleNamespace(returncode=1, stdout=b"partial", stderr=b"Permission denied"), "Permission denied"),
            (SimpleNamespace(returncode=0, stdout=b"partial", stderr=b"Permission denied"), "Permission denied"),
            (SimpleNamespace(returncode=0, stdout=b"  \n", stderr=b""), "empty command output"),
            (SimpleNamespace(returncode=0, stdout=b"x" * (1024 * 1024 + 1), stderr=b""), "output limit"),
        ):
            with self.subTest(reason=reason, code=result.returncode), \
                    mock.patch.object(collector["shutil"], "which", return_value="/mock/uname"), \
                    mock.patch.object(subprocess, "run", return_value=result):
                with self.assertRaisesRegex(NotCollected, reason):
                    self.host.run(UNAME)

    def test_command_oserror_and_timeout_are_not_collected(self):
        for error in (PermissionError("synthetic permission denied"), subprocess.TimeoutExpired("mock", 15)):
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(collector["shutil"], "which", return_value="/mock/uname"), \
                    mock.patch.object(subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(NotCollected, "unavailable or timed out"):
                    self.host.run(UNAME)

    def test_source_open_is_read_only_nonblocking_and_bounded(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.read.return_value = b"synthetic"
        stream.fileno.return_value = 123
        with mock.patch.object(os, "open", return_value=123) as opened, \
                mock.patch.object(os, "fdopen", return_value=stream) as fdopen, \
                mock.patch.object(os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)):
            self.assertEqual(self.host.read("/mock/source"), b"synthetic")
            opened.assert_called_once_with("/mock/source", os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            fdopen.assert_called_once_with(123, "rb")
            stream.read.assert_called_once_with(256 * 1024 + 1)

    def test_nonregular_empty_and_large_source_are_not_collected(self):
        for mode, data, reason in ((stat.S_IFCHR, b"device", "not a regular"),
                                   (stat.S_IFIFO, b"pipe", "not a regular"),
                                   (stat.S_IFREG, b"", "empty file"),
                                   (stat.S_IFREG, b"x" * (256 * 1024 + 1), "read limit")):
            stream = mock.MagicMock()
            stream.__enter__.return_value = stream
            stream.fileno.return_value = 123
            stream.read.return_value = data
            with self.subTest(mode=mode, reason=reason), \
                    mock.patch.object(os, "open", return_value=123), \
                    mock.patch.object(os, "fdopen", return_value=stream), \
                    mock.patch.object(os, "fstat", return_value=SimpleNamespace(st_mode=mode)):
                with self.assertRaisesRegex(NotCollected, reason):
                    self.host.read("/mock/source")

    def test_read_list_and_readlink_permissions_are_not_collected(self):
        for method, operation in (("read", "open"), ("names", "listdir"), ("readlink", "readlink")):
            with self.subTest(method=method), \
                    mock.patch.object(os, operation, side_effect=PermissionError("synthetic read denied")):
                with self.assertRaisesRegex(NotCollected, "synthetic read denied"):
                    getattr(self.host, method)("/mock/source")


if __name__ == "__main__":
    unittest.main(argv=[str(SCRIPT)], verbosity=2)
PY
