#!/usr/bin/env python3
"""Small host fixtures only; never mount, connect, or write a block device."""
import hashlib
import io
import json
import lzma
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import factory_firmware as fw

UUID = "ee0d711d-5cce-4fa5-9425-50d0bcb26096"


def dtb(properties):
    """Minimal FDT fixture encoder, independent of mkimage/fdtput."""
    tree, strings = {}, bytearray()
    offsets = {}
    for (path, key), value in properties.items():
        node = tree
        for part in path.strip("/").split("/") if path != "/" else []:
            node = node.setdefault(part, {})
        node[key] = value
        if key not in offsets:
            offsets[key] = len(strings)
            strings += key.encode() + b"\0"
    block = bytearray()

    def emit(name, node):
        block.extend(struct.pack(">I", 1) + name.encode() + b"\0")
        block.extend(bytes((-len(block)) % 4))
        for key, val in node.items():
            if isinstance(val, bytes):
                block.extend(struct.pack(">III", 3, len(val), offsets[key]) + val)
                block.extend(bytes((-len(block)) % 4))
        for key, val in node.items():
            if isinstance(val, dict):
                emit(key, val)
        block.extend(struct.pack(">I", 2))
    emit("", tree)
    block.extend(struct.pack(">I", 9))
    total = 56 + len(block) + len(strings)
    return struct.pack(">10I", 0xd00dfeed, total, 56, 56 + len(block), 40, 17, 16, 0,
                       len(strings), len(block)) + bytes(16) + block + strings


def fixture(dt_change=None, fit_change=None):
    dt = {("/", "compatible"): b"edgepi,e87n\0mediatek,mt7987a\0",
          ("/memory", "reg"): fw.MEMORY,
          ("/chosen", "bootargs"): (fw.bootargs(UUID) + "\0").encode()}
    for name, (address, length) in fw.RESERVATIONS.items():
        dt[("/reserved-memory/" + name, "reg")] = struct.pack(">4I", 0, address, 0, length)
        if not name.startswith("ramoops"):
            dt[("/reserved-memory/" + name, "no-map")] = b""
    if dt_change:
        dt_change(dt)
    raw = bytes(8) + struct.pack("<QQQ", 0, 64, 10) + bytes(24) + b"ARM\x64" + bytes(4)
    packed = lzma.compress(raw, format=lzma.FORMAT_ALONE, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": 8 * fw.MIB, "lc": 1, "lp": 2, "pb": 2}])
    packed = packed[:5] + len(raw).to_bytes(8, "little") + packed[13:]
    payloads = {"kernel": packed, "ramdisk": b"070701fixture", "fdt": dtb(dt)}
    props = {("/configurations", "default"): b"conf-1\0"}
    for name, kind, compression in (("kernel", "kernel", "lzma"), ("ramdisk", "ramdisk", "none"), ("fdt", "flat_dt", "none")):
        node = "/images/" + name + "-1"
        props[("/configurations/conf-1", name)] = (name + "-1\0").encode()
        for key, value in (("type", kind), ("arch", "arm64"), ("compression", compression)):
            props[(node, key)] = (value + "\0").encode()
        props[(node, "load")] = struct.pack(">I", fw.LOADS[name])
        if name != "fdt":
            props[(node, "os")] = b"linux\0"
        props[(node, "data")] = payloads[name]
        props[(node + "/hash-1", "algo")] = b"sha256\0"
        props[(node + "/hash-1", "value")] = hashlib.sha256(payloads[name]).digest()
    props[("/images/kernel-1", "entry")] = struct.pack(">I", fw.LOADS["kernel"])
    if fit_change:
        fit_change(props)
    result = dtb(props)
    return result + bytes((-len(result)) % 512)


class FitTests(unittest.TestCase):
    def test_valid_fit(self):
        self.assertEqual(set(fw.check_fit(fixture(), UUID)), {"kernel", "ramdisk", "fdt"})

    def test_wrong_memory(self):
        with self.assertRaisesRegex(ValueError, "1 GiB"):
            fw.check_fit(fixture(lambda p: p.update({("/memory", "reg"): struct.pack(">4I", 0, 0x40000000, 0, 0x10000000)})), UUID)

    def test_missing_secure_reservation(self):
        with self.assertRaisesRegex(ValueError, "reservation"):
            fw.check_fit(fixture(lambda p: p.pop(("/reserved-memory/secmon@7ff80000", "reg"))), UUID)

    def test_wrong_root_and_duplicate_root(self):
        for args in ("root=PARTLABEL=rootfs", f"root=UUID={UUID} root=/dev/mmcblk0p2"):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "root command"):
                fw.check_fit(fixture(lambda p: p.update({("/chosen", "bootargs"): (args + "\0").encode()})), UUID)

    def test_hash_corruption(self):
        with self.assertRaisesRegex(ValueError, "SHA256"):
            fw.check_fit(fixture(fit_change=lambda p: p.update({("/images/kernel-1/hash-1", "value"): bytes(32)})), UUID)

    def test_unexpected_init_override(self):
        with self.assertRaisesRegex(ValueError, "init override"):
            fw.check_fit(fixture(lambda p: p.update({("/chosen", "bootargs"):
                (fw.bootargs(UUID) + " init=/bin/sh\0").encode()})), UUID)

    def test_external_data(self):
        with self.assertRaisesRegex(ValueError, "external"):
            fw.check_fit(fixture(fit_change=lambda p: p.update({("/images/kernel-1", "data-position"): bytes(4)})), UUID)

    def test_extra_loadables_and_configurations(self):
        for prop in (("/configurations/conf-1", "loadables"), ("/configurations/conf-2", "kernel")):
            with self.subTest(prop=prop), self.assertRaisesRegex(ValueError, "extra FIT"):
                fw.check_fit(fixture(fit_change=lambda p: p.update({prop: b"kernel-1\0"})), UUID)

    def test_outer_reservation_map_rejected(self):
        data = bytearray(fixture())
        data[40] = 1
        with self.assertRaisesRegex(ValueError, "reserve map"):
            fw.check_fit(data, UUID)

    def test_effective_image_size_and_offset(self):
        for offset, size in ((0, 0), (0, 64 * fw.MIB), (0x80000, 64)):
            def change(props):
                node = "/images/kernel-1"
                raw = bytearray(lzma.decompress(props[node, "data"]))
                struct.pack_into("<QQ", raw, 8, offset, size)
                packed = lzma.compress(raw, format=lzma.FORMAT_ALONE, filters=[{
                    "id": lzma.FILTER_LZMA1, "dict_size": 8 * fw.MIB, "lc": 1, "lp": 2, "pb": 2}])
                packed = packed[:5] + len(raw).to_bytes(8, "little") + packed[13:]
                props[node, "data"] = packed
                props[node + "/hash-1", "value"] = hashlib.sha256(packed).digest()
            with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                fw.check_fit(fixture(fit_change=change), UUID)

    def test_wrong_load_address(self):
        with self.assertRaisesRegex(ValueError, "load"):
            fw.check_fit(fixture(fit_change=lambda p: p.update({("/images/ramdisk-1", "load"): bytes(4)})), UUID)

    def test_oversize_and_unaligned(self):
        for data in (fixture()[:-1], bytes(fw.KERNEL_LIMIT + 512)):
            with self.assertRaisesRegex(ValueError, "size/alignment"):
                fw.check_fit(data, UUID)

    def test_invalid_fdt_bounds(self):
        data = bytearray(fixture())
        struct.pack_into(">I", data, 8, 0xffffffff)
        with self.assertRaisesRegex(ValueError, "bounds"):
            fw.check_fit(data, UUID)


class TarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e87n-firmware-test-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.root = bytearray(65536)
        self.root[1028:1032] = struct.pack("<I", 16)
        self.root[1048:1052] = struct.pack("<I", 2)
        self.root[1080:1082] = b"\x53\xef"

    def archive(self, *, names=None, change=None, root=None):
        payloads = {"kernel": fixture(), "root": bytes(self.root if root is None else root)}
        control = {"format": fw.FORMAT, "layout": fw.LAYOUT, "kernel_release": fw.RELEASE,
                   "root_uuid": UUID, "hardware_validation": "pending",
                   "payloads": {n: {"bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()} for n, v in payloads.items()}}
        if change:
            change(control)
        payloads["CONTROL"] = json.dumps(control).encode()
        target = self.work / "firmware.tar"
        with tarfile.open(target, "w:", format=tarfile.USTAR_FORMAT) as archive:
            for name in (names or [fw.PREFIX + n for n in payloads]):
                data = payloads.get(name.split("/")[-1], b"forbidden")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return target

    def inspect(self, path):
        dest = self.work / "extract"
        dest.mkdir()
        return fw.inspect_tar(path, dest)

    def test_valid_container(self):
        paths, control, _ = self.inspect(self.archive())
        self.assertEqual(paths["root"].stat().st_size, 65536)
        self.assertEqual(control["layout"]["kernel"], [21504, 65536])

    def test_unmodified_vendor_parser_selects_same_payloads(self):
        target = self.archive()
        self.inspect(target)
        fw.check_vendor_parser(target, self.work)

    def test_forbid_bootloader_gpt_paths(self):
        for name in ("gpt", "fip", "u-boot-env", "../root", "kernel/extra"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.inspect(self.archive(names=[fw.PREFIX + name]))
            (self.work / "extract").rmdir()

    def test_manifest_mismatch(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.inspect(self.archive(change=lambda c: c["payloads"]["root"].update(sha256="0" * 64)))

    def test_wrong_protected_layout(self):
        with self.assertRaisesRegex(ValueError, "contract"):
            self.inspect(self.archive(change=lambda c: c.update(layout={})))

    def test_root_boundary_must_be_exact(self):
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.inspect(self.archive(root=self.root + bytes(512)))

    def test_root_block_size_matches_boot_helper(self):
        self.root[1048:1052] = bytes(4)
        with self.assertRaisesRegex(ValueError, "4096"):
            self.inspect(self.archive())

    def test_corrupt_tar_header(self):
        target = self.archive()
        with target.open("r+b") as stream:
            stream.write(b"x")
        with self.assertRaises((ValueError, tarfile.TarError)):
            self.inspect(target)

    def test_trailing_hidden_data(self):
        target = self.archive()
        with target.open("ab") as stream:
            stream.write(b"x" * 512)
        with self.assertRaisesRegex(ValueError, "trailing"):
            self.inspect(target)

    def test_upload_limit_rejects_sparse_file(self):
        target = self.work / "big.tar"
        with target.open("wb") as stream:
            stream.truncate(fw.UPLOAD_LIMIT + 512)
        with self.assertRaisesRegex(ValueError, "limit"):
            self.inspect(target)

    def test_devices_and_symlinks_rejected(self):
        with self.assertRaises(ValueError):
            fw.regular(Path("/dev/null"))
        target = self.archive()
        alias = self.work / "alias.tar"
        alias.symlink_to(target)
        with self.assertRaises(ValueError):
            fw.regular(alias)


if __name__ == "__main__":
    unittest.main()
