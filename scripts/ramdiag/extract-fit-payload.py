#!/usr/bin/env python3
"""Extract embedded FIT payloads from an existing diagnostic FIT.

This is useful when the only available copy of the previously tested RAM
diagnostic is an ``.itb`` file under ``output/ramdiag``.  It reads the FIT
only; it does not execute payloads and does not write to the board.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fit_common import fdt, regular, sha256_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fit", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        fit_path = regular(args.fit, 128 * 1024 * 1024)
        props = fdt(fit_path.read_bytes())
        output_dir = args.output_dir.absolute()
        output_dir.mkdir(parents=True, exist_ok=True)
        result = {"source": str(fit_path), "source_sha256": sha256_bytes(fit_path.read_bytes()),
                  "payloads": {}}
        for image, filename in (("kernel-1", "kernel.payload"),
                                ("ramdisk-1", "ramdisk.payload"),
                                ("fdt-1", "board.dtb")):
            key = (f"/images/{image}", "data")
            if key not in props:
                continue
            payload = props[key]
            destination = output_dir / filename
            if destination.exists():
                raise ValueError(f"refusing to overwrite existing file: {destination}")
            destination.write_bytes(payload)
            result["payloads"][image] = {
                "file": filename,
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
        if not result["payloads"].get("kernel-1") or not result["payloads"].get("fdt-1"):
            raise ValueError("FIT must contain kernel-1 and fdt-1 payloads")
        manifest = output_dir / "MANIFEST.json"
        if manifest.exists():
            raise ValueError(f"refusing to overwrite existing manifest: {manifest}")
        manifest.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

