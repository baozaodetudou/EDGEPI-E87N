#!/usr/bin/env python3
"""Shared helpers for the E87N RAM-only FIT diagnostics.

This module is deliberately independent from the production firmware builder.
It only describes a FIT loaded temporarily by U-Boot and never writes a block
device or changes a production image.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import lzma
from pathlib import Path
import re
import struct
from typing import Any

import sys


SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from factory_firmware import fdt  # noqa: E402


MIB = 1024 * 1024
RAM_BASE = 0x40000000
RAM_END = 0x80000000
RAMDISK_LOAD = 0x43000000
FDT_LOAD = 0x45E00000
FIT_UPLOAD_BUFFER = 0x46000000
MAX_FIT_BYTES = 128 * MIB
MAX_KERNEL_UNCOMPRESSED = 64 * MIB
E87N_MEMORY = struct.pack(">4I", 0, RAM_BASE, 0, RAM_BASE)
E87N_COMPATIBLE = b"edgepi,e87n"
EMMC_NODE = "/soc/mmc@11230000"
BOOTARGS_DEFAULT = (
    "console=ttyS0,115200n8 "
    "earlycon=uart8250,mmio32,0x11000000 "
    "loglevel=8 ignore_loglevel "
    "net.ifnames=0 consoleblank=0 panic=0"
)


@dataclass(frozen=True)
class Variant:
    """One experiment in the three-way loader matrix."""

    name: str
    kernel_load: int
    with_initrd: bool


VARIANTS = {
    "40000000-initrd": Variant("40000000-initrd", 0x40000000, True),
    "40080000-initrd": Variant("40080000-initrd", 0x40080000, True),
    "40000000-no-initrd": Variant("40000000-no-initrd", 0x40000000, False),
}


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path: Path, limit: int | None = None) -> Path:
    path = Path(path)
    require(not path.is_symlink(), f"symlink input is forbidden: {path}")
    info = path.stat()
    require(info.st_mode & 0o170000 == 0o100000 and info.st_size > 0,
            f"need a non-empty regular file: {path}")
    if limit is not None:
        require(info.st_size <= limit, f"file exceeds limit ({limit}): {path}")
    return path.resolve()


def arm64_image_header(raw: bytes) -> dict[str, int]:
    require(len(raw) >= 64 and raw[56:60] == b"ARM\x64",
            "kernel payload is not an ARM64 Linux Image")
    text_offset, image_size, flags = struct.unpack_from("<QQQ", raw, 8)
    require(text_offset == 0, f"unsupported ARM64 text_offset: 0x{text_offset:x}")
    require(0 < image_size <= MAX_KERNEL_UNCOMPRESSED,
            f"invalid ARM64 image_size: 0x{image_size:x}")
    require(len(raw) <= image_size, "kernel file is larger than its image_size")
    return {"text_offset": text_offset, "image_size": image_size, "flags": flags}


def is_lzma_alone(data: bytes) -> bool:
    return len(data) >= 13 and data[:5] == b"\x6d\x00\x00\x80\x00"


def decompress_kernel(data: bytes, compression: str) -> bytes:
    if compression == "none":
        return data
    require(compression == "lzma", f"unsupported kernel compression: {compression}")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=128 * MIB)
    raw = decoder.decompress(data, max_length=MAX_KERNEL_UNCOMPRESSED + 1)
    require(decoder.eof and not decoder.unused_data,
            "LZMA kernel did not terminate cleanly")
    require(len(raw) <= MAX_KERNEL_UNCOMPRESSED,
            "decompressed kernel exceeds diagnostic limit")
    return raw


def compress_kernel(raw: bytes) -> bytes:
    """Use the same deterministic LZMA-Alone profile as the factory FIT."""
    arm64_image_header(raw)
    packed = lzma.compress(
        raw,
        format=lzma.FORMAT_ALONE,
        filters=[{
            "id": lzma.FILTER_LZMA1,
            "dict_size": 8 * MIB,
            "lc": 1,
            "lp": 2,
            "pb": 2,
        }],
    )
    # The vendor loader expects the real uncompressed length in the LZMA
    # header rather than the all-ones "unknown" value.
    return packed[:5] + len(raw).to_bytes(8, "little") + packed[13:]


def normalize_kernel(path: Path, requested: str) -> tuple[bytes, str, dict[str, int]]:
    data = regular(path, MAX_KERNEL_UNCOMPRESSED)
    payload = data.read_bytes()
    if requested == "auto":
        requested = "lzma" if is_lzma_alone(payload) else "lzma"
    if requested == "none":
        require(not is_lzma_alone(payload),
                "--kernel-compression=none requires an uncompressed ARM64 Image")
        raw = payload
        compression = "none"
    elif requested == "lzma":
        if is_lzma_alone(payload):
            raw = decompress_kernel(payload, "lzma")
            compression = "lzma"
        else:
            raw = payload
            compression = "lzma"
            payload = compress_kernel(raw)
    else:
        raise ValueError(f"unsupported --kernel-compression: {requested}")
    header = arm64_image_header(raw)
    return payload, compression, header


def render_its(
    *,
    kernel_file: str,
    dtb_file: str,
    kernel_load: int,
    kernel_compression: str,
    with_initrd: bool,
    initrd_file: str | None,
    ramdisk_load: int,
    fdt_load: int,
) -> str:
    """Render a closed, embedded-data-only FIT source."""
    require(kernel_load >= RAM_BASE and kernel_load < RAM_END, "kernel load outside RAM")
    require(fdt_load >= RAM_BASE and fdt_load < RAM_END, "FDT load outside RAM")
    require(not with_initrd or initrd_file, "initrd variant needs an initrd file")
    images = [
        f'''kernel-1 {{
            description = "E87N RAM diagnostic ARM64 kernel";
            data = /incbin/("{kernel_file}");
            type = "kernel"; arch = "arm64"; os = "linux";
            compression = "{kernel_compression}";
            load = <0x{kernel_load:x}>; entry = <0x{kernel_load:x}>;
            hash-1 {{ algo = "sha256"; }};
        }};''',
    ]
    if with_initrd:
        images.append(
            f'''ramdisk-1 {{
            description = "E87N RAM-only diagnostic root; eMMC disabled";
            data = /incbin/("{initrd_file}");
            type = "ramdisk"; arch = "arm64"; os = "linux";
            compression = "none"; load = <0x{ramdisk_load:x}>;
            hash-1 {{ algo = "sha256"; }};
        }};'''
        )
    images.append(
        f'''fdt-1 {{
            description = "E87N RAM diagnostic DTB; eMMC disabled";
            data = /incbin/("{dtb_file}");
            type = "flat_dt"; arch = "arm64"; compression = "none";
            load = <0x{fdt_load:x}>;
            hash-1 {{ algo = "sha256"; }};
        }};'''
    )
    references = ["kernel = \"kernel-1\";"]
    if with_initrd:
        references.append('ramdisk = "ramdisk-1";')
    references.append('fdt = "fdt-1";')
    return (
        "/dts-v1/;\n"
        "/ {\n"
        '    description = "E87N RAM-only FIT diagnostic; NEVER install";\n'
        "    #address-cells = <1>;\n"
        "    images {\n"
        + "\n".join(images)
        + "\n    };\n"
        '    configurations { default = "conf-1";\n'
        '        conf-1 { description = "E87N RAM diagnostic";\n'
        + "            " + "\n            ".join(references)
        + "\n        };\n    };\n};\n"
    )


def _prop(props: dict[tuple[str, str], bytes], node: str, name: str) -> bytes:
    require((node, name) in props, f"FIT missing {node}:{name}")
    return props[(node, name)]


def _string(value: bytes, label: str) -> str:
    require(value.endswith(b"\0"), f"{label} is not a NUL-terminated string")
    return value[:-1].decode("ascii")


def audit_fit(
    data: bytes,
    *,
    variant: Variant,
    kernel_compression: str,
    expected_kernel_sha256: str,
    expected_initrd_sha256: str | None,
    ramdisk_load: int = RAMDISK_LOAD,
    fdt_load: int = FDT_LOAD,
) -> dict[str, Any]:
    """Audit one FIT and return a JSON-safe evidence record."""
    require(0 < len(data) <= MAX_FIT_BYTES, "FIT exceeds diagnostic upload limit")
    props = fdt(data)
    total, = struct.unpack_from(">I", data, 4)
    require(total <= len(data) and not any(data[total:]),
            "FIT has non-zero bytes outside its FDT total size")
    require(_prop(props, "/configurations", "default") == b"conf-1\0",
            "wrong FIT default configuration")
    image_nodes = {
        node for node, _ in props
        if node.startswith("/images/") and node.count("/") == 2
    }
    expected_nodes = {"/images/kernel-1", "/images/fdt-1"}
    if variant.with_initrd:
        expected_nodes.add("/images/ramdisk-1")
    require(image_nodes == expected_nodes, f"unexpected FIT image nodes: {image_nodes}")
    conf_keys = {name for node, name in props if node == "/configurations/conf-1"}
    allowed_conf = {"description", "kernel", "fdt", "ramdisk"}
    require(conf_keys <= allowed_conf, f"unexpected FIT configuration keys: {conf_keys}")
    require(_string(_prop(props, "/configurations/conf-1", "kernel"), "kernel ref") == "kernel-1",
            "wrong kernel configuration reference")
    require(_string(_prop(props, "/configurations/conf-1", "fdt"), "FDT ref") == "fdt-1",
            "wrong FDT configuration reference")
    if variant.with_initrd:
        require(_string(_prop(props, "/configurations/conf-1", "ramdisk"), "ramdisk ref") == "ramdisk-1",
                "wrong ramdisk configuration reference")
    else:
        require("ramdisk" not in conf_keys, "no-initrd FIT still references a ramdisk")

    payloads: dict[str, bytes] = {}
    specs = [("kernel", "kernel", kernel_compression, variant.kernel_load),
             ("fdt", "flat_dt", "none", fdt_load)]
    if variant.with_initrd:
        specs.insert(1, ("ramdisk", "ramdisk", "none", ramdisk_load))
    for name, kind, compression, load in specs:
        node = f"/images/{name}-1"
        require(_string(_prop(props, node, "type"), f"{name} type") == kind,
                f"wrong {name} FIT type")
        require(_string(_prop(props, node, "arch"), f"{name} arch") == "arm64",
                f"wrong {name} architecture")
        require(_string(_prop(props, node, "compression"), f"{name} compression") == compression,
                f"wrong {name} compression")
        require(_prop(props, node, "load") == struct.pack(">I", load),
                f"wrong {name} load address")
        require(not any((node, key) in props for key in ("data-offset", "data-position", "data-size")),
                f"external FIT data forbidden for {name}")
        payload = _prop(props, node, "data")
        require(payload, f"empty {name} payload")
        require(_string(_prop(props, f"{node}/hash-1", "algo"), f"{name} hash") == "sha256",
                f"{name} is not SHA-256 protected")
        require(_prop(props, f"{node}/hash-1", "value") == bytes.fromhex(sha256_bytes(payload)),
                f"{name} hash mismatch")
        payloads[name] = payload
    require(_prop(props, "/images/kernel-1", "entry") == struct.pack(">I", variant.kernel_load),
            "wrong kernel entry address")
    require(sha256_bytes(payloads["kernel"]) == expected_kernel_sha256,
            "kernel payload hash differs from the build input")
    raw_kernel = decompress_kernel(payloads["kernel"], kernel_compression)
    header = arm64_image_header(raw_kernel)
    require(variant.kernel_load + header["image_size"] <= ramdisk_load if variant.with_initrd else
            variant.kernel_load + header["image_size"] < fdt_load,
            "kernel image overlaps a diagnostic payload")
    if variant.with_initrd:
        require(expected_initrd_sha256 is not None, "missing expected initrd hash")
        require(sha256_bytes(payloads["ramdisk"]) == expected_initrd_sha256,
                "ramdisk payload hash differs from the build input")
        require(not payloads["ramdisk"].startswith(b"\x27\x05\x19\x56"),
                "legacy uInitrd wrapper must not be nested in FIT")
        require(ramdisk_load + len(payloads["ramdisk"]) < fdt_load,
                "ramdisk overlaps FDT")
    require(fdt_load + len(payloads["fdt"]) < FIT_UPLOAD_BUFFER,
            "FDT overlaps the documented U-Boot upload buffer")

    dt = fdt(payloads["fdt"])
    compatible = _prop(dt, "/", "compatible")
    require(E87N_COMPATIBLE in compatible.split(b"\0"), "DTB is not for EdgePi E87N")
    require(_prop(dt, "/memory", "reg") == E87N_MEMORY,
            "diagnostic DTB must describe the measured 1 GiB RAM window")
    require(_prop(dt, EMMC_NODE, "status") == b"disabled\0",
            "diagnostic DTB must disable eMMC")
    bootargs = _string(_prop(dt, "/chosen", "bootargs"), "bootargs")
    args = bootargs.split()
    require("root=" not in args and "rootwait" not in args and "rw" not in args,
            "RAM-only diagnostic bootargs must not mount a block root")
    require("console=ttyS0,115200n8" in args and
            "earlycon=uart8250,mmio32,0x11000000" in args and
            "panic=0" in args,
            "diagnostic bootargs lack serial console or panic=0")
    if variant.with_initrd:
        require("rdinit=/init" in args, "initrd variant must use rdinit=/init")
    else:
        require(not any(token.startswith("rdinit=") for token in args),
                "no-initrd variant must not refer to rdinit")
    return {
        "bytes": len(data),
        "sha256": sha256_bytes(data),
        "variant": variant.name,
        "kernel_load": f"0x{variant.kernel_load:08x}",
        "kernel_entry": f"0x{variant.kernel_load:08x}",
        "kernel_compression": kernel_compression,
        "kernel_payload_sha256": sha256_bytes(payloads["kernel"]),
        "kernel_uncompressed_bytes": len(raw_kernel),
        "kernel_image_size": header["image_size"],
        "ramdisk": variant.with_initrd,
        "ramdisk_load": f"0x{ramdisk_load:08x}" if variant.with_initrd else None,
        "ramdisk_bytes": len(payloads["ramdisk"]) if variant.with_initrd else 0,
        "ramdisk_sha256": sha256_bytes(payloads["ramdisk"]) if variant.with_initrd else None,
        "fdt_load": f"0x{fdt_load:08x}",
        "fdt_bytes": len(payloads["fdt"]),
        "fdt_sha256": sha256_bytes(payloads["fdt"]),
        "emmc_disabled": True,
        "bootargs": bootargs,
        "hardware_validation": "pending",
    }


def variant_from_name(name: str) -> Variant:
    try:
        return VARIANTS[name]
    except KeyError as error:
        choices = ", ".join(VARIANTS)
        raise ValueError(f"unknown RAM diagnostic variant {name!r}; choose {choices}") from error
