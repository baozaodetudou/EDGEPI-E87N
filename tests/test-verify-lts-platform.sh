#!/usr/bin/env bash
# Independent synthetic DTBs, compiled/read with host dtc/fdtget/fdtput.
# These tests are NOT evidence of a real kernel build or hardware support.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 - "$repo_dir" "$@" <<'PY'
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SCRIPT = Path(sys.argv[1]) / "scripts/verify-lts-platform.sh"
for name in ("dtc", "fdtget", "fdtput"):
    if not shutil.which(name):
        sys.exit("FAIL: host %s is required for synthetic fixture tests" % name)

ETH = "/soc/ethernet@15100000"
SGMII = "/soc_clksys/syscon@10060000"
TOP = "/soc_clksys/topckgen@1001b000"
INFRA = "/soc_clksys/infracfg@10001000"
WDT = "/soc/watchdog@1001c000"
PWM = "/soc/pwm@10048000"
LVTS = "/soc/lvts@1100a000"
EFUSE = "/soc/efuse@11d30000"
ZONE = "/thermal-zones/cpu-thermal"
FAN = "/pwm-fan"
# Expectations are independent: do not import/execute the validator's Python.
BUILTIN = """ARM64 ARCH_MEDIATEK OF PINCTRL_MT7987 COMMON_CLK_MT7987
THERMAL THERMAL_OF MTK_THERMAL MTK_LVTS_THERMAL THERMAL_GOV_STEP_WISE
THERMAL_DEFAULT_GOV_STEP_WISE PWM PWM_MEDIATEK HWMON SENSORS_PWM_FAN
NVMEM NVMEM_MTK_EFUSE""".split()
CONFIG = "# Linux/arm64 6.18.51 Kernel Configuration\n" + "".join(
    "CONFIG_%s=y\n" % name for name in BUILTIN) + (
    "# CONFIG_CPU_FREQ is not set\n# CONFIG_CPU_THERMAL is not set\n")
STORAGE_Y = "MODULES BLOCK MD NET INET IPV6 XFRM CRYPTO DM_UEVENT".split()
STORAGE_M = """BLK_DEV_DM DM_CRYPT DM_SNAPSHOT DM_THIN_PROVISIONING
DM_MIRROR DM_ZERO DM_MULTIPATH BLK_DEV_MD MD_LINEAR MD_RAID0 MD_RAID1
MD_RAID10 MD_RAID456 XFRM_USER XFRM_INTERFACE""".split()
STORAGE_ENABLED = """CRYPTO_AES CRYPTO_CBC CRYPTO_XTS CRYPTO_ESSIV CRYPTO_HMAC
CRYPTO_SHA256 CRYPTO_SHA512 CRC32 DM_BUFIO DM_BIO_PRISON DM_PERSISTENT_DATA
RAID6_PQ ASYNC_MEMCPY ASYNC_XOR ASYNC_PQ ASYNC_RAID6_RECOV""".split()
STORAGE_CONFIG = CONFIG + "".join("CONFIG_%s=y\n" % name for name in STORAGE_Y) + "".join(
    "CONFIG_%s=m\n" % name for name in STORAGE_M + STORAGE_ENABLED) + (
    "# CONFIG_MD_AUTODETECT is not set\n# CONFIG_DM_INIT is not set\n")
DTS = r'''
/dts-v1/;
/ {
    compatible = "edgepi,e87n", "mediatek,mt7987a", "mediatek,mt7987";
    cpus { cpu@0 { device_type = "cpu"; }; cpu@1 { device_type = "cpu"; }; };
    soc_clksys {
        top: topckgen@1001b000 {
            compatible = "mediatek,mt7987-topckgen";
            #clock-cells = <1>; phandle = <1>;
        };
        infra: infracfg@10001000 {
            compatible = "mediatek,mt7987-infracfg";
            #clock-cells = <1>; #reset-cells = <1>; phandle = <2>;
        };
        sgmii: syscon@10060000 {
            compatible = "mediatek,mt7987-sgmiisys0", "syscon";
            #clock-cells = <1>; phandle = <3>;
            clock-names = "sgmii_sel", "sgmii_tx", "sgmii_rx";
            clocks = <&top 55>, <&sgmii 0>, <&sgmii 1>;
            resets = <&wdt 1>; mediatek,phya_trx_ck;
        };
    };
    soc {
        wdt: watchdog@1001c000 {
            compatible = "mediatek,mt7987-wdt";
            #reset-cells = <1>; phandle = <4>;
        };
        pwm: pwm@10048000 {
            compatible = "mediatek,mt7987-pwm";
            #pwm-cells = <2>; phandle = <5>; status = "okay";
        };
        lvts: lvts@1100a000 {
            compatible = "mediatek,mt7987-lvts-ap";
            #thermal-sensor-cells = <1>; phandle = <6>; status = "okay";
            clocks = <&infra 25>; resets = <&infra 1>;
            nvmem-cells = <&calib>; nvmem-cell-names = "lvts-calib-data-1";
        };
        efuse@11d30000 {
            compatible = "mediatek,efuse";
            calib: calib@918 { reg = <0x918 0x10>; phandle = <7>; };
        };
        ethernet@15100000 {
            compatible = "mediatek,mt7987-eth"; status = "okay";
            mediatek,sgmiisys = <&sgmii>;
            mac@0 { reg = <0>; phy-mode = "2500base-x"; };
            mac@1 { reg = <1>; phy-mode = "internal"; };
        };
    };
    fan: pwm-fan {
        compatible = "pwm-fan"; status = "okay"; phandle = <8>;
        #cooling-cells = <2>; cooling-levels = <0 128 192 255>;
        pwms = <&pwm 1 50000>;
    };
    thermal-zones {
        cpu-thermal {
            polling-delay = <1000>; polling-delay-passive = <1000>;
            thermal-sensors = <&lvts 0>;
            trips {
                low: active-low { temperature = <50000>; hysteresis = <2000>; type = "active"; phandle = <9>; };
                med: active-med { temperature = <65000>; hysteresis = <2000>; type = "active"; phandle = <10>; };
                high: active-high { temperature = <75000>; hysteresis = <2000>; type = "active"; phandle = <11>; };
                crit { temperature = <125000>; hysteresis = <2000>; type = "critical"; };
            };
            cooling-maps {
                low { trip = <&low>; cooling-device = <&fan 1 1>; };
                med { trip = <&med>; cooling-device = <&fan 2 2>; };
                high { trip = <&high>; cooling-device = <&fan 3 3>; };
            };
        };
    };
};
'''


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError("fixture command failed: %r\n%s%s" % (args, result.stdout, result.stderr))
    return result.stdout


cases = []
with tempfile.TemporaryDirectory(prefix="e87n-lts-platform-tests.") as scratch:
    root = Path(scratch)
    source, baseline = root / "fixture.dts", root / "fixture.dtb"
    source.write_text(DTS)
    command(["dtc", "-q", "-I", "dts", "-O", "dtb", "-o", str(baseline), str(source)])

    def run_case(entry):
        number, (name, edits, config, error, data, storage) = entry
        # Include spaces to exercise quoting; never touch the input real build.
        case = root / ("case %d" % number)
        case.mkdir()
        dtb, conf = case / "board image.dtb", case / "kernel .config"
        dtb.write_bytes(baseline.read_bytes() if data is None else data)
        conf.write_text(config)
        for mode, node, prop, values in edits:
            if mode in ("delete", "remove", "create"):
                opts = {"delete": "-d", "remove": "-r", "create": "-c"}
                args = ["fdtput", opts[mode], str(dtb), node]
                if mode == "delete":
                    args.append(prop)
            else:
                args = ["fdtput", "-t", mode, str(dtb), node, prop, *map(str, values)]
            command(args)
        before = (dtb.read_bytes(), conf.read_bytes())
        flags = ["--require-storage"] if storage else []
        result = subprocess.run(["bash", str(SCRIPT), "--dtb", str(dtb), "--config", str(conf), *flags],
                                capture_output=True, text=True, timeout=120)
        assert (dtb.read_bytes(), conf.read_bytes()) == before, "validator mutated inputs: " + name
        output = result.stdout + result.stderr
        if error is None:
            assert result.returncode == 0 and "PASS:" in output, name + "\n" + output
        else:
            assert result.returncode != 0 and "FAIL:" in output and error in output, name + "\n" + output
        return "PASS fixture: " + name

    def check(name, edits=(), config=CONFIG, error=None, data=None, storage=False):
        cases.append((name, edits, config, error, data, storage))

    def check_storage(name, config=STORAGE_CONFIG, error=None):
        check(name, config=config, error=error, storage=True)

    def cells(node, prop, *values):
        return ("u", node, prop, values)

    def strings(node, prop, *values):
        return ("s", node, prop, values)

    def delete(node, prop):
        return ("delete", node, prop, ())

    check("valid 6.18.51 DTB/config")
    if len(sys.argv) == 3:
        check("supplied real config with SYNTHETIC DTB (not real platform validation)",
              config=Path(sys.argv[2]).read_text())
    elif len(sys.argv) != 2:
        sys.exit("usage: test-verify-lts-platform.sh [FINAL_CONFIG]")
    check("hidden CPU_THERMAL omitted", config=CONFIG.replace("# CONFIG_CPU_THERMAL is not set\n", ""))
    check("valid localversion header", config=CONFIG.replace("6.18.51", "6.18.51-current-filogic"))
    check("valid mixed-case symbol", config=CONFIG + "CONFIG_MT76x02_LIB=m\n# CONFIG_MT76x0U is not set\n")
    check("invalid mixed-case value", config=CONFIG + "CONFIG_MT76x02_LIB=banana\n", error="malformed config assignment")
    check("valid typed Kconfig values", config=CONFIG + 'CONFIG_TEST_HEX=0x100\nCONFIG_TEST_INT=-1\nCONFIG_TEST_STRING="hello world"\n')
    check("clock order may follow clock-names", [strings(SGMII, "clock-names", "sgmii_rx", "sgmii_sel", "sgmii_tx"),
                                              cells(SGMII, "clocks", 3, 1, 1, 55, 3, 0)])
    check("nonzero alternative polling", [cells(ZONE, "polling-delay", 2000)])
    check("wrong board", [strings("/", "compatible", "another,board")], error="wrong compatible")
    check("legacy PCS handle anywhere", [cells(ETH + "/mac@0", "pcs-handle", 3)], error="legacy pcs-handle")
    check("legacy CPU OPP", [cells("/cpus/cpu@0", "operating-points", 1000000, 900000)], error="OPP property")
    check("CPU OPPv2", [cells("/cpus/cpu@0", "operating-points-v2", 9)], error="OPP property")
    check("orphan OPP table", [("create", "/opp-table", "", ()),
                              strings("/opp-table", "compatible", "operating-points-v2")], error="OPP table")
    check("missing PCS list", [delete(ETH, "mediatek,sgmiisys")], error="missing")
    check("wrong PCS target", [cells(ETH, "mediatek,sgmiisys", 1)], error="MAC0 slot")
    check("dangling PCS target", [cells(ETH, "mediatek,sgmiisys", 999)], error="unresolved phandle")
    check("extra PCS slot", [cells(ETH, "mediatek,sgmiisys", 3, 3)], error="MAC0 slot")
    check("missing named clock", [strings(SGMII, "clock-names", "sgmii_sel", "sgmii_tx")], error="three named clocks")
    check("duplicate clock name", [strings(SGMII, "clock-names", "sgmii_sel", "sgmii_tx", "sgmii_tx")], error="three named clocks")
    check("wrong clock ID", [cells(SGMII, "clocks", 1, 54, 3, 0, 3, 1)], error="clock names/providers/IDs")
    check("truncated clock cells", [cells(SGMII, "clocks", 1, 55, 3, 0, 3)], error="truncated/invalid")
    check("missing clock provider cells", [delete(TOP, "#clock-cells")], error="missing")
    check("disabled clock ancestor", [strings("/soc_clksys", "status", "disabled")], error="disabled/unavailable")
    check("missing PCS reset", [delete(SGMII, "resets")], error="missing")
    check("wrong PCS reset ID", [cells(SGMII, "resets", 4, 2)], error="SGMII0 reset")
    check("extra reset specifier", [cells(SGMII, "resets", 4, 1, 4, 1)], error="SGMII0 reset")
    check("missing PHYA flag", [delete(SGMII, "mediatek,phya_trx_ck")], error="missing")
    check("nonboolean PHYA flag", [cells(SGMII, "mediatek,phya_trx_ck", 1)], error="must be boolean")
    check("wrong internal MAC mode", [strings(ETH + "/mac@1", "phy-mode", "sgmii")], error="MAC1 interface")
    check("PWM provider three cells", [cells(PWM, "#pwm-cells", 3)], error="#pwm-cells must be 2")
    check("PWM extra flags cell", [cells(FAN, "pwms", 5, 1, 50000, 0)], error="unresolved phandle")
    check("PWM short specifier", [cells(FAN, "pwms", 5, 1)], error="truncated/invalid")
    check("wrong fan PWM provider", [cells(FAN, "pwms", 3, 1, 50000)], error="missing")
    check("wrong fan PWM channel", [cells(FAN, "pwms", 5, 2, 50000)], error="fan PWM must be")
    check("wrong fan PWM period", [cells(FAN, "pwms", 5, 1, 40000)], error="fan PWM must be")
    check("disabled PWM", [strings(PWM, "status", "disabled")], error="disabled/unavailable")
    check("disabled fan", [strings(FAN, "status", "disabled")], error="disabled/unavailable")
    check("wrong fan levels", [cells(FAN, "cooling-levels", 0, 128, 255)], error="0/128/192/255")
    check("wrong fan cooling cells", [cells(FAN, "#cooling-cells", 1)], error="#cooling-cells must be 2")
    check("zero LVTS polling", [cells(ZONE, "polling-delay", 0)], error="polling-delay must be nonzero")
    check("zero passive polling", [cells(ZONE, "polling-delay-passive", 0)], error="polling-delay-passive must be nonzero")
    check("missing polling", [delete(ZONE, "polling-delay")], error="missing")
    check("passive polling slower than normal", [cells(ZONE, "polling-delay-passive", 2000)], error="must not exceed")
    check("LVTS interrupts", [cells(LVTS, "interrupts", 0, 42, 4)], error="must not declare IRQ")
    check("LVTS extended interrupts", [cells(LVTS, "interrupts-extended", 4, 42)], error="must not declare IRQ")
    check("empty LVTS IRQ property", [cells(LVTS, "interrupts")], error="must not declare IRQ")
    check("disabled LVTS", [strings(LVTS, "status", "disabled")], error="disabled/unavailable")
    check("wrong LVTS sensor", [cells(ZONE, "thermal-sensors", 6, 1)], error="LVTS sensor 0")
    check("missing calibration reference", [delete(LVTS, "nvmem-cells")], error="missing")
    check("wrong calibration offset", [cells(EFUSE + "/calib@918", "reg", 0x919, 0x10)], error="calibration contract")
    check("disabled efuse", [strings(EFUSE, "status", "disabled")], error="disabled/unavailable")
    check("wrong trip temperature", [cells(ZONE + "/trips/active-med", "temperature", 60000)], error="50/65/75")
    check("wrong trip type", [strings(ZONE + "/trips/active-med", "type", "passive")], error="50/65/75")
    check("critical temperature changed", [cells(ZONE + "/trips/crit", "temperature", 130000)], error="one critical trip at 125000")
    check("critical trip missing", [("remove", ZONE + "/trips/crit", "", ())], error="one critical trip at 125000")
    check("duplicate critical trip", [strings(ZONE + "/trips/active-high", "type", "critical")], error="one critical trip at 125000")
    check("zero critical hysteresis", [cells(ZONE + "/trips/crit", "hysteresis", 0)], error="trip hysteresis")
    check("missing active hysteresis", [delete(ZONE + "/trips/active-low", "hysteresis")], error="missing")
    check("excessive hysteresis", [cells(ZONE + "/trips/active-med", "hysteresis", 10001)], error="trip hysteresis")
    check("wrong cooling state", [cells(ZONE + "/cooling-maps/med", "cooling-device", 8, 3, 3)], error="need 2/2")
    check("wildcard cooling state", [cells(ZONE + "/cooling-maps/low", "cooling-device", 8, 0xffffffff, 1)], error="need 1/1")
    check("duplicate cooling trip", [cells(ZONE + "/cooling-maps/high", "trip", 10)], error="duplicate cooling map")
    check("missing cooling map", [("remove", ZONE + "/cooling-maps/high", "", ())], error="missing fan cooling map")
    check("dangling trip phandle", [cells(ZONE + "/cooling-maps/high", "trip", 999)], error="unresolved phandle")
    check("duplicate phandle", [cells(PWM, "phandle", 3)], error="duplicate phandle")
    check("conflicting linux phandle", [cells(PWM, "linux,phandle", 99)], error="conflicting phandle")
    check("non-cell PWM value", [("bx", FAN, "pwms", ("01",))], error="non-cell property")
    check("unterminated compatible", [("bx", "/", "compatible", ("65", "64"))], error="unterminated string")
    check("corrupt DTB", data=b"not a DTB", error="fdtget failed")
    check("wrong kernel series", config=CONFIG.replace("6.18.51", "6.12.109"), error="6.18.x")
    check("wrong architecture", config=CONFIG.replace("Linux/arm64", "Linux/x86"), error="Linux/arm64")
    check("header missing", config=CONFIG.split("\n", 1)[1], error="Configuration header")
    check("CPU_FREQ enabled", config=CONFIG.replace("# CONFIG_CPU_FREQ is not set", "CONFIG_CPU_FREQ=y"), error="CPU_FREQ")
    check("CPU_FREQ missing", config=CONFIG.replace("# CONFIG_CPU_FREQ is not set\n", ""), error="CPU_FREQ")
    check("CPU_THERMAL enabled", config=CONFIG.replace("# CONFIG_CPU_THERMAL is not set", "CONFIG_CPU_THERMAL=y"), error="CPU_THERMAL")
    check("duplicate config symbol", config=CONFIG + "CONFIG_THERMAL=y\n", error="duplicate config symbol")
    check("malformed config", config=CONFIG + "CONFIG_BAD LINE\n", error="malformed config assignment")
    for symbol in BUILTIN:
        check("modular " + symbol, config=CONFIG.replace("CONFIG_%s=y" % symbol, "CONFIG_%s=m" % symbol), error="CONFIG_" + symbol)
    check("missing efuse symbol", config=CONFIG.replace("CONFIG_NVMEM_MTK_EFUSE=y\n", ""), error="CONFIG_NVMEM_MTK_EFUSE")

    check_storage("valid optional storage profile")
    check_storage("legacy config rejected only with storage flag", config=CONFIG, error="CONFIG_MODULES")
    check("storage profile accepted by legacy checker", config=STORAGE_CONFIG)
    for name in STORAGE_Y + STORAGE_M + STORAGE_ENABLED:
        value = "y" if name in STORAGE_Y else "m"
        line = "CONFIG_%s=%s\n" % (name, value)
        check_storage("missing storage " + name, config=STORAGE_CONFIG.replace(line, ""), error="CONFIG_" + name)
        check_storage("disabled storage " + name,
                      config=STORAGE_CONFIG.replace(line, "# CONFIG_%s is not set\n" % name), error="CONFIG_" + name)
        replacement = "m" if name in STORAGE_Y else "y"
        # Selected crypto/parity helpers may be promoted to y by other users.
        error = None if name in STORAGE_ENABLED else "CONFIG_" + name
        check_storage("changed storage linkage " + name,
                      config=STORAGE_CONFIG.replace(line, "CONFIG_%s=%s\n" % (name, replacement)), error=error)
    for name in ("MD_AUTODETECT", "DM_INIT"):
        line = "# CONFIG_%s is not set\n" % name
        check_storage("hidden disabled " + name, config=STORAGE_CONFIG.replace(line, ""))
        for value in ("y", "m"):
            check_storage("early assembly %s=%s" % (name, value),
                          config=STORAGE_CONFIG.replace(line, "CONFIG_%s=%s\n" % (name, value)), error="CONFIG_" + name)
    for name in ("CPU_FREQ", "CPU_THERMAL"):
        check_storage("storage must not relax " + name,
                      config=STORAGE_CONFIG.replace("# CONFIG_%s is not set" % name, "CONFIG_%s=y" % name),
                      error="CONFIG_" + name)
    check_storage("duplicate storage config", config=STORAGE_CONFIG + "CONFIG_DM_CRYPT=m\n", error="duplicate config symbol")
    check_storage("malformed storage config", config=STORAGE_CONFIG + "CONFIG_DM_BAD=banana\n", error="malformed config assignment")

    # fdtget processes are host-only and each worker has its own fixture files.
    with ThreadPoolExecutor(max_workers=4) as workers:
        for message in workers.map(run_case, enumerate(cases)):
            print(message, flush=True)

print("PASS: %d independent synthetic LTS platform fixtures; input bytes unchanged; no real build/hardware validated" % len(cases))
PY
