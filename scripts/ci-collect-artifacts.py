#!/usr/bin/env python3
"""Collect only this job's allowlisted outputs, also after failed builds.

Hard links avoid duplicating multi-GB images on the runner. No rootfs, cache,
environment dump, credentials, device evidence, or previous local output is read.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys

REPO = Path(__file__).resolve().parents[1]
DEST = REPO / "output/ci/artifacts"


def contained_path(path):
    """Reject symlinks in any component, including the output directory itself."""
    current = REPO
    for part in path.relative_to(REPO).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink is not an artifact: " + str(current.relative_to(REPO)))
    if not path.resolve().is_relative_to(REPO):
        raise ValueError("artifact escapes workspace")
    return path


def collect_tree(source, directory, suffixes, errors):
    count = 0
    try:
        contained_path(source)
        if not source.exists():
            return count
        for root, dirs, names in os.walk(source, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(root) / d).is_symlink())
            for name in sorted(names):
                if name.startswith(".") or not name.endswith(suffixes):
                    continue
                path = Path(root) / name
                try:
                    contained_path(path)
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("not a regular file: " + str(path.relative_to(REPO)))
                    relative = path.relative_to(source)
                    if any(c in str(relative) for c in "\r\n\\"):
                        raise ValueError("unsupported artifact filename")
                    target = contained_path(DEST / directory / relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise ValueError("refusing to replace artifact: " + str(target.relative_to(DEST)))
                    try:
                        os.link(path, target)
                    except OSError as error:
                        if error.errno != errno.EXDEV:
                            raise
                        shutil.copyfile(path, target)
                    # Armbian can leave root-owned outputs after an interrupted run.
                    target.chmod(0o644)
                    count += 1
                except (OSError, ValueError) as error:
                    errors.append(str(error))
    except (OSError, ValueError) as error:
        errors.append(str(error))
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("image", "display"), required=True)
    parser.add_argument("--status", choices=("success", "failure", "cancelled", "skipped", ""), required=True)
    args = parser.parse_args()
    contained_path(DEST)
    # A rerun must use a fresh runner; never merge with a previous export.
    DEST.mkdir(parents=True, exist_ok=False)
    errors = []
    counts = {}
    if args.kind == "image":
        output = REPO / "source/armbian-build/output"
        counts["images"] = collect_tree(REPO / "output/ci/firmware", "images", (".tar",), errors)
        counts["packages"] = collect_tree(output / "debs", "packages/armbian", (".deb",), errors)
        counts["framework_logs"] = collect_tree(output / "logs", "logs/armbian", (".log", ".txt", ".html", ".json", ".gz", ".xz", ".zst"), errors)
    else:
        counts["packages"] = collect_tree(REPO / "output/ci/display-debs", "packages/display", (".deb",), errors)
    counts["ci_logs"] = collect_tree(REPO / "output/ci/logs", "logs/ci", (".log", ".exit-code", ".txt"), errors)

    factory_audit_passed = False
    if args.status == "success":
        for required in (("images", "packages") if args.kind == "image" else ("packages",)):
            if not counts[required]:
                errors.append("successful build is missing " + required)
        if args.kind == "image":
            if counts["images"] != 1:
                errors.append("successful build requires exactly one factory firmware .tar")
            try:
                with contained_path(DEST / "logs/ci/factory-firmware-audit-1.log").open("rb") as audit:
                    factory_audit_passed = any(line.strip() == b"PASS" or
                        line.startswith((b"PASS:", b"PASS ")) for line in audit)
                if not factory_audit_passed:
                    errors.append("factory firmware audit log is missing PASS")
            except (OSError, ValueError) as error:
                errors.append("required factory firmware audit log: " + str(error))
    metadata = {
        "kind": args.kind,
        "build_step_outcome": args.status or "not-started",
        "source_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "unknown"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "unknown"),
        "target": {"debian": "13", "release": "trixie", "kernel": "6.18.51", "extra_storage": "no"},
        "armbian_commit": "7c1bb29eb0e7bd75b0703d86fe654b2680e646da",
        "kernel_commit": "f6388029ea9e2c9e807d73827658738ea131faee",
        "image_static_audit": ("passed" if args.status == "success" else "not proven") if args.kind == "image" else "not applicable",
        "factory_format": "e87n-uboot-firmware-tar-v1" if args.kind == "image" else "not applicable",
        "factory_static_audit": ("passed" if factory_audit_passed else "not proven") if args.kind == "image" else "not applicable",
        "board_validation": "pending; a successful build or checksum is not hardware acceptance",
        "failure_artifacts": "may be incomplete; consult build_step_outcome and logs before use",
        "files_collected": counts,
        "collection_errors": errors,
    }
    version = REPO / "packaging/e87n-display/VERSION"
    if version.is_file():
        contained_path(version)
        metadata["display_version_source"] = version.read_text().strip()
    (DEST / "build-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    # Stream hashes; never load a disk image into memory. Paths are relative to
    # the downloaded artifact's root, so `sha256sum -c SHA256SUMS` works there.
    with (DEST / "SHA256SUMS").open("w") as checksums:
        for path in sorted(DEST.rglob("*")):
            if not path.is_file() or path.name == "SHA256SUMS":
                continue
            digest = hashlib.sha256()
            with path.open("rb") as payload:
                for chunk in iter(lambda: payload.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksums.write(digest.hexdigest() + "  " + path.relative_to(DEST).as_posix() + "\n")
    print(json.dumps(metadata, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        sys.exit("FAIL: artifact collection: " + str(error))
