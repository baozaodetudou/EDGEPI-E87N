#!/usr/bin/env python3
"""Verify a single E87N RAM-only FIT without building or touching a board."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fit_common import (
    FDT_LOAD,
    MAX_FIT_BYTES,
    RAMDISK_LOAD,
    audit_fit,
    fdt,
    regular,
    sha256_bytes,
    sha256_file,
    variant_from_name,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fit", type=Path)
    parser.add_argument("--variant", choices=[
        "40000000-initrd", "40080000-initrd", "40000000-no-initrd"
    ], required=True)
    parser.add_argument(
        "--kernel-compression", choices=["auto", "lzma", "none"], default="lzma",
        help="expected FIT kernel compression; default lzma matches captured E87N U-Boot",
    )
    parser.add_argument("--kernel-sha256", required=True,
                        help="SHA-256 of the embedded kernel payload, not the raw Image source")
    parser.add_argument("--initrd-sha256",
                        help="SHA-256 of the embedded initrd payload; required for initrd variants")
    parser.add_argument("--ramdisk-load", type=lambda value: int(value, 0), default=RAMDISK_LOAD)
    parser.add_argument("--fdt-load", type=lambda value: int(value, 0), default=FDT_LOAD)
    args = parser.parse_args()
    try:
        fit = regular(args.fit, MAX_FIT_BYTES)
        fit_data = fit.read_bytes()
        kernel_compression = args.kernel_compression
        if kernel_compression == "auto":
            props = fdt(fit_data)
            encoded = props.get(("/images/kernel-1", "compression"))
            if encoded is None or not encoded.endswith(b"\0"):
                raise ValueError("FIT kernel compression metadata is missing")
            kernel_compression = encoded[:-1].decode("ascii")
            if kernel_compression not in ("lzma", "none"):
                raise ValueError(f"unsupported FIT kernel compression: {kernel_compression}")
        result = audit_fit(
            fit_data,
            variant=variant_from_name(args.variant),
            kernel_compression=kernel_compression,
            expected_kernel_sha256=args.kernel_sha256,
            expected_initrd_sha256=args.initrd_sha256,
            ramdisk_load=args.ramdisk_load,
            fdt_load=args.fdt_load,
        )
        result.update({
            "file": str(fit),
            "file_sha256": sha256_file(fit),
            "verified": True,
        })
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
