#!/usr/bin/env bash
# Synthetic config/listing/depmod fixtures only. No image, initrd or target code is loaded.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 - "$repo_dir" <<'PY'
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

repo = Path(sys.argv[1])
script = repo / "scripts/verify-initramfs.py"
release = "6.12.108-current-filogic"
# Independent fixture expectations, not imported from the verifier.
drivers = {
    "USB_COMMON": "drivers/usb/common/usb-common",
    "USB": "drivers/usb/core/usbcore",
    "USB_XHCI_HCD": "drivers/usb/host/xhci-hcd",
    "USB_XHCI_MTK": "drivers/usb/host/xhci-mtk-hcd",
    "USB_STORAGE": "drivers/usb/storage/usb-storage",
    "USB_UAS": "drivers/usb/storage/uas",
    "SCSI_COMMON": "drivers/scsi/scsi_common",
    "SCSI": "drivers/scsi/scsi_mod",
    "BLK_DEV_SD": "drivers/scsi/sd_mod",
    "PHY_MTK_TPHY": "drivers/phy/mediatek/phy-mtk-tphy",
}
paths = {s: "kernel/" + n + ".ko" for s, n in drivers.items()}
extra = "kernel/lib/fixture-usb-dependency.ko"
count = 0


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def snapshot(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                result[str(path)] = ("link", os.readlink(path))
            elif path.is_file():
                result[str(path)] = (path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
    return result


with tempfile.TemporaryDirectory(prefix="e87n-usb-root-fixtures-") as temporary:
    work = Path(temporary)
    boot, root, listing = work / "boot", work / "root", work / "listing"
    boot.mkdir()
    root.mkdir()
    config = boot / ("config-" + release)
    dep = root / "usr/lib/modules" / release / "modules.dep"
    (root / "lib").symlink_to("usr/lib")
    prefix = "lib/modules/" + release + "/"
    options = {symbol: ("y" if symbol == "PHY_MTK_TPHY" else "m") for symbol in drivers}
    config_text = "CONFIG_MODULES=y\n" + "".join("CONFIG_%s=%s\n" % pair for pair in options.items())
    graph = {path: [] for symbol, path in paths.items() if options[symbol] == "m"}
    graph[paths["USB"]] = [paths["USB_COMMON"], extra]
    graph[paths["USB_XHCI_MTK"]] = [paths["USB_XHCI_HCD"], paths["USB"]]
    graph[paths["USB_STORAGE"]] = [paths["SCSI"], paths["USB"]]
    graph[paths["USB_UAS"]] = [paths["USB_STORAGE"]]
    graph[paths["SCSI"]] = [paths["SCSI_COMMON"]]
    graph[paths["BLK_DEV_SD"]] = [paths["SCSI"]]
    graph[extra] = []
    dep_text = "".join(name + ": " + " ".join(deps) + "\n" for name, deps in graph.items())
    lines = [".", "init", "lib", prefix + "modules.dep", prefix + "modules.alias"] + [prefix + name for name in graph]
    list_text = "\n".join(lines) + "\n"

    def reset():
        put(config, config_text)
        put(dep, dep_text)
        put(listing, list_text)

    def check(name, success, diagnostic):
        global count
        before = snapshot(work)
        result = subprocess.run([sys.executable, str(script), "--listing", str(listing),
                                 "--boot-dir", str(boot), "--root-dir", str(root)], capture_output=True, text=True)
        output = result.stdout + result.stderr
        assert (result.returncode == 0) == success and diagnostic in output, name + "\n" + output
        assert "Traceback" not in output and before == snapshot(work), name + ": changed fixtures/unhandled error"
        count += 1
        print("PASS fixture %02d: %s" % (count, name), flush=True)
        reset()

    print("SYNTHETIC FIXTURES ONLY; no real initramfs or USB hardware validation", flush=True)
    reset()
    check("modular USB root with dependency closure", True, "10 required modules/dependencies")
    for suffix in (".xz", ".zst"):
        put(listing, list_text.replace(".ko", ".ko" + suffix).replace("lib/modules/", "./usr/lib/modules/"))
        put(dep, dep_text.replace(".ko", ".ko" + suffix))
        check("usr/lib compressed modules " + suffix, True, "10 required")
    put(listing, list_text.replace("lib/modules/", "/lib/modules/"))
    check("absolute lib listing paths", True, "10 required")
    put(listing, list_text.replace("modules.dep", "modules.dep.bin").replace("modules.alias", "modules.alias.bin"))
    check("binary lookup metadata present", True, "10 required")
    for symbol, path in paths.items():
        if options[symbol] != "m":
            continue
        put(listing, list_text.replace(prefix + path + "\n", ""))
        check("missing " + symbol, False, "missing required module/dependency")
    put(listing, list_text.replace(prefix + extra + "\n", ""))
    check("missing transitive dependency", False, "fixture-usb-dependency.ko")
    put(listing, list_text.replace(release, "6.12.107-wrong"))
    check("wrong kernel release", False, "wrong kernel release")
    put(listing, list_text + "lib/modules/6.12.107-wrong/kernel/other.ko\n")
    check("mixed releases rejected", False, "wrong kernel release")
    put(config, config_text.replace("CONFIG_USB_XHCI_MTK=m", "# CONFIG_USB_XHCI_MTK is not set"))
    check("disabled controller config", False, "CONFIG_USB_XHCI_MTK must be y or m")
    put(config, config_text.replace("CONFIG_USB_UAS=m\n", ""))
    check("missing config", False, "CONFIG_USB_UAS must be y or m")
    put(config, config_text + "CONFIG_USB=m\n")
    check("duplicate config", False, "duplicate CONFIG_USB")
    put(config, config_text + "not-config\n")
    check("malformed config", False, "malformed kernel config")
    put(config, config_text + "CONFIG_MT76x02_LIB=m\n")
    check("valid mixed-case Kconfig symbol", True, "10 required")
    put(config, config_text.replace("CONFIG_USB_XHCI_MTK=m", "CONFIG_USB_XHCI_MTK=y"))
    check("built-in controller cannot depend on modular core", False, "depends on modular")
    put(config, config_text.replace("CONFIG_MODULES=y", "CONFIG_MODULES=n"))
    check("modules disabled", False, "CONFIG_MODULES=y required")
    for metadata in ("modules.dep", "modules.alias"):
        put(listing, list_text.replace(prefix + metadata + "\n", ""))
        check("missing lookup " + metadata, False, "missing module lookup metadata")
    dep.unlink()
    check("missing same-release rootfs modules.dep", False, "No such file")
    put(dep, dep_text.replace(extra + ": \n", ""))
    check("dependency missing from dep graph", False, "modules.dep missing required entry")
    put(dep, dep_text + "bad dep line\n")
    check("malformed modules.dep", False, "malformed modules.dep")
    put(dep, dep_text + extra + ":\n")
    check("duplicate modules.dep entry", False, "duplicate modules.dep entry")
    put(dep, dep_text.replace(extra + ": \n", extra + ": " + paths["USB"] + "\n"))
    check("dependency cycle", False, "cyclic modules.dep")
    put(listing, list_text.replace(prefix + extra, prefix + "../" + extra))
    check("listing path traversal", False, "noncanonical")
    put(dep, dep_text.replace(extra, "../" + extra))
    check("dependency path traversal", False, "noncanonical")
    put(listing, list_text.replace(extra, extra + ".gz"))
    check("unsupported suffix fails closed", False, "unsupported module path/suffix")
    put(listing, list_text + prefix + paths["USB"] + ".xz\n")
    check("ambiguous duplicate module", False, "duplicate/ambiguous")
    put(listing, list_text.replace("init\n", ""))
    check("no init", False, "lacks /init")
    put(listing, list_text + "-rw-r--r-- verbose-output\n")
    check("verbose/malformed listing not accepted", False, "invalid listing/dep path")
    alternate = boot / "config-6.12.other"
    put(alternate, config_text)
    check("ambiguous boot configs", False, "exactly one config")
    alternate.unlink()
    put(config, config_text.replace("=m", "=y"))
    put(listing, "init\n")
    dep.unlink()
    check("all drivers built-in need no ko or dep metadata", True, "driver chain built-in")
    put(config, config_text.replace("CONFIG_PHY_MTK_TPHY=y", "CONFIG_PHY_MTK_TPHY=m"))
    check("modular T-PHY also required", False, "phy-mtk-tphy.ko")
    # Test the real config snapshot against synthetic listings, NOT real build outputs.
    actual = repo / "output/runtime/kernel-final-6.12.108.config"
    if actual.is_file():
        put(config, actual.read_text())
        check("config snapshot with synthetic module listing", True, "10 required")
    print("PASS: %d read-only synthetic fixture tests; no initrd execution or hardware result." % count)
PY
