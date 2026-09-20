#!/usr/bin/env python3
"""Record actual recipe bytes, including uncommitted edits, in the new image."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

from build_config import BUILD

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, help="verify the actual copied build inputs before compilation")
    args = parser.parse_args()
    manifest = {}
    for directory in ("scripts", "userpatches", "board-support", "firmware", "packaging", "patches", "docs"):
        for item in sorted((ROOT / directory).rglob("*")):
            if "__pycache__" in item.parts or item.name.startswith((".", "._")):
                continue
            if item.is_file() and item.suffix != ".pyc":
                if not item.resolve().is_relative_to(ROOT):
                    raise ValueError(f"source escapes repository: {item}")
                manifest[item.relative_to(ROOT).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    for name in ("build.sh", "build-armbian.sh", "containers/build/Dockerfile"):
        item = ROOT / name
        if item.is_file():
            manifest[name] = hashlib.sha256(item.read_bytes()).hexdigest()
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    revision = os.environ.get("E87N_SOURCE_COMMIT", os.environ.get("GITHUB_SHA", ""))
    if not revision:
        result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True)
        revision = result.stdout.strip() if result.returncode == 0 else "unknown"
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("a Git checkout or E87N_SOURCE_COMMIT/GITHUB_SHA full commit is required")
    dirty = os.environ.get("E87N_SOURCE_DIRTY")
    if dirty is None:
        status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                                capture_output=True, text=True)
        source_dirty = status.returncode != 0 or bool(status.stdout.strip())
    else:
        if dirty not in ("true", "false"):
            raise ValueError("E87N_SOURCE_DIRTY must be true or false")
        source_dirty = dirty == "true"
    if args.overlay:
        for name, expected in manifest.items():
            relative = Path(name)
            mapping = {
                "userpatches": args.overlay,
                "board-support": args.overlay / "overlay/e87n-board-support",
                "firmware": args.overlay / "overlay/e87n-firmware",
            }
            if relative.parts[0] in mapping:
                copied = mapping[relative.parts[0]].joinpath(*relative.parts[1:])
                if not copied.is_file() or hashlib.sha256(copied.read_bytes()).hexdigest() != expected:
                    raise ValueError(f"source changed while staging: {name}; rebuild from a stable snapshot")
    value = {"schema": 1, "build": BUILD, "source_commit": revision,
             "source_dirty": source_dirty,
             "recipe_sha256": hashlib.sha256(encoded).hexdigest(),
             "source_files": manifest, "hardware_validation": "pending"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("Recipe SHA256:", value["recipe_sha256"])


if __name__ == "__main__":
    main()
