#!/usr/bin/env python3
"""Build a small, network-first E87N RAM diagnostic initrd.

The target root filesystem is read-only input.  The generated initrd contains
only the userspace needed to prove that Linux reached PID 1, the direct wired
port came up, eMMC stayed absent, and an SSH listener can run entirely from
RAM.  It never mounts, opens, or writes a block device.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


RELEASE = "6.18.51-current-filogic"
ASSETS = Path(__file__).resolve().parent / "assets"
MAX_INITRD = 256 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def run(*args: object, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def regular(path: Path) -> Path:
    path = Path(path)
    info = path.stat()
    require(not path.is_symlink() and stat.S_ISREG(info.st_mode) and info.st_size > 0,
            f"need a non-empty regular file: {path}")
    return path.resolve()


def safe_target(root: Path, relative: str) -> Path:
    relative = relative.lstrip("/")
    require(".." not in Path(relative).parts, f"path traversal: {relative}")
    path = (root / relative).resolve(strict=True)
    require(path == root or path.is_relative_to(root), f"target path escapes root: {relative}")
    return path


class InitrdTree:
    def __init__(self, source: Path, destination: Path):
        self.source = source.resolve(strict=True)
        self.destination = destination.resolve()
        require(self.source.is_dir() and self.destination != self.source,
                "source and initrd destination must be distinct directories")
        self.destination.mkdir(parents=True, exist_ok=False)
        self.copied: set[str] = set()
        self.elfs: set[str] = set()

    def dest(self, relative: str) -> Path:
        path = self.destination / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def copy_file(self, relative: str, *, required: bool = True) -> None:
        if relative in self.copied:
            return
        source = self.source / relative.lstrip("/")
        if not source.exists() and not source.is_symlink():
            if required:
                raise ValueError(f"missing target file: /{relative.lstrip('/')}")
            return
        actual = source.resolve(strict=True)
        require(actual.is_relative_to(self.source) and actual.is_file(),
                f"target file escapes root or is not regular: /{relative.lstrip('/')}")
        target = self.dest(relative)
        shutil.copy2(actual, target)
        self.copied.add(relative)
        if actual.read_bytes()[:4] == b"\x7fELF":
            self.elfs.add(relative)

    def copy_tree(self, relative: str, *, required: bool = True) -> None:
        source = self.source / relative.lstrip("/")
        if not source.exists() and not source.is_symlink():
            if required:
                raise ValueError(f"missing target directory: /{relative.lstrip('/')}")
            return
        actual = source.resolve(strict=True)
        require(actual.is_relative_to(self.source) and actual.is_dir(),
                f"target directory escapes root or is not a directory: /{relative.lstrip('/')}")
        destination = self.dest(relative)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(actual, destination, symlinks=True)

    def write(self, relative: str, content: bytes, mode: int = 0o644) -> None:
        target = self.dest(relative)
        target.write_bytes(content)
        target.chmod(mode)

    def mkdir(self, relative: str, mode: int = 0o755) -> None:
        target = self.destination / relative.lstrip("/")
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(mode)

    def char_device(self, relative: str, mode: int, major: int, minor: int) -> None:
        """Create a minimal initrd device node needed by chroot-time tools.

        Runtime boot mounts devtmpfs over ``/dev``.  These nodes are still
        required while assembling and auditing the initrd: OpenSSH and a few
        libc paths open ``/dev/null`` even for a configuration-only check.
        Keeping the standard nodes in the cpio also makes the initrd usable
        for the short interval before devtmpfs is mounted.
        """
        target = self.destination / relative.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        os.mknod(target, stat.S_IFCHR | mode, os.makedev(major, minor))


def parse_ldd(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        for candidate in re.findall(r"(?:=>\s+|^\s*)(/[^\s]+)", line):
            if candidate not in paths:
                paths.append(candidate)
    return paths


def resolve_command(root: Path, *candidates: str) -> str:
    for candidate in candidates:
        path = root / candidate.lstrip("/")
        if path.exists() or path.is_symlink():
            return candidate
    raise ValueError("none of the target commands exist: " + ", ".join(candidates))


def password_hash(password: str) -> str:
    """Return a SHA-512 crypt hash without making Python's ``crypt`` mandatory.

    Python 3.13 removed the stdlib ``crypt`` module.  The image builder still
    needs to work on a newer host, so use it when available and fall back to
    the host's OpenSSL command without putting the password in argv.
    """
    encoded = ""
    try:
        import crypt as crypt_module  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError):
        pass
    else:
        encoded = crypt_module.crypt(password, crypt_module.mksalt(crypt_module.METHOD_SHA512))
    # Some platforms expose ``crypt`` but only implement legacy DES; do not
    # silently place that weak 13-character hash in the diagnostic image.
    if not encoded.startswith("$6$"):
        try:
            result = subprocess.run(
                ["openssl", "passwd", "-6", "-stdin"],
                input=(password + "\n").encode(),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ValueError("need SHA-512 crypt support or host openssl for the diagnostic password hash") from error
        encoded = result.stdout.decode("ascii", errors="strict").strip()
    require(encoded.startswith("$6$") and len(encoded) > 20,
            "failed to create a SHA-512 crypt password hash")
    return encoded


def add_runtime_dependencies(tree: InitrdTree, root: Path) -> None:
    # ldd must execute inside the native ARM64 target root.  The image job is
    # deliberately native ARM64, so this does not need a host emulator.
    copy_queue = sorted(tree.elfs)
    processed: set[str] = set()
    while copy_queue:
        relative = copy_queue.pop(0)
        if relative in processed:
            continue
        processed.add(relative)
        result = subprocess.run(
            ["chroot", str(root), "/usr/bin/ldd", "/" + relative],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output = result.stdout + result.stderr
        # ldd returns non-zero for static ELF files.  That is a valid result:
        # there are no shared libraries to copy for such a binary.
        if "not a dynamic executable" in output or "statically linked" in output:
            continue
        require(result.returncode == 0, f"ldd failed for /{relative}: {output.strip()}")
        for dependency in parse_ldd(output):
            dependency_relative = dependency.lstrip("/")
            before = len(tree.copied)
            tree.copy_file(dependency_relative)
            if len(tree.copied) != before and dependency_relative in tree.elfs:
                copy_queue.append(dependency_relative)


def write_system_files(tree: InitrdTree) -> None:
    tree.write("etc/passwd", b"root:x:0:0:root:/root:/bin/sh\n")
    password = password_hash("doumao")
    tree.write("etc/shadow", f"root:{password}:20000:0:99999:7:::\n".encode(), 0o600)
    tree.write("etc/group", b"root:x:0:\n")
    tree.write("etc/nsswitch.conf", b"passwd: files\ngroup: files\nshadow: files\nhosts: files\n")
    tree.write("etc/hostname", b"e87n-ramdiag\n")
    tree.write("etc/hosts", b"127.0.0.1 localhost\n192.168.1.1 e87n-ramdiag\n")
    tree.write("etc/fstab", b"# RAM diagnostic: deliberately no block-device mounts.\n")
    tree.write("etc/ssh/sshd_config", (ASSETS / "sshd_config").read_bytes())


def build(source_root: Path, boot: Path, output: Path) -> dict[str, object]:
    require(os.geteuid() == 0, "initrd assembly requires root for a faithful chroot/device environment")
    source_root = source_root.resolve(strict=True)
    boot = boot.resolve(strict=True)
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("init", "services", "beacon.py", "stop-watchdog.py", "sshd_config"):
        require((ASSETS / name).is_file(), f"missing diagnostic asset: {name}")

    with tempfile.TemporaryDirectory(prefix="e87n-ramdiag-initrd-", dir=output) as temporary:
        work = Path(temporary)
        tree = InitrdTree(source_root, work / "root")
        commands = (
            "/bin/sh", "/usr/bin/mount", "/bin/mount", "/usr/bin/sleep",
            "/bin/sleep", "/usr/bin/mkdir", "/bin/mkdir", "/usr/bin/chmod",
            "/bin/chmod", "/usr/bin/ln", "/bin/ln", "/usr/bin/hostname",
            "/bin/hostname", "/usr/bin/readlink", "/bin/readlink", "/usr/bin/tr",
            "/bin/tr", "/usr/bin/ssh-keygen", "/usr/bin/python3", "/usr/bin/true",
            "/bin/true", "/usr/bin/false", "/bin/false", "/usr/bin/kill", "/bin/kill",
            "/usr/sbin/sshd", "/usr/bin/sshd", "/usr/sbin/modprobe", "/usr/bin/modprobe",
            "/usr/bin/dmesg", "/bin/dmesg",
        )
        selected: set[str] = set()
        for command in commands:
            # Several entries are merged-/usr aliases.  Copy the first existing
            # spelling only; duplicate copies inflate the initrd and can mask
            # non-merged-/usr target layouts.
            basename = Path(command).name
            if basename in selected:
                continue
            if (source_root / command.lstrip("/")).exists() or (source_root / command.lstrip("/")).is_symlink():
                tree.copy_file(command.lstrip("/"), required=True)
                selected.add(basename)
        command_paths = {
            "sshd": ("usr/sbin/sshd", "usr/bin/sshd", "sbin/sshd", "bin/sshd"),
            "ip": ("usr/sbin/ip", "usr/bin/ip", "sbin/ip", "bin/ip"),
            "modprobe": ("usr/sbin/modprobe", "usr/bin/modprobe", "sbin/modprobe", "bin/modprobe"),
        }
        for name, candidates in command_paths.items():
            command = resolve_command(source_root, *candidates)
            tree.copy_file(command.lstrip("/"), required=True)
            selected.add(name)
        tree.copy_file("usr/lib/openssh/sshd-session", required=True)
        # OpenSSH releases differ: some ship sshd-auth, while newer releases
        # use sshd-session for the complete server-side session path.
        tree.copy_file("usr/lib/openssh/sshd-auth", required=False)
        tree.copy_file("usr/bin/dmesg", required=False)
        require("usr/bin/python3" in tree.copied, "target python3 is required for the UDP beacon")
        sshd_command = command_paths["sshd"]
        sshd_path = resolve_command(source_root, *sshd_command)
        require(sshd_path.lstrip("/") in tree.copied,
                "target openssh-server is required for the diagnostic")
        tree.copy_tree(f"usr/lib/modules/{RELEASE}")
        tree.copy_tree("usr/lib/firmware", required=False)
        python_dirs = sorted((source_root / "usr/lib").glob("python3.*"))
        require(python_dirs, "target Python standard library is missing")
        for directory in python_dirs:
            tree.copy_tree(str(directory.relative_to(source_root)))
        tree.destination.joinpath("lib").symlink_to("usr/lib")
        tree.destination.joinpath("lib64").symlink_to("usr/lib64" if (source_root / "usr/lib64").exists() else "usr/lib")
        for directory, mode in (("proc", 0o555), ("sys", 0o555), ("dev", 0o755),
                                ("dev/pts", 0o755),
                                ("run", 0o755), ("tmp", 0o1777), ("root", 0o700),
                                ("var/empty", 0o755), ("run/ssh", 0o700), ("run/sshd", 0o755),
                                ("run/ramdiag", 0o755)):
            tree.mkdir(directory, mode)
        # Linux standard device numbers.  These are hidden by devtmpfs at
        # runtime but must exist for the chrooted sshd/configuration checks.
        for name, mode, major, minor in (
            ("dev/null", 0o666, 1, 3),
            ("dev/zero", 0o666, 1, 5),
            ("dev/random", 0o666, 1, 8),
            ("dev/urandom", 0o666, 1, 9),
            ("dev/console", 0o600, 5, 1),
        ):
            tree.char_device(name, mode, major, minor)
        write_system_files(tree)
        tree.write("init", (ASSETS / "init-network-first").read_bytes(), 0o755)
        tree.write("usr/lib/ramdiag/services", (ASSETS / "services").read_bytes(), 0o755)
        tree.write("usr/lib/ramdiag/beacon.py", (ASSETS / "beacon.py").read_bytes(), 0o755)
        tree.write("usr/lib/ramdiag/stop-watchdog.py", (ASSETS / "stop-watchdog.py").read_bytes(), 0o755)
        tree.write("bin/init", b"#!/bin/sh\nexec /init\n", 0o755)
        add_runtime_dependencies(tree, source_root)
        run("chroot", tree.destination, "/bin/sh", "-n", "/init")
        run("chroot", tree.destination, "/bin/sh", "-n", "/usr/lib/ramdiag/services")
        run("chroot", tree.destination, "/" + sshd_path.lstrip("/"), "-t", "-f", "/etc/ssh/sshd_config")
        modprobe_path = command_paths["modprobe"]
        run("chroot", tree.destination, "/" + resolve_command(source_root, *modprobe_path).lstrip("/"),
            "--dry-run", "--set-version", RELEASE, "realtek")
        cpio = work / "ramdiag.cpio"
        with cpio.open("xb") as stream:
            listing = subprocess.Popen(["find", ".", "-print0"], cwd=tree.destination, stdout=subprocess.PIPE)
            result = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"],
                                    cwd=tree.destination, stdin=listing.stdout, stdout=stream,
                                    check=True, stderr=subprocess.PIPE, text=True)
            assert listing.stdout is not None
            listing.stdout.close()
            require(listing.wait() == 0 and result.returncode == 0, "failed to create cpio archive")
        payload = work / "ramdisk.payload"
        import gzip
        with cpio.open("rb") as source, payload.open("xb") as destination:
            with gzip.GzipFile(fileobj=destination, mode="wb", compresslevel=6, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
        require(payload.stat().st_size < MAX_INITRD, "diagnostic initrd exceeds 256 MiB")
        target = output / "ramdisk.payload"
        if target.exists():
            raise ValueError(f"refusing to overwrite diagnostic initrd: {target}")
        shutil.copyfile(payload, target)
        return {
            "release": RELEASE,
            "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "root_cpio_bytes": cpio.stat().st_size,
            "mode": "network-first-ram-only",
            "writes_production_storage": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="read-only mounted target rootfs")
    parser.add_argument("--boot", type=Path, required=True, help="read-only mounted target bootfs")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(build(args.root, args.boot, args.output_dir))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
