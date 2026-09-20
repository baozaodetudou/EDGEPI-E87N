#!/usr/bin/env python3
"""Build and audit the three E87N RAM-only FIT diagnostic variants.

The inputs are ordinary files supplied by the caller.  This tool does not
mount an image, access a block device, flash a board, or modify any input.
The DTB is copied to a private temporary directory and patched there so that
the eMMC controller is disabled for every diagnostic variant.

The default ``all`` matrix is:

* 0x40000000 kernel + initrd — baseline;
* 0x40080000 kernel + initrd — address A/B experiment;
* 0x40000000 kernel without initrd — serial-only loader/kernel experiment.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from fit_common import (
    BOOTARGS_DEFAULT,
    EMMC_NODE,
    FDT_LOAD,
    MIB,
    RAMDISK_LOAD,
    VARIANTS,
    Variant,
    audit_fit,
    normalize_kernel,
    regular,
    render_its,
    require,
    sha256_bytes,
    sha256_file,
    variant_from_name,
)


def run(*args: object, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def tool(name: str) -> str:
    path = shutil.which(name)
    require(path, f"required host tool not found: {name}")
    return path


def patch_dtb(source: Path, destination: Path, bootargs: str) -> None:
    shutil.copyfile(source, destination)
    try:
        run("fdtget", "-t", "s", destination, EMMC_NODE, "status")
    except subprocess.CalledProcessError as error:
        raise ValueError(f"DTB lacks required E87N eMMC node {EMMC_NODE}") from error
    run("fdtput", "-t", "s", destination, EMMC_NODE, "status", "disabled")
    try:
        run("fdtget", "-p", destination, "/chosen")
    except subprocess.CalledProcessError:
        run("fdtput", "-c", destination, "/chosen")
    run("fdtput", "-t", "s", destination, "/chosen", "bootargs", bootargs)
    status = run("fdtget", "-t", "s", destination, EMMC_NODE, "status").stdout.strip()
    require(status == "disabled", "failed to patch the diagnostic DTB eMMC status")


def publish_no_overwrite(path: Path, data: bytes) -> None:
    require(not path.exists(), f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
        os.link(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def publish_json_no_overwrite(path: Path, value: object) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    publish_no_overwrite(path, encoded)


def build_one(
    *,
    variant: Variant,
    output_dir: Path,
    kernel_payload: bytes,
    kernel_compression: str,
    kernel_sha256: str,
    kernel_header: dict[str, int],
    dtb_source: Path,
    initrd_source: Path | None,
    initrd_sha256: str | None,
    bootargs: str,
    ramdisk_load: int,
    fdt_load: int,
    work: Path,
) -> dict[str, object]:
    variant_work = work / variant.name
    variant_work.mkdir()
    kernel_file = variant_work / "kernel.payload"
    dtb_file = variant_work / "board.dtb"
    its_file = variant_work / "diagnostic.its"
    fit_file = variant_work / "diagnostic.itb"
    kernel_file.write_bytes(kernel_payload)
    patch_dtb(dtb_source, dtb_file, bootargs + (" rdinit=/init" if variant.with_initrd else ""))
    initrd_file = None
    if variant.with_initrd:
        require(initrd_source is not None and initrd_sha256 is not None,
                "initrd variant requested without an initrd")
        initrd_file = variant_work / "ramdisk.payload"
        shutil.copyfile(initrd_source, initrd_file)
    its_file.write_text(
        render_its(
            kernel_file=kernel_file.name,
            dtb_file=dtb_file.name,
            kernel_load=variant.kernel_load,
            kernel_compression=kernel_compression,
            with_initrd=variant.with_initrd,
            initrd_file=initrd_file.name if initrd_file else None,
            ramdisk_load=ramdisk_load,
            fdt_load=fdt_load,
        )
    )
    environment = os.environ.copy()
    environment.setdefault("SOURCE_DATE_EPOCH", "0")
    run("mkimage", "-f", its_file.name, fit_file.name, cwd=variant_work, env=environment)
    fit = fit_file.read_bytes()
    report = audit_fit(
        fit,
        variant=variant,
        kernel_compression=kernel_compression,
        expected_kernel_sha256=sha256_bytes(kernel_payload),
        expected_initrd_sha256=initrd_sha256,
        ramdisk_load=ramdisk_load,
        fdt_load=fdt_load,
    )
    report.update({
        "input_kernel_sha256": kernel_sha256,
        "input_kernel_payload_sha256": sha256_bytes(kernel_payload),
        "input_kernel_source_header": kernel_header,
        "input_dtb_sha256": sha256_file(dtb_source),
        "patched_dtb_sha256": sha256_bytes((variant_work / "board.dtb").read_bytes()),
        "input_initrd_sha256": initrd_sha256,
        "bootargs_requested": bootargs + (" rdinit=/init" if variant.with_initrd else ""),
    })
    output_name = f"E87N-ramdiag-{variant.name}.itb"
    report_name = output_name + ".json"
    publish_no_overwrite(output_dir / output_name, fit)
    publish_json_no_overwrite(output_dir / report_name, report)
    return {"file": output_name, **report}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True,
                        help="ARM64 Image or vendor-compatible Image.lzma")
    parser.add_argument("--dtb", type=Path, required=True,
                        help="E87N DTB describing 1 GiB RAM")
    parser.add_argument("--initrd", type=Path,
                        help="raw initrd image (normally gzip-compressed cpio; no uInitrd wrapper)")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new or existing directory for FITs and JSON reports")
    parser.add_argument("--variant", choices=["all", *VARIANTS], default="all")
    parser.add_argument(
        "--kernel-compression", choices=["auto", "lzma", "none"], default="auto",
                        help="FIT kernel compression; auto (default) uses the production LZMA profile",
    )
    parser.add_argument("--bootargs", default=BOOTARGS_DEFAULT,
                        help="base RAM-only bootargs; rdinit=/init is added only for initrd variants")
    parser.add_argument("--ramdisk-load", type=lambda value: int(value, 0), default=RAMDISK_LOAD)
    parser.add_argument("--fdt-load", type=lambda value: int(value, 0), default=FDT_LOAD)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        tool("mkimage")
        tool("fdtget")
        tool("fdtput")
        kernel_source = regular(args.kernel, 128 * MIB)
        dtb_source = regular(args.dtb, 2 * MIB)
        initrd_source = regular(args.initrd, 256 * MIB) if args.initrd else None
        selected = list(VARIANTS.values()) if args.variant == "all" else [variant_from_name(args.variant)]
        require(all(not item.with_initrd for item in selected) or initrd_source is not None,
                "the selected initrd variant(s) require --initrd")
        require(args.ramdisk_load >= 0x41000000 and args.ramdisk_load < args.fdt_load,
                "ramdisk load must be below fdt load")
        require(args.fdt_load >= 0x44000000 and args.fdt_load < 0x80000000,
                "FDT load must be within the measured RAM window")
        require("root=" not in args.bootargs.split() and "rw" not in args.bootargs.split(),
                "RAM-only bootargs must not contain root= or rw")
        kernel_payload, compression, header = normalize_kernel(kernel_source, args.kernel_compression)
        kernel_sha256 = sha256_file(kernel_source)
        initrd_sha256 = sha256_file(initrd_source) if initrd_source else None
        output_dir = args.output_dir.absolute()
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "MANIFEST.json"
        require(not manifest_path.exists(), f"refusing to overwrite existing manifest: {manifest_path}")
        with tempfile.TemporaryDirectory(prefix="e87n-ramdiag-", dir=output_dir) as temporary:
            work = Path(temporary)
            results = [build_one(
                variant=item,
                output_dir=output_dir,
                kernel_payload=kernel_payload,
                kernel_compression=compression,
                kernel_sha256=kernel_sha256,
                kernel_header=header,
                dtb_source=dtb_source,
                initrd_source=initrd_source,
                initrd_sha256=initrd_sha256,
                bootargs=args.bootargs,
                ramdisk_load=args.ramdisk_load,
                fdt_load=args.fdt_load,
                work=work,
            ) for item in selected]
        manifest = {
            "schema_version": 1,
            "tool": "scripts/ramdiag/build-fits.py",
            "variants": results,
            "input_kernel": {"path": str(kernel_source), "sha256": kernel_sha256,
                              "payload_sha256": sha256_bytes(kernel_payload),
                              "compression": compression, "header": header},
            "input_dtb": {"path": str(dtb_source), "sha256": sha256_file(dtb_source)},
            "input_initrd": ({"path": str(initrd_source), "sha256": initrd_sha256}
                             if initrd_source else None),
            "ramdisk_load": f"0x{args.ramdisk_load:08x}",
            "fdt_load": f"0x{args.fdt_load:08x}",
            "hardware_validation": "pending",
            "writes_production_storage": False,
        }
        publish_json_no_overwrite(manifest_path, manifest)
        for result in results:
            print(json.dumps(result, sort_keys=True))
        print(f"PASS: {len(results)} RAM-only FIT diagnostic variant(s) written to {output_dir}")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
