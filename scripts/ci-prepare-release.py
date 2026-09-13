#!/usr/bin/env python3
"""Validate completed collector artifacts and prepare a new local release directory.

Never unpack or execute payloads. SHA256SUMS is written last; a write failure
leaves an incomplete directory that must not be published or reused.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
TARGET = {"debian": "13", "release": "trixie", "kernel": "6.18.51", "extra_storage": "no"}
CHUNK = 1024 * 1024
MAX_ENTRIES = 10000


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
    result = {"logs/ci": (".log", ".exit-code", ".txt")}
    if kind == "image":
        result.update({"images": (".img.xz",), "packages/armbian": (".deb",),
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
                "armbian_commit": "7c1bb29eb0e7bd75b0703d86fe654b2680e646da",
                "kernel_commit": "f6388029ea9e2c9e807d73827658738ea131faee",
                "display_version_source": version,
                "image_static_audit": "passed" if kind == "image" else "not applicable"}
    for key, value in expected.items():
        require(metadata.get(key) == value, "metadata mismatch: " + kind + "." + key)
    require({f"logs/ci/{kind}.log", f"logs/ci/{kind}.exit-code"} <= files, "missing build log or exit-code")
    for name in files:
        if name.endswith(".exit-code"):
            require(small_text(root / name, 64, hashes[name]).strip() == "0", "nonzero or invalid exit-code: " + name)
    images = sorted(n for n in files if n.startswith("images/"))
    packages = sorted(n for n in files if n.startswith("packages/"))
    require(packages and all((root / n).stat().st_size for n in images + packages), "missing or empty payload")
    if kind == "image":
        require(len(images) == 1, "expected exactly one .img.xz image (collision or missing payload)")
    else:
        require(packages == [f"packages/display/e87n-display_{version}_all.deb"], "expected one standalone versioned display payload")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("image-artifact", "display-artifact", "output", "source-commit", "run-id", "run-attempt", "tag", "repository"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    for value, pattern in ((args.source_commit, r"[0-9a-fA-F]{40}"), (args.run_id, r"[0-9]+"),
                           (args.run_attempt, r"[0-9]+"), (args.tag, r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}"),
                           (args.repository, r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")):
        require(re.fullmatch(pattern, value) is not None, "invalid release argument: " + repr(value))
    require(".." not in args.tag and not args.tag.endswith((".", ".lock")), "unsafe release tag")
    output = checked_path(args.output)
    require(not output.exists(), "output already exists; refusing to overwrite")
    for artifact in (args.image_artifact, args.display_artifact):
        require(not output.is_relative_to(checked_path(artifact)), "output overlaps input artifact")
    version = small_text(REPO / "packaging/e87n-display/VERSION", 128).strip()
    require(re.fullmatch(r"[0-9][A-Za-z0-9.+~\-]*", version) is not None, "invalid display VERSION")
    image = validate(args.image_artifact, "image", args, version)
    display = validate(args.display_artifact, "display", args, version)
    output.mkdir(parents=True, exist_ok=False)
    for artifact, name, target in ((image, image[2][0], Path(image[2][0]).name),
                                   (display, display[3][0], Path(display[3][0]).name),
                                   (image, "build-metadata.json", "image-build-metadata.json"),
                                   (display, "build-metadata.json", "display-build-metadata.json")):
        with Reader(artifact[0] / name, artifact[1][name]) as source, (output / target).open("xb") as dest:
            shutil.copyfileobj(source, dest, CHUNK)
    archive(output / "kernel-packages.tar.xz", [(image[0] / n, n, image[1][n]) for n in image[3]])
    archive(output / "build-evidence.tar.xz", [
        (artifact[0] / n, kind + "/" + n, checksum)
        for kind, artifact in (("image", image), ("display", display))
        for n, checksum in sorted(artifact[1].items())
        if n in ("SHA256SUMS", "build-metadata.json") or n.startswith("logs/")])
    base = "https://github.com/" + args.repository
    image_name, display_name = Path(image[2][0]).name, Path(display[3][0]).name
    download_base = base + "/releases/download/" + quote(args.tag, safe="")
    notes = f"""# E87N {args.tag} — experimental release

## 下载 / Downloads

- [系统镜像（.img.xz）]({download_base}/{quote(image_name, safe='')})
- [屏幕控制安装包（.deb）]({download_base}/{quote(display_name, safe='')})

本 Release 仅有以上两个二进制附件：镜像已预装屏幕程序，独立安装包用于安装/升级。
GitHub 自带的 Source code (zip/tar.gz) 是源码，不是可刷写镜像。

## SHA-256

```text
{image[1][image[2][0]]}  {image_name}
{display[1][display[3][0]]}  {display_name}
```

下载后运行 `sha256sum 文件名`（macOS：`shasum -a 256 文件名`），与上面的摘要比较。

Experimental Debian 13 (trixie) + Linux 6.18.51; extra_storage=no.
Default login: root / doumao over SSH port 22. First connect only to a trusted
LAN; change the public default password immediately after login with `passwd`.
Wired interfaces request DHCP; locale zh_CN.UTF-8, timezone Asia/Shanghai.

Screen and fan support passed static checks only. No board has been validated:
boot, networking, display and thermal behavior still require hardware testing.
Do not flash this whole image over the original eMMC: the GPT, boot chain and
factory data may be overwritten. This release provides no whole-eMMC installer.

The standalone e87n-display .deb comes from the independent display job.
Kernel packages, metadata, checksum manifests and full logs remain in the
source build's Actions artifacts (14-day retention); they are not extra
installation downloads on this Release. The source tag and commit remain
available alongside these two binary downloads.

Source: {base}/commit/{args.source_commit}
Build run: {base}/actions/runs/{args.run_id}
Run attempt: {base}/actions/runs/{args.run_id}/attempts/{args.run_attempt}
"""
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
