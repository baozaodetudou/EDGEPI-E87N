"""E87N 1 GiB / factory 8 GB eMMC firmware contract; no device writes.

Container: MediaTek parse_tar_image(), NOT an OpenWrt operating system.
Reference: Yuzhii0718/bl-mt798x-dhcpd@4d5f0ffe02c5410c545bfb3f4112346877c75a72
board/mediatek/common/{untar,mmc_helper}.c. Hardware acceptance remains separate.
"""
import contextlib
import hashlib
import json
import lzma
import os
from pathlib import Path
import stat
import struct
import subprocess
import tarfile

FORMAT = "e87n-uboot-firmware-tar-v1"
RELEASE = "6.18.51-current-filogic"
MIB = 1024 * 1024
KERNEL_LIMIT = 32 * MIB
# Conservative packaging policy, NOT a measured free-RAM guarantee. A running
# bootloader must still be checked before any upload. Upload starts at 0x46000000.
UPLOAD_LIMIT = 768 * MIB
ROOT_LIMIT = 15181791 * 512
LAYOUT = {"u-boot-env": [8192, 1024], "factory": [9216, 8192],
          "fip": [17408, 4096], "kernel": [21504, 65536],
          "rootfs": [87040, 15181791]}
LOADS = {"kernel": 0x40000000, "ramdisk": 0x44000000, "fdt": 0x45e00000}
PREFIX = "sysupgrade-edgepi-e87n/"
MEMORY = struct.pack(">4I", 0, 0x40000000, 0, 0x40000000)
RESERVATIONS = {"wmcpu-reserved@50000000": (0x50000000, 0x100000),
                "ramoops@7ff70000": (0x7ff70000, 0x10000),
                "secmon@7ff80000": (0x7ff80000, 0x80000)}


def bootargs(root_uuid):
    return ("console=ttyS0,115200n8 earlycon=uart8250,mmio32,0x11000000 "
            f"root=UUID={root_uuid} rootwait rootfstype=ext4 rw "
            "fsck.repair=yes net.ifnames=0 consoleblank=0")


def require(value, message):
    if not value:
        raise ValueError(message)


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path, limit=None):
    path = Path(path).absolute()
    require(not path.is_symlink(), "symlink input forbidden: " + str(path))
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size > 0,
            "need nonempty regular file, never a device: " + str(path))
    require(limit is None or info.st_size <= limit, "file exceeds limit: " + str(path))
    return path


def fdt(data):
    """Bounded, duplicate-rejecting DTB/FIT property reader; embedded data only."""
    require(len(data) >= 40, "short FDT")
    magic, total, off, strings_off, _, version, compat, _, strings_len, size = struct.unpack_from(
        ">10I", data)
    require(magic == 0xd00dfeed and 40 <= total <= len(data) and version == 17 and compat <= 17,
            "invalid FDT header")
    require(40 <= off < total and off + size <= total and 40 <= strings_off < total and
            strings_off + strings_len <= total and off + size <= strings_off, "invalid FDT bounds")
    block, strings = data[off:off + size], data[strings_off:strings_off + strings_len]
    cursor, stack, props, nodes = 0, [], {}, set()
    while cursor + 4 <= len(block):
        token, = struct.unpack_from(">I", block, cursor)
        cursor += 4
        if token == 1:
            end = block.find(b"\0", cursor)
            require(end >= cursor, "unterminated FDT node")
            name = block[cursor:end].decode("ascii")
            require("/" not in name and (bool(stack) or name == ""), "invalid FDT node")
            stack.append(name)
            path = "/" + "/".join(stack[1:])
            require(path not in nodes, "duplicate FDT node")
            nodes.add(path)
            cursor = (end + 4) & ~3
        elif token == 2:
            require(stack, "unbalanced FDT")
            stack.pop()
        elif token == 3:
            require(stack and cursor + 8 <= len(block), "invalid FDT property")
            length, nameoff = struct.unpack_from(">II", block, cursor)
            cursor += 8
            end = strings.find(b"\0", nameoff)
            require(nameoff < len(strings) and end >= nameoff and cursor + length <= len(block),
                    "FDT property outside bounds")
            key = ("/" + "/".join(stack[1:]), strings[nameoff:end].decode("ascii"))
            require(key not in props, "duplicate FDT property")
            props[key] = block[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            require(not stack and "/" in nodes, "unfinished FDT")
            return props
        else:
            raise ValueError("unknown FDT token")
    raise ValueError("missing FDT end")


def check_fit(data, root_uuid):
    require(0 < len(data) <= KERNEL_LIMIT and len(data) % 512 == 0, "FIT size/alignment")
    props = fdt(data)
    reserve, = struct.unpack_from(">I", data, 16)
    structure, = struct.unpack_from(">I", data, 8)
    require(40 <= reserve and reserve % 8 == 0 and reserve + 16 <= structure and
            not any(data[reserve:structure]), "outer FIT reserve map must be empty")
    configurations = {node for node, _ in props if node.startswith("/configurations/")}
    require(configurations == {"/configurations/conf-1"}, "extra FIT configurations forbidden")
    require({key for node, key in props if node == "/configurations/conf-1"} <=
            {"description", "kernel", "ramdisk", "fdt"}, "extra FIT loadables/firmware forbidden")
    require({node.split("/")[2] for node, _ in props if node.startswith("/images/")} ==
            {"kernel-1", "ramdisk-1", "fdt-1"}, "extra FIT images forbidden")
    total, = struct.unpack_from(">I", data, 4)
    require(not any(data[total:]), "nonzero external FIT data/padding forbidden")
    require(props.get(("/configurations", "default")) == b"conf-1\0", "wrong FIT default")
    payloads = {}
    for name, kind, compression in (("kernel", "kernel", "lzma"),
                                     ("ramdisk", "ramdisk", "none"),
                                     ("fdt", "flat_dt", "none")):
        node = "/images/" + name + "-1"
        require(props.get(("/configurations/conf-1", name)) == (name + "-1\0").encode(),
                "FIT configuration reference mismatch: " + name)
        for key, value in (("type", kind), ("arch", "arm64"), ("compression", compression)):
            require(props.get((node, key)) == (value + "\0").encode(), "wrong FIT " + name + ":" + key)
        require(props.get((node, "load")) == struct.pack(">I", LOADS[name]), "wrong FIT load: " + name)
        if name != "fdt":
            require(props.get((node, "os")) == b"linux\0", "wrong FIT OS")
        require(not any((node, key) in props for key in ("data-offset", "data-position", "data-size")),
                "external FIT data forbidden")
        payload = props.get((node, "data"), b"")
        require(payload and props.get((node + "/hash-1", "algo")) == b"sha256\0" and
                props.get((node + "/hash-1", "value")) == hashlib.sha256(payload).digest(),
                "FIT SHA256 mismatch: " + name)
        payloads[name] = payload
    require(props.get(("/images/kernel-1", "entry")) == struct.pack(">I", LOADS["kernel"]),
            "wrong kernel entry")
    # Known original E87N LZMA properties: lc=1, lp=2, pb=2, 8 MiB dictionary.
    packed = payloads["kernel"]
    require(len(packed) >= 13 and packed[:5] == b"\x6d\x00\x00\x80\x00", "LZMA properties mismatch")
    maximum = 0x41e00000 - LOADS["kernel"]
    require(int.from_bytes(packed[5:13], "little") <= maximum, "kernel overlaps loader text")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=32 * MIB)
    raw = decoder.decompress(packed, max_length=maximum + 1)
    require(decoder.eof and not decoder.unused_data and len(raw) <= maximum and
            len(raw) == int.from_bytes(packed[5:13], "little"), "invalid/big LZMA kernel")
    require(len(raw) >= 64 and raw[56:60] == b"ARM\x64", "not ARM64 Linux Image")
    text_offset, memory_size, flags = struct.unpack_from("<QQQ", raw, 8)
    require(text_offset == 0 and (LOADS["kernel"] - text_offset) % (2 * MIB) == 0,
            "kernel text_offset/load must follow ARM64 2 MiB alignment")
    require(not flags & 1 and len(raw) <= memory_size <= maximum,
            "kernel effective image_size/endian exceeds reviewed RAM window")
    require(len(payloads["ramdisk"]) < LOADS["fdt"] - LOADS["ramdisk"], "initrd overlaps FDT")
    require(not payloads["ramdisk"].startswith(b"\x27\x05\x19\x56"), "nested legacy uInitrd forbidden")
    require(len(payloads["fdt"]) < 0x46000000 - LOADS["fdt"], "FDT overlaps upload buffer")
    dt = fdt(payloads["fdt"])
    require(b"edgepi,e87n\0" in dt.get(("/", "compatible"), b""), "wrong board DTB")
    require(dt.get(("/memory", "reg")) == MEMORY, "DTB must describe measured 1 GiB RAM")
    for name, (address, length) in RESERVATIONS.items():
        node = "/reserved-memory/" + name
        require(dt.get((node, "reg")) == struct.pack(">4I", 0, address, 0, length),
                "missing firmware RAM reservation: " + name)
        if name != "ramoops@7ff70000":
            require(dt.get((node, "no-map")) == b"", "secure/WM reservation lacks no-map")
    args = dt.get(("/chosen", "bootargs"), b"").rstrip(b"\0").decode("ascii").split()
    for key, expected in (("root=", "root=UUID=" + root_uuid), ("rootfstype=", "rootfstype=ext4")):
        require([s for s in args if s.startswith(key)] == [expected], "wrong root command line")
    require("rw" in args and "rootwait" in args and "console=ttyS0,115200n8" in args,
            "incomplete bootargs")
    require(args == bootargs(root_uuid).split(), "unexpected bootargs/init override")
    return payloads


def ext4_size(path):
    with Path(path).open("rb") as stream:
        stream.seek(1024)
        sb = stream.read(1024)
    require(len(sb) == 1024 and sb[56:58] == b"\x53\xef", "root is not ext4")
    blocks = int.from_bytes(sb[4:8], "little")
    incompat = int.from_bytes(sb[96:100], "little")
    if incompat & 0x80:
        blocks |= int.from_bytes(sb[336:340], "little") << 32
    log = int.from_bytes(sb[24:28], "little")
    require(log == 2 and blocks > 0, "factory root must use 4096-byte ext4 blocks")
    return blocks * (1024 << log)


def inspect_tar(path, work):
    path = regular(path, UPLOAD_LIMIT)
    require(path.stat().st_size % 512 == 0, "TAR alignment")
    paths = {}
    with tarfile.open(path, "r:") as archive:
        members = []
        # Do not materialize millions of member objects from a hostile TAR.
        for _ in range(4):
            member = archive.next()
            if member is None:
                break
            members.append(member)
        require(len(members) == 3, "TAR must contain exactly three members")
        require([m.name for m in members] == [PREFIX + n for n in ("kernel", "root", "CONTROL")],
                "TAR must contain exactly kernel, root, CONTROL in canonical order")
        for member in members:
            name = member.name[len(PREFIX):]
            require(member.isfile() and not member.pax_headers and 0 < member.size <= ROOT_LIMIT,
                    "nonregular/extended/empty TAR member")
            with path.open("rb") as stream:
                stream.seek(member.offset)
                header = stream.read(512)
            require(header[257:263] == b"ustar\0" and header[156:157] == b"0" and
                    header[345:500] == bytes(155), "need simple USTAR for vendor parser")
            require(header[:100].split(b"\0", 1)[0].decode() == member.name,
                    "vendor TAR name differs from host TAR name")
            limit = KERNEL_LIMIT if name == "kernel" else (65536 if name == "CONTROL" else ROOT_LIMIT)
            require(member.size <= limit, "TAR member too large")
            target = work / name
            with archive.extractfile(member) as source, target.open("xb") as dest:
                while chunk := source.read(MIB):
                    dest.write(chunk)
            paths[name] = target
        end = members[-1].offset_data + ((members[-1].size + 511) // 512) * 512
    with path.open("rb") as stream:
        stream.seek(end)
        tail_size = 0
        while chunk := stream.read(MIB):
            require(not any(chunk), "trailing/noncanonical TAR data")
            tail_size += len(chunk)
    require(tail_size >= 1024, "truncated TAR end marker")
    control = json.loads(paths["CONTROL"].read_text())
    require(type(control.get("headless", False)) is bool, "invalid headless firmware profile")
    require(control.get("format") == FORMAT and control.get("layout") == LAYOUT and
            control.get("kernel_release") == RELEASE and control.get("hardware_validation") == "pending",
            "wrong firmware manifest contract")
    for name in ("kernel", "root"):
        require(control["payloads"][name] == {"bytes": paths[name].stat().st_size, "sha256": sha(paths[name])},
                "firmware payload checksum/size mismatch: " + name)
    root_size = paths["root"].stat().st_size
    require(root_size == ext4_size(paths["root"]), "root payload must end at exact filesystem boundary")
    # Vendor erases 512 KiB after the rootfs, aligned to 64 KiB. Never let this
    # erase touch filesystem blocks or exceed the existing rootfs partition.
    require(((root_size + 65535) // 65536) * 65536 + 512 * 1024 <= ROOT_LIMIT,
            "missing vendor trailing-erase guard")
    payloads = check_fit(paths["kernel"].read_bytes(), control["root_uuid"])
    return paths, control, payloads


def check_vendor_parser(path, work):
    """Run unmodified vendor C parser after strict host validation, never flash."""
    source = Path(__file__).resolve().parents[1] / "tests/vendor-untar"
    for name, digest in (("untar.c", "2a4e02c9ab41e4e8c5910aaed581765477563d627754eb8cdd1600af74467c97"),
                         ("untar.h", "08783dc4981fd9eeb1fbd933828e5bfc7ca3ac303d74a9ba84fe18e3f7b656d9")):
        require(sha(source / name) == digest, "vendor parser source changed")
    binary = work / "vendor-untar-test"
    run("cc", "-std=gnu11", "-O2", "-I", source / "compat", "-I", source,
        source / "untar.c", source / "main.c", "-o", binary)
    actual = [int(s) for s in run(binary, path, capture_output=True, text=True).stdout.split()]
    with tarfile.open(path, "r:") as archive:
        kernel, root = [archive.getmember(PREFIX + name) for name in ("kernel", "root")]
    require(actual == [kernel.offset_data, kernel.size, root.offset_data, root.size],
            "vendor C parser and strict host parser disagree")
    print("PASS: unmodified MediaTek C parser selects exactly the intended kernel/root payloads")


@contextlib.contextmanager
def mounted(image, target, *, readonly=True, offset=0, size=None):
    """Allocate a new loop only. Never accept a caller's block device."""
    image = regular(image)
    target.mkdir()
    args = ["losetup", "--find", "--show"]
    if readonly:
        args.append("--read-only")
    if offset:
        args += ["--offset", str(offset)]
    if size:
        args += ["--sizelimit", str(size)]
    loop = run(*args, image, capture_output=True, text=True).stdout.strip()
    require(loop.startswith("/dev/loop") and loop[9:].isdigit(), "unexpected owned loop")
    mounted_ok = False
    try:
        options = "ro,noload,nodev,nosuid,noexec" if readonly else "rw,nodev,nosuid,noexec"
        run("mount", "-t", "ext4", "-o", options, loop, target)
        mounted_ok = True
        yield target
    finally:
        if mounted_ok:
            actual = run("findmnt", "-n", "-o", "SOURCE", "--mountpoint", target,
                         capture_output=True, text=True).stdout.strip()
            require(actual == loop, "refusing to unmount unowned source")
            run("umount", target)  # Failure retains loop for manual recovery.
        run("losetup", "-d", loop)
