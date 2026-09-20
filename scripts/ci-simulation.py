#!/usr/bin/env python3
"""Fail-closed release policy on top of the shared QEMU evidence validator."""
from pathlib import Path
import json
import re
import sys

from build_config import BUILD

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from testing.validate import sha256_file, validate_report


def load_report(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("simulation report must be a regular file without symlinks")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate simulation evidence key: " + key)
            value[key] = item
        return value
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("oversized simulation report")
    return json.loads(data, object_pairs_hook=unique)


def verify_report(report, *, firmware_sha256, display_sha256, source_commit, run_id, run_attempt):
    """Bind a validated runner report to already-hashed CI inputs and identity."""
    try:
        validate_report(report, firmware_sha256, display_sha256, source_commit, run_id, run_attempt)
    except (KeyError, TypeError) as error:
        raise ValueError("malformed simulation evidence") from error
    expected = {"source_commit": source_commit, "run_id": run_id, "run_attempt": run_attempt}
    for key, value in expected.items():
        pattern = r"[0-9a-f]{40}" if key == "source_commit" else r"[0-9]+"
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("simulation requires a valid CI identity: " + key)
    for key, expected_hash in (("firmware_tar_sha256", firmware_sha256),
                               ("display_deb_sha256", display_sha256)):
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError("simulation requires an artifact SHA256: " + key)
    guest = report["guest"]
    if guest.get("kernel_release") != BUILD["kernel_release"]:
        raise ValueError("simulation kernel release mismatch")
    if guest.get("debian_version") != BUILD["debian_point"]:
        raise ValueError("simulation Debian version mismatch")
    return report
