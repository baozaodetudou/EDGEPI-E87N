#!/usr/bin/env python3
"""Offline conversion of an audited Armbian GPT image to E87N U-Boot firmware.

Linux/root only. Does NOT connect to a board, flash a disk or alter the input.
Only new private scratch files are writable; retained for troubleshooting.
"""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True
from factory_firmware import (FORMAT, KERNEL_LIMIT, LAYOUT, LOADS, MIB, RELEASE,
                              RESERVATIONS, UPLOAD_LIMIT, PREFIX, bootargs, check_fit,
                              ext4_size, mounted, regular, require, run, sha)

SCRIPTS = Path(__file__).resolve().parent


def command(*args):
    return run(*args, capture_output=True, text=True).stdout.strip()


def filesystem_manifest(root):
    """Compare every copied inode's data, ownership, permissions, links and xattrs."""
    root_stat = root.stat()
    result = {".": [root_stat.st_mode, root_stat.st_uid, root_stat.st_gid,
                    {key: os.getxattr(root, key).hex() for key in os.listxattr(root)}]}
    links = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            require(not stat.S_ISSOCK(info.st_mode), "unexpected socket in offline rootfs")
            attributes = {key: os.getxattr(path, key, follow_symlinks=False).hex()
                          for key in os.listxattr(path, follow_symlinks=False)}
            entry = [info.st_mode, info.st_uid, info.st_gid, attributes]
            if stat.S_ISLNK(info.st_mode):
                entry += [os.readlink(path)]
            elif stat.S_ISREG(info.st_mode):
                entry += [info.st_size, sha(path)]
                if info.st_nlink > 1:
                    links.setdefault((info.st_dev, info.st_ino), []).append(relative)
            elif not stat.S_ISDIR(info.st_mode):
                entry += [info.st_rdev]
            result[relative] = entry
    return result, sorted(sorted(group) for group in links.values())


def compact_rootfs(root_image, work, root_uuid):
    # The original ext4's resize2fs minimum can far exceed its occupied blocks.
    # Re-layout a new bounded filesystem instead of dropping modules/packages.
    # Reserve a full 32 MiB kernel slot and 1 MiB container allowance in RAM.
    size = UPLOAD_LIMIT - KERNEL_LIMIT - MIB
    fresh = work / "root-compact.img"
    with fresh.open("xb") as stream:
        stream.truncate(size)
    run("mkfs.ext4", "-q", "-F", "-b", "4096", "-m", "0", "-U", root_uuid,
        "-L", "armbi_root", fresh)
    with mounted(root_image, work / "copy-source") as source:
        before = filesystem_manifest(source)
        with mounted(fresh, work / "copy-target", readonly=False) as target:
            run("cp", "-a", "--preserve=all", str(source) + "/.", target)
            require(filesystem_manifest(target) == before,
                    "repacked rootfs differs in content/ownership/mode/hardlinks/xattrs")
    run("e2fsck", "-fn", fresh)
    require(fresh.stat().st_size == ext4_size(fresh), "fresh ext4 boundary mismatch")
    root_image.rename(work / "prepared-root-copy.img")
    fresh.rename(root_image)
    print("PASS: bounded ext4 rebuilt with identical files, ownership, permissions, hardlinks and xattrs")


def patch_boot_dtb(path, root_uuid):
    # Applies the same board-specific RAM correction as patch 902. This also
    # permits auditing a pre-902 candidate without rebuilding its kernel.
    run("fdtput", "-t", "x", path, "/memory", "reg", "0", "40000000", "0", "40000000")
    for name, (address, size) in RESERVATIONS.items():
        node = "/reserved-memory/" + name
        existing = command("fdtget", "-l", path, "/reserved-memory").splitlines()
        if name not in existing:
            run("fdtput", "-c", path, node)
        run("fdtput", "-t", "x", path, node, "reg", "0", f"{address:x}", "0", f"{size:x}")
        if name != "ramoops@7ff70000":
            run("fdtput", path, node, "no-map")
        else:
            run("fdtput", "-t", "s", path, node, "compatible", "ramoops")
            run("fdtput", "-t", "x", path, node, "record-size", "10000")
    run("fdtput", "-t", "s", path, "/chosen", "bootargs", bootargs(root_uuid))


def make_fit(boot, work, root_uuid):
    kernel = regular(boot / ("vmlinuz-" + RELEASE), 32 * MIB).read_bytes()
    require(kernel[56:60] == b"ARM\x64", "expected an uncompressed ARM64 Image")
    raw_initrd = regular(boot / ("initrd.img-" + RELEASE), 30 * MIB)
    dtb = regular(boot / ("dtb-" + RELEASE) / "mediatek/mt7987a-edgepi-e87n.dtb", MIB)
    patch_boot_dtb(dtb, root_uuid)
    # Match the encoder properties of the installed OpenWrt FIT. A concrete
    # uncompressed length is required by the vendor's LZMA loader.
    packed = lzma.compress(kernel, format=lzma.FORMAT_ALONE, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": 8 * MIB, "lc": 1, "lp": 2, "pb": 2}])
    packed = packed[:5] + len(kernel).to_bytes(8, "little") + packed[13:]
    (work / "Image.lzma").write_bytes(packed)
    shutil.copyfile(raw_initrd, work / "initrd")
    shutil.copyfile(dtb, work / "board.dtb")
    images = []
    for name, filename, kind, compression in (("kernel", "Image.lzma", "kernel", "lzma"),
                                               ("ramdisk", "initrd", "ramdisk", "none"),
                                               ("fdt", "board.dtb", "flat_dt", "none")):
        os_prop = 'os = "linux";' if name != "fdt" else ""
        entry = f"entry = <0x{LOADS[name]:x}>;" if name == "kernel" else ""
        images.append(f'''{name}-1 {{
            description = "E87N {RELEASE} {name}";
            data = /incbin/("{filename}"); type = "{kind}"; arch = "arm64";
            compression = "{compression}"; {os_prop}
            load = <0x{LOADS[name]:x}>; {entry}
            hash-1 {{ algo = "sha256"; }};
        }};''')
    (work / "firmware.its").write_text('''/dts-v1/;
/ { description = "E87N Debian 13 factory-layout FIT"; #address-cells = <1>;
    images { ''' + "\n".join(images) + ''' };
    configurations { default = "conf-1";
        conf-1 { description = "EdgePi E87N 1 GiB eMMC";
            kernel = "kernel-1"; ramdisk = "ramdisk-1"; fdt = "fdt-1";
        };
    };
};
''')
    run("mkimage", "-f", "firmware.its", "kernel", cwd=work)
    fit = work / "kernel"
    with fit.open("ab") as stream:
        stream.write(bytes((-fit.stat().st_size) % 512))
    require(fit.stat().st_size <= KERNEL_LIMIT, "FIT exceeds original 32 MiB kernel partition")
    check_fit(fit.read_bytes(), root_uuid)
    return fit


def build(image, output):
    require(sys.platform == "linux" and os.geteuid() == 0, "Linux root host required")
    image = regular(image)
    require(image.suffix == ".img", "input must be an uncompressed audited .img")
    output = output.absolute()
    require(output.name.endswith("-uboot-firmware.tar"), "output must end -uboot-firmware.tar")
    require(not os.path.lexists(output), "refusing to overwrite output")
    output.parent.mkdir(parents=True, exist_ok=True)
    before = sha(image)
    table = json.loads(command("sfdisk", "--json", image))["partitiontable"]
    parts = table["partitions"]
    require(table["label"] == "gpt" and table["sectorsize"] == 512 and len(parts) == 2,
            "expected source GPT with two partitions")
    boot, root = parts
    require((boot["start"], boot["size"], root["start"]) == (32768, 524288, 557056),
            "unexpected source partition layout")
    require(root["size"] > 0 and (root["start"] + root["size"]) * 512 <= image.stat().st_size,
            "root partition outside source image")
    work = Path(tempfile.mkdtemp(prefix=".factory-build.", dir=output.parent))
    print("Private scratch retained:", work, flush=True)
    root_image = work / "root"
    with image.open("rb") as source, root_image.open("xb") as dest:
        source.seek(root["start"] * 512)
        remaining = root["size"] * 512
        while remaining:
            chunk = source.read(min(4 * MIB, remaining))
            require(chunk, "truncated source rootfs")
            dest.write(chunk)
            remaining -= len(chunk)
    run("e2fsck", "-fn", root_image)
    root_uuid = command("blkid", "-p", "-s", "UUID", "-o", "value", root_image)
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", root_uuid), "invalid root UUID")
    with mounted(image, work / "source-boot", offset=boot["start"] * 512,
                 size=boot["size"] * 512) as boot_dir:
        with mounted(root_image, work / "root-mount", readonly=False) as root_dir:
            run(sys.executable, SCRIPTS / "prepare-factory-rootfs.py", "--root", root_dir,
                "--boot", boot_dir, "--uuid", root_uuid)
            fit = make_fit(root_dir / "boot", work, root_uuid)
            run(sys.executable, SCRIPTS / "verify-system.py", "--rootfs", root_dir,
                "--bootfs", root_dir / "boot")
            run(sys.executable, SCRIPTS / "verify-display-fan.py", "--rootfs", root_dir,
                "--config", root_dir / "boot" / ("config-" + RELEASE),
                "--dtb", work / "board.dtb")
    run("e2fsck", "-fn", root_image)
    compact_rootfs(root_image, work, root_uuid)
    manifest = {"format": FORMAT, "kernel_release": RELEASE, "debian": "13",
                "layout": LAYOUT, "root_uuid": root_uuid, "source_image_sha256": before,
                "hardware_validation": "pending", "upload_limit_policy_bytes": UPLOAD_LIMIT,
                "payloads": {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
                             for p in (fit, root_image)}}
    control = work / "CONTROL"
    control.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    candidate = work / "firmware.tar"
    with tarfile.open(candidate, "w:", format=tarfile.USTAR_FORMAT) as archive:
        for path in (fit, root_image, control):
            info = tarfile.TarInfo(PREFIX + path.name)
            info.size = path.stat().st_size
            info.mode = 0o644
            with path.open("rb") as payload:
                archive.addfile(info, payload)
    require(candidate.stat().st_size <= UPLOAD_LIMIT, "firmware exceeds conservative upload RAM policy")
    require(sha(image) == before, "input image changed during conversion")
    # Full independent audit before exposing a completed product to CI.
    run(sys.executable, SCRIPTS / "verify-factory-firmware.py", candidate)
    os.link(candidate, output)  # Same-filesystem atomic no-overwrite publication.
    output.chmod(0o644)
    print("PASS: factory-layout firmware produced (static only):", output)
    print("SHA256:", sha(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.image, args.output)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, "FAIL: " + str(error) + "\n")


if __name__ == "__main__":
    main()
