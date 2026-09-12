#!/usr/bin/env bash
# Synthetic regression fixtures, NOT verification of a real Armbian build.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 - "$repo_dir" <<'PY'
import gzip
import hashlib
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

REPO = Path(sys.argv[1])
SCRIPT = REPO / "scripts/verify-artifacts.sh"
RELEASE = "6.12.108-current-filogic"
UUID = "12345678-1234-4abc-8def-123456789abc"
DTB = "mt7987a-edgepi-e87n.dtb"
# Independent expectations: do not import validator functions or source board code.
REQUIRED = """ARM64 ARCH_MEDIATEK OF PINCTRL_MT7987 COMMON_CLK_MT7987
WATCHDOG MEDIATEK_WATCHDOG MFD_SYSCON NVMEM NVMEM_MTK_EFUSE
REGULATOR_FIXED_VOLTAGE MMC MMC_BLOCK MMC_MTK PARTITION_ADVANCED EFI_PARTITION
SERIAL_8250 SERIAL_8250_CONSOLE SERIAL_8250_MT6577 SERIAL_OF_PLATFORM
SERIAL_EARLYCON DEVTMPFS DEVTMPFS_MOUNT BLK_DEV_INITRD RD_GZIP RD_ZSTD
EXT4_FS EXT4_FS_POSIX_ACL EXT4_FS_SECURITY FW_LOADER MODULES""".split()
CONFIG = "# SYNTHETIC TEST CONFIG, NOT A BUILD RESULT\n" + "".join(
    "CONFIG_" + name + "=y\n" for name in REQUIRED) + "CONFIG_MEDIATEK_2P5G_PHY=m\n"
EXTLINUX = ("# SYNTHETIC TEST INPUT\ndefault Armbian\nlabel Armbian\n"
            " kernel /Image\n initrd /uInitrd\n fdt /dtb/mediatek/" + DTB + "\n"
            " append root=UUID=" + UUID + " console=ttyS0,115200n8 rootwait rootfstype=ext4 rw\n")


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def make_dtb(compatible=None, bootargs="console=ttyS0,115200n8 rootfstype=ext4", nested=False,
             memory=True, memory_type=b"memory\0", memory_reg=struct.pack(">4I", 0, 0x40000000, 0, 0x10000000)):
    # Minimal binary FDT, not strings masquerading as a compiled DTB.
    names = b"compatible\0bootargs\0device_type\0reg\0"
    compat = compatible or b"edgepi,e87n\0mediatek,mt7987a\0mediatek,mt7987\0"
    u32 = lambda value: struct.pack(">I", value)
    align = lambda data: data + b"\0" * (-len(data) % 4)
    begin = lambda name: u32(1) + align(name + b"\0")
    prop = lambda offset, value: u32(3) + u32(len(value)) + u32(offset) + align(value)
    tree = begin(b"")
    if nested:
        tree += begin(b"unrelated-child") + prop(0, compat) + u32(2)
    else:
        tree += prop(0, compat)
    tree += begin(b"chosen") + prop(11, bootargs.encode() + b"\0") + u32(2)
    if memory:
        tree += begin(b"memory")
        if memory_type is not None:
            tree += prop(20, memory_type)
        if memory_reg is not None:
            tree += prop(32, memory_reg)
        tree += u32(2)
    tree += u32(2) + u32(9)
    header = struct.pack(">10I", 0xd00dfeed, 56 + len(tree) + len(names),
                         56, 56 + len(tree), 40, 17, 16, 0, len(names), len(tree))
    return header + bytes(16) + tree + names


IMAGE = bytearray(128)
IMAGE[56:60] = b"ARM\x64"
IMAGE[64:] = b"SYNTHETIC ARM64 HEADER ONLY; NOT EXECUTABLE".ljust(64, b"\0")
ELF = bytearray(128)
ELF[:6] = b"\x7fELF\x02\x01"
struct.pack_into("<HH", ELF, 16, 1, 183)
ELF[64:] = b"SYNTHETIC MODULE HEADER ONLY; NOT LOADABLE".ljust(64, b"\0")


def make_root(path):
    put(path / "boot" / ("vmlinuz-" + RELEASE), IMAGE)
    put(path / "boot" / ("config-" + RELEASE), CONFIG)
    put(path / "boot" / ("dtb-" + RELEASE) / "mediatek" / DTB, make_dtb())
    put(path / "boot/uInitrd", b"SYNTHETIC INITRD PRESENCE FIXTURE; NOT BOOTABLE\n" * 4)
    put(path / "boot/extlinux/extlinux.conf", EXTLINUX)
    (path / "boot/Image").symlink_to("vmlinuz-" + RELEASE)
    (path / "boot/dtb").symlink_to("dtb-" + RELEASE)
    put(path / "usr/lib/modules" / RELEASE / "kernel/drivers/net/phy/mtk-2p5ge.ko", ELF)
    (path / "lib").symlink_to("usr/lib")
    put(path / "usr/lib/os-release", 'ID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n')
    put(path / "etc/fstab", "UUID=" + UUID + " / ext4 defaults 0 1\n")
    (path / "etc/os-release").symlink_to("/usr/lib/os-release")
    for name in ("i2p5ge-phy-DSPBitTb.bin", "i2p5ge-phy-pmb.bin"):
        target = path / "usr/lib/firmware/mediatek/mt7987" / name
        put(target, (REPO / "firmware/mediatek/mt7987" / name).read_bytes())
    put(path / "usr/share/doc/e87n-phy-firmware/LICENCE.mediatek",
        (REPO / "firmware/LICENCE.mediatek").read_bytes())


def tar_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            data = data.encode() if isinstance(data, str) else data
            info = tarfile.TarInfo("./" + name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_deb(path, name, files, version="1.0-fixture", arch="arm64", compression="gz"):
    control = tar_bytes({"control": "Package: %s\nVersion: %s\nArchitecture: %s\nDescription: SYNTHETIC FIXTURE\n" %
                         (name, version, arch),
                         "postinst": "#!/bin/sh\necho MUST_NOT_EXECUTE\nexit 99\n"})
    payload = tar_bytes(files)
    if compression == "gz":
        payload = gzip.compress(payload)
    elif compression == "xz":
        import lzma
        payload = lzma.compress(payload)
    elif compression == "zst":
        payload = subprocess.run(["zstd", "-q", "-c"], input=payload, capture_output=True, check=True).stdout
    members = {"debian-binary": b"2.0\n", "control.tar.gz": gzip.compress(control), "data.tar." + compression: payload}
    data = bytearray(b"!<arch>\n")
    for member, body in members.items():
        header = "%-16s%-12s%-6s%-6s%-8s%-10s`\n" % (member + "/", "0", "0", "0", "100644", len(body))
        assert len(header) == 60
        data.extend(header.encode() + body + (b"\n" if len(body) % 2 else b""))
    put(path, data)


def make_packages(path, version="1.0-fixture", arch="arm64", compression="gz", config=CONFIG):
    make_deb(path / "linux-image-current-filogic_fixture_arm64.deb", "linux-image-current-filogic", {
        "boot/vmlinuz-" + RELEASE: IMAGE,
        "boot/config-" + RELEASE: config,
        "lib/modules/" + RELEASE + "/kernel/drivers/net/phy/mtk-2p5ge.ko": ELF,
    }, arch=arch, compression=compression)
    make_deb(path / "linux-dtb-current-filogic_fixture_arm64.deb", "linux-dtb-current-filogic", {
        "boot/dtb-" + RELEASE + "/mediatek/" + DTB: make_dtb(),
    }, version=version, compression=compression)


def snapshot(path):
    result = {}
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(directory) / name
            relative = str(item.relative_to(path))
            if item.is_symlink():
                result[relative] = ("link", os.readlink(item))
            elif item.is_file():
                result[relative] = ("file", hashlib.sha256(item.read_bytes()).hexdigest(), item.stat().st_mode)
            else:
                result[relative] = ("dir", item.stat().st_mode)
    return result


count = 0


def run_case(name, args, expected, contains, watched):
    global count
    before = snapshot(watched)
    result = subprocess.run(["bash", str(SCRIPT)] + [str(a) for a in args], capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert (result.returncode == 0) == expected, "%s: unexpected exit %s\n%s" % (name, result.returncode, output)
    assert contains in output, "%s: missing diagnostic %r\n%s" % (name, contains, output)
    assert "Traceback" not in output, name + ": unhandled exception\n" + output
    assert before == snapshot(watched), name + ": verifier changed input files"
    count += 1
    print("PASS fixture test %02d: %s" % (count, name), flush=True)


with tempfile.TemporaryDirectory(prefix="e87n-verify-fixtures-") as temporary:
    work = Path(temporary)
    print("SYNTHETIC FIXTURE TESTS ONLY; no real kernel/rootfs/build output is being verified.", flush=True)
    root = work / "root"
    make_root(root)
    base = ["--fixture", "--extracted-rootfs", root]
    run_case("help", ["--help"], True, "No mounts, flashing, downloads", work)
    run_case("missing mode", [], False, "required", work)
    run_case("unknown option", base + ["--not-an-option"], False, "unrecognized", work)
    run_case("valid root incl. absolute os-release and merged-/usr symlinks", base, True, "PASS: FIXTURE", work)
    run_case("independent root UUID", base + ["--expected-root-uuid", UUID], True, "PASS: FIXTURE", work)
    run_case("wrong independent UUID", base + ["--expected-root-uuid", "aaaaaaaa-1234-4abc-8def-123456789abc"],
             False, "differs from --expected-root-uuid", work)
    run_case("explicit DTB cross-check", base + ["--dtb", root / "boot/dtb" / "mediatek" / DTB], True, "PASS: FIXTURE", work)
    put(work / "wrong.dtb", make_dtb(b"wrong,board\0"))
    run_case("explicit DTB cannot hide selected wrong file", base + ["--dtb", work / "wrong.dtb"], False, "differs from --dtb", work)
    put(work / ".config", CONFIG)
    run_case("unversioned config requires release", base + ["--config", work / ".config"], False, "requires --kernel-release", work)
    run_case("unversioned final config with release", base + ["--config", work / ".config", "--kernel-release", RELEASE],
             True, "PASS: FIXTURE", work)
    run_case("explicit release cannot mask versioned config mismatch", base + ["--kernel-release", "6.12.wrong"],
             False, "config filename/--kernel-release mismatch", work)
    changes = [
        ("missing built-in MMC driver", "boot/config-" + RELEASE, CONFIG.replace("CONFIG_MMC_MTK=y\n", ""), "CONFIG_MMC_MTK=MISSING"),
        ("ext4 must be built in", "boot/config-" + RELEASE, CONFIG.replace("CONFIG_EXT4_FS=y", "CONFIG_EXT4_FS=m"), "CONFIG_EXT4_FS=m"),
        ("wrong PHY config symbol cannot pass", "boot/config-" + RELEASE,
         CONFIG.replace("CONFIG_MEDIATEK_2P5G_PHY=m", "CONFIG_UNRELATED_PHY=m"), "CONFIG_MEDIATEK_2P5G_PHY=MISSING"),
        ("duplicate config symbol", "boot/config-" + RELEASE, CONFIG + "CONFIG_EXT4_FS=y\n", "duplicate config symbol"),
        ("wrong kernel magic", "boot/vmlinuz-" + RELEASE, b"not an ARM64 Image" * 8, "not an uncompressed arm64 Image"),
        ("wrong DTB root compatible", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(b"other,board\0"), "wrong DTB root compatible"),
        ("nested compatible cannot spoof root", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(nested=True), "root compatible missing"),
        ("missing DTB memory node", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(memory=False), "needs device_type=memory"),
        ("missing memory device_type", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(memory_type=None), "needs device_type=memory"),
        ("wrong memory device_type", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(memory_type=b"reserved\0"), "needs device_type=memory"),
        ("missing memory reg", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(memory_reg=None), "needs non-empty reg"),
        ("empty memory reg", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(memory_reg=b""), "needs non-empty reg"),
        ("DTB chosen root override", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(bootargs="root=PARTLABEL=rootfs"), "must leave root="),
        ("DTB old filesystem args", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb(bootargs="rootfstype=squashfs,f2fs"), "legacy DTB"),
        ("truncated DTB", "boot/dtb-" + RELEASE + "/mediatek/" + DTB, make_dtb()[:60], "invalid DTB magic/size"),
        ("wrong OS", "usr/lib/os-release", "ID=openwrt\nVERSION_CODENAME=bookworm\n", "must identify Debian Bookworm"),
        ("missing PHY module bytes", "usr/lib/modules/" + RELEASE + "/kernel/drivers/net/phy/mtk-2p5ge.ko", b"", "not an arm64 relocatable ELF"),
        ("corrupt firmware same size", "usr/lib/firmware/mediatek/mt7987/i2p5ge-phy-pmb.bin", bytes(98304), "firmware sha256/size mismatch"),
        ("corrupt firmware licence", "usr/share/doc/e87n-phy-firmware/LICENCE.mediatek", "bad licence", "licence checksum mismatch"),
        ("duplicate root parameter", "boot/extlinux/extlinux.conf", EXTLINUX.replace(" rw", " root=UUID=" + UUID + " rw"), "one root=UUID"),
        ("legacy PARTLABEL root", "boot/extlinux/extlinux.conf", EXTLINUX.replace("root=UUID=" + UUID, "root=PARTLABEL=rootfs"), "one root=UUID"),
        ("extlinux/fstab mismatch", "etc/fstab", "UUID=aaaaaaaa-1234-4abc-8def-123456789abc / ext4 defaults 0 1\n", "one root=UUID"),
        ("duplicate fstab roots", "etc/fstab", ("UUID=" + UUID + " / ext4 defaults 0 1\n") * 2, "exactly one complete root entry"),
        ("duplicate extlinux kernel", "boot/extlinux/extlinux.conf", EXTLINUX.replace(" kernel /Image", " kernel /Image\n linux /Image"), "duplicate extlinux directive"),
        ("extlinux path traversal", "boot/extlinux/extlinux.conf", EXTLINUX.replace("kernel /Image", "kernel /../Image"), "unsupported extlinux artifact path"),
        ("missing initrd", "boot/uInitrd", b"", "missing/truncated extlinux initrd"),
        ("bad serial console", "boot/extlinux/extlinux.conf", EXTLINUX.replace("115200n8", "115200n1"), "needs ext4, rootwait and ttyS0"),
    ]
    for option in ("WATCHDOG", "MEDIATEK_WATCHDOG", "MFD_SYSCON", "NVMEM", "NVMEM_MTK_EFUSE"):
        changes.append(("missing built-in " + option, "boot/config-" + RELEASE,
                        CONFIG.replace("CONFIG_" + option + "=y\n", ""), "CONFIG_" + option + "=MISSING"))
    for name, relative, replacement, diagnostic in changes:
        target = root / relative
        original = target.read_bytes()
        put(target, replacement)
        run_case(name, base, False, diagnostic, work)
        put(target, original)
    # Check every bootable label, not just the default one.
    put(root / "boot/extlinux/extlinux.conf", EXTLINUX + EXTLINUX.replace("label Armbian", "label rescue").replace("root=UUID=" + UUID, "root=/dev/mmcblk0p2"))
    run_case("bad secondary label", base, False, "one root=UUID", work)
    put(root / "boot/extlinux/extlinux.conf", EXTLINUX)
    put(root / "boot/config-ambiguous", CONFIG)
    run_case("ambiguous configs", base, False, "expected exactly one, found 2", work)
    (root / "boot/config-ambiguous").unlink()
    link = root / "etc/os-release"
    link.unlink()
    link.symlink_to("../../outside-os-release")
    put(work / "outside-os-release", 'ID=debian\nVERSION_CODENAME=bookworm\n')
    run_case("symlink traversal cannot read host fixture", base, False, "path escapes target root", work)
    link.unlink()
    link.symlink_to(str(work / "outside-os-release"))
    run_case("host absolute symlink is target-root-relative", base, False, "No such file", work)
    link.unlink()
    link.symlink_to("os-release")
    run_case("symlink loop", base, False, "symlink loop", work)
    link.unlink()
    link.symlink_to("/usr/lib/os-release")
    # A separate bootfs is checked without writing a mount point.
    shutil.move(str(root / "boot"), str(work / "bootfs"))
    run_case("separate extracted bootfs", base + ["--boot-dir", work / "bootfs"], True, "PASS: FIXTURE", work)
    shutil.move(str(work / "bootfs"), str(root / "boot"))
    packages = work / "debs"
    packages.mkdir()
    deb_args = ["--fixture", "--debs", packages]
    run_case("empty deb output is not success", deb_args, False, "no linux-image/linux-dtb", work)
    make_packages(packages)
    run_case("valid synthetic debs have limited scope", deb_args, True, "Debian rootfs, firmware, initrd and extlinux NOT VERIFIED", work)
    run_case("debs rejects rootfs-only options", deb_args + ["--config", work / ".config"], False, "require --extracted-rootfs", work)
    make_packages(packages, compression="xz")
    run_case("xz deb payload", deb_args, True, "PASS: FIXTURE", work)
    if shutil.which("zstd"):
        make_packages(packages, compression="zst")
        run_case("zstd deb payload", deb_args, True, "PASS: FIXTURE", work)
    else:
        print("SKIP fixture: zstd payload (zstd not installed)", flush=True)
    make_packages(packages, version="2.0-other-fixture")
    run_case("mismatched package versions", deb_args, False, "matching-version", work)
    make_packages(packages, arch="amd64")
    run_case("wrong package architecture", deb_args, False, "deb is not arm64", work)
    make_packages(packages, config=CONFIG.replace("CONFIG_MEDIATEK_2P5G_PHY=m", "CONFIG_MEDIATEK_2P5G_PHY=y"))
    run_case("packaged PHY must remain modular", deb_args, False, "CONFIG_MEDIATEK_2P5G_PHY=y", work)
    make_packages(packages)
    image_deb = packages / "linux-image-current-filogic_fixture_arm64.deb"
    shutil.copyfile(image_deb, packages / "linux-image-current-filogic_duplicate_arm64.deb")
    run_case("ambiguous package sets", deb_args, False, "multiple kernel package sets", work)
    (packages / "linux-image-current-filogic_duplicate_arm64.deb").unlink()
    put(image_deb, image_deb.read_bytes()[:80])
    run_case("truncated deb", deb_args, False, "invalid deb ar member", work)
    run_case("missing real-artifact input", ["--extracted-rootfs", work / "nonexistent"], False, "No such file", work)
    print("PASS: %d synthetic fixture tests; verifier left fixture contents unchanged." % count)
    print("NO real Armbian artifacts, kernel compilation, initrd boot, GPT, U-Boot or hardware were validated.")
PY
