#!/usr/bin/env python3
"""E87N USB-root listing check only: no extraction, target execution or writes."""
import argparse
import os
from pathlib import Path
import re
import stat
import sys

# BPI-Router-Linux b864732ee285e7868fb0857d69a8ff349e37003e:
# drivers/usb/{common,core,host,storage}/Makefile, drivers/scsi/Makefile,
# drivers/phy/mediatek/Makefile. T-PHY is the E87N DTS USB PHY provider.
DRIVERS = {
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
PREREQUISITES = {
    "USB": ("USB_COMMON",), "USB_XHCI_HCD": ("USB",),
    "USB_XHCI_MTK": ("USB_XHCI_HCD",), "SCSI": ("SCSI_COMMON",),
    "BLK_DEV_SD": ("SCSI",), "USB_STORAGE": ("USB", "SCSI"),
    "USB_UAS": ("USB_STORAGE", "USB", "SCSI"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rooted(root, name):
    """Absolute target symlinks stay in the supplied bootfs/rootfs, not the host."""
    parts, pending, hops = [], name.split("/"), 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            require(bool(parts), "path escapes target root: " + name)
            parts.pop()
            continue
        path = root.joinpath(*parts, part)
        if path.is_symlink():
            hops += 1
            require(hops <= 40, "target symlink loop: " + name)
            target = os.readlink(path)
            if target.startswith("/"):
                parts = []
            pending = target.split("/") + pending
        else:
            parts.append(part)
    return root.joinpath(*parts)


def read(path):
    require(stat.S_ISREG(path.stat().st_mode), "not a regular file: " + str(path))
    return path.read_text(encoding="utf-8")


def safe_path(name):
    require(bool(name) and not any(c.isspace() or ord(c) < 32 for c in name), "invalid listing/dep path: " + repr(name))
    while name.startswith("./"):
        name = name[2:]
    name = name.lstrip("/").rstrip("/")
    require(".." not in name.split("/") and "//" not in name and "." not in name.split("/"),
            "noncanonical listing/dep path: " + name)
    return name


def module_key(name):
    # Preserve exact paths/names, but packaging may compress the same module.
    require(not name.startswith("/"), "absolute modules.dep entry: " + name)
    name = safe_path(name)
    match = re.fullmatch(r"(.+\.ko)(?:\.(?:xz|zst))?", name)
    require(bool(match), "unsupported module path/suffix: " + name)
    return match[1]


def verify(args):
    boot, root = Path(args.boot_dir).resolve(strict=True), Path(args.root_dir).resolve(strict=True)
    configs = [p.name for p in boot.iterdir() if p.name.startswith("config-")]
    require(len(configs) == 1, "bootfs needs exactly one config-<kernel-release>")
    release = configs[0][len("config-"):]
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+\-]*", release)), "invalid config kernel release")
    options = {}
    for line in read(rooted(boot, configs[0])).splitlines():
        match = re.fullmatch(r"CONFIG_([A-Za-z0-9_]+)=(.*)", line)
        unset = re.fullmatch(r"# CONFIG_([A-Za-z0-9_]+) is not set", line)
        if match or unset:
            key, value = (match[1], match[2]) if match else (unset[1], "n")
            require(key not in options, "duplicate CONFIG_" + key)
            options[key] = value
        elif line and not line.startswith("#"):
            raise ValueError("malformed kernel config line: " + line)
    for symbol in DRIVERS:
        require(options.get(symbol) in ("y", "m"), "CONFIG_%s must be y or m (got %s)" % (symbol, options.get(symbol, "missing")))
    for symbol, dependencies in PREREQUISITES.items():
        for dependency in dependencies:
            require(not (options[symbol] == "y" and options[dependency] == "m"),
                    "built-in CONFIG_%s depends on modular CONFIG_%s" % (symbol, dependency))
    names, modules = set(), set()
    for line in read(Path(args.listing)).splitlines():
        if line in (".", "./", "/"):
            continue
        name = safe_path(line)
        if name.startswith("usr/lib/"):
            name = name[4:]
        names.add(name)
        match = re.fullmatch(r"lib/modules/([^/]+)/(.+\.ko(?:\.[^/]+)?)", name)
        if match:
            require(match[1] == release, "initramfs module has wrong kernel release: " + name)
            key = module_key(match[2])
            require(key not in modules, "duplicate/ambiguous initramfs module: " + key)
            modules.add(key)
    require("init" in names, "initramfs listing lacks /init")
    requested = {"kernel/" + path + ".ko" for symbol, path in DRIVERS.items() if options[symbol] == "m"}
    if requested:
        require(options.get("MODULES") == "y", "CONFIG_MODULES=y required for modular USB root")
        prefix = "lib/modules/" + release + "/"
        for metadata in ("modules.dep", "modules.alias"):
            require(prefix + metadata in names or prefix + metadata + ".bin" in names,
                    "initramfs missing module lookup metadata: " + metadata)
        dep_path = rooted(root, prefix + "modules.dep")
        if not dep_path.exists():
            dep_path = rooted(root, "usr/" + prefix + "modules.dep")
        graph = {}
        for line in read(dep_path).splitlines():
            require(line.count(":") == 1, "malformed modules.dep line: " + line)
            name, dependencies = line.split(":")
            key = module_key(name)
            require(key not in graph, "duplicate modules.dep entry: " + key)
            graph[key] = [module_key(n) for n in dependencies.split()]
        pending, seen, visiting = [(n, False) for n in requested], set(), set()
        while pending:
            name, finished = pending.pop()
            if finished:
                visiting.remove(name)
                seen.add(name)
                continue
            if name in seen:
                continue
            require(name not in visiting, "cyclic modules.dep dependency: " + name)
            require(name in modules, "initramfs missing required module/dependency for %s: %s" % (release, name))
            require(name in graph, "modules.dep missing required entry: " + name)
            visiting.add(name)
            pending.append((name, True))
            pending.extend((n, False) for n in graph[name])
        print("OK: USB-root listing has %d required modules/dependencies for %s" % (len(seen), release))
    else:
        print("OK: USB-root driver chain built-in for " + release)
    print("Static config/listing check only; no target code executed, no USB hardware/boot validation.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listing", required=True, help="plain host lsinitramfs listing, not verbose output")
    parser.add_argument("--boot-dir", required=True, help="bootfs with one final config-<release>")
    parser.add_argument("--root-dir", required=True, help="rootfs containing same-release modules.dep")
    args = parser.parse_args()
    try:
        verify(args)
    except (OSError, ValueError) as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
