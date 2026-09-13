"""Bounded Linux hardware samples and narrowly scoped E87N backlight writes.

Public readers use the real Debian paths; only the private backend accepts a
fixture root/identity. There are no environment or CLI path overrides. Missing
config uses defaults; an existing config must contain exactly the documented
fields, with integer refresh_seconds in 2..60. Invalid config raises
HardwareError, so a worker cannot accidentally turn on a disabled display.
"""

import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
from contextlib import ExitStack, contextmanager


_DEFAULT_CONFIG = {
    "enabled": True,
    "brightness_percent": 20,
    "screen": "overview",
    "refresh_seconds": 2,
}
_SCREENS = ("overview", "thermal", "network", "storage")
_CONFIG_DIR = "etc/e87n"
_CONFIG_NAME = "display.json"
_DT = "sys/firmware/devicetree/base"
_BACKLIGHT = "sys/devices/platform/backlight/backlight/backlight"
_PLATFORM = "sys/devices/platform/backlight"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_MAX_BYTES = 16384
_MAX_ENTRIES = 256


class HardwareError(ValueError):
    """Configuration or hardware identity failed validation; no fallback write."""


def _validate_config(config):
    if type(config) is not dict or set(config) != set(_DEFAULT_CONFIG):
        raise HardwareError("display config must contain exactly: " + ", ".join(_DEFAULT_CONFIG))
    if type(config["enabled"]) is not bool:
        raise HardwareError("enabled must be a boolean")
    percent = config["brightness_percent"]
    if type(percent) is not int or not 0 <= percent <= 100:
        raise HardwareError("brightness_percent must be an integer in 0..100")
    if type(config["screen"]) is not str or config["screen"] not in _SCREENS:
        raise HardwareError("screen must be overview, thermal, network or storage")
    refresh = config["refresh_seconds"]
    if type(refresh) is not int or not 2 <= refresh <= 60:
        raise HardwareError("refresh_seconds must be an integer in 2..60")
    return dict(config)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HardwareError("duplicate config key: " + key)
        result[key] = value
    return result


def _raw_brightness(percent, maximum=26):
    if type(percent) is not int or not 0 <= percent <= 100 or maximum != 26:
        raise HardwareError("invalid E87N backlight brightness or maximum")
    return (maximum * (100 - percent) + 50) // 100


def _integer(text, minimum, maximum):
    if text is None or not re.fullmatch(r"-?[0-9]{1,20}", text):
        return None
    value = int(text)
    return value if minimum <= value <= maximum else None


class _Hardware:
    """Private dependency injection seam; production always uses / and uid 0."""

    def __init__(self, *, _root="/", _owner_uid=0, _euid=None):
        self.root = Path(_root)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise HardwareError("fixture root must be absolute")
        self.owner_uid = _owner_uid
        self.euid = os.geteuid if _euid is None else _euid
        self._previous_cpu = None

    def _trusted(self, info, *, directory=False):
        valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not valid_type or info.st_uid != self.owner_uid
                or info.st_mode & 0o022 or (not directory and info.st_nlink != 1)):
            raise HardwareError("config must be root-owned, regular and not group/world writable; no links")

    @contextmanager
    def _directory(self, relative, *, trusted=False, create=False):
        """Walk each component with openat/O_NOFOLLOW; keep the parent pinned."""
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise HardwareError("invalid relative path")
        fd = os.open(self.root, _DIR_FLAGS)
        try:
            if trusted:
                self._trusted(os.fstat(fd), directory=True)
            for part in parts:
                try:
                    child = os.open(part, _DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                    child = os.open(part, _DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                if trusted:
                    self._trusted(os.fstat(fd), directory=True)
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _read_at(directory, name, *, limit=_MAX_BYTES, validate=None):
        fd = os.open(name, _READ_FLAGS, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise HardwareError("not a regular attribute/config file")
            if validate is not None:
                validate(info)
            chunks = bytearray()
            while len(chunks) <= limit:
                chunk = os.read(fd, limit + 1 - len(chunks))
                if not chunk:
                    return bytes(chunks)
                chunks.extend(chunk)
            raise HardwareError("attribute/config exceeds size limit")
        finally:
            os.close(fd)

    def _read_direct(self, relative, *, limit=_MAX_BYTES):
        path = Path(relative)
        with self._directory(path.parent) as directory:
            return self._read_at(directory, path.name, limit=limit)

    def _sys_directory(self, relative):
        # Kernel class/device links are expected. Resolve directories only,
        # constrain their destination, then reopen every component no-follow.
        resolved = (self.root / relative).resolve(strict=True)
        allowed = ("sys/devices", "sys/class/thermal", "sys/class/hwmon",
                   "sys/class/net", "sys/class/nvme", _DT)
        if not any(resolved.is_relative_to(self.root / prefix) for prefix in allowed):
            raise HardwareError("sysfs link leaves the selected device classes")
        return resolved.relative_to(self.root)

    def _text(self, relative):
        try:
            path = Path(relative)
            if self.root == Path("/") and path.parts[:2] == ("proc", "net"):
                # /proc/net -> self/net and /proc/self -> PID are symlinks.
                # Open our own numeric PID directly, preserving O_NOFOLLOW
                # and reading the same network namespace without fixture leaks.
                path = Path("proc") / str(os.getpid()) / "net" / path.relative_to("proc/net")
            parent = self._sys_directory(path.parent) if path.parts[0] == "sys" else path.parent
            with self._directory(parent) as directory:
                return self._read_at(directory, path.name).decode("ascii").strip()
        except (OSError, ValueError, RuntimeError):
            return None

    def _number(self, relative, minimum=0, maximum=2**64 - 1):
        return _integer(self._text(relative), minimum, maximum)

    def _entries(self, relative, pattern, limit=64):
        try:
            parent = self._sys_directory(relative)
            with self._directory(parent) as directory, os.scandir(directory) as entries:
                names = []
                for index, entry in enumerate(entries):
                    if index >= _MAX_ENTRIES:
                        break
                    if re.fullmatch(pattern, entry.name):
                        names.append(entry.name)
                return sorted(names)[:limit]
        except (OSError, ValueError, RuntimeError):
            return []

    def _same_device(self, first, second):
        try:
            return self._sys_directory(first) == self._sys_directory(second)
        except (OSError, ValueError, RuntimeError):
            return False

    def _cpu_usage(self):
        # Aggregate jiffies, not load average. guest/guest_nice are already
        # included in user/nice; idle and iowait are both non-busy time.
        lines = (self._text("proc/stat") or "").splitlines()
        fields = lines[0].split() if lines else []
        previous, self._previous_cpu = self._previous_cpu, None
        if not fields or fields[0] != "cpu" or not 5 <= len(fields) <= 11:
            return None
        values = [_integer(field, 0, 2**64 - 1) for field in fields[1:]]
        if any(value is None for value in values):
            return None
        current = tuple(values[:8])
        self._previous_cpu = current
        if previous is None or len(previous) != len(current):
            return None
        delta = [now - before for now, before in zip(current, previous)]
        if min(delta) < 0 or sum(delta) == 0:
            return None
        idle = delta[3] + (delta[4] if len(delta) > 4 else 0)
        return (sum(delta) - idle) * 100 / sum(delta)

    def _ipv4_address(self, name):
        # SIOCGIFADDR reads the primary IPv4 address locally. No bind, connect,
        # packet, DNS lookup or subprocess. Fixture roots never query the host.
        if self.root != Path("/") or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,15}", name):
            return None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
                reply = fcntl.ioctl(channel.fileno(), 0x8915,
                                    struct.pack("256s", name.encode("ascii")))
            if len(reply) < 24 or struct.unpack_from("H", reply, 16)[0] != socket.AF_INET:
                return None
            address = ipaddress.IPv4Address(reply[20:24])
            if not (address.is_loopback or address.is_unspecified or address.is_multicast):
                return str(address)
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _ipv6_addresses(self):
        addresses = {}
        for line in (self._text("proc/net/if_inet6") or "").splitlines():
            fields = line.split()
            if (len(fields) != 6 or not re.fullmatch(r"[0-9a-fA-F]{32}", fields[0])
                    or any(not re.fullmatch(r"[0-9a-fA-F]{2,8}", field) for field in fields[1:5])
                    or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,15}", fields[5])):
                continue
            # Do not advertise tentative, duplicate or deprecated addresses.
            if int(fields[2], 16) > 128 or int(fields[4], 16) & (0x40 | 0x08 | 0x20):
                continue
            address = ipaddress.IPv6Address(int(fields[0], 16))
            if address.is_loopback or address.is_unspecified or address.is_multicast:
                continue
            values = addresses.setdefault(fields[5], [])
            if len(values) < 8 and str(address) not in values:
                values.append(str(address))
        return addresses

    def _local_ipv4_addresses(self):
        # The installed service allows AF_UNIX only. fib_trie exposes local
        # host routes without sockets, but does not identify their interfaces.
        # Never mistake a gateway, subnet or broadcast route for a local IP.
        result, previous = set(), ""
        for line in (self._text("proc/net/fib_trie") or "").splitlines():
            if line.strip() == "/32 host LOCAL":
                match = re.fullmatch(r"\s*\|-- ([0-9.]{7,15})\s*", previous)
                if match:
                    try:
                        address = ipaddress.IPv4Address(match[1])
                    except ValueError:
                        pass
                    else:
                        if not (address.is_loopback or address.is_multicast or address.is_unspecified):
                            result.add(address)
            previous = line
        return [str(address) for address in sorted(result, key=lambda item: (item.is_link_local, int(item)))[:32]]

    def snapshot(self):
        cpu_usage = self._cpu_usage()
        thermal = "sys/class/thermal"
        zones = [f"{thermal}/{name}" for name in self._entries(thermal, r"thermal_zone[0-9]+")]
        cpu_zones = [zone for zone in zones if re.search(
            r"(?:^|[^a-z])(?:cpu|soc)[0-9]*(?:[^a-z]|$)",
            (self._text(f"{zone}/type") or "").lower())]
        cpu = [self._number(f"{zone}/temp", -40000, 200000) for zone in cpu_zones]
        hwmons = [f"sys/class/hwmon/{name}" for name in
                  self._entries("sys/class/hwmon", r"hwmon[0-9]+")]
        names = {path: self._text(f"{path}/name") for path in hwmons}
        phy = [self._number(f"{path}/temp1_input", -40000, 200000)
               for path, name in names.items()
               if name and re.fullmatch(r"mdio_bus(?:[:_-][A-Za-z0-9_.:-]+)?", name)]
        cooling = next((f"{thermal}/{name}" for name in
                        self._entries(thermal, r"cooling_device[0-9]+")
                        if self._text(f"{thermal}/{name}/type") == "pwm-fan"), None)
        fan_hwmon = next((path for path, name in names.items() if name == "pwmfan"), None)
        fan = {"state": None, "max_state": None, "pwm": None, "rpm": None, "policy": None}
        if cooling:
            fan["max_state"] = self._number(f"{cooling}/max_state", 0, 255)
            fan["state"] = self._number(f"{cooling}/cur_state", 0,
                                        fan["max_state"] if fan["max_state"] is not None else 255)
            for zone in cpu_zones:
                bound = any(self._same_device(f"{zone}/{link}", cooling)
                            for link in self._entries(zone, r"cdev[0-9]+"))
                if bound:
                    policy = self._text(f"{zone}/policy")
                    if policy and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", policy):
                        fan["policy"] = policy
                        break
        if fan_hwmon:
            fan["pwm"] = self._number(f"{fan_hwmon}/pwm1", 0, 255)
            fan["rpm"] = self._number(f"{fan_hwmon}/fan1_input", 0, 200000)

        network = []
        ipv6 = self._ipv6_addresses()
        for name in self._entries("sys/class/net", r"[a-zA-Z0-9_.:-]{1,15}", limit=32):
            if name == "lo":
                continue
            path = f"sys/class/net/{name}"
            # Discard escaped or vanished class entries, not their missing metrics.
            try:
                self._sys_directory(path)
            except (OSError, ValueError, RuntimeError):
                continue
            network.append({"name": name,
                            "ipv4": self._ipv4_address(name), "ipv6": ipv6.get(name, []),
                            "rx_bytes": self._number(f"{path}/statistics/rx_bytes"),
                            "tx_bytes": self._number(f"{path}/statistics/tx_bytes"),
                            "carrier": self._number(f"{path}/carrier", 0, 1)})
        storage = []
        for name in self._entries("sys/class/nvme", r"nvme[0-9]+", limit=16):
            device = f"sys/class/nvme/{name}"
            try:
                self._sys_directory(device)
            except (OSError, ValueError, RuntimeError):
                continue
            temperature = None
            for path, kind in names.items():
                if kind != "nvme" or not self._same_device(f"{path}/device", device):
                    continue
                for channel in self._entries(path, r"temp[0-9]+_input", limit=32):
                    label = self._text(f"{path}/{channel[:-6]}_label")
                    if label and label.casefold() == "composite":
                        temperature = self._number(f"{path}/{channel}", -40000, 200000)
                        break
                break
            storage.append({"name": name, "temp_mc": temperature})

        load = [None, None, None]
        text = self._text("proc/loadavg")
        if text:
            fields = text.split()
            if len(fields) >= 3:
                for index, field in enumerate(fields[:3]):
                    try:
                        value = float(field)
                        if math.isfinite(value) and value >= 0:
                            load[index] = value
                    except ValueError:
                        pass
        memory = {}
        for line in (self._text("proc/meminfo") or "").splitlines():
            match = re.fullmatch(r"(MemTotal|MemAvailable):\s+([0-9]{1,20})\s+kB", line)
            if match:
                memory[match[1]] = _integer(match[2], 0, 2**64 - 1)
        uptime = None
        try:
            value = float((self._text("proc/uptime") or "").split()[0])
            if math.isfinite(value) and value >= 0:
                uptime = value
        except (ValueError, IndexError):
            pass
        return {"cpu_usage_percent": cpu_usage,
                "cpu_temp_mc": max((x for x in cpu if x is not None), default=None),
                "phy_temp_mc": max((x for x in phy if x is not None), default=None),
                "fan": fan, "loadavg": load,
                "mem_total_kib": memory.get("MemTotal"),
                "mem_available_kib": memory.get("MemAvailable"),
                "uptime_seconds": uptime, "network": network,
                "local_ipv4": self._local_ipv4_addresses(), "storage": storage}

    def _load_config_at(self, directory):
        try:
            data = self._read_at(directory, _CONFIG_NAME, validate=self._trusted)
        except FileNotFoundError:
            return dict(_DEFAULT_CONFIG)
        try:
            return _validate_config(json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise HardwareError(f"invalid display config: {error}") from error

    def load_display_config(self):
        try:
            with self._directory(_CONFIG_DIR, trusted=True) as directory:
                return self._load_config_at(directory)
        except FileNotFoundError:
            return dict(_DEFAULT_CONFIG)

    def _save_config_at(self, directory, config):
        payload = (json.dumps(_validate_config(config), sort_keys=True, indent=2) + "\n").encode("utf-8")
        # Recheck an existing target; never overwrite a symlink or special file.
        try:
            self._trusted(os.stat(_CONFIG_NAME, dir_fd=directory, follow_symlinks=False))
        except FileNotFoundError:
            pass
        temporary = ".display-" + secrets.token_hex(16) + ".tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        try:
            self._trusted(os.fstat(fd))
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(fd)
            os.replace(temporary, _CONFIG_NAME, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _require_write_authority(self):
        if self.euid() != 0:
            raise HardwareError("display writes require root")
        compatible = self._read_direct(f"{_DT}/compatible", limit=4096)
        if not compatible.endswith(b"\0") or b"edgepi,e87n" not in compatible.split(b"\0"):
            raise HardwareError("display writes require board compatible edgepi,e87n")

    def _expect_link(self, relative, expected):
        path = self.root / relative
        if not path.is_symlink() or path.resolve(strict=True) != self.root / expected:
            raise HardwareError(f"unexpected E87N sysfs device link: {relative}")

    def _cells(self, relative):
        data = self._read_direct(relative, limit=256)
        if not data or len(data) % 4:
            raise HardwareError("invalid device-tree PWM property")
        return struct.unpack(f">{len(data) // 4}I", data)

    @contextmanager
    def _backlight_files(self):
        self._expect_link("sys/class/backlight/backlight", _BACKLIGHT)
        self._expect_link(f"{_BACKLIGHT}/device", _PLATFORM)
        self._expect_link(f"{_PLATFORM}/driver", "sys/bus/platform/drivers/pwm-backlight")
        self._expect_link(f"{_PLATFORM}/of_node", f"{_DT}/backlight")
        if self._read_direct(f"{_DT}/backlight/compatible") != b"pwm-backlight\0":
            raise HardwareError("unexpected backlight compatible")
        pwm = f"{_DT}/soc/pwm@10048000"
        spec = self._cells(f"{_DT}/backlight/pwms")
        count = self._cells(f"{pwm}/#pwm-cells")
        phandle = self._cells(f"{pwm}/phandle")
        if (count not in ((2,), (3,)) or len(spec) != count[0] + 1
                or phandle != spec[:1] or phandle == (0,) or spec[1:3] != (2, 50000)
                or (count == (3,) and spec[3] != 0)):
            raise HardwareError("backlight requires E87N PWM2, 50000 ns, normal polarity")
        with self._directory(_BACKLIGHT) as directory:
            maximum = self._read_at(directory, "max_brightness").strip()
            if maximum != b"26":
                raise HardwareError("E87N backlight max_brightness must be 26")
            descriptors = {}
            try:
                for name in ("bl_power", "brightness"):
                    fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=directory)
                    descriptors[name] = fd
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise HardwareError("backlight attribute must be a regular sysfs file")
                yield descriptors
            finally:
                for fd in descriptors.values():
                    os.close(fd)

    @staticmethod
    def _apply_config(config, descriptors):
        percent = config["brightness_percent"] if config["enabled"] else 0
        raw = _raw_brightness(percent)
        # This circuit stays at bl_power=0, including when logically off.
        # Never use power-down (4) or raw zero to mean off on active-low E87N.
        for name, value in (("bl_power", 0), ("brightness", raw)):
            data = f"{value}\n".encode("ascii")
            if os.write(descriptors[name], data) != len(data):
                raise OSError("short backlight attribute write")

    def display(self, changes=None):
        """Apply once; setters atomically persist under a directory lock first."""
        self._require_write_authority()
        with self._backlight_files() as descriptors:
            if changes is None:
                with ExitStack() as stack:
                    try:
                        directory = stack.enter_context(self._directory(_CONFIG_DIR, trusted=True))
                    except FileNotFoundError:
                        config = dict(_DEFAULT_CONFIG)
                    else:
                        fcntl.flock(directory, fcntl.LOCK_EX)
                        config = self._load_config_at(directory)
                    self._apply_config(config, descriptors)
            else:
                with self._directory(_CONFIG_DIR, trusted=True, create=True) as directory:
                    fcntl.flock(directory, fcntl.LOCK_EX)
                    config = self._load_config_at(directory)
                    config.update(changes)
                    config = _validate_config(config)
                    self._save_config_at(directory, config)
                    self._apply_config(config, descriptors)
        return dict(config)


_snapshot_backend = None


def snapshot():
    """Read bounded telemetry; CPU % needs two calls. Includes local IP addresses."""
    global _snapshot_backend
    if _snapshot_backend is None:
        _snapshot_backend = _Hardware()
    return _snapshot_backend.snapshot()


def load_display_config():
    """Return validated config/defaults; reject unsafe paths, ownership and invalid JSON."""
    return _Hardware().load_display_config()
