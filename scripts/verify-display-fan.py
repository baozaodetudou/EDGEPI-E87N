#!/usr/bin/env python3
"""Read-only, host-side E87N display/fan build audit (Python >= 3.8).

Usage: python3 -B scripts/verify-display-fan.py --rootfs PATH --config PATH --dtb PATH
PATH for rootfs is an already accessible directory; this program never mounts,
extracts, chroots, imports target packages, executes target code, or writes files.
Only the host's fdtget (device-tree-compiler) is executed. Python sources are
parsed/compiled in memory, JSON and Kconfig are data, and dependencies/modules
are checked by installed files. Unit inspection catches conflicting fan units
and direct PWM/cooling writers; it is not a general analysis of arbitrary code.
Success means a STATIC artifact contract only, never boot or hardware PASS.
The caller must supply stable artifacts from the same build.
"""

import sys

sys.dont_write_bytecode = True

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

RELEASE = "6.18.51-current-filogic"
REQUIRED_Y = """SPI SPI_MASTER SPI_MT65XX STAGING FB FB_DEVICE
BACKLIGHT_CLASS_DEVICE BACKLIGHT_PWM THERMAL MTK_LVTS_THERMAL PWM
PWM_MEDIATEK HWMON SENSORS_PWM_FAN""".split()
DISPLAY = "/soc/spi@11009800/display@0"
PWM = "/soc/pwm@10048000"
BACKLIGHT = "/backlight"
FAN = "/pwm-fan"
LVTS = "/soc/lvts@1100a000"
ZONE = "/thermal-zones/cpu-thermal"
PACKAGE = "/usr/lib/python3/dist-packages/e87n"
SERVICE = "/usr/lib/systemd/system/e87n-display.service"
ENABLE = "/etc/systemd/system/multi-user.target.wants/e87n-display.service"
DEFAULT = {"enabled": True, "brightness_percent": 20, "screen": "overview",
           "refresh_seconds": 2}
UNIT_DIRS = ("/etc/systemd/system", "/run/systemd/system",
             "/usr/local/lib/systemd/system", "/usr/lib/systemd/system",
             "/lib/systemd/system")


class Invalid(ValueError):
    """An input does not meet this board's static build contract."""


def require(condition, message):
    if not condition:
        raise Invalid(message)


def read_file(path, limit=2 * 1024 * 1024, allow_empty=False):
    info = path.stat()
    require(stat.S_ISREG(info.st_mode), "not a regular file: " + str(path))
    require((allow_empty or info.st_size > 0) and info.st_size <= limit,
            "invalid file size: " + str(path))
    return path.read_bytes()


def rooted(root, name):
    """Resolve target symlinks inside root, including absolute and usr-merge links."""
    parts, pending, hops = [], name.split("/"), 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            require(bool(parts), "path escapes rootfs: " + name)
            parts.pop()
            continue
        path = root.joinpath(*parts, part)
        if path.is_symlink():
            hops += 1
            require(hops <= 40, "rootfs symlink loop: " + name)
            target = os.readlink(path)
            if target.startswith("/"):
                parts = []
            pending = target.split("/") + pending
        else:
            parts.append(part)
    return root.joinpath(*parts)


def check_config(data):
    text = data.decode("utf-8")
    headers = re.findall(r"^# Linux/.* Kernel Configuration$", text, re.M)
    require(len(headers) == 1 and headers[0] in (
        "# Linux/arm64 6.18.51 Kernel Configuration",
        "# Linux/arm64 " + RELEASE + " Kernel Configuration"),
        "config needs one Linux/arm64 6.18.51 Kernel Configuration header")
    options = {}
    value = r'(?:[ymn]|-?[0-9]+|0[xX][0-9a-fA-F]+|"(?:[^"\\\r\n]|\\[^\r\n])*")'
    for line in text.splitlines():
        assignment = re.fullmatch(r"CONFIG_([A-Za-z0-9_]+)=(" + value + r")", line)
        unset = re.fullmatch(r"# CONFIG_([A-Za-z0-9_]+) is not set", line)
        if assignment or unset:
            key, setting = (assignment[1], assignment[2]) if assignment else (unset[1], "n")
            require(key not in options, "duplicate CONFIG_" + key)
            options[key] = setting
        else:
            require(not line.strip() or line.startswith("#"),
                    "malformed config line: " + line)
    expected = dict.fromkeys(REQUIRED_Y, "y")
    expected.update(FB_TFT="m", FB_TFT_NV3007="m", FRAMEBUFFER_CONSOLE="n", CPU_FREQ="n")
    for key, setting in expected.items():
        require(options.get(key) == setting, "CONFIG_%s must be %s" % (key, setting))


class Fdt:
    """Small lazy fdtget adapter; inspect only this board's relevant nodes."""

    def __init__(self, tool, path):
        self.tool, self.path = str(tool), str(path)
        self.cache = {}

    def run(self, *args):
        result = subprocess.run([self.tool, *args], capture_output=True,
                                text=True, timeout=10)
        require(result.returncode == 0, "host fdtget failed: " + result.stderr.strip())
        return result.stdout

    def properties(self, node):
        key = (node, None)
        if key not in self.cache:
            self.cache[key] = set(self.run("-p", self.path, node).splitlines())
        return self.cache[key]

    def children(self, node):
        names = self.run("-l", self.path, node).splitlines()
        require(len(names) <= 256 and len(set(names)) == len(names) and
                all(name and "/" not in name and name not in (".", "..") for name in names),
                "invalid child names at " + node)
        return [node.rstrip("/") + "/" + name for name in names]

    def raw(self, node, prop):
        require(prop in self.properties(node), "missing %s:%s" % (node, prop))
        key = (node, prop)
        if key not in self.cache:
            words = self.run("-t", "bx", self.path, node, prop).split()
            require(all(re.fullmatch(r"[0-9a-fA-F]{1,2}", word) for word in words),
                    "invalid fdtget bytes at %s:%s" % key)
            self.cache[key] = bytes(int(word, 16) for word in words)
        return self.cache[key]

    def cells(self, node, prop):
        data = self.raw(node, prop)
        require(len(data) % 4 == 0, "non-cell property %s:%s" % (node, prop))
        return [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]

    def scalar(self, node, prop):
        values = self.cells(node, prop)
        require(len(values) == 1, "expected one cell at %s:%s" % (node, prop))
        return values[0]

    def strings(self, node, prop):
        data = self.raw(node, prop)
        require(data.endswith(b"\0"), "unterminated DT string at %s:%s" % (node, prop))
        values = data[:-1].decode("ascii").split("\0")
        require(all(values), "empty DT string at %s:%s" % (node, prop))
        return values

    def enabled(self, node):
        while True:
            if "status" in self.properties(node):
                require(self.strings(node, "status") in (["okay"], ["ok"]),
                        "disabled/unavailable DT node " + node)
            if node == "/":
                return
            node = node.rsplit("/", 1)[0] or "/"

    def compatible(self, node, value):
        self.enabled(node)
        require(value in self.strings(node, "compatible"), "wrong compatible at " + node)

    def phandle(self, node):
        values = [self.scalar(node, prop) for prop in ("phandle", "linux,phandle")
                  if prop in self.properties(node)]
        require(values and len(set(values)) == 1 and values[0] not in (0, 0xffffffff),
                "missing/invalid phandle at " + node)
        return values[0]


def check_dtb(dt):
    dt.compatible("/", "edgepi,e87n")
    dt.compatible(DISPLAY, "newvisionu,nv3007")
    for prop, value in {"reg": 0, "width": 142, "height": 428, "rotate": 270,
                        "spi-max-frequency": 52000000, "fps": 30}.items():
        require(dt.scalar(DISPLAY, prop) == value, "display %s must be %d" % (prop, value))
    dt.compatible(PWM, "mediatek,mt7987-pwm")
    require(dt.scalar(PWM, "#pwm-cells") == 2,
            "PWM #pwm-cells must be 2 (normal polarity, no inversion flags)")
    pwm = dt.phandle(PWM)
    for node, compatible, channel in ((BACKLIGHT, "pwm-backlight", 2), (FAN, "pwm-fan", 1)):
        dt.compatible(node, compatible)
        require(dt.cells(node, "pwms") == [pwm, channel, 50000],
                "%s PWM must use channel %d, period 50000, exactly two arguments; normal polarity" %
                (node, channel))
    require(dt.cells(BACKLIGHT, "brightness-levels") == list(range(0, 251, 10)) + [255],
            "backlight brightness-levels must be 0,10..250,255")
    require(dt.scalar(BACKLIGHT, "default-brightness-level") == 26,
            "backlight default-brightness-level must be 26")
    require(dt.cells(FAN, "cooling-levels") == [0, 128, 192, 255],
            "fan cooling-levels must be 0/128/192/255")
    require(dt.scalar(FAN, "#cooling-cells") == 2, "fan #cooling-cells must be 2")
    dt.compatible(LVTS, "mediatek,mt7987-lvts-ap")
    dt.enabled(ZONE)
    require(dt.scalar(LVTS, "#thermal-sensor-cells") == 1 and
            dt.cells(ZONE, "thermal-sensors") == [dt.phandle(LVTS), 0],
            "cpu-thermal must reference LVTS sensor 0")
    for prop in ("polling-delay", "polling-delay-passive"):
        require(0 < dt.scalar(ZONE, prop) <= 0x7fffffff, prop + " must be positive")
    active = {}
    for trip in dt.children(ZONE + "/trips"):
        dt.enabled(trip)
        if dt.strings(trip, "type") == ["active"]:
            handle = dt.phandle(trip)
            require(handle not in active, "duplicate active trip phandle")
            active[handle] = dt.scalar(trip, "temperature")
    require(len(active) == 3 and set(active.values()) == {50000, 65000, 75000},
            "active thermal trips must be exactly 50/65/75 C")
    fan = dt.phandle(FAN)
    require(len({pwm, fan, dt.phandle(LVTS), *active}) == 6,
            "duplicate phandle in display/fan thermal providers")
    seen, states = set(), {50000: 1, 65000: 2, 75000: 3}
    for mapping in dt.children(ZONE + "/cooling-maps"):
        dt.enabled(mapping)
        trip = dt.scalar(mapping, "trip")
        require(trip in active and trip not in seen, "wrong/duplicate fan cooling map trip")
        seen.add(trip)
        state = states[active[trip]]
        require(dt.cells(mapping, "cooling-device") == [fan, state, state],
                "fan cooling map must use state %d/%d at %d C" % (state, state, active[trip] // 1000))
    require(seen == set(active), "missing fan cooling maps for 50/65/75 C")


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate display.json key: " + key)
        result[key] = value
    return result


def unit_entries(data):
    """Parse assignments and continuations without invoking systemd or a shell."""
    section, pending, entries = None, "", []
    for raw in data.decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        line = pending + line
        if line.endswith("\\"):
            pending = line[:-1].rstrip() + " "
            continue
        pending = ""
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        require(section and "=" in line, "malformed systemd unit line: " + line)
        key, value = line.split("=", 1)
        entries.append((section, key.strip(), value.strip()))
    require(not pending, "unfinished systemd unit continuation")
    return entries


def check_service(root):
    service = rooted(root, SERVICE)
    entries = unit_entries(read_file(service))
    for key, command in (("ExecStartPre", "/usr/bin/e87nctl display apply"),
                         ("ExecStart", "/usr/bin/python3 -I -m e87n.display --daemon")):
        values = [value for section, name, value in entries if section == "Service" and name == key]
        require(values == [command], "e87n-display.service %s must be exactly %s" % (key, command))
    require(any(section == "Install" and key == "WantedBy" and
                "multi-user.target" in value.split() for section, key, value in entries),
            "e87n-display.service must have WantedBy=multi-user.target")
    link = rooted(root, "/etc/systemd/system/multi-user.target.wants") / "e87n-display.service"
    require(link.is_symlink(), "missing enable symlink: " + ENABLE)
    require(rooted(root, ENABLE) == service, "enable symlink must resolve to " + SERVICE)
    for directory in UNIT_DIRS:
        candidate = rooted(root, directory) / "e87n-display.service"
        if candidate.exists() or candidate.is_symlink():
            require(rooted(root, directory + "/e87n-display.service") == service,
                    "unexpected e87n-display.service override in " + directory)
        for name in ("service.d", "e87n-.service.d", "e87n-display.service.d"):
            dropins = rooted(root, directory + "/" + name)
            if dropins.exists():
                require(not any(p.name.endswith(".conf") for p in dropins.iterdir()),
                        "unreviewed display service drop-in: " + directory + "/" + name)


def check_conflicts(root):
    legacy = {"display", "display-control", "display_control", "fancontrol", "fan-control",
              "fan_control", "pwm-fan", "pwmfan"}
    for directory in ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin",
                      "/usr/local/sbin", "/etc/init.d", "/etc/config"):
        path = rooted(root, directory)
        if path.exists():
            for item in path.iterdir():
                require(re.sub(r"\.sh$", "", item.name) not in legacy,
                        "forbidden OpenWrt/display/fancontrol program or config: " + directory + "/" + item.name)
    visited = set()
    for directory in UNIT_DIRS:
        pending = [directory]
        while pending:
            logical = pending.pop()
            path = rooted(root, logical)
            if not path.exists() or path in visited:
                continue
            visited.add(path)
            require(len(visited) <= 8192, "excessive systemd directory hierarchy")
            for item in path.iterdir():
                name = logical + "/" + item.name
                # Resolve aliases inside the target root; visited prevents loops.
                if item.is_symlink() and os.readlink(item) == "/dev/null":
                    continue
                target = rooted(root, name)
                if target.is_dir():
                    pending.append(name)
                    continue
                if not item.name.endswith((".service", ".timer", ".conf")):
                    continue
                require(not re.search(r"fan|pwm[-_]", item.name, re.I),
                        "additional fan writer unit: " + name)
                if not target.exists():
                    continue  # unrelated dangling enable link; required link checked separately
                entries = unit_entries(read_file(target, allow_empty=True))
                commands = "\n".join(value for section, key, value in entries
                                     if section == "Service" and key.startswith("Exec"))
                require(not re.search(r"fan[-_]?control|pwm[-_]?fan|\be87nctl\s+fan\s+(?:set|auto|manual)\b|"
                                      r"/(?:sys|dev)/[^\s;]*?(?:pwm[0-9]|pwmchip|cooling_device|cur_state)|"
                                      r"\b(?:pwm[0-9]+(?:_enable)?|duty_cycle|cur_state)\b", commands, re.I),
                        "additional fan/PWM writer command: " + name)


def check_rootfs(root):
    require(root.is_dir(), "--rootfs must be an already accessible directory")
    for name in ("hardware.py", "display.py", "__main__.py", "__init__.py"):
        path = rooted(root, PACKAGE + "/" + name)
        data = read_file(path, allow_empty=(name == "__init__.py"))
        tree = ast.parse(data, filename=str(path))
        compile(tree, str(path), "exec")  # in memory only: never execute/import target code
    control = rooted(root, "/usr/bin/e87nctl")
    read_file(control)
    require(control.stat().st_mode & 0o111, "/usr/bin/e87nctl must be executable")
    config = json.loads(read_file(rooted(root, "/etc/e87n/display.json")),
                        object_pairs_hook=json_object)
    require(isinstance(config, dict) and config == DEFAULT and
            all(type(config[key]) is type(value) for key, value in DEFAULT.items()),
            "display.json must contain exactly the defaults: " + json.dumps(DEFAULT, sort_keys=True))
    check_service(root)
    modules = read_file(rooted(root, "/etc/modules-load.d/e87n-display.conf")).decode("utf-8")
    names = [line.strip() for line in modules.splitlines()
             if line.strip() and not line.lstrip().startswith(("#", ";"))]
    require(names == ["fb_nv3007"], "e87n-display.conf must load fb_nv3007 exactly once")
    for base in ("fb_nv3007", "fbtft"):
        found = set()
        for directory in ("/lib/modules/", "/usr/lib/modules/"):
            for suffix in (".ko", ".ko.zst", ".ko.xz"):
                path = rooted(root, directory + RELEASE + "/kernel/drivers/staging/fbtft/" + base + suffix)
                if path.exists():
                    info = path.stat()
                    require(stat.S_ISREG(info.st_mode) and info.st_size > 0,
                            "invalid installed module: " + str(path))
                    found.add(path)
        require(len(found) == 1, "need exactly one installed %s module for %s in kernel/drivers/staging/fbtft" %
                (base, RELEASE))
    for dependency in ("/usr/lib/python3/dist-packages/PIL/Image.py",
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        read_file(rooted(root, dependency), limit=16 * 1024 * 1024)
    check_conflicts(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rootfs", required=True, type=Path, help="already accessible target root directory")
    parser.add_argument("--config", required=True, type=Path, help="final Linux/arm64 6.18.51 kernel config")
    parser.add_argument("--dtb", required=True, type=Path, help="final compiled E87N DTB (not DTS source)")
    args = parser.parse_args(argv)
    try:
        root = args.rootfs.resolve(strict=True)
        config_path, dtb_path = args.config.resolve(strict=True), args.dtb.resolve(strict=True)
        config, dtb = read_file(config_path), read_file(dtb_path, limit=16 * 1024 * 1024)
        check_config(config)
        check_rootfs(root)
        tool = shutil.which("fdtget")
        require(tool is not None, "host fdtget is required (install device-tree-compiler on the host)")
        tool = Path(tool).resolve(strict=True)
        require(root != tool and root not in tool.parents, "fdtget must be a host tool outside --rootfs")
        check_dtb(Fdt(tool, dtb_path))
        require(read_file(config_path) == config and read_file(dtb_path, limit=16 * 1024 * 1024) == dtb,
                "config/DTB changed during audit; retry with stable artifacts")
    except (Invalid, OSError, ValueError, UnicodeError, SyntaxError, RecursionError,
            subprocess.SubprocessError) as exc:
        print("FAIL: static E87N display/fan audit: " + str(exc), file=sys.stderr)
        return 1
    print("CONFIG SHA256: " + hashlib.sha256(config).hexdigest())
    print("DTB SHA256: " + hashlib.sha256(dtb).hexdigest())
    print("PASS: STATIC E87N display/fan contract for " + RELEASE +
          "; no boot, runtime, or hardware validation performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
