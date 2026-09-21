#!/usr/bin/env python3
"""Validate completed collector artifacts and prepare a new local release directory.

Never unpack or execute payloads. SHA256SUMS is written last; a write failure
leaves an incomplete directory that must not be published or reused.
"""
import argparse
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
from urllib.parse import quote

from build_config import BUILD, TARGET
from factory_firmware import FORMAT
from release_identity import display_filename, image_filename, release_tag, release_title

simulation = import_module("ci-simulation")

REPO = Path(__file__).resolve().parents[1]
CHUNK = 1024 * 1024
MAX_ENTRIES = 10000
RAMDIAG_FILES = frozenset({
    "E87N-ramdiag-40000000-initrd.itb",
    "E87N-ramdiag-40000000-initrd.itb.json",
    "E87N-ramdiag-40000000-no-initrd.itb",
    "E87N-ramdiag-40000000-no-initrd.itb.json",
    "E87N-ramdiag-40080000-initrd.itb",
    "E87N-ramdiag-40080000-initrd.itb.json",
    "MANIFEST.json",
    "README.txt",
})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_path(path):
    path = Path(path).absolute()
    require(".." not in path.parts, "parent traversal in input/output path")
    for item in (path, *path.parents):
        require(not item.is_symlink(), "symlink is not allowed: " + str(item))
    return path


def safe_name(name):
    parts = name.split("/")
    require(len(name) <= 1024 and len(parts) <= 32 and all(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~\-]*", p) and ".." not in p for p in parts),
            "unsafe artifact filename: " + repr(name))
    return name


class Reader:
    """Bounded streaming hashes also verify the exact bytes copied into archives."""
    def __init__(self, path, expected=None):
        path = checked_path(path)
        require(stat.S_ISREG(path.lstat().st_mode), "not a regular file: " + str(path))
        self.file = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb")
        self.info = os.fstat(self.file.fileno())
        if not stat.S_ISREG(self.info.st_mode):
            self.file.close()
            raise ValueError("not a regular file: " + str(path))
        self.digest, self.expected = hashlib.sha256(), expected

    def read(self, size):
        data = self.file.read(size)
        self.digest.update(data)
        return data

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.file.close()
        if kind is None and self.expected is not None:
            require(self.digest.hexdigest() == self.expected, "SHA256 mismatch while staging")


def digest(path):
    with Reader(path) as source:
        while source.read(CHUNK):
            pass
        return source.digest.hexdigest()


def small_text(path, limit, expected=None):
    with Reader(path, expected) as source:
        data = source.read(limit + 1)
        require(len(data) <= limit, "oversized control file: " + str(path))
    return data.decode("utf-8")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate metadata key: " + key)
        result[key] = value
    return result


def namespaces(kind):
    result = {"logs/ci": (".log", ".exit-code", ".txt"),
              "logs/failure": (".log", ".exit-code", ".txt", ".json", ".tail")}
    if kind == "image":
        result.update({"images": (".tar",), "diagnostics": (".itb", ".json", ".txt"),
                       "packages/armbian": (".deb",),
                       "packages/simulation": (".deb",),
                       "validation/qemu": (".json", ".log", ".txt", ".exit-code"),
                       "logs/armbian": (".log", ".txt", ".html", ".json", ".gz", ".xz", ".zst")})
    else:
        result["packages/display"] = (".deb",)
    return result


def validate(root, kind, args, version):
    root, files, entries = checked_path(root), set(), 0
    require(root.is_dir(), "artifact directory missing: " + str(root))
    spaces = namespaces(kind)
    def walk_error(error):
        raise error
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        for name in dirs + names:
            entries += 1
            require(entries <= MAX_ENTRIES, "too many artifact entries")
            path = checked_path(Path(directory) / name)
            relative = safe_name(path.relative_to(root).as_posix())
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                require(any(relative == p or p.startswith(relative + "/") or relative.startswith(p + "/")
                            for p in spaces), "unexpected directory: " + relative)
                continue
            require(stat.S_ISREG(mode), "not a regular file: " + relative)
            require(relative in ("SHA256SUMS", "build-metadata.json") or any(
                relative.startswith(p + "/") and relative.endswith(s) for p, s in spaces.items()),
                "unexpected artifact file: " + relative)
            files.add(relative)
    require({"SHA256SUMS", "build-metadata.json"} <= files, "missing manifest or metadata")
    hashes = {}
    manifest = small_text(root / "SHA256SUMS", 2 * CHUNK)
    for line in manifest.split("\n"):
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        require(match is not None, "malformed SHA256SUMS entry")
        checksum, name = match.groups()
        safe_name(name)
        require(name not in hashes, "duplicate manifest filename: " + name)
        hashes[name] = checksum.lower()
    require(set(hashes) == files - {"SHA256SUMS"}, "manifest coverage mismatch")
    for name, checksum in hashes.items():
        require(digest(root / name) == checksum, "SHA256 mismatch: " + name)
    metadata = json.loads(small_text(root / "build-metadata.json", 65536, hashes["build-metadata.json"]),
                          object_pairs_hook=unique_object)
    require(isinstance(metadata, dict), "metadata must be an object")
    expected = {"kind": kind, "build_step_outcome": "success", "target": TARGET,
                "source_commit": args.source_commit, "run_id": args.run_id,
                "run_attempt": args.run_attempt, "collection_errors": [],
                "armbian_commit": BUILD["armbian_commit"],
                "kernel_commit": BUILD["kernel_commit"],
                "kernel_source": BUILD["kernel_source"],
                "kernel_release": BUILD["kernel_release"],
                "display_version_source": version,
                "image_static_audit": "passed" if kind == "image" else "not applicable"}
    if kind == "image":
        expected.update(factory_format=FORMAT, factory_static_audit="passed",
                        simulation_validation="passed")
    for key, value in expected.items():
        require(metadata.get(key) == value, "metadata mismatch: " + kind + "." + key)
    require({f"logs/ci/{kind}.log", f"logs/ci/{kind}.exit-code"} <= files, "missing build log or exit-code")
    for name in files:
        if name.endswith(".exit-code"):
            require(small_text(root / name, 64, hashes[name]).strip() == "0", "nonzero or invalid exit-code: " + name)
    images = sorted(n for n in files if n.startswith("images/"))
    package_prefix = "packages/armbian/" if kind == "image" else "packages/display/"
    packages = sorted(n for n in files if n.startswith(package_prefix))
    require(packages and all((root / n).stat().st_size for n in images + packages), "missing or empty payload")
    if kind == "image":
        diagnostics = {name.removeprefix("diagnostics/") for name in files
                       if name.startswith("diagnostics/")}
        require(diagnostics == RAMDIAG_FILES,
                "incomplete RAM diagnostic matrix")
        require(all((root / "diagnostics" / name).stat().st_size for name in diagnostics),
                "empty RAM diagnostic payload")
        require(len(images) == 1, "expected exactly one factory firmware .tar (collision or missing payload)")
        audit_name = "logs/ci/factory-firmware-audit-1.log"
        require(audit_name in files, "missing required factory firmware audit log")
        audit = small_text(root / audit_name, CHUNK, hashes[audit_name])
        require(any(line.strip() == "PASS" or line.startswith(("PASS:", "PASS ")) for line in audit.splitlines()),
                "factory firmware audit log is missing PASS")
        tested = [n for n in files if n.startswith("packages/simulation/")]
        require(tested == [f"packages/simulation/{display_filename(version=version)}"],
                "expected one versioned simulation display package")
        require((root / tested[0]).stat().st_size > 0, "empty simulation display package")
        require("validation/qemu/result.json" in files, "missing simulation evidence")
        simulation.verify_report(
            simulation.load_report(root / "validation/qemu/result.json"),
            firmware_sha256=hashes[images[0]], display_sha256=hashes[tested[0]],
            source_commit=args.source_commit, run_id=args.run_id, run_attempt=args.run_attempt)
    else:
        require(packages == [f"packages/display/{display_filename(version=version)}"],
                "expected one standalone versioned display payload")
    hashes["SHA256SUMS"] = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    return root, hashes, images, packages


def archive(output, members):
    with tarfile.open(output, "x:xz") as bundle:
        for path, name, checksum in members:
            safe_name(name)
            with Reader(path, checksum) as source:
                info = tarfile.TarInfo(name)
                info.size, info.mode = source.info.st_size, 0o644
                bundle.addfile(info, source)


def copy_file(source, checksum, target):
    with Reader(source, checksum) as reader, target.open("xb") as destination:
        shutil.copyfileobj(reader, destination, CHUNK)


def firmware_notes(args, artifact, version):
    _, hashes, images, _ = artifact
    image_name = image_filename()
    tested_display = f"packages/simulation/{display_filename(version=version)}"
    base = "https://github.com/" + args.repository
    download_base = base + "/releases/download/" + quote(args.tag, safe="")
    return f"""# {release_title("image")} — experimental release

Release tag: `{args.tag}`

## 下载 / Download

- [实验性 U-Boot 系统固件（.tar）]({download_base}/{quote(image_name, safe='')})

本 Firmware Release 只有以上一个二进制附件。GitHub 自带的 Source code
(zip/tar.gz) 是源码，不是可刷写固件。屏幕安装包使用独立的 Display Release
发布；升级屏幕程序不需要重新构建或刷写固件。

## SHA-256

```text
{hashes[images[0]]}  {image_name}
```

下载后运行 `sha256sum 文件名`（macOS：`shasum -a 256 文件名`），与上面的摘要比较。

Experimental Debian {TARGET['debian']} ({TARGET['release']}) + Linux {TARGET['kernel']}; extra_storage={TARGET['extra_storage']}.
Kernel release: {BUILD['kernel_release']}
Kernel source: {BUILD['kernel_source']} at {BUILD['kernel_commit']}
Armbian source commit: {BUILD['armbian_commit']}
Default login: root / doumao over SSH port 22. First connect only to a trusted
LAN; change the public default password immediately after login with `passwd`.
Wired interfaces request DHCP; locale zh_CN.UTF-8, timezone Asia/Shanghai.

The firmware passed static checks and same-build Docker/QEMU acceptance. The
QEMU guest used this firmware's kernel/initrd, a private copy of its production
rootfs, and the embedded baseline display package `{Path(tested_display).name}`
(SHA-256 `{hashes[tested_display]}`). That package is retained as Actions build
evidence and is not a second Release download. Newer display packages may be
published independently.

The complete firmware path is not hardware validated: real U-Boot handoff,
networking, storage, reboot/recovery and long-duration thermal behavior still
require board testing. Separate real-board validation of a display package does
not establish those whole-firmware properties. The uncompressed USTAR archive
uses format `{FORMAT}` and contains `sysupgrade-edgepi-e87n/kernel` (FIT) and
`sysupgrade-edgepi-e87n/root` (ext4).

Use this .tar only in the original U-Boot recovery page's `firmware` field.
Never use the SIMG, GPT or FIP fields; never use LuCI sysupgrade or the OpenWrt
`sysupgrade` command, despite the archive member names. Do not flash it as a
whole-eMMC image. A complete, verified recovery backup (including the original
eMMC GPT, boot chain and factory data) and a successful hardware RAM test boot
of this candidate are required before any flash. This release does not establish
either prerequisite and provides no whole-eMMC installer.

此固件为实验版本，已通过软件静态校验及同构建 Docker/QEMU 验收，但完整固件路径仍需实机验证。
仅可用于原厂 U-Boot 恢复页面的 `firmware` 字段，禁止用于 SIMG/GPT/FIP 或 LuCI
sysupgrade。刷写前必须完成并验证恢复备份，并通过本候选固件的硬件 RAM 启动测试。

Kernel packages, metadata, checksum manifests, the QEMU-tested baseline display
package and full logs remain in the source build's Actions artifacts (14-day
retention); they are not extra installation downloads on this Release.

Source: {base}/commit/{args.source_commit}
Build run: {base}/actions/runs/{args.run_id}
Run attempt: {base}/actions/runs/{args.run_id}/attempts/{args.run_attempt}
"""


def display_notes(args, artifact, version):
    _, hashes, _, packages = artifact
    package_name = Path(packages[0]).name
    base = "https://github.com/" + args.repository
    download_base = base + "/releases/download/" + quote(args.tag, safe="")
    return f"""# {release_title("display", version=version)}

Release tag: `{args.tag}`

## 下载 / Download

- [屏幕控制安装包（.deb）]({download_base}/{quote(package_name, safe='')})

本 Display Release 只有以上一个二进制附件。它独立于 Firmware Release 发布，
安装或升级屏幕包不需要重新构建、下载或刷写 ARM64 固件镜像。GitHub 自带的
Source code (zip/tar.gz) 是源码，不是 Debian 安装包。

## SHA-256

```text
{hashes[packages[0]]}  {package_name}
```

下载后运行 `sha256sum 文件名`（macOS：`shasum -a 256 文件名`），与上面的摘要比较。
在目标 Debian 系统中可使用 `sudo apt-get install ./包名.deb` 安装；包管理器会独立
记录 display 版本。固件镜像内的基线版本可能更旧，这是两个发布通道的预期行为。

项目为独立显示包保留实体设备验收记录；当前 Release 的具体版本、提交和设备适配状态
仍应以本次构建及项目验收文档为准。显示包验收不代表完整固件、U-Boot 恢复、网络、
存储或长期散热均已通过实机验证。

Metadata, checksum manifests and full logs remain in the source build's Actions
artifacts (14-day retention); they are not extra installation downloads on this Release.

Source: {base}/commit/{args.source_commit}
Build run: {base}/actions/runs/{args.run_id}
Run attempt: {base}/actions/runs/{args.run_id}/attempts/{args.run_attempt}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("image", "display"))
    for option in ("artifact", "output", "source-commit", "run-id", "run-attempt", "tag", "repository"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    for value, pattern in ((args.source_commit, r"[0-9a-fA-F]{40}"), (args.run_id, r"[0-9]+"),
                           (args.run_attempt, r"[0-9]+"), (args.tag, r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}"),
                           (args.repository, r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")):
        require(re.fullmatch(pattern, value) is not None, "invalid release argument: " + repr(value))
    require(".." not in args.tag and not args.tag.endswith((".", ".lock")), "unsafe release tag")
    require(args.tag == release_tag(args.kind),
            "release tag does not match the current channel version")
    output = checked_path(args.output)
    require(not output.exists(), "output already exists; refusing to overwrite")
    artifact_root = checked_path(args.artifact)
    require(not output.is_relative_to(artifact_root), "output overlaps input artifact")
    version = small_text(REPO / "packaging/e87n-display/VERSION", 128).strip()
    require(re.fullmatch(r"[0-9][A-Za-z0-9.+~\-]*", version) is not None, "invalid display VERSION")
    artifact = validate(artifact_root, args.kind, args, version)
    output.mkdir(parents=True, exist_ok=False)
    root, hashes, images, packages = artifact
    if args.kind == "image":
        tested_display = f"packages/simulation/{display_filename(version=version)}"
        for name, target in ((images[0], image_filename()),
                             (tested_display, Path(tested_display).name),
                             ("build-metadata.json", "image-build-metadata.json"),
                             ("validation/qemu/result.json", "simulation-result.json")):
            copy_file(root / name, hashes[name], output / target)
        archive(output / "kernel-packages.tar.xz", [(root / n, n, hashes[n]) for n in packages])
        notes = firmware_notes(args, artifact, version)
    else:
        for name, target in ((packages[0], Path(packages[0]).name),
                             ("build-metadata.json", "display-build-metadata.json")):
            copy_file(root / name, hashes[name], output / target)
        notes = display_notes(args, artifact, version)
    archive(output / "build-evidence.tar.xz", [
        (root / n, args.kind + "/" + n, checksum)
        for n, checksum in sorted(hashes.items())
        if n in ("SHA256SUMS", "build-metadata.json") or n.startswith(("logs/", "validation/"))])
    (output / "RELEASE-NOTES.md").write_text(notes, encoding="utf-8")
    with (output / "SHA256SUMS").open("x", encoding="utf-8") as checksums:
        for path in sorted(output.iterdir()):
            if path.name != "SHA256SUMS":
                checksums.write(digest(path) + "  " + path.name + "\n")
    print("Prepared local release assets: " + str(output))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, tarfile.TarError) as error:
        sys.exit("FAIL: release preparation: " + str(error))
