"""Bounded, stdlib-only observations; never a functional hardware validation.

``doctor(root='/')`` returns a JSON-serializable dict, without printing, spawning
commands, opening /dev, or changing configuration. An absolute fixture root is
the only injection seam. Import performs no diagnostic I/O.
"""

from collections import deque
import gzip
import io
import os
from pathlib import Path
import re
import shlex
import stat
import zlib


_VERSION = "6.18.52"
_DT = "sys/firmware/devicetree/base"
_SMALL = 16384
_LARGE = 1024 * 1024
_SCAN = 256
_ENTRIES = 64
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CONTAINERS = (
    "CGROUPS", "MEMCG", "CGROUP_PIDS", "NAMESPACES", "UTS_NS", "IPC_NS",
    "PID_NS", "NET_NS", "USER_NS", "SECCOMP", "SECCOMP_FILTER", "VETH",
    "BRIDGE", "BRIDGE_NETFILTER", "NF_TABLES", "NF_NAT", "OVERLAY_FS",
)
_STORAGE = (
    "EXT4_FS", "MMC", "MMC_BLOCK", "MMC_MTK", "BLK_DEV_NVME", "SCSI",
    "BLK_DEV_SD", "USB_STORAGE", "USB_UAS",
)
_SYMBOLS = _CONTAINERS + _STORAGE + ("BTRFS_FS", "FB_TFT", "FB_TFT_NV3007", "SENSORS_PWM_FAN")
_MODULES = (
    "overlay", "bridge", "br_netfilter", "veth", "nf_tables", "nf_nat",
    "ext4", "btrfs", "nvme", "nvme_core", "mmc_block", "mmc_core",
    "usb_storage", "uas", "fb_nv3007", "fbtft", "pwm_fan",
)


def _normal(parts):
    result = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not result:
                raise ValueError("path escapes root")
            result.pop()
        else:
            result.append(part)
    if len(result) > 64:
        raise ValueError("path too deep")
    return result


class _Reader:
    """Pin directories with openat/no-follow; interpret links inside the root.

    Absolute links are virtual-root relative (as in a chroot), never host-root
    relative. proc and sys links must also stay inside their respective trees.
    No exception text or resolved device paths are included in the report.
    """

    def __init__(self, root):
        self.fd = None
        self.root_parts = []
        try:
            path = Path(root)
            if not path.is_absolute() or ".." in path.parts:
                return
            # Canonicalize only the caller-selected root (macOS /var is a link).
            path = path.resolve(strict=True)
            self.root_parts = list(path.parts[1:])
            self.fd = os.open(path, _DIR_FLAGS)
        except (OSError, ValueError, RuntimeError, TypeError):
            pass

    def close(self):
        if self.fd is not None:
            os.close(self.fd)

    def _open(self, relative, directory=False):
        if self.fd is None:
            raise OSError("root unavailable")
        original = relative.split("/")
        if relative.startswith("/") or ".." in original or not relative:
            raise ValueError("invalid relative path")
        pending = deque(_normal(original))
        walked = []
        hops = 0
        fd = os.dup(self.fd)
        try:
            while pending:
                part = pending.popleft()
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    hops += 1
                    if hops > 16:
                        raise ValueError("too many links")
                    target = os.readlink(part, dir_fd=fd)
                    if len(target) > 4096:
                        raise ValueError("link too long")
                    target_parts = target.split("/")
                    if target.startswith("/"):
                        target_parts = _normal(target_parts)
                        # Also accept an absolute fixture-local link created by
                        # Path.symlink_to(root / target); never resolve on host.
                        if self.root_parts and target_parts[:len(self.root_parts)] == self.root_parts:
                            target_parts = target_parts[len(self.root_parts):]
                        prefix = []
                    else:
                        prefix = walked
                    expanded = _normal(prefix + target_parts + list(pending))
                    if original[0] in ("sys", "proc") and expanded[:1] != original[:1]:
                        raise ValueError("link leaves pseudo filesystem")
                    if relative in ("etc/os-release", "usr/lib/os-release") and expanded not in (
                            ["etc", "os-release"], ["usr", "lib", "os-release"]):
                        raise ValueError("os-release link leaves selected files")
                    pending = deque(expanded)
                    walked = []
                    child = os.dup(self.fd)
                    os.close(fd)
                    fd = child
                    continue
                is_dir = bool(pending) or directory
                if not (stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode)):
                    raise ValueError("not a directory or regular attribute")
                child = os.open(part, _DIR_FLAGS if is_dir else _READ_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                opened = os.fstat(fd)
                if not (stat.S_ISDIR(opened.st_mode) if is_dir else stat.S_ISREG(opened.st_mode)):
                    raise ValueError("attribute changed type")
                walked.append(part)
            result, fd = fd, None
            return result
        finally:
            if fd is not None:
                os.close(fd)

    def read(self, path, limit=_SMALL):
        try:
            fd = self._open(path)
            try:
                data = bytearray()
                while len(data) <= limit:
                    chunk = os.read(fd, limit + 1 - len(data))
                    if not chunk:
                        return bytes(data)
                    data.extend(chunk)
            finally:
                os.close(fd)
        except (OSError, ValueError, RuntimeError):
            pass
        return None

    def text(self, path, limit=_SMALL):
        data = self.read(path, limit)
        try:
            return None if data is None else data.decode("ascii").strip()
        except UnicodeError:
            return None

    def number(self, path, minimum=0, maximum=2**50):
        return _number(self.text(path), minimum, maximum)

    def entries(self, path, pattern):
        """Return (selected names, complete); never recurse or silently truncate."""
        try:
            fd = self._open(path, directory=True)
            try:
                names = []
                with os.scandir(fd) as entries:
                    for index, entry in enumerate(entries):
                        if index >= _SCAN:
                            return sorted(names), False
                        if re.fullmatch(pattern, entry.name):
                            if len(names) >= _ENTRIES:
                                return sorted(names), False
                            names.append(entry.name)
                return sorted(names), True
            finally:
                os.close(fd)
        except (OSError, ValueError, RuntimeError):
            return [], False


def _number(value, minimum=0, maximum=2**50):
    if value is None or not re.fullmatch(r"-?[0-9]{1,16}", value):
        return None
    value = int(value)
    return value if minimum <= value <= maximum else None


def _check(status, summary, **details):
    return {"status": status, "summary": summary, "details": details}


def _identity(reader):
    source = "etc/os-release"
    text = reader.text(source)
    if text is None:
        source, text = "usr/lib/os-release", reader.text("usr/lib/os-release")
    values = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in ("ID", "VERSION_ID", "VERSION_CODENAME"):
            continue
        try:
            fields = shlex.split(value, comments=True)
            parsed = fields[0] if len(fields) == 1 else None
        except ValueError:
            parsed = None
        values[key] = None if key in values else parsed
    matches = {
        "debian": None if values.get("ID") is None else values["ID"] == "debian",
        "version_13": None if values.get("VERSION_ID") is None else bool(
            re.fullmatch(r"13(?:\.[0-9]{1,3})?", values["VERSION_ID"])),
        "trixie": None if values.get("VERSION_CODENAME") is None else values["VERSION_CODENAME"] == "trixie",
    }
    status = "error" if False in matches.values() else (
        "ok" if matches["debian"] and matches["version_13"] else "unknown")
    os_check = _check(status, "Compare observed os-release with Debian 13 Trixie.",
                      source="/" + source if text is not None else None, **matches)
    release = reader.text("proc/sys/kernel/osrelease")
    match = re.fullmatch(r"([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})(?:[-+][A-Za-z0-9_.+-]{1,95})?", release or "")
    version = match[1] if match else None
    kernel = _check("unknown" if version is None else "ok" if version == _VERSION else "error",
                    "Compare the running kernel release with the pinned target; suffix is omitted.",
                    version=version, expected_version=_VERSION)
    model = reader.read(f"{_DT}/model")
    compatible = reader.read(f"{_DT}/compatible")
    model_match = None if not model or not model.endswith(b"\0") else model.rstrip(b"\0").lower() == b"edgepi e87n"
    compatible_match = None if not compatible or not compatible.endswith(b"\0") else b"edgepi,e87n" in compatible.split(b"\0")
    dt_matches = [model_match, compatible_match]
    dt = _check("error" if False in dt_matches else "ok" if all(dt_matches) else "unknown",
                "Observe E87N device-tree identity; this is not a board functionality test.",
                model_matches=model_match, compatible_matches=compatible_match)
    return os_check, kernel, dt, release if match else None


def _memory(reader):
    text = reader.text("proc/meminfo")
    lines = [line for line in (text or "").splitlines() if line.startswith("MemTotal:")]
    match = re.fullmatch(r"MemTotal:\s+([0-9]{1,16})\s+kB", lines[0]) if len(lines) == 1 else None
    total = _number(match[1], 1) if match else None
    low = None if total is None else total <= 256 * 1024
    return _check("unknown" if total is None else "warning" if low else "ok",
                  "MemTotal is kernel-visible RAM. At or below 256 MiB, review DT/U-Boot memory handoff against the known 1 GiB board.",
                  mem_total_kib=total, known_board_nominal_mib=1024,
                  low_memory_threshold_mib=256, at_or_below_threshold=low)


def _root_filesystem(reader):
    source, text = "proc/self/mountinfo", reader.text("proc/self/mountinfo", _LARGE)
    if text is None:
        source, text = "proc/mounts", reader.text("proc/mounts", _LARGE)
    roots = []
    for line in (text or "").splitlines():
        if source.endswith("mountinfo"):
            before, sep, after = line.partition(" - ")
            left, right = before.split(), after.split()
            if sep and len(left) >= 6 and len(right) >= 3 and left[4] == "/":
                roots.append((right[0], [left[5].split(","), right[2].split(",")]))
        else:
            fields = line.split()
            if len(fields) >= 4 and fields[1] == "/":
                roots.append((fields[2], [fields[3].split(",")]))
    fstype, read_only = None, None
    if len(roots) == 1:
        kind, option_groups = roots[0]
        fstype = kind if kind in ("ext4", "btrfs", "f2fs", "xfs", "overlay", "tmpfs", "rootfs", "squashfs") else "other"
        if all(("ro" in options) != ("rw" in options) for options in option_groups):
            read_only = any("ro" in options for options in option_groups)
    status = "unknown" if text is None or len(roots) > 1 or (roots and read_only is None) else (
        "warning" if not roots or read_only or fstype in ("tmpfs", "rootfs", "squashfs") else "ok")
    return _check(status, "Observe the caller's mounted root; no disk probing or filesystem write test.",
                  source="/" + source if text is not None else None,
                  root_present=None if text is None else bool(roots),
                  ambiguous=len(roots) > 1, filesystem=fstype, read_only=read_only)


def _cmdline(reader):
    text = reader.text("proc/cmdline")
    try:
        fields = shlex.split(text) if text is not None else None
    except ValueError:
        fields = None
    roots = [field for field in fields or () if field.startswith("root=")]
    root_present = None if fields is None else any(len(field) > 5 for field in roots)
    memory_limit = None if fields is None else any(field.startswith("mem=") for field in fields)
    conflict = None if fields is None else "ro" in fields and "rw" in fields
    status = "unknown" if fields is None else "warning" if not root_present or len(roots) != 1 or memory_limit or conflict else "ok"
    return _check(status, "Report selected boot-argument presence only; argument values are never returned.",
                  readable=text is not None, nonempty=None if fields is None else bool(fields),
                  root_argument_present=root_present,
                  multiple_root_arguments=None if fields is None else len(roots) > 1,
                  memory_limit_present=memory_limit,
                  rootwait_present=None if fields is None else "rootwait" in fields,
                  conflicting_ro_rw=conflict)


def _network(reader):
    names, complete = reader.entries("sys/class/net", r"[A-Za-z0-9_.:-]{1,15}")
    interfaces = []
    for name in names:
        if name == "lo":
            continue
        path = f"sys/class/net/{name}"
        carrier = reader.number(f"{path}/carrier", 0, 1)
        assignment = reader.number(f"{path}/addr_assign_type", 0, 3)
        state = reader.text(f"{path}/operstate")
        if state not in ("unknown", "notpresent", "down", "lowerlayerdown", "testing", "dormant", "up"):
            state = None
        interfaces.append({"interface": len(interfaces) + 1, "carrier": carrier,
                           "operstate": state, "addr_assign_type": assignment,
                           "address_assignment": None if assignment is None else
                           ("permanent", "random", "inherited", "set")[assignment]})
    linked = any(item["carrier"] == 1 for item in interfaces)
    if not linked and (not complete or any(item["carrier"] is None for item in interfaces)):
        linked = None
    random = any(item["addr_assign_type"] == 1 for item in interfaces)
    if not random and (not complete or any(item["addr_assign_type"] is None for item in interfaces)):
        random = None
    missing = any(item["carrier"] is None or item["addr_assign_type"] is None for item in interfaces)
    status = "ok" if complete and linked and not random and not missing else "warning"
    return _check(status, "Observe non-loopback links and address provenance; carrier is not DHCP, DNS or connectivity validation.",
                  enumeration_complete=complete, interfaces=interfaces, any_carrier=linked,
                  random_address_observed=random)


def _thermal_fan(reader):
    names, thermal_complete = reader.entries("sys/class/thermal", r"(?:thermal_zone|cooling_device)[0-9]{1,6}")
    zones, cooling = [], []
    for name in names:
        path = f"sys/class/thermal/{name}"
        kind = reader.text(f"{path}/type")
        if name.startswith("thermal_zone"):
            cpu = bool(re.search(r"(?:^|[^a-z])(?:cpu|soc)[0-9]*(?:[^a-z]|$)", (kind or "").lower()))
            zones.append({"zone": len(zones) + 1, "cpu_or_soc": cpu if kind is not None else None,
                          "temp_mc": reader.number(f"{path}/temp", -40000, 200000)})
        elif kind == "pwm-fan":
            maximum = reader.number(f"{path}/max_state", 0, 255)
            cooling.append({"state": reader.number(f"{path}/cur_state", 0, maximum if maximum is not None else 255),
                            "max_state": maximum})
    names, hwmon_complete = reader.entries("sys/class/hwmon", r"hwmon[0-9]{1,6}")
    fans = []
    for name in names:
        path = f"sys/class/hwmon/{name}"
        if reader.text(f"{path}/name") in ("pwmfan", "pwm-fan"):
            fans.append({"pwm": reader.number(f"{path}/pwm1", 0, 255),
                         "rpm": reader.number(f"{path}/fan1_input", 0, 200000)})
    cpu_available = any(item["cpu_or_soc"] and item["temp_mc"] is not None for item in zones)
    thermal = _check("ok" if thermal_complete and cpu_available else "warning",
                     "Observe temperature attributes; this does not establish sensor accuracy or safe load temperatures.",
                     enumeration_complete=thermal_complete, zones=zones)
    control = any(item["state"] is not None and item["max_state"] is not None for item in cooling)
    fan = _check("ok" if thermal_complete and hwmon_complete and control else "warning",
                 "Observe pwm-fan cooling controls and optional tachometer; a control value does not prove rotation or cooling.",
                 enumeration_complete=thermal_complete and hwmon_complete,
                 cooling_devices=cooling, hwmon=fans, control_available=control)
    return thermal, fan


def _kernel_features(reader, release):
    source, config = None, None
    for path in ("proc/config.gz", "proc/config") + ((f"boot/config-{release}",) if release else ()):
        data = reader.read(path, _LARGE)
        if data is None:
            continue
        source = path
        try:
            if path.endswith(".gz"):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                    data = compressed.read(_LARGE + 1)
            if len(data) <= _LARGE:
                config = data.decode("ascii")
        except (OSError, EOFError, UnicodeError, zlib.error):
            pass
        break
    states, seen = dict.fromkeys("CONFIG_" + symbol for symbol in _SYMBOLS), set()
    for line in (config or "").splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=(.*)", line)
        disabled = re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line)
        key = match[1] if match else disabled[1] if disabled else None
        if key in states:
            value = match[2] if match else "n"
            states[key] = value if key not in seen and value in ("y", "m", "n") else None
            seen.add(key)
    is_runtime = source in ("proc/config.gz", "proc/config")
    config_known = any(value is not None for value in states.values())
    config_check = _check("unknown" if not config_known else "ok" if is_runtime else "warning",
                          "Read kernel configuration; a matching boot filename alone does not verify the running binary.",
                          source=None if source is None else "/" + source if is_runtime else "/boot/config-<running-release>",
                          running_kernel_source=is_runtime if source else None,
                          readable=config is not None)
    modules_text = reader.text("proc/modules", _LARGE)
    loaded = set()
    valid = modules_text is not None
    for line in (modules_text or "").splitlines():
        fields = line.split()
        if len(fields) < 6 or not re.fullmatch(r"[A-Za-z0-9_]{1,128}", fields[0]) or not fields[1].isdigit():
            valid = False
            continue
        if fields[0] in _MODULES:
            loaded.add(fields[0])
    modules = {name: name in loaded if valid else None for name in _MODULES}
    module_check = _check("ok" if valid else "unknown",
                          "Selected loaded modules only. False does not exclude built-in or unloaded support; no module is loaded by doctor.",
                          loaded=modules)
    groups = []
    for symbols in (_CONTAINERS, _STORAGE):
        selected = {"CONFIG_" + symbol: states["CONFIG_" + symbol] for symbol in symbols}
        status = "warning" if "n" in selected.values() else "unknown" if None in selected.values() else "ok"
        if status == "ok" and not is_runtime:
            status = "warning"
        groups.append(_check(status, "Selected configuration prerequisites only: y=built-in, m=module, n=disabled, null=unavailable or ambiguous. No runtime workload is tested.",
                             config=selected))
    return config_check, module_check, groups[0], groups[1], states, modules


def _nv3007(reader, states, modules):
    names, complete = reader.entries("sys/class/graphics", r"fb[0-9]{1,6}")
    framebuffers = []
    for name in names:
        path = f"sys/class/graphics/{name}"
        if reader.text(f"{path}/name") != "fb_nv3007":
            continue
        size = reader.text(f"{path}/virtual_size")
        match = re.fullmatch(r"([0-9]{1,5}),([0-9]{1,5})", size or "")
        dimensions = [int(match[1]), int(match[2])] if match else None
        framebuffers.append({"size": dimensions, "bits_per_pixel": reader.number(f"{path}/bits_per_pixel", 1, 64)})
    expected = bool(framebuffers) and all(item["size"] == [428, 142] and item["bits_per_pixel"] == 16 for item in framebuffers)
    return _check("ok" if complete and expected else "warning",
                  "Observe an NV3007 framebuffer registration and geometry; pixels, backlight and panel function are not tested.",
                  enumeration_complete=complete, framebuffers=framebuffers,
                  module_loaded=modules["fb_nv3007"], config=states["CONFIG_FB_TFT_NV3007"])


def doctor(root="/"):
    """Return fixed named observations; unknown data is nonzero, never invented.

    ``status`` is error if any check errors, warning if any warns/is unknown,
    otherwise ok. ``exit_code`` is respectively 2, 1, 0. CLI integration should
    print JSON with allow_nan=False and return exit_code, without claiming that
    an exit code of zero is a functional or security certification.
    """
    reader = _Reader(root)
    try:
        os_check, kernel, dt, release = _identity(reader)
        thermal, fan = _thermal_fan(reader)
        config, modules, containers, storage, states, loaded = _kernel_features(reader, release)
        checks = {
            "root_access": _check("ok" if reader.fd is not None else "error",
                                  "The selected diagnostic root must be an accessible absolute directory."),
            "os": os_check, "kernel": kernel, "device_tree": dt,
            "memory": _memory(reader), "root_filesystem": _root_filesystem(reader),
            "cmdline": _cmdline(reader), "network": _network(reader),
            "thermal": thermal, "fan": fan, "nv3007": _nv3007(reader, states, loaded),
            "kernel_config": config, "kernel_modules": modules,
            "containers": containers, "storage": storage,
        }
        warnings = [name for name, check in checks.items() if check["status"] in ("warning", "unknown")]
        errors = [name for name, check in checks.items() if check["status"] == "error"]
        status = "error" if errors else "warning" if warnings else "ok"
        return {"schema_version": 1, "status": status, "exit_code": 2 if errors else 1 if warnings else 0,
                "hardware_validation": "not-performed", "checks": checks,
                "warnings": warnings, "errors": errors}
    finally:
        reader.close()
