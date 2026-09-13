#!/usr/bin/env bash
# Log collection only: no throughput, stability, boot or flash-safety validation.
# Completion is NOT hardware acceptance. Never write sysfs or configure devices.
# Run manually on the Linux board later; no sudo, remote access or probing.
set -euo pipefail
umask 077
if ! command -v python3 >/dev/null 2>&1; then
	printf 'NOT-COLLECTED: python3 (>= 3.8) is required; HARDWARE=NOT-VALIDATED\n' >&2
	exit 2
fi
# Ignore PYTHONPATH, user-site hooks and the current directory when importing.
exec python3 -I - "$@" <<'PY'
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

NOTICE = (
    "LOG COLLECTION ONLY. No throughput, stress, stability, boot or flash-safety validation.\n"
    "Completion is not hardware acceptance. HARDWARE=NOT-VALIDATED.\n"
    "No elevation, remote access, interface scanning, traffic capture or device configuration.\n"
)
READ_LIMIT = 256 * 1024
COMMAND_LIMIT = 1024 * 1024
JOURNAL = ("journalctl", "--kernel", "--boot", "--lines=2000", "--no-pager",
           "--output=short-monotonic", "--quiet")
DMESG = ("dmesg", "--color=never")  # No --clear, --read-clear or console changes.
COMMANDS = (
    ("uname", ("uname", "-a")),
    ("lsblk", ("lsblk", "--json", "--output", "NAME,TYPE,SIZE,RO,FSTYPE,MOUNTPOINTS")),
    ("findmnt", ("findmnt", "--json", "--output", "TARGET,SOURCE,FSTYPE")),
    ("ip-link", ("ip", "-json", "link", "show")),
    ("ip-addr", ("ip", "-json", "address", "show")),
)
ALLOWED_COMMANDS = {command for _, command in COMMANDS} | {JOURNAL, DMESG}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b[\w.-]*(?:password|passwd|token|secret|api_key|private_key|credential)"
    r"[\w.-]*\s*=\s*)(\"[^\"]*\"|'[^']*'|\S+)")
PACKET_LOG = re.compile(r"\b(?:IN|OUT|SRC|DST|PROTO|SPT|DPT|MAC)=|"
                        r"\b(?:packet dump|packet payload|hex dump)\b", re.I)


class NotCollected(Exception):
    pass


def clean_text(value):
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    text = SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)
    text = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+:[^/\s@]+@",
                  r"\1[REDACTED]@", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", text)


class Host:
    def system(self):
        return os.uname().sysname

    def read(self, path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise NotCollected("not a regular proc/sysfs/text file")
                data = stream.read(READ_LIMIT + 1)
            if len(data) > READ_LIMIT:
                raise NotCollected("read limit exceeded")
            if not data:
                raise NotCollected("empty file; data availability cannot be confirmed")
            return data
        except OSError as error:
            raise NotCollected(str(error)) from error

    def names(self, path):
        try:
            return sorted(os.listdir(path))
        except OSError as error:
            raise NotCollected(str(error)) from error

    def readlink(self, path):
        try:
            return os.readlink(path)
        except OSError as error:
            raise NotCollected(str(error)) from error

    def run(self, command):
        if command not in ALLOWED_COMMANDS:
            raise NotCollected("command is outside the read-only allowlist")
        executable = shutil.which(command[0])
        if not executable:
            raise NotCollected("missing command: " + command[0])
        # Do not pass secret environment variables, pager commands or credentials
        # to subprocesses, and never read their stdin or invoke a shell.
        env = {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C",
               "SYSTEMD_COLORS": "0"}
        try:
            result = subprocess.run((executable,) + command[1:], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=15, env=env, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NotCollected("command unavailable or timed out: " + str(error)) from error
        if result.returncode or result.stderr.strip():
            raise NotCollected("command exit %d: %s" %
                               (result.returncode, clean_text(result.stderr[:4096]).strip()))
        if len(result.stdout) > COMMAND_LIMIT:
            raise NotCollected("command output limit exceeded")
        if not result.stdout.strip():
            raise NotCollected("empty command output; data availability cannot be confirmed")
        return result.stdout


def safe_parent(path):
    parent = Path(path).resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("report parent is not a directory")
    for forbidden in (Path("/proc"), Path("/sys"), Path("/dev")):
        if parent == forbidden or forbidden in parent.parents:
            raise ValueError("report directory cannot be inside /proc, /sys or /dev")
    return parent


def new_report_dir(output):
    if output is None:
        parent = safe_parent(os.environ.get("TMPDIR") or "/tmp")
        return Path(tempfile.mkdtemp(prefix="e87n-board-evidence-", dir=str(parent)))
    path = Path(os.path.abspath(output))
    parent = safe_parent(path.parent)
    path = parent / path.name
    # No parents=True, exist_ok, reuse, deletion or overwriting, including links.
    path.mkdir(mode=0o700)
    return path


class Report:
    def __init__(self, directory, host):
        self.directory = directory
        self.host = host
        self.entries = []
        self.warnings = []
        self.write("README.txt", NOTICE +
                   "Only files in this NEW private directory are written.\n"
                   "Kernel logs are bounded; packet/firewall records are excluded.\n"
                   "Known secret assignments and URI passwords are redacted.\n"
                   "No environment dump or network traffic is collected.\n"
                   "Missing data and permission errors are NOT-COLLECTED, not successful evidence.\n"
                   "Exit 0: collection available; 1: incomplete; 2: setup/report failure.\n")

    def write(self, name, text):
        # Names are fixed by this script, not by sysfs contents or command output.
        fd = os.open(self.directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)

    def record(self, name, source, text="", problems=()):
        status = "NOT-COLLECTED" if problems else "COLLECTED"
        problems = [clean_text(problem) for problem in problems]
        header = "STATUS=%s\nSOURCE=%s\nHARDWARE=NOT-VALIDATED\n" % (status, source)
        if problems:
            header += "Available partial data below does not cover this source completely.\n"
        for problem in problems:
            warning = "NOT-COLLECTED: %s: %s" % (name, problem)
            self.warnings.append(warning)
            header += warning + "\n"
        self.write(name + ".txt", header + "\n" + clean_text(text).rstrip() + "\n")
        self.entries.append({"name": name, "source": source, "status": status,
                             "file": name + ".txt", "warnings": problems})

    def file(self, name, path, nul_separated=False):
        try:
            data = self.host.read(path)
            if nul_separated:
                data = data.replace(b"\0", b"\n")
            self.record(name, path, data)
        except NotCollected as error:
            self.record(name, path, problems=[str(error)])

    def command(self, name, command):
        try:
            self.record(name, " ".join(command), self.host.run(command))
        except NotCollected as error:
            self.record(name, " ".join(command), problems=[str(error)])

    def names(self, path, pattern, problems):
        try:
            names = [name for name in self.host.names(path) if re.fullmatch(pattern, name)]
            if len(names) > 128:
                problems.append(path + ": more than 128 entries; collection truncated")
            return names[:128]
        except NotCollected as error:
            problems.append(path + ": " + str(error))
            return []

    def attributes(self, path, names, lines, problems):
        for name in names:
            source = path + "/" + name
            try:
                lines.append("[%s]\n%s" % (source, clean_text(self.host.read(source)).strip()))
            except NotCollected as error:
                problems.append(source + ": " + str(error))

    def sysfs_group(self, name, base, nodes, attributes, required, driver=False):
        lines, problems = [], []
        devices = self.names(base, nodes, problems)
        if not devices:
            problems.append(base + ": no matching sysfs nodes available")
        for device in devices:
            path = base + "/" + device
            fields = self.names(path, attributes, problems)
            for field in required(device):
                if field not in fields:
                    problems.append(path + "/" + field + ": required evidence attribute unavailable")
            self.attributes(path, fields, lines, problems)
            if driver:
                try:
                    lines.append("[%s/driver]\n%s" % (path, self.host.readlink(path + "/driver")))
                except NotCollected as error:
                    problems.append(path + "/driver: " + str(error))
        self.record(name, base + " (existing nodes; attribute allowlist)", "\n\n".join(lines), problems)

    def pwm(self):
        base = "/sys/class/pwm"
        lines, problems = [], []
        chips = self.names(base, r"pwmchip[0-9]+", problems)
        if not chips:
            problems.append(base + ": no PWM chips available")
        for chip in chips:
            path = base + "/" + chip
            self.attributes(path, ["npwm"], lines, problems)
            channels = self.names(path, r"pwm[0-9]+", problems)
            if not channels:
                problems.append(path + ": no already-exported PWM channels; none will be exported")
            for channel in channels:
                self.attributes(path + "/" + channel, ["period", "duty_cycle", "enable", "polarity"],
                                lines, problems)
        self.record("pwm-sysfs", base + " (already-exported channels only)", "\n\n".join(lines), problems)

    def kernel_logs(self):
        data, selected = None, "journalctl/dmesg"
        for label, command in (("journal-kernel-attempt", JOURNAL), ("dmesg-attempt", DMESG)):
            try:
                data = self.host.run(command)
                selected = " ".join(command)
                break
            except NotCollected as error:
                self.record(label, " ".join(command), problems=[str(error)])
        if data is None:
            self.record("kernel-log-recent", selected, problems=["no readable kernel log source"])
            self.record("phy-firmware-log", selected, problems=["no readable kernel log source"])
            return
        # Do not persist packet/firewall logs or an unfiltered journal copy.
        lines = [line for line in clean_text(data).splitlines() if not PACKET_LOG.search(line)]
        if not lines:
            self.record("kernel-log-recent", selected, problems=["no non-traffic kernel messages available"])
        else:
            self.record("kernel-log-recent", selected + " (last 300 non-traffic lines)", "\n".join(lines[-300:]))
        firmware = [line for line in lines if re.search(r"firmware|mediatek|mt798[78]|mtk[-_].*phy|2p5g|rtl822|\bphy\b", line, re.I)]
        self.record("phy-firmware-log", selected + " (PHY/firmware matches, at most 300)",
                    "\n".join(firmware[-300:]),
                    [] if firmware else ["no PHY/firmware matches in the available bounded kernel log"])

    def finish(self):
        status = "INCOMPLETE" if self.warnings else "COLLECTED"
        self.write("warnings.txt", "\n".join(self.warnings) +
                   ("\n" if self.warnings else "No collection warnings. Hardware remains NOT-VALIDATED.\n"))
        self.write("manifest.json", json.dumps({"collection_status": status, "hardware_status": "NOT-VALIDATED",
                                                "notice": NOTICE, "entries": self.entries,
                                                "warnings": self.warnings}, indent=2) + "\n")
        for warning in self.warnings:
            print(warning, file=sys.stderr)
        print("COLLECTION=%s; HARDWARE=NOT-VALIDATED; report=%s" % (status, self.directory))
        return 1 if self.warnings else 0


def main(argv=None, host=None):
    parser = argparse.ArgumentParser(description=NOTICE, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", metavar="NEW_DIRECTORY",
                        help="create this directory exclusively; parent must exist (default: private mktemp directory)")
    args = parser.parse_args(argv)
    host = host or Host()
    try:
        if host.system() != "Linux":
            raise ValueError("requires Linux; no collection was attempted")
        directory = new_report_dir(args.output)
        print("NEW report directory: " + str(directory))
        report = Report(directory, host)
        for name, path in (("os-release", "/etc/os-release"), ("cmdline", "/proc/cmdline"),
                           ("cpuinfo", "/proc/cpuinfo"), ("meminfo", "/proc/meminfo")):
            report.file(name, path)
        report.file("dt-model", "/sys/firmware/devicetree/base/model", nul_separated=True)
        report.file("dt-compatible", "/sys/firmware/devicetree/base/compatible", nul_separated=True)
        for name, command in COMMANDS:
            report.command(name, command)
        report.sysfs_group("phy-drivers", "/sys/bus/mdio_bus/devices", r"[A-Za-z0-9_.:@+-]+",
                           r"phy_id|modalias", lambda _: ("phy_id",), driver=True)
        report.sysfs_group("thermal-sysfs", "/sys/class/thermal", r"thermal_zone[0-9]+|cooling_device[0-9]+",
                           r"type|temp|mode|policy|cur_state|max_state|trip_point_[0-9]+_(?:temp|type|hyst)",
                           lambda node: ("type", "temp") if node.startswith("thermal_zone") else
                           ("type", "cur_state", "max_state"))
        report.sysfs_group("hwmon-sysfs", "/sys/class/hwmon", r"hwmon[0-9]+",
                           r"name|temp[0-9]+_(?:input|label|max|crit|offset|fault|alarm)|"
                           r"fan[0-9]+_(?:input|min|max|target|fault|alarm)|pwm[0-9]+(?:_(?:enable|mode|freq))?",
                           lambda _: ("name",))
        report.pwm()
        report.kernel_logs()
        return report.finish()
    except (OSError, ValueError) as error:
        print("NOT-COLLECTED: setup/report error: %s; HARDWARE=NOT-VALIDATED" % clean_text(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
PY
