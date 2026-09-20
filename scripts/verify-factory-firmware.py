#!/usr/bin/env python3
"""Read-only audit of the final E87N firmware TAR. Never connects or flashes.

PASS checks software structure, contents and checksums, NOT physical boot.
Requires Linux/root for new read-only loop mounts; scratch is retained.
"""
import argparse
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from factory_firmware import (RELEASE, audit_initrd_listing, audit_initrd_modules, collect_evidence, verify_module_indexes,
                              check_vendor_parser, inspect_tar, mounted, regular, require, run)

SCRIPTS = Path(__file__).resolve().parent


def verify(path, headless=False):
    require(sys.platform == "linux" and os.geteuid() == 0, "Linux root host required")
    path = regular(path)
    # Debian can mount /tmp as RAM-backed tmpfs. A firmware audit expands a
    # large ext4 payload; prefer the CI disk scratch or persistent /var/tmp.
    scratch = Path(os.environ.get("RUNNER_TEMP", "/var/tmp"))
    require(shutil.disk_usage(scratch).free >= path.stat().st_size + 64 * 1024 * 1024,
            "insufficient audit scratch space; set RUNNER_TEMP to a disk directory")
    work = Path(tempfile.mkdtemp(prefix="e87n-factory-audit.", dir=scratch))
    print("Read-only firmware audit scratch retained:", work, flush=True)
    paths, control, payloads = inspect_tar(path, work)
    require(control.get("headless", False) == headless,
            "firmware profile mismatch: use --headless only for a headless firmware")
    check_vendor_parser(path, work)
    run("e2fsck", "-fn", paths["root"])
    uuid = run("blkid", "-p", "-s", "UUID", "-o", "value", paths["root"],
               capture_output=True, text=True).stdout.strip()
    require(uuid == control["root_uuid"], "FIT/manifest/ext4 UUID mismatch")
    with mounted(paths["root"], work / "root-mount") as root:
        boot = root / "boot"
        run(sys.executable, SCRIPTS / "prepare-factory-rootfs.py", "--verify",
            "--root", root, "--boot", boot, "--uuid", uuid)
        fstab = (root / "etc/fstab").read_text().splitlines()
        entries = [s.split() for s in fstab if s.strip() and not s.lstrip().startswith("#")]
        require([s[0] for s in entries if s[1] == "/"] == ["UUID=" + uuid], "root fstab mismatch")
        require(not any(s[1] == "/boot" for s in entries), "obsolete separate /boot mount")
        require((root / "root/.no_rootfs_resize").is_file(), "generic resize skip marker absent")
        mask = root / "etc/systemd/system/armbian-resize-filesystem.service"
        require(mask.is_symlink() and os.readlink(mask) == "/dev/null", "generic GPT resize not masked")
        require((root / "usr/lib/e87n/factory-boot.py").is_file(), "factory boot helper absent")
        dtb = boot / ("dtb-" + RELEASE) / "mediatek/mt7987a-edgepi-e87n.dtb"
        require(dtb.read_bytes() == payloads["fdt"], "FIT vs installed DTB differs")
        require((boot / ("vmlinuz-" + RELEASE)).read_bytes() == lzma.decompress(payloads["kernel"]),
                "FIT vs installed kernel differs")
        require((boot / ("initrd.img-" + RELEASE)).read_bytes() == payloads["ramdisk"],
                "FIT vs installed initrd differs")
        evidence, dependencies = collect_evidence(root)
        require(evidence == control["evidence"], "final rootfs evidence differs from CONTROL")
        verify_module_indexes(root, dependencies)
        listing = run("lsinitramfs", boot / ("initrd.img-" + RELEASE),
                      capture_output=True, text=True).stdout
        require(not re.search(r"(^|/)(growroot|growpart|resize2fs)(\s|$)", listing, re.M),
                "initramfs contains uncontrolled expansion helper")
        audit_initrd_listing(listing, dependencies)
        audit_initrd_modules(root, boot / ("initrd.img-" + RELEASE), dependencies)
        if headless:
            require("etc/modprobe.d/e87n-headless.conf" in listing.splitlines(),
                    "headless initramfs lacks the NV3007 automatic-probe blacklist")
        profile_args = ["--headless"] if headless else []
        run(sys.executable, SCRIPTS / "verify-system.py", "--rootfs", root, "--bootfs", boot, *profile_args)
        if not headless:
            run(sys.executable, SCRIPTS / "verify-display-fan.py", "--rootfs", root,
                "--config", boot / ("config-" + RELEASE), "--dtb", dtb)
        run("bash", SCRIPTS / "verify-lts-platform.sh", "--config", boot / ("config-" + RELEASE), "--dtb", dtb)
    print("PASS: E87N firmware TAR, FIT hashes/load bounds, 1 GiB DTB, ext4 UUID, panic=0 bring-up mode,")
    print("      observed Debian/packages, compiled recipe, module ABI/dependencies/indexes and PHY firmware bound to build ID:", control["build_id"])
    print("      %s verified. Hardware boot and recovery test still pending." %
          ("headless system and thermal/fan configuration" if headless else "minimal system/display/fan files"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firmware", type=Path)
    parser.add_argument("--headless", action="store_true",
                        help="require the headless profile recorded in CONTROL")
    args = parser.parse_args()
    try:
        verify(args.firmware, args.headless)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, "FAIL: " + str(error) + "\n")


if __name__ == "__main__":
    main()
