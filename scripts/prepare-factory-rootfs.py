#!/usr/bin/env python3
"""Adapt an idle offline loop-mounted E87N rootfs; never chroot or access hardware.

--verify and check_offline(root, boot, uuid) are read-only integrity gates.
Adaptation requires Linux/root, a dedicated rw ext4 loop mount, and lsinitramfs.
No mount, unmount, partition, filesystem resize, package or target commands run.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ASSETS = Path(__file__).resolve().parents[1] / "board-support/factory-boot"
FILES = {
    "usr/lib/e87n/factory-boot.py": ("factory_boot.py", 0o644),
    "usr/lib/systemd/system/e87n-factory-mac.service": ("e87n-factory-mac.service", 0o644),
    "usr/lib/systemd/system/e87n-factory-resize.service": ("e87n-factory-resize.service", 0o644),
    "etc/systemd/system/systemd-networkd.service.d/20-e87n-factory-mac.conf":
        ("20-e87n-factory-mac.conf", 0o644),
    "usr/share/doc/e87n/factory-boot.md": ("README.md", 0o644),
}
LINKS = {
    "etc/systemd/system/armbian-resize-filesystem.service": "/dev/null",
    "etc/systemd/system/multi-user.target.wants/e87n-factory-resize.service":
        "/usr/lib/systemd/system/e87n-factory-resize.service",
}
SKIP = "root/.no_rootfs_resize"
FORBIDDEN = ("usr/share/initramfs-tools/hooks/growroot",
             "usr/share/initramfs-tools/scripts/local-bottom/growroot",
             "etc/initramfs-tools/scripts/local-bottom/growroot")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def directory(value):
    path = Path(os.path.abspath(value))
    require(path != Path("/") and path.is_dir(), "refusing / or missing directory")
    require(path.resolve(strict=True) == path and not path.is_symlink(), "symlink directory/ancestor refused")
    require(not os.path.samefile(path, "/"), "refusing a bind alias of the live root")
    return path


def target(root, relative):
    parts = Path(relative).parts
    require(parts and not Path(relative).is_absolute() and ".." not in parts, "unsafe relative target")
    cursor = root
    for part in parts[:-1]:
        cursor /= part
        require(not cursor.is_symlink(), f"redirected target ancestor: {cursor}")
        require(not cursor.exists() or cursor.is_dir(), f"non-directory ancestor: {cursor}")
    return root / relative


def regular(path):
    require(not path.is_symlink() and path.is_file(), f"need regular non-symlink file: {path}")
    return path.read_bytes()


def fstab(contents, uuid):
    require(re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", uuid), "invalid UUID")
    output, roots, boots = [], 0, 0
    for line in contents.splitlines(keepends=True):
        fields = line.split("#", 1)[0].split()
        if not fields:
            output.append(line)
            continue
        require(len(fields) == 6, "unexpected fstab record")
        if fields[1] == "/":
            roots += 1
            require(fields[0].lower() == "uuid=" + uuid.lower() and fields[2] == "ext4",
                    "fstab root UUID/type differs; refusing to replace filesystem identity")
        require(not fields[1].startswith("/boot/"), "unexpected nested boot mount")
        if fields[1] == "/boot":
            boots += 1
        else:
            output.append(line)
    require(roots == 1 and boots <= 1, "ambiguous root/boot fstab")
    return "".join(output)


def holds(root):
    data = regular(target(root, "var/lib/dpkg/status")).decode()
    statuses = {}
    for paragraph in data.split("\n\n"):
        fields = dict(re.findall(r"^(Package|Status): (.+)$", paragraph, re.M))
        if "Package" in fields:
            require(fields["Package"] not in statuses, "duplicate dpkg package record")
            statuses[fields["Package"]] = fields.get("Status")
    for package in ("linux-image-current-filogic", "linux-dtb-current-filogic"):
        require(statuses.get(package) == "hold ok installed", f"missing existing package hold: {package}")
    require(not statuses.get("cloud-initramfs-growroot", "").endswith("ok installed"),
            "remove cloud-initramfs-growroot and regenerate initrd before adaptation")
    for name in FORBIDDEN:
        require(not os.path.lexists(target(root, name)), f"unexpected partition growth hook: {name}")


def tree(path, exclude_lost=True):
    """No symlink traversal, special files, or nested mounts; links remain links."""
    result = {}
    if not path.exists():
        return result
    require(path.is_dir() and not path.is_symlink(), f"unsafe tree root: {path}")
    def visit(directory, prefix=""):
        for item in sorted(directory.iterdir()):
            name = prefix + item.name
            if not prefix and exclude_lost and item.name == "lost+found":
                continue
            info = item.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                result[name] = ("link", os.readlink(item))
            elif stat.S_ISDIR(info.st_mode):
                require(not os.path.ismount(item), f"nested mount refused: {item}")
                result[name] = ("dir", mode)
                visit(item, name + "/")
            else:
                require(stat.S_ISREG(info.st_mode), f"special file refused: {item}")
                with item.open("rb") as stream:
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(chunk)
                    digest = hasher.hexdigest()
                result[name] = ("file", mode, info.st_size, digest)
    visit(path)
    return result


def cache_tree(root):
    path = target(root, "var/lib/apt/lists")
    entries = tree(path, exclude_lost=False)
    require(all(item[0] != "link" for item in entries.values()), "symlink in APT lists refused")
    return path, entries


def disabled_links(root):
    result = []
    for directory_name in ("etc/systemd/system", "usr/lib/systemd/system"):
        directory_path = target(root, directory_name)
        if not directory_path.exists():
            continue
        for folder in directory_path.iterdir():
            if folder.name.endswith((".wants", ".requires")):
                require(not folder.is_symlink(), f"redirected unit dependency directory: {folder}")
                path = folder / "armbian-resize-filesystem.service"
                if os.path.lexists(path):
                    require(path.is_symlink() and Path(os.readlink(path)).name == path.name,
                            f"unexpected generic resize dependency: {path}")
                    result.append(path)
    return result


def preflight(root, boot, uuid):
    root, boot = directory(root), directory(boot)
    require(root not in boot.parents and boot not in root.parents and root != boot,
            "root and source bootfs must be disjoint")
    original = regular(target(root, "etc/fstab")).decode()
    converted = fstab(original, uuid)
    holds(root)
    source = tree(boot)
    require(source and any(name.startswith("initrd.img-") for name in source), "source bootfs lacks raw initrd")
    destination = target(root, "boot")
    require(not destination.is_symlink() and not os.path.ismount(destination), "destination boot is linked/mounted")
    existing = tree(destination)
    require(not existing or existing == source, "existing /boot differs; refusing unexpected state")
    for name, (asset, mode) in FILES.items():
        expected = regular(ASSETS / asset)
        path = target(root, name)
        if os.path.lexists(path):
            require(regular(path) == expected and stat.S_IMODE(path.stat().st_mode) == mode,
                    f"conflicting managed file: {name}")
    for name, value in LINKS.items():
        path = target(root, name)
        require(not os.path.lexists(path) or path.is_symlink() and os.readlink(path) == value,
                f"conflicting unit link/mask: {name}")
    skip = target(root, SKIP)
    require(not os.path.lexists(skip) or regular(skip) == b"", "unexpected resize skip marker")
    disabled_links(root)
    cache_tree(root)
    return root, boot, converted, source


def atomic_file(root, name, data, mode):
    path = target(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
        return
    require(not path.is_symlink(), f"refusing symlink file: {path}")
    fd, temporary = tempfile.mkstemp(prefix=".e87n-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            os.fchmod(output.fileno(), mode)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def adapt(root, boot, uuid):
    root, boot, converted, source = preflight(root, boot, uuid)
    destination = target(root, "boot")
    if tree(destination) != source:
        staging = Path(tempfile.mkdtemp(prefix=".e87n-boot-", dir=root))
        try:
            shutil.copytree(boot, staging, symlinks=True, dirs_exist_ok=True,
                            ignore=lambda path, names: ["lost+found"] if Path(path) == boot else [])
            require(tree(staging) == source, "bootfs changed during copy")
            if destination.exists():
                # Only an empty directory was admitted by preflight.
                destination.rmdir()
            staging.rename(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    mode = stat.S_IMODE(target(root, "etc/fstab").stat().st_mode)
    atomic_file(root, "etc/fstab", converted.encode(), mode)
    for name, (asset, mode) in FILES.items():
        atomic_file(root, name, regular(ASSETS / asset), mode)
    for name, value in LINKS.items():
        path = target(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not os.path.lexists(path):
            path.symlink_to(value)
    atomic_file(root, SKIP, b"", 0o644)
    for path in disabled_links(root):
        path.unlink()
    cache, entries = cache_tree(root)
    # Retain empty directory ownership/modes (notably _apt's partial/).
    for name, item in entries.items():
        if item[0] == "file":
            (cache / name).unlink()
    return check_offline(root, boot, uuid)


def check_offline(root, boot, uuid):
    """Read-only prepared-root integrity gate; never executes any target program."""
    root, boot = directory(root), directory(boot)
    original = regular(target(root, "etc/fstab")).decode()
    require(fstab(original, uuid) == original, "old /boot mount remains")
    holds(root)
    for name, (asset, mode) in FILES.items():
        path = target(root, name)
        require(regular(path) == regular(ASSETS / asset) and stat.S_IMODE(path.stat().st_mode) == mode,
                f"helper/service/policy integrity failed: {name}")
    for name, value in LINKS.items():
        path = target(root, name)
        require(path.is_symlink() and os.readlink(path) == value, f"missing/wrong unit linkage: {name}")
    require(regular(target(root, SKIP)) == b"", "missing/invalid resize skip marker")
    require(not disabled_links(root), "generic partition expansion is still enabled")
    require(not any(item[0] == "file" for item in cache_tree(root)[1].values()), "APT lists are not empty")
    require(tree(target(root, "boot")) == tree(boot) and tree(boot), "copied bootfs integrity failed")
    return {"root_uuid": uuid.lower(), "helper": "/usr/lib/e87n/factory-boot.py",
            "boot_files": len(tree(boot)), "status": "prepared; static verification only"}


def verify_mount(root, uuid):
    require(sys.platform == "linux" and os.geteuid() == 0, "adaptation requires Linux root")
    spec = importlib.util.spec_from_file_location("e87n_factory_runtime", ASSETS / "factory_boot.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    mounts = runtime.parse_mountinfo(Path("/proc/self/mountinfo").read_text())
    matched = [entry for entry in mounts if entry["mount"] == str(root)]
    require(len(matched) == 1, "root must be a dedicated offline mount")
    entry = matched[0]
    require(entry["root"] == "/" and entry["type"] == "ext4" and "rw" in entry["options"] and
            re.fullmatch(r"/dev/loop\d+(?:p\d+)?", entry["source"]),
            "root must be a writable ext4 loop mount, never a physical device or bind subtree")
    require(not any(Path(item["mount"]) != root and root in Path(item["mount"]).parents for item in mounts),
            "nested mounts below root refused")
    tool = shutil.which("blkid")
    require(tool, "host blkid is required")
    actual = subprocess.run([tool, "-p", "-s", "UUID", "-o", "value", entry["source"]],
                            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    require(actual.lower() == uuid.lower(), "actual ext4 UUID differs from --uuid")


def verify_initrds(boot):
    tool = shutil.which("lsinitramfs")
    require(tool, "host lsinitramfs is required; do not execute target initrd")
    for path in boot.glob("initrd.img-*"):
        regular(path)
        listing = subprocess.run([tool, str(path)], capture_output=True, text=True,
                                 check=True, timeout=60).stdout.splitlines()
        require(any(name.lstrip("./") == "init" for name in listing), "initrd lacks /init")
        require(not any(Path(name).name in {"growroot", "growpart", "resize2fs"} for name in listing),
                "initrd includes growroot/growpart/resize2fs; rebuild a reviewed initrd before adaptation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--boot", required=True, type=Path)
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--verify", action="store_true", help="read-only prepared-root integrity check")
    args = parser.parse_args()
    try:
        if args.verify:
            result = check_offline(args.root, args.boot, args.uuid)
        else:
            root, boot, _, _ = preflight(args.root, args.boot, args.uuid)
            verify_mount(root, args.uuid)
            verify_initrds(boot)
            result = adapt(root, boot, args.uuid)
        print("PASS:", result)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
