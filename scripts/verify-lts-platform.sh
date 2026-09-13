#!/usr/bin/env bash
# Host-only E87N Linux 6.18 DTB/config policy checks. No mounts or target code.
set -euo pipefail
if ! command -v python3 >/dev/null 2>&1; then
	printf 'ERROR: python3 (>= 3.8) is required\n' >&2
	exit 2
fi
exec python3 - "$@" <<'PY'
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

# Contract from userpatches/kernel/edgepi-e87n-6.18/0000-*.patch and 361/750.
# This is intentionally a board/profile validator, not a general DT schema.
ETH = "/soc/ethernet@15100000"
SGMII = "/soc_clksys/syscon@10060000"
TOP = "/soc_clksys/topckgen@1001b000"
INFRA = "/soc_clksys/infracfg@10001000"
WDT = "/soc/watchdog@1001c000"
PWM = "/soc/pwm@10048000"
FAN = "/pwm-fan"
LVTS = "/soc/lvts@1100a000"
EFUSE = "/soc/efuse@11d30000"
CALIB = EFUSE + "/calib@918"
ZONE = "/thermal-zones/cpu-thermal"
REQUIRED_Y = """ARM64 ARCH_MEDIATEK OF PINCTRL_MT7987 COMMON_CLK_MT7987
THERMAL THERMAL_OF MTK_THERMAL MTK_LVTS_THERMAL THERMAL_GOV_STEP_WISE
THERMAL_DEFAULT_GOV_STEP_WISE PWM PWM_MEDIATEK HWMON SENSORS_PWM_FAN
NVMEM NVMEM_MTK_EFUSE""".split()
# Opt-in contract for rebuilt images; legacy releases remain auditable without
# this flag. Names/select dependencies checked against the 6.18.51 headers'
# drivers/md/Kconfig, drivers/md/persistent-data/Kconfig and net/xfrm/Kconfig.
STORAGE_Y = "MODULES BLOCK MD NET INET IPV6 XFRM CRYPTO DM_UEVENT".split()
STORAGE_M = """BLK_DEV_DM DM_CRYPT DM_SNAPSHOT DM_THIN_PROVISIONING
DM_MIRROR DM_ZERO DM_MULTIPATH BLK_DEV_MD MD_LINEAR MD_RAID0 MD_RAID1
MD_RAID10 MD_RAID456 XFRM_USER XFRM_INTERFACE""".split()
STORAGE_ENABLED = """CRYPTO_AES CRYPTO_CBC CRYPTO_XTS CRYPTO_ESSIV CRYPTO_HMAC
CRYPTO_SHA256 CRYPTO_SHA512 CRC32 DM_BUFIO DM_BIO_PRISON DM_PERSISTENT_DATA
RAID6_PQ ASYNC_MEMCPY ASYNC_XOR ASYNC_PQ ASYNC_RAID6_RECOV""".split()


class Invalid(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def ok(message):
    print("OK: " + message)


def read_input(path, limit):
    info = path.stat()
    require(stat.S_ISREG(info.st_mode), "not a regular file: " + str(path))
    require(0 < info.st_size <= limit, "invalid input size: " + str(path))
    return path.read_bytes()


def check_config(data, require_storage=False):
    text = data.decode("utf-8")
    versions = re.findall(r"^# Linux/arm64 (\S+) Kernel Configuration$", text, re.M)
    require(len(versions) == 1 and
            re.fullmatch(r"6\.18\.\d+(?:[-+][A-Za-z0-9_.+-]+)?", versions[0]),
            "config must have a Linux/arm64 6.18.x Kernel Configuration header")
    options = {}
    value_pattern = r'(?:[ymn]|-?[0-9]+|0[xX][0-9a-fA-F]+|"(?:[^"\\\r\n]|\\[^\r\n])*")'
    for line in text.splitlines():
        set_value = re.fullmatch(r"CONFIG_([A-Za-z0-9_]+)=(" + value_pattern + r")", line)
        unset = re.fullmatch(r"# CONFIG_([A-Za-z0-9_]+) is not set", line)
        if set_value or unset:
            key, value = (set_value[1], set_value[2]) if set_value else (unset[1], "n")
            require(key not in options, "duplicate config symbol: CONFIG_" + key)
            options[key] = value
        elif line.startswith("CONFIG_"):
            raise Invalid("malformed config assignment: " + line)
    for key in REQUIRED_Y:
        require(options.get(key) == "y", "CONFIG_%s must be built-in (=y)" % key)
    require(options.get("CPU_FREQ") == "n", "CONFIG_CPU_FREQ must be explicitly disabled")
    # Kconfig may omit CPU_THERMAL entirely when its CPU_FREQ dependency is off.
    require(options.get("CPU_THERMAL", "n") == "n", "CONFIG_CPU_THERMAL must be disabled")
    ok("config %s: thermal/PWM/efuse built-in; CPU_FREQ/CPU_THERMAL disabled" % versions[0])
    if require_storage:
        for key in STORAGE_Y:
            require(options.get(key) == "y", "CONFIG_%s must be built-in (=y)" % key)
        for key in STORAGE_M:
            require(options.get(key) == "m", "CONFIG_%s must be modular (=m)" % key)
        for key in STORAGE_ENABLED:
            require(options.get(key) in ("y", "m"), "CONFIG_%s must be enabled (=y or =m)" % key)
        # These hidden bools can disappear when their built-in-only parents
        # are modular. An absent symbol is disabled, not a missing feature.
        for key in ("MD_AUTODETECT", "DM_INIT"):
            require(options.get(key, "n") == "n", "CONFIG_%s must be disabled" % key)
        ok("storage: MD enabled; DM/RAID/XFRM targets modular; crypto and selected dependencies enabled; MD_AUTODETECT/DM_INIT disabled")


class Fdt:
    def __init__(self, tool, path):
        self.tool, self.path = tool, str(path)
        self.props, self.children, self.cache, self.phandles = {}, {}, {}, {}
        pending = ["/"]
        while pending:
            node = pending.pop()
            require(node not in self.props and len(self.props) < 4096 and
                    node.count("/") <= 128, "invalid/excessive DTB node hierarchy")
            props = self.run("-p", self.path, node).splitlines()
            require(len(props) == len(set(props)), "duplicate properties at " + node)
            self.props[node] = set(props)
            names = self.run("-l", self.path, node).splitlines()
            require(len(names) == len(set(names)) and
                    all(name and "/" not in name and name not in (".", "..") for name in names),
                    "invalid/duplicate child nodes at " + node)
            self.children[node] = [node.rstrip("/") + "/" + name for name in names]
            pending.extend(self.children[node])
        for node in self.props:
            values = [self.scalar(node, prop) for prop in ("phandle", "linux,phandle")
                      if self.has(node, prop)]
            if values:
                require(len(set(values)) == 1 and values[0] not in (0, 0xffffffff),
                        "invalid/conflicting phandle at " + node)
                require(values[0] not in self.phandles, "duplicate phandle at " + node)
                self.phandles[values[0]] = node

    def run(self, *args):
        result = subprocess.run([self.tool, *args], capture_output=True, text=True, timeout=10)
        require(result.returncode == 0,
                "fdtget failed (%s): %s" % (" ".join(args), result.stderr.strip()))
        return result.stdout

    def has(self, node, prop):
        return prop in self.props.get(node, ())

    def raw(self, node, prop):
        require(self.has(node, prop), "missing %s:%s" % (node, prop))
        key = (node, prop)
        if key not in self.cache:
            # Byte mode preserves lengths and embedded NULs, unlike string output.
            words = self.run("-t", "bx", self.path, node, prop).split()
            require(all(re.fullmatch(r"[0-9a-fA-F]{1,2}", word) for word in words),
                    "invalid byte output at %s:%s" % key)
            self.cache[key] = bytes(int(word, 16) for word in words)
        return self.cache[key]

    def cells(self, node, prop):
        data = self.raw(node, prop)
        require(len(data) % 4 == 0, "non-cell property %s:%s" % (node, prop))
        return [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]

    def scalar(self, node, prop):
        cells = self.cells(node, prop)
        require(len(cells) == 1, "expected one cell at %s:%s" % (node, prop))
        return cells[0]

    def strings(self, node, prop):
        data = self.raw(node, prop)
        require(data.endswith(b"\0"), "unterminated string at %s:%s" % (node, prop))
        values = data[:-1].decode("ascii").split("\0")
        require(all(values), "empty string at %s:%s" % (node, prop))
        return values

    def enabled(self, node):
        require(node in self.props, "missing node " + node)
        current = node
        while True:
            if self.has(current, "status"):
                require(self.strings(current, "status") in (["okay"], ["ok"]),
                        "disabled/unavailable node " + current)
            if current == "/":
                break
            current = current.rsplit("/", 1)[0] or "/"

    def compatible(self, node, value):
        self.enabled(node)
        require(value in self.strings(node, "compatible"), "wrong compatible at " + node)

    def target(self, value):
        require(value in self.phandles, "unresolved phandle 0x%x" % value)
        node = self.phandles[value]
        self.enabled(node)
        return node

    def refs(self, node, prop, cells_prop=None):
        values, result, index = self.cells(node, prop), [], 0
        while index < len(values):
            provider = self.target(values[index])
            count = self.scalar(provider, cells_prop) if cells_prop else 0
            require(count <= 16 and index + 1 + count <= len(values),
                    "truncated/invalid specifier at %s:%s" % (node, prop))
            result.append((provider, values[index + 1:index + 1 + count]))
            index += 1 + count
        return result


def check_dtb(dt):
    dt.compatible("/", "edgepi,e87n")
    dt.compatible("/", "mediatek,mt7987a")
    dt.compatible("/", "mediatek,mt7987")
    # The current E87N profile has no validated OPPs (including orphan tables).
    for node, props in dt.props.items():
        require("pcs-handle" not in props, "legacy pcs-handle at " + node)
        require(not props.intersection(("operating-points", "operating-points-v2", "opp-hz")),
                "unvalidated OPP property at " + node)
        if dt.has(node, "compatible"):
            require(not any(value.startswith("operating-points-v2")
                            for value in dt.strings(node, "compatible")),
                    "unvalidated OPP table at " + node)
    dt.enabled("/cpus")
    cpus = [node for node in dt.children["/cpus"]
            if dt.has(node, "device_type") and dt.strings(node, "device_type") == ["cpu"]]
    require(bool(cpus), "no CPU nodes found")
    ok("no legacy pcs-handle or unvalidated CPU/OPP tables")

    dt.compatible(ETH, "mediatek,mt7987-eth")
    dt.compatible(SGMII, "mediatek,mt7987-sgmiisys0")
    dt.compatible(SGMII, "syscon")
    dt.compatible(TOP, "mediatek,mt7987-topckgen")
    dt.compatible(WDT, "mediatek,mt7987-wdt")
    require(dt.refs(ETH, "mediatek,sgmiisys") == [(SGMII, [])],
            "Ethernet must reference sgmiisys0 in MAC0 slot only")
    names = dt.strings(SGMII, "clock-names")
    refs = dt.refs(SGMII, "clocks", "#clock-cells")
    require(len(names) == 3 and len(refs) == 3 and len(set(names)) == 3,
            "sgmiisys0 needs exactly three named clocks")
    require(dict(zip(names, refs)) == {"sgmii_sel": (TOP, [55]),
                                     "sgmii_tx": (SGMII, [0]),
                                     "sgmii_rx": (SGMII, [1])},
            "sgmiisys0 clock names/providers/IDs do not match 361/750")
    require(dt.scalar(SGMII, "#clock-cells") == 1 and dt.scalar(WDT, "#reset-cells") == 1,
            "SGMII clock/reset provider cell counts must be one")
    require(dt.refs(SGMII, "resets", "#reset-cells") == [(WDT, [1])],
            "sgmiisys0 must use watchdog SGMII0 reset 1")
    require(dt.raw(SGMII, "mediatek,phya_trx_ck") == b"", "PHYA TRX flag must be boolean")
    for mac, mode in ((0, "2500base-x"), (1, "internal")):
        node = ETH + "/mac@%d" % mac
        dt.enabled(node)
        require(dt.scalar(node, "reg") == mac and dt.strings(node, "phy-mode") == [mode],
                "wrong MAC%d interface contract" % mac)
    ok("Ethernet sgmiisys0 library PCS: 3 named clocks, reset and PHYA TRX flag")

    dt.compatible(FAN, "pwm-fan")
    dt.compatible(PWM, "mediatek,mt7987-pwm")
    require(dt.scalar(PWM, "#pwm-cells") == 2, "PWM provider #pwm-cells must be 2")
    require(dt.refs(FAN, "pwms", "#pwm-cells") == [(PWM, [1, 50000])],
            "fan PWM must be channel 1, period 50000 ns, with exactly two arguments")
    require(dt.scalar(FAN, "#cooling-cells") == 2, "fan #cooling-cells must be 2")
    require(dt.cells(FAN, "cooling-levels") == [0, 128, 192, 255],
            "fan cooling-levels must be 0/128/192/255")
    ok("fan PWM provider/arguments match; four cooling levels 0/128/192/255")

    dt.compatible(LVTS, "mediatek,mt7987-lvts-ap")
    dt.enabled(ZONE)
    require(not dt.props[LVTS].intersection(("interrupts", "interrupts-extended", "interrupt-names")),
            "polled LVTS must not declare IRQ properties")
    require(dt.scalar(LVTS, "#thermal-sensor-cells") == 1 and
            dt.refs(ZONE, "thermal-sensors", "#thermal-sensor-cells") == [(LVTS, [0])],
            "cpu-thermal must use LVTS sensor 0")
    for prop in ("polling-delay", "polling-delay-passive"):
        require(0 < dt.scalar(ZONE, prop) <= 0x7fffffff, prop + " must be nonzero")
    require(dt.scalar(ZONE, "polling-delay-passive") <= dt.scalar(ZONE, "polling-delay"),
            "polling-delay-passive must not exceed polling-delay")
    dt.compatible(INFRA, "mediatek,mt7987-infracfg")
    require(dt.refs(LVTS, "clocks", "#clock-cells") == [(INFRA, [25])],
            "LVTS thermal clock does not match 361")
    require(dt.refs(LVTS, "resets", "#reset-cells") == [(INFRA, [1])],
            "LVTS reset does not match 361")
    dt.compatible(EFUSE, "mediatek,efuse")
    require(dt.refs(LVTS, "nvmem-cells") == [(CALIB, [])] and
            dt.strings(LVTS, "nvmem-cell-names") == ["lvts-calib-data-1"] and
            dt.cells(CALIB, "reg") == [0x918, 0x10], "LVTS efuse calibration contract mismatch")
    ok("LVTS enabled, polled without IRQs; sensor/clock/reset/efuse references valid")

    trip_root, map_root = ZONE + "/trips", ZONE + "/cooling-maps"
    dt.enabled(trip_root)
    dt.enabled(map_root)
    active, critical = {}, []
    for trip in dt.children[trip_root]:
        dt.enabled(trip)
        temperature = dt.scalar(trip, "temperature")
        hysteresis = dt.scalar(trip, "hysteresis")
        # Software-policy sanity range: (0, 10 C], not a silicon specification.
        require(0 < hysteresis <= 10000 and hysteresis < temperature,
                "trip hysteresis must be 1..10000 mC and below temperature: " + trip)
        trip_type = dt.strings(trip, "type")
        if trip_type == ["active"]:
            active[trip] = temperature
        elif trip_type == ["critical"]:
            critical.append(temperature)
    require(critical == [125000],
            "exactly one critical trip at 125000 mC is required (vendor software policy only)")
    require(len(active) == 3 and set(active.values()) == {50000, 65000, 75000},
            "active trips must be exactly 50/65/75 C")
    expected = {50000: 1, 65000: 2, 75000: 3}
    seen = set()
    for mapping in dt.children[map_root]:
        dt.enabled(mapping)
        trips = dt.refs(mapping, "trip")
        require(len(trips) == 1 and trips[0][0] in active,
                "cooling map must reference one active CPU trip: " + mapping)
        trip = trips[0][0]
        require(trip not in seen, "duplicate cooling map for " + trip)
        seen.add(trip)
        state = expected[active[trip]]
        require(dt.refs(mapping, "cooling-device", "#cooling-cells") == [(FAN, [state, state])],
                "wrong fan cooling state for %d C (need %d/%d)" % (active[trip] // 1000, state, state))
    require(seen == set(active), "missing fan cooling map for active trip")
    ok("active trips 50/65/75 C map exactly to fan states 1/2/3")
    ok("unique critical trip 125000 mC, nonzero sane hysteresis; vendor software policy, NOT a hardware temperature limit")


def main():
    parser = argparse.ArgumentParser(prog="verify-lts-platform.sh", description="Read-only E87N Linux 6.18 DTB/config platform policy checks; no mounts or target execution.")
    parser.add_argument("--dtb", type=Path, required=True, help="final mt7987a-edgepi-e87n.dtb")
    parser.add_argument("--config", type=Path, required=True, help="final kernel .config or installed config file")
    parser.add_argument("--require-storage", action="store_true",
                        help="require modular DM/RAID/XFRM, crypto dependencies and disabled early assembly (new builds)")
    args = parser.parse_args()
    tool = shutil.which("fdtget")
    require(tool is not None, "host fdtget is required (device-tree-compiler package)")
    dtb_path, config_path = args.dtb.resolve(strict=True), args.config.resolve(strict=True)
    dtb = read_input(dtb_path, 16 * 1024 * 1024)
    config = read_input(config_path, 2 * 1024 * 1024)
    check_config(config, require_storage=args.require_storage)
    check_dtb(Fdt(tool, dtb_path))
    require(dtb_path.read_bytes() == dtb and config_path.read_bytes() == config,
            "input changed during verification; retry with stable artifacts")
    print("DTB SHA256: " + hashlib.sha256(dtb).hexdigest())
    print("CONFIG SHA256: " + hashlib.sha256(config).hexdigest())
    print("PASS: Linux 6.18 E87N static platform contract only; not kernel/firmware/boot or hardware validation.")


try:
    main()
except (Invalid, OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
    print("FAIL: " + str(exc), file=sys.stderr)
    sys.exit(1)
PY
