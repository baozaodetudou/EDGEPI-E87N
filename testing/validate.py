"""Shared, dependency-free validator for the container/QEMU gate result.

CI collector, prepare and publish stages should import this module instead of
implementing independent result/schema checks. It validates the JSON contract
and proves that the FIT, root member and optional display package are the exact
bytes supplied to the runner.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tarfile
from typing import Any


SCHEMA_VERSION = 1
HARDWARE_EXEMPTIONS = {
    "e87n-factory-resize.service": "requires physical /dev/mmcblk0p5 and must not run on QEMU virt",
    "e87n-factory-mac.service": "requires E87N DT aliases and factory p2 MAC bytes",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ValidationError(ValueError):
    """Raised when a runner result or artifact binding is not trustworthy."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _sha(value: Any, name: str, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    _require(isinstance(value, str) and SHA256_RE.fullmatch(value) is not None,
             f"{name} must be a lowercase SHA-256")


def validate_result(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a result object suitable for CI gate decisions."""
    _require(isinstance(value, dict), "result must be a JSON object")
    _require(value.get("schema_version") == SCHEMA_VERSION, "unsupported result schema")
    _require(value.get("status") == "PASS", "container gate did not pass")
    _require(set(value) == {"schema_version", "status", "artifacts", "guest", "hardware_exemptions", "binding"},
             "result has unexpected or missing top-level fields")

    artifacts = value["artifacts"]
    _require(isinstance(artifacts, dict), "artifacts must be an object")
    _require(isinstance(artifacts.get("firmware_tar"), str) and artifacts["firmware_tar"],
             "artifacts.firmware_tar is required")
    _sha(artifacts.get("firmware_tar_sha256"), "artifacts.firmware_tar_sha256")
    _sha(artifacts.get("fit_sha256"), "artifacts.fit_sha256")
    _sha(artifacts.get("root_sha256"), "artifacts.root_sha256")
    _sha(artifacts.get("display_deb_sha256"), "artifacts.display_deb_sha256", nullable=True)

    guest = value["guest"]
    _require(isinstance(guest, dict), "guest must be an object")
    for key in ("systemd_pid1", "dhcp", "dns", "timezone", "locale", "minimal",
                "persistence", "first_login", "ssh", "apt", "warm_reboot",
                "cold_reboot", "host_keys", "display_package"):
        _require(guest.get(key) is True, f"guest.{key} is not true")
    _require(isinstance(guest.get("kernel_release"), str) and guest["kernel_release"],
             "guest.kernel_release is required")
    _require(isinstance(guest.get("debian_version"), str) and guest["debian_version"],
             "guest.debian_version is required")

    exemptions = value["hardware_exemptions"]
    _require(isinstance(exemptions, list), "hardware_exemptions must be an array")
    actual = {item.get("unit"): item.get("reason") for item in exemptions if isinstance(item, dict)}
    _require(actual == HARDWARE_EXEMPTIONS, "hardware exemption list is not the reviewed QEMU list")

    binding = value["binding"]
    _require(isinstance(binding, dict), "binding must be an object")
    for key in ("source_commit", "run_id", "run_attempt"):
        _require(binding.get(key) is None or isinstance(binding.get(key), str),
                 f"binding.{key} must be string or null")
    return value


def _tar_payloads(tar_path: Path) -> dict[str, str]:
    _require(tar_path.is_file() and not tar_path.is_symlink(), "firmware TAR is not a regular file")
    with tarfile.open(tar_path, "r:") as archive:
        members = [member for member in archive if member.isfile()]
        names = {member.name.rsplit("/", 1)[-1]: member for member in members}
        _require(set(names) >= {"kernel", "root", "CONTROL"}, "firmware TAR lacks kernel/root/CONTROL")
        result = {}
        for name in ("kernel", "root", "CONTROL"):
            digest = hashlib.sha256()
            stream = archive.extractfile(names[name])
            _require(stream is not None, f"cannot read TAR member {name}")
            with stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[name] = digest.hexdigest()
        return result


def validate_artifact_binding(
    result: dict[str, Any],
    tar_path: Path,
    fit_path: Path | None = None,
    display_deb_path: Path | None = None,
    source_commit: str | None = None,
    run_id: str | None = None,
    run_attempt: str | None = None,
) -> dict[str, Any]:
    """Validate result plus same-build hashes and optional CI identity binding."""
    validate_result(result)
    tar_path = Path(tar_path)
    artifacts = result["artifacts"]
    _require(sha256_file(tar_path) == artifacts["firmware_tar_sha256"], "firmware TAR SHA mismatch")
    payloads = _tar_payloads(tar_path)
    _require(payloads["kernel"] == artifacts["fit_sha256"], "FIT hash differs from TAR kernel member")
    _require(payloads["root"] == artifacts["root_sha256"], "root hash differs from TAR root member")
    if fit_path is not None:
        _require(sha256_file(Path(fit_path)) == payloads["kernel"], "provided FIT is not the TAR FIT")
    if display_deb_path is not None:
        expected = artifacts["display_deb_sha256"]
        _require(expected is not None and sha256_file(Path(display_deb_path)) == expected,
                 "display package hash mismatch")
    elif artifacts["display_deb_sha256"] is not None:
        raise ValidationError("result records a display package but no package was supplied")
    expected_binding = {
        "source_commit": source_commit,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }
    for key, expected in expected_binding.items():
        if expected is not None:
            _require(result["binding"].get(key) == expected, f"CI binding mismatch: {key}")
    return result


def load_and_validate(path: Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        value = json.load(stream)
    return validate_result(value)


def validate_report(
    report: dict[str, Any],
    firmware_sha256: str,
    display_sha256: str,
    source_commit: str,
    run_id: str,
    run_attempt: str,
) -> dict[str, Any]:
    """Release gate used by collector/prepare/publish without filesystem access."""
    validate_result(report)
    _require(all(isinstance(value, str) and value for value in
                 (firmware_sha256, display_sha256, source_commit, run_id, run_attempt)),
             "release binding values must be non-empty strings")
    _require(report["artifacts"]["firmware_tar_sha256"] == firmware_sha256,
             "firmware_tar_sha256 does not match the final TAR")
    _require(report["artifacts"]["display_deb_sha256"] == display_sha256,
             "display_deb_sha256 does not match the display package")
    expected = {"source_commit": source_commit, "run_id": run_id, "run_attempt": run_attempt}
    _require(report["binding"] == expected, "source/run/attempt binding mismatch")
    return report
