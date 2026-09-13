#!/usr/bin/env bash
# Read-only static checks. Never mount images or execute target/package scripts.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if ! command -v python3 >/dev/null 2>&1; then
	printf 'ERROR: python3 (>= 3.8) is required\n' >&2
	exit 2
fi
exec python3 - "$repo_dir" "$@" <<'PY'
import argparse
import bz2
import gzip
import hashlib
import io
import lzma
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys
import tarfile
import zlib

REPO = Path(sys.argv.pop(1))
DTB_NAME = "mt7987a-edgepi-e87n.dtb"
DEBIAN_RELEASES = {"bookworm": "12", "trixie": "13"}
PHY_PROFILES = {
    "6.12": {"symbol": "MEDIATEK_2P5G_PHY", "directory": "kernel/drivers/net/phy"},
    "6.18": {"symbol": "MEDIATEK_2P5GE_PHY", "directory": "kernel/drivers/net/phy/mediatek"},
}
REQUIRED_Y = """ARM64 ARCH_MEDIATEK OF PINCTRL_MT7987 COMMON_CLK_MT7987
WATCHDOG MEDIATEK_WATCHDOG MFD_SYSCON NVMEM NVMEM_MTK_EFUSE
REGULATOR_FIXED_VOLTAGE MMC MMC_BLOCK MMC_MTK PARTITION_ADVANCED EFI_PARTITION
SERIAL_8250 SERIAL_8250_CONSOLE SERIAL_8250_MT6577 SERIAL_OF_PLATFORM
SERIAL_EARLYCON DEVTMPFS DEVTMPFS_MOUNT BLK_DEV_INITRD RD_GZIP RD_ZSTD
EXT4_FS EXT4_FS_POSIX_ACL EXT4_FS_SECURITY FW_LOADER MODULES""".split()
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class Invalid(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def ok(message):
    print("OK: " + message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path):
    require(stat.S_ISREG(path.stat().st_mode), "not a regular file: " + str(path))
    return path.read_bytes()


class Root:
    """Resolve target absolute symlinks inside its root, never in the host root."""
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        require(self.path.is_dir(), "not a directory: " + str(path))

    def resolve(self, name):
        pending, parts, hops = str(name).split("/"), [], 0
        while pending:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                require(bool(parts), "path escapes target root: " + str(name))
                parts.pop()
                continue
            candidate = self.path.joinpath(*parts, part)
            if candidate.is_symlink():
                hops += 1
                require(hops <= 40, "symlink loop: " + str(name))
                target = os.readlink(candidate)
                if target.startswith("/"):
                    parts = []
                pending = target.split("/") + pending
            else:
                parts.append(part)
        return self.path.joinpath(*parts)

    def read(self, name):
        return read_file(self.resolve(name))


def one(values, description):
    values = sorted(values)
    require(len(values) == 1,
            "%s: expected exactly one, found %d: %s" % (description, len(values), values))
    return values[0]


def check_kernel(data, name):
    require(len(data) >= 64 and data[56:60] == b"ARM\x64",
            "kernel is not an uncompressed arm64 Image: " + name)
    ok("arm64 Image header; %s; sha256=%s" % (name, digest(data)))


def check_config(data, release):
    series = re.fullmatch(r"(6\.(?:12|18))\.[0-9]+(?:[-+][A-Za-z0-9_.+\-]+)?", release)
    require(series is not None, "unsupported kernel series for PHY validation: " + release)
    profile = PHY_PROFILES[series[1]]
    options = {}
    for line in data.decode("utf-8").splitlines():
        match = re.fullmatch(r"CONFIG_([A-Z0-9_]+)=(.*)", line)
        unset = re.fullmatch(r"# CONFIG_([A-Z0-9_]+) is not set", line)
        if match or unset:
            key, value = (match[1], match[2]) if match else (unset[1], "n")
            require(key not in options, "duplicate config symbol: CONFIG_" + key)
            options[key] = value
    expected = dict.fromkeys(REQUIRED_Y, "y")
    expected[profile["symbol"]] = "m"
    wrong = ["CONFIG_%s=%s (need %s)" % (key, options.get(key, "MISSING"), value)
             for key, value in expected.items() if options.get(key) != value]
    require(not wrong, "required kernel config: " + "; ".join(wrong))
    for other in PHY_PROFILES.values():
        if other != profile:
            require(options.get(other["symbol"]) in (None, "n"),
                    "wrong PHY config for kernel %s: CONFIG_%s must be absent or disabled" %
                    (release, other["symbol"]))
    modules = ["mtk-2p5ge"]
    if series[1] == "6.18":
        # Kconfig selects this shared library: built-in consumers may promote it
        # to y even though the 2.5G driver itself must remain modular.
        require(options.get("MTK_NET_PHYLIB") in ("m", "y"),
                "CONFIG_MTK_NET_PHYLIB must be m or y for Linux 6.18")
        if options["MTK_NET_PHYLIB"] == "m":
            modules.append("mtk-phy-lib")
    ok("final kernel config: %d built-ins and CONFIG_%s=m for %s" %
       (len(REQUIRED_Y), profile["symbol"], release))
    return profile, modules


def phy_module_pattern(name):
    return re.escape(name) + r"\.ko(?:\.(?:xz|gz|zst))?"


def fdt_properties(data):
    require(len(data) >= 40, "DTB header truncated")
    magic, total, off_struct, off_strings, off_reserve, version, compat, _, nstr, nstruct = struct.unpack_from(
        ">10I", data)
    require(magic == 0xd00dfeed and 40 <= total <= len(data), "invalid DTB magic/size")
    require(version >= 17 and compat <= 17, "unsupported DTB version")
    require(40 <= off_reserve < total and off_reserve % 8 == 0, "invalid DTB reserve offset")
    require(off_struct % 4 == 0 and 40 <= off_struct < total and off_struct + nstruct <= total,
            "invalid DTB structure bounds")
    require(40 <= off_strings <= total and off_strings + nstr <= total,
            "invalid DTB string bounds")
    require(off_struct + nstruct <= off_strings or off_strings + nstr <= off_struct,
            "overlapping DTB blocks")
    strings, block = data[off_strings:off_strings + nstr], data[off_struct:off_struct + nstruct]
    cursor, stack, props, ended, root_seen = 0, [], {}, False, False
    while cursor + 4 <= len(block):
        token = struct.unpack_from(">I", block, cursor)[0]
        cursor += 4
        if token == 1:
            end = block.find(b"\0", cursor)
            require(end >= cursor, "unterminated DTB node")
            name = block[cursor:end].decode("ascii")
            if not stack:
                require(not root_seen and name == "", "invalid DTB root node")
                root_seen = True
            stack.append(name)
            cursor = (end + 4) & ~3
        elif token == 2:
            require(bool(stack), "unbalanced DTB node")
            stack.pop()
        elif token == 3:
            require(bool(stack) and cursor + 8 <= len(block), "truncated DTB property")
            length, offset = struct.unpack_from(">II", block, cursor)
            cursor += 8
            end = strings.find(b"\0", offset)
            require(offset < len(strings) and end >= offset and cursor + length <= len(block),
                    "invalid DTB property bounds")
            key = ("/" + "/".join(stack[1:]), strings[offset:end].decode("ascii"))
            require(key not in props, "duplicate DTB property: " + repr(key))
            props[key] = block[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            require(root_seen and not stack, "unfinished DTB tree")
            ended = True
            break
        else:
            raise Invalid("unknown DTB token: " + str(token))
    require(ended, "DTB missing END token")
    return props


def check_dtb(data):
    props = fdt_properties(data)
    compatible = props.get(("/", "compatible"), b"")
    require(compatible.endswith(b"\0"), "DTB root compatible missing/unterminated")
    names = compatible[:-1].decode("ascii").split("\0")
    require(all(name in names for name in ("edgepi,e87n", "mediatek,mt7987a", "mediatek,mt7987")),
            "wrong DTB root compatible: " + repr(names))
    require(props.get(("/memory", "device_type")) == b"memory\0",
            "DTB /memory needs device_type=memory")
    require(bool(props.get(("/memory", "reg"))), "DTB /memory needs non-empty reg")
    bootargs = props.get(("/chosen", "bootargs"), b"").rstrip(b"\0").decode("ascii")
    require(not any(word.startswith("root=") for word in bootargs.split()),
            "DTB /chosen/bootargs must leave root= to extlinux")
    require(not any(fs in bootargs for fs in ("squashfs", "f2fs")), "legacy DTB root filesystem args")
    ok("DTB root compatible, /memory device_type/reg and no stale root override; sha256=" + digest(data))


def decompress(data):
    if data.startswith(b"\x1f\x8b"):
        return gzip.decompress(data)
    if data.startswith(b"\xfd7zXZ\0"):
        return lzma.decompress(data)
    if data.startswith(b"BZh"):
        return bz2.decompress(data)
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        result = subprocess.run(["zstd", "-d", "-q", "-c"], input=data, capture_output=True)
        require(result.returncode == 0, "zstd decompression failed")
        return result.stdout
    return data


def check_module(data, name):
    elf = decompress(data)
    require(len(elf) >= 64 and elf[:6] == b"\x7fELF\x02\x01" and
            struct.unpack_from("<HH", elf, 16) == (1, 183),
            "PHY module is not an arm64 relocatable ELF: " + name)
    ok("PHY module arm64 ELF: " + name)


def package_members(path):
    # Debian ar envelopes have short member names. No extraction/maintainer scripts.
    require(stat.S_ISREG(path.stat().st_mode), "not a regular deb file: " + str(path))
    result = {}
    with path.open("rb") as stream:
        require(stream.read(8) == b"!<arch>\n", "invalid deb ar header: " + str(path))
        while True:
            header = stream.read(60)
            if not header:
                break
            require(len(header) == 60 and header[58:] == b"`\n", "invalid deb ar member")
            name = header[:16].decode("ascii").strip().rstrip("/")
            size = int(header[48:58].decode("ascii").strip())
            require(size >= 0 and name not in result, "invalid/duplicate deb ar member")
            data = stream.read(size)
            require(len(data) == size, "truncated deb ar member")
            result[name] = data
            if size % 2:
                require(len(stream.read(1)) == 1, "missing deb ar padding")
    require(result.get("debian-binary") == b"2.0\n", "unsupported deb version")
    return result


def tar_files(data, wanted):
    # Stream gzip/xz/bzip2 tar payloads rather than materializing every module.
    # Python 3.8's tarfile lacks zstd support; only that format uses the helper.
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        data = decompress(data)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r|*") as archive:
        for member in archive:
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            require(not name.startswith("/") and ".." not in name.split("/"),
                    "unsafe path in deb tar: " + name)
            if wanted(name):
                require(member.isfile(), "expected regular file in deb: " + name)
                yield name, archive.extractfile(member).read()


def check_debs(directory):
    path = Path(directory)
    require(path.is_dir(), "debs directory missing: " + directory)
    files, versions, kinds = {}, set(), set()
    packages = sorted(path.rglob("linux-image-*.deb")) + sorted(path.rglob("linux-dtb-*.deb"))
    require(bool(packages), "no linux-image/linux-dtb .deb artifacts in " + directory)
    for package in packages:
        members = package_members(package)
        control_key = one([n for n in members if n == "control.tar" or n.startswith("control.tar.")],
                          "deb control archive")
        control = dict(tar_files(members[control_key], lambda n: n == "control"))
        require("control" in control, "deb control metadata missing")
        fields = dict(re.findall(r"^([A-Za-z-]+):[ \t]*(.*)$", control["control"].decode(), re.M))
        name = fields.get("Package", "")
        require(fields.get("Architecture") == "arm64", "deb is not arm64: " + str(package))
        require(name in ("linux-image-current-filogic", "linux-dtb-current-filogic"),
                "unexpected E87N package: " + name)
        require(name not in kinds, "multiple kernel package sets; select a single build directory")
        require(bool(fields.get("Version")), "deb Version missing")
        kinds.add(name)
        versions.add(fields["Version"])
        key = one([n for n in members if n == "data.tar" or n.startswith("data.tar.")], "deb data archive")
        wanted = lambda n: (n.startswith("boot/") and
                            (n.startswith(("boot/vmlinuz-", "boot/config-")) or n.endswith("/" + DTB_NAME))) or bool(
                                re.search(r"/kernel/drivers/net/phy/(?:mediatek/)?"
                                          r"(?:mtk-2p5ge|mtk-phy-lib)\.ko(?:\.(?:xz|gz|zst))?$", n))
        for entry, data in tar_files(members[key], wanted):
            require(entry not in files, "duplicate package payload: " + entry)
            files[entry] = data
        ok("read package without extraction: " + str(package))
    require(kinds == {"linux-image-current-filogic", "linux-dtb-current-filogic"} and len(versions) == 1,
            "need matching-version linux-image-current-filogic and linux-dtb-current-filogic packages")
    kernel = one([n for n in files if n.startswith("boot/vmlinuz-")], "packaged kernel")
    release = kernel[len("boot/vmlinuz-"):]
    config = "boot/config-" + release
    dtb = "boot/dtb-" + release + "/mediatek/" + DTB_NAME
    require(config in files and dtb in files, "packaged config/DTB do not match kernel release " + release)
    profile, modules = check_config(files[config], release)
    for name in modules:
        module = one([n for n in files if re.fullmatch(
            r"(?:usr/)?lib/modules/" + re.escape(release) +
            r"/kernel/drivers/net/phy/(?:mediatek/)?" + phy_module_pattern(name), n)],
            "packaged PHY module %s for %s" % (name, release))
        require(re.fullmatch(r"(?:usr/)?lib/modules/" + re.escape(release + "/" + profile["directory"]) +
                             "/" + phy_module_pattern(name), module),
                "wrong PHY module path for kernel %s: %s (need %s/)" %
                (release, module, profile["directory"]))
        check_module(files[module], module)
    check_kernel(files[kernel], kernel)
    check_dtb(files[dtb])
    print("SCOPE: kernel packages only; Debian rootfs, firmware, initrd and extlinux NOT VERIFIED")


def boot_labels(text):
    labels, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pair = line.split(None, 1)
        key, value = pair[0].lower(), pair[1].strip() if len(pair) > 1 else ""
        if key == "label":
            require(value and value not in labels, "missing/duplicate extlinux label")
            current = labels.setdefault(value, {})
        elif key in ("linux", "kernel", "initrd", "fdt", "fdtdir", "append"):
            require(current is not None, "global extlinux boot directives unsupported; use per-label entries")
            key = "kernel" if key == "linux" else key
            require(key not in current, "duplicate extlinux directive: " + key)
            current[key] = value
        elif key == "include":
            raise Invalid("extlinux include unsupported; validate a self-contained file")
    require(bool(labels), "no extlinux boot labels")
    return labels


def check_rootfs(args):
    root = Root(args.extracted_rootfs)
    boot = Root(args.boot_dir) if args.boot_dir else root
    prefix = "" if args.boot_dir else "boot/"
    boot_read = lambda name: boot.read(prefix + name.lstrip("/"))
    config_dir = boot.resolve(prefix or ".")
    config_path = Path(args.config) if args.config else config_dir / one(
        [p.name for p in config_dir.iterdir() if p.name.startswith("config-")], "boot config (or use --config)")
    config_data = read_file(config_path) if args.config else boot_read(config_path.name)
    require(config_path.name.startswith("config-") or args.kernel_release,
            "unversioned --config requires --kernel-release to check module directory")
    release = args.kernel_release or config_path.name[len("config-"):]
    require(bool(re.fullmatch(r"[A-Za-z0-9_.+\-]+", release)), "invalid kernel release")
    if config_path.name.startswith("config-"):
        require(config_path.name == "config-" + release, "config filename/--kernel-release mismatch")
    profile, required_modules = check_config(config_data, release)
    module_root = root.resolve("lib/modules/" + release)
    # Some extracted roots keep /usr/lib without a /lib compatibility symlink.
    if not module_root.is_dir():
        module_root = root.resolve("usr/lib/modules/" + release)
    for name in required_modules:
        candidates = []
        for candidate_profile in PHY_PROFILES.values():
            directory = root.resolve(str((module_root / candidate_profile["directory"]).relative_to(root.path)))
            if directory.is_dir():
                candidates.extend(p for p in directory.iterdir() if re.fullmatch(phy_module_pattern(name), p.name))
        module_path = one(candidates, "installed PHY module %s for %s" % (name, release))
        expected_dir = root.resolve(str((module_root / profile["directory"]).relative_to(root.path)))
        require(module_path.parent == expected_dir,
                "wrong PHY module path for kernel %s: %s (need %s/)" %
                (release, module_path, profile["directory"]))
        module_path = root.resolve(str(module_path.relative_to(root.path)))
        check_module(read_file(module_path), str(module_path))
    os_release = root.read("etc/os-release").decode("utf-8")
    fields = {}
    for line in os_release.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z_]+)=(.*)", line)
        require(bool(match) and match[1] not in fields, "invalid/duplicate os-release entry")
        values = shlex.split(match[2])
        require(len(values) <= 1, "invalid os-release value")
        fields[match[1]] = values[0] if values else ""
    expected = {"ID": "debian", "VERSION_ID": DEBIAN_RELEASES[args.release],
                "VERSION_CODENAME": args.release}
    wrong = ["%s=%r (need %r)" % (key, fields.get(key), value)
             for key, value in expected.items() if fields.get(key) != value]
    target_os = "Debian %s %s" % (expected["VERSION_ID"], args.release.capitalize())
    require(not wrong, "target os-release must identify %s for --release %s: %s "
            "(not the build container OS)" % (target_os, args.release, "; ".join(wrong)))
    ok("target /etc/os-release: %s (parsed as data, not executed)" % target_os)
    manifest = {}
    for line in (REPO / "firmware/SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(bool(match), "invalid repository firmware/SHA256SUMS")
        require(match[2] not in manifest, "duplicate firmware checksum entry")
        manifest[match[2]] = match[1]
    for name, size in (("i2p5ge-phy-DSPBitTb.bin", 28672), ("i2p5ge-phy-pmb.bin", 98304)):
        relative = "mediatek/mt7987/" + name
        data = root.read("usr/lib/firmware/" + relative)
        require(len(data) == size and digest(data) == manifest.get(relative), "PHY firmware sha256/size mismatch: " + name)
        ok("installed PHY firmware: %s; sha256=%s" % (name, digest(data)))
    license_data = root.read("usr/share/doc/e87n-phy-firmware/LICENCE.mediatek")
    require(digest(license_data) == manifest.get("LICENCE.mediatek"), "installed PHY firmware licence checksum mismatch")
    ok("installed PHY firmware licence SHA-256")
    entries = [shlex.split(line, comments=True) for line in root.read("etc/fstab").decode().splitlines()]
    roots = [entry for entry in entries if len(entry) >= 2 and entry[1] == "/"]
    require(len(roots) == 1 and len(roots[0]) >= 6, "fstab needs exactly one complete root entry")
    source = roots[0][0]
    require(source.startswith("UUID=") and UUID_RE.fullmatch(source[5:]) and roots[0][2] == "ext4",
            "fstab root must use UUID=<ext4 UUID> and type ext4")
    uuid = source[5:].lower()
    if args.expected_root_uuid:
        require(UUID_RE.fullmatch(args.expected_root_uuid) and uuid == args.expected_root_uuid.lower(),
                "fstab root UUID differs from --expected-root-uuid")
    extlinux = read_file(Path(args.extlinux)) if args.extlinux else boot_read("extlinux/extlinux.conf")
    for label, entry in boot_labels(extlinux.decode()).items():
        require(all(entry.get(key) for key in ("kernel", "initrd", "fdt", "append")) and "fdtdir" not in entry,
                "extlinux label needs kernel/initrd/explicit fdt/append: " + label)
        words = shlex.split(entry["append"])
        roots = [word for word in words if word.startswith("root=")]
        require(len(roots) == 1 and roots[0].lower() == "root=uuid=" + uuid,
                "extlinux must contain one root=UUID matching fstab: " + label)
        require([w for w in words if w.startswith("rootfstype=")] == ["rootfstype=ext4"] and
                "rootwait" in words and "console=ttyS0,115200n8" in words,
                "extlinux needs ext4, rootwait and ttyS0,115200n8: " + label)
        require(not any(fs in entry["append"] for fs in ("squashfs", "f2fs")), "legacy extlinux filesystem args")
        for key in ("kernel", "initrd", "fdt"):
            require(not any(char.isspace() for char in entry[key]) and "," not in entry[key] and
                    ".." not in entry[key].split("/"), "unsupported extlinux artifact path: " + entry[key])
        kernel, dtb = boot_read(entry["kernel"]), boot_read(entry["fdt"])
        if args.kernel:
            require(kernel == read_file(Path(args.kernel)), "extlinux kernel differs from --kernel")
        if args.dtb:
            require(dtb == read_file(Path(args.dtb)), "extlinux DTB differs from --dtb")
        require(Path(entry["fdt"]).name == DTB_NAME, "extlinux does not select the E87N DTB")
        kernel_path = boot.resolve(prefix + entry["kernel"].lstrip("/"))
        if kernel_path.name.startswith("vmlinuz-"):
            require(kernel_path.name == "vmlinuz-" + release, "kernel filename/config release mismatch")
        check_kernel(kernel, entry["kernel"])
        check_dtb(dtb)
        require(len(boot_read(entry["initrd"])) >= 64, "missing/truncated extlinux initrd")
        ok("extlinux label %s: root=UUID=%s once, matching fstab; initrd present (contents NOT VERIFIED)" % (label, uuid))
    print("SCOPE: extracted rootfs/boot static checks; filesystem UUID itself and UUID uniqueness across devices NOT VERIFIED")


def main():
    parser = argparse.ArgumentParser(
        description="Read-only E87N Debian Armbian artifact checks. No mounts, flashing, downloads or target code execution.",
        epilog="""Examples:
  bash scripts/verify-artifacts.sh --debs source/armbian-build/output/debs
  bash scripts/verify-artifacts.sh --extracted-rootfs /path/to/root --boot-dir /path/to/boot
  bash scripts/verify-artifacts.sh --release bookworm --extracted-rootfs /path/to/old-root
  bash scripts/verify-artifacts.sh --extracted-rootfs /path/to/root --config /path/to/.config --kernel-release 6.12.108-current-filogic
Requires Python >=3.8; zstd executable only for zstd-compressed inputs.
--debs checks one matching arm64 current-filogic image/DTB package set recursively,
not rootfs/firmware/extlinux. Directory mode checks all self-contained extlinux
labels against one config/release; boot paths are relative to the bootfs root.
Rootfs checks require ID=debian and matching VERSION_ID/VERSION_CODENAME for
--release (default: 13/trixie; bookworm: 12/bookworm). --debs does not verify the OS.
PHY layouts are specific to Linux 6.12 (MEDIATEK_2P5G_PHY, flat phy directory)
or 6.18 (MEDIATEK_2P5GE_PHY, phy/mediatek plus MTK_NET_PHYLIB). Other series fail.
Absolute target symlinks are interpreted inside the extracted root (or --boot-dir).
--kernel/--dtb cross-check the files actually selected by extlinux, not substitutes.
These checks do NOT prove build provenance, initrd contents, U-Boot/extlinux/booti
support, GPT safety, preserved bootloader/fixed MAC, or successful compilation/boot.
Never write a whole .img to eMMC on the strength of this result.
Fixture input is synthetic and must be explicitly labelled --fixture.""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--debs", metavar="DIR", help="kernel packages only (no extraction)")
    mode.add_argument("--extracted-rootfs", metavar="DIR", help="already extracted target Debian root directory")
    parser.add_argument("--release", choices=DEBIAN_RELEASES, default="trixie",
                        help="target Debian rootfs release (default: trixie); not checked in --debs mode")
    parser.add_argument("--boot-dir", metavar="DIR", help="separately extracted bootfs; default ROOT/boot")
    parser.add_argument("--config", metavar="FILE", help="final kernel config; default unique boot/config-*")
    parser.add_argument("--kernel-release", help="module release for unversioned --config (e.g. .config)")
    parser.add_argument("--kernel", metavar="FILE", help="expected arm64 Image; compare to extlinux-selected kernel")
    parser.add_argument("--dtb", metavar="FILE", help="expected E87N DTB; compare to extlinux-selected DTB")
    parser.add_argument("--extlinux", metavar="FILE", help="explicit extlinux.conf; references still resolve inside bootfs")
    parser.add_argument("--expected-root-uuid", metavar="UUID", help="independently obtained test rootfs UUID")
    parser.add_argument("--fixture", action="store_true", help="label synthetic test inputs; does not bypass any checks")
    args = parser.parse_args()
    if args.debs and any((args.boot_dir, args.config, args.kernel_release, args.kernel, args.dtb,
                          args.extlinux, args.expected_root_uuid)):
        parser.error("boot/rootfs options require --extracted-rootfs")
    print("INPUT: " + ("FIXTURE ONLY — not real artifact verification" if args.fixture else "user-supplied artifacts (provenance not authenticated)"), flush=True)
    if args.debs:
        check_debs(args.debs)
    else:
        check_rootfs(args)
    print("PASS: " + ("FIXTURE static checks only; no real build artifacts verified" if args.fixture else
                      "static checks in the scope above only; experimental, NOT board-validated"))


try:
    main()
except (Invalid, OSError, ValueError, EOFError, struct.error, tarfile.TarError, lzma.LZMAError, zlib.error) as error:
    print("FAIL: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
