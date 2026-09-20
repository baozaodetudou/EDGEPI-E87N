"""E87N 1 GiB / factory 8 GB eMMC firmware contract; no device writes.

Container: MediaTek parse_tar_image(), NOT an OpenWrt operating system.
Reference: Yuzhii0718/bl-mt798x-dhcpd@4d5f0ffe02c5410c545bfb3f4112346877c75a72
board/mediatek/common/{untar,mmc_helper}.c. Hardware acceptance remains separate.
"""
import contextlib
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import tarfile
import tempfile
import zlib
from build_config import BUILD, KERNEL_RELEASE

FORMAT = "e87n-uboot-firmware-tar-v2"
RELEASE = KERNEL_RELEASE
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
CONTROL_LIMIT = 2 * MIB
REPO = Path(__file__).resolve().parents[1]
MODULE_LIMIT = 64 * MIB
PROVENANCE = "usr/share/e87n/build-provenance.json"
BUILD_RECIPE = "usr/share/e87n/build-recipe.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field: " + key)
        result[key] = value
    return result


def read_json(data):
    return json.loads(data, object_pairs_hook=json_object,
                      parse_constant=lambda value: require(False, "nonfinite JSON: " + value))


def relative_path(name):
    require(isinstance(name, str) and name and not name.startswith("/") and
            all(part not in ("", ".", "..") for part in name.split("/")) and
            not any(c.isspace() or ord(c) < 32 for c in name), "unsafe relative path: " + repr(name))
    return name


def rooted(root, name):
    """Resolve target symlinks in the image, including Debian's merged /usr."""
    root = Path(root).resolve(strict=True)
    pending, parts, hops = str(name).split("/"), [], 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            require(parts, "target symlink escapes image: " + str(name))
            parts.pop()
            continue
        candidate = root.joinpath(*parts, part)
        if candidate.is_symlink():
            hops += 1
            require(hops <= 40, "target symlink loop: " + str(name))
            link = os.readlink(candidate)
            if link.startswith("/"):
                parts = []
            pending = link.split("/") + pending
        else:
            parts.append(part)
    return root.joinpath(*parts)


def file_record(path):
    path = regular(path)
    return {"bytes": path.stat().st_size, "sha256": sha(path)}


def tree_record(directory):
    """Bind every inode path, mode and payload/link without following directory links."""
    require(directory.is_dir() and not directory.is_symlink(), "missing inventory directory: " + str(directory))
    records = {}
    for folder, dirs, files in os.walk(directory, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(folder) / name
            info = path.lstat()
            record = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
            if stat.S_ISLNK(info.st_mode):
                record.update(type="link", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                record.update(type="directory")
            else:
                require(stat.S_ISREG(info.st_mode), "special file in inventory: " + str(path))
                record.update(type="file", bytes=info.st_size, sha256=sha(path))
            records[path.relative_to(directory).as_posix()] = record
    return {"entries": len(records), "sha256": digest(canonical(records))}


def installed_packages(root):
    """Actual dpkg database, not package names or versions inferred from the recipe."""
    data = regular(rooted(root, "var/lib/dpkg/status"), 32 * MIB).read_text()
    packages, seen = [], set()
    for paragraph in data.split("\n\n"):
        fields = {}
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")):
                continue
            key, sep, value = line.partition(": ")
            if sep and key in ("Package", "Architecture", "Version", "Status"):
                require(key not in fields, "duplicate dpkg field: " + key)
                fields[key] = value
        if not fields:
            continue
        identity = (fields.get("Package"), fields.get("Architecture"))
        require(identity not in seen, "duplicate dpkg package record")
        seen.add(identity)
        if fields.get("Status", "").endswith(" ok installed"):
            require(all(fields.get(k) for k in ("Package", "Architecture", "Version")),
                    "incomplete installed dpkg record")
            packages.append({k.lower(): fields[k] for k in ("Package", "Architecture", "Version", "Status")})
    required = ["linux-image-current-" + BUILD["linux_family"], "linux-dtb-current-" + BUILD["linux_family"]]
    kernels = [p for p in packages if p["package"] in required]
    require(len(kernels) == 2 and {p["package"] for p in kernels} == set(required) and
            all(p["status"] == "hold ok installed" and p["architecture"] == "arm64" for p in kernels) and
            len({p["version"] for p in kernels}) == 1, "kernel/DTB packages must be held, arm64, matching versions")
    return sorted(packages, key=lambda p: (p["package"], p["architecture"]))


def module_bytes(path):
    data = regular(path, MODULE_LIMIT).read_bytes()
    if path.suffix == ".gz":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(MODULE_LIMIT + 1)
    elif path.suffix == ".xz":
        decoder = lzma.LZMADecompressor(memlimit=128 * MIB)
        data = decoder.decompress(data, max_length=MODULE_LIMIT + 1)
        require(decoder.eof and not decoder.unused_data, "invalid/oversize xz module")
    elif path.suffix == ".zst":
        # Host decompressor only; never invoke a target ELF or load a module.
        with subprocess.Popen(["zstd", "-d", "-q", "-c", str(path)], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            data = process.stdout.read(MODULE_LIMIT + 1)
            if len(data) > MODULE_LIMIT:
                process.kill()
            require(process.wait(timeout=30) == 0, "invalid/oversize zstd module")
    require(len(data) <= MODULE_LIMIT, "oversize decompressed module")
    return data


def module_info(path):
    data = module_bytes(path)
    require(len(data) >= 64 and data[:6] == b"\x7fELF\x02\x01" and
            struct.unpack_from("<HH", data, 16) == (1, 183), "module is not arm64 relocatable ELF: " + str(path))
    offset, = struct.unpack_from("<Q", data, 40)
    size, count, strings_index = struct.unpack_from("<HHH", data, 58)
    require(size == 64 and 0 < count <= 65535 and strings_index < count and
            offset >= 64 and offset + size * count <= len(data), "invalid ELF section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * size) for i in range(count)]
    def section_bytes(section):
        start, length = section[4:6]
        require(start + length <= len(data), "ELF section outside module")
        return data[start:start + length]
    strings = section_bytes(sections[strings_index])
    infos = []
    for section in sections:
        end = strings.find(b"\0", section[0])
        require(section[0] < len(strings) and end >= section[0], "invalid ELF section name")
        if strings[section[0]:end] == b".modinfo":
            infos.append(section_bytes(section))
    require(len(infos) == 1, "module needs one .modinfo section")
    result = {}
    for entry in infos[0].split(b"\0"):
        key, sep, value = entry.partition(b"=")
        if sep and key in (b"name", b"vermagic", b"depends"):
            key = key.decode()
            require(key not in result, "duplicate module metadata: " + key)
            result[key] = value.decode("ascii")
    require(all(k in result for k in ("name", "vermagic", "depends")), "missing module identity/vermagic/depends")
    require(result["vermagic"].split()[:1] == [RELEASE], "module vermagic differs from FIT release: " + str(path))
    return result


def module_name(path):
    match = re.fullmatch(r"(.+)\.ko(?:\.(?:xz|gz|zst))?", Path(path).name)
    require(match is not None, "unsupported module filename: " + path)
    return match[1].replace("-", "_")


def audit_modules(root, config):
    base = rooted(root, "lib/modules/" + RELEASE)
    if not base.is_dir():
        base = rooted(root, "usr/lib/modules/" + RELEASE)
    require(base.is_dir(), "matching kernel module directory missing")
    modules, infos, names = {}, {}, {}
    for folder, dirs, files in os.walk(base, followlinks=False):
        for filename in files:
            if ".ko" not in filename:
                continue
            path = Path(folder) / filename
            relative = path.relative_to(base).as_posix()
            relative_path(relative)
            name = module_name(relative)
            require(name not in names, "duplicate module name: " + name)
            info = module_info(path)
            require(info["name"].replace("-", "_") == name, "module name differs from filename")
            names[name], modules[relative], infos[relative] = relative, path, info
    require(modules and len({i["vermagic"] for i in infos.values()}) == 1, "modules have inconsistent vermagic")
    builtins = {module_name(relative_path(line)) for line in regular(base / "modules.builtin").read_text().splitlines() if line}
    deps = {}
    for line in regular(base / "modules.dep").read_text().splitlines():
        name, sep, tail = line.partition(":")
        require(sep and name in modules and name not in deps, "invalid/stale/duplicate modules.dep entry: " + name)
        values = tail.split()
        require(len(values) == len(set(values)) and all(v in modules for v in values), "missing module dependency: " + name)
        deps[name] = set(values)
    require(set(deps) == set(modules), "modules.dep does not cover installed modules")
    for path, info in infos.items():
        for dependency in filter(None, info["depends"].replace("-", "_").split(",")):
            require(dependency in builtins or dependency in names and names[dependency] in deps[path],
                    "module metadata dependency missing from modules.dep: " + dependency)
    # Include soft dependencies needed by modprobe (e.g. pre: crypto helpers).
    softdep = base / "modules.softdep"
    if softdep.exists():
        for line in softdep.read_text().splitlines():
            words = line.split("#", 1)[0].split()
            if not words:
                continue
            require(len(words) >= 3 and words[0] == "softdep", "invalid modules.softdep")
            owner = words[1].replace("-", "_")
            require(owner in names or owner in builtins, "unknown softdep owner")
            for dependency in words[2:]:
                if dependency in ("pre:", "post:"):
                    continue
                dependency = dependency.replace("-", "_")
                require(dependency in names or dependency in builtins, "missing soft dependency: " + dependency)
                if owner in names and dependency in names:
                    deps[names[owner]].add(names[dependency])
    for metadata in ("modules.alias", "modules.dep.bin", "modules.alias.bin"):
        regular(base / metadata)
    require(config.get("MEDIATEK_2P5GE_PHY") == "m" and config.get("MTK_NET_PHYLIB") in ("m", "y"),
            "required modular MT7987 PHY/shared library config missing")
    required = ["mtk_2p5ge"] + (["mtk_phy_lib"] if config["MTK_NET_PHYLIB"] == "m" else [])
    for name in required:
        require(name in names and names[name].startswith("kernel/drivers/net/phy/mediatek/"),
                "required PHY module missing/wrong path: " + name)
    for name in deps:
        module_closure(deps, name)
    return {"tree": tree_record(base), "count": len(modules),
            "vermagic": next(iter(infos.values()))["vermagic"]}, deps


def audit_initrd_listing(listing, deps):
    names = {name.removeprefix("./").rstrip("/") for name in listing.splitlines()}
    present = set()
    uncompressed = {re.sub(r"\.(xz|gz|zst)$", "", path): path for path in deps}
    for name in names:
        match = re.fullmatch(r"(?:usr/)?lib/modules/([^/]+)/(.+\.ko(?:\.(?:xz|gz|zst))?)", name)
        if match:
            key = re.sub(r"\.(xz|gz|zst)$", "", match[2])
            require(match[1] == RELEASE and key in uncompressed, "initrd has foreign/unknown module: " + name)
            present.add(uncompressed[key])
    for name in present:
        require(deps[name] <= present, "initrd missing module dependency for: " + name)
    require({"init", "scripts/local", "scripts/functions"} <= names, "initrd missing normal local root resolver")


def audit_initrd_modules(root, initrd, deps):
    """Parse newc in memory; compare module bytes, never extract cpio paths to disk."""
    limit = 256 * MIB
    data = regular(initrd, KERNEL_LIMIT).read_bytes()
    config = regular(rooted(root, "boot/config-" + RELEASE), CONTROL_LIMIT).read_text().splitlines()
    consumed, position, frames, seen, last_name = 0, 0, 0, set(), None
    seen_firmware = set()
    base = rooted(root, "lib/modules/" + RELEASE)
    if not base.is_dir():
        base = rooted(root, "usr/lib/modules/" + RELEASE)
    by_key = {re.sub(r"\.(xz|gz|zst)$", "", p): p for p in deps}
    while position < len(data):
        while position < len(data) and data[position] == 0:
            position += 1  # cpio archives may have 512B padding
        if position == len(data):
            break
        prefix = data[position:position + 6]
        if prefix[:2] == b"\x1f\x8b" or prefix == b"\xfd7zXZ\0" or prefix[:4] == b"\x28\xb5\x2f\xfd":
            frames += 1
            require(frames <= 64, "excessive initrd compression frames")
            data, position = data[position:], 0
        if prefix[:2] == b"\x1f\x8b":
            require("CONFIG_RD_GZIP=y" in config, "kernel cannot decompress gzip initrd")
            decoder = zlib.decompressobj(31)
            expanded = decoder.decompress(data, limit - consumed + 1)
            require(decoder.eof and not decoder.unconsumed_tail, "invalid/oversize gzip initrd")
            data = expanded + decoder.unused_data
            require(len(data) + consumed <= limit, "oversize expanded initrd")
            continue
        if prefix == b"\xfd7zXZ\0":
            require("CONFIG_RD_XZ=y" in config, "kernel cannot decompress xz initrd")
            decoder = lzma.LZMADecompressor(memlimit=128 * MIB)
            expanded = decoder.decompress(data, max_length=limit - consumed + 1)
            require(decoder.eof, "invalid/oversize xz initrd")
            data = expanded + decoder.unused_data
            require(len(data) + consumed <= limit, "oversize expanded initrd")
            continue
        if prefix[:4] == b"\x28\xb5\x2f\xfd":
            require("CONFIG_RD_ZSTD=y" in config, "kernel cannot decompress zstd initrd")
            # zstd can decode concatenated frames; use a private input file so
            # stdout can be bounded without a stdin/stdout pipe deadlock.
            with tempfile.NamedTemporaryFile(prefix="e87n-initrd-", suffix=".zst") as source:
                source.write(data)
                source.flush()
                with subprocess.Popen(["zstd", "-d", "-q", "-c", source.name], stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL) as process:
                    expanded = process.stdout.read(limit - consumed + 1)
                    if len(expanded) + consumed > limit:
                        process.kill()
                    require(process.wait(timeout=30) == 0, "invalid/oversize zstd initrd")
            require(len(expanded) + consumed <= limit, "oversize expanded initrd")
            data = expanded
            continue
        record = memoryview(data)[position:]
        require(len(record) >= 110 and prefix in (b"070701", b"070702"), "unsupported/truncated initrd cpio")
        values = [int(record[6 + n * 8:14 + n * 8].tobytes(), 16) for n in range(13)]
        mode, file_size, name_size, checksum = values[1], values[6], values[11], values[12]
        start = (110 + name_size + 3) & ~3
        end = (start + file_size + 3) & ~3
        require(name_size > 0 and end <= len(record) and record[110 + name_size - 1] == 0,
                "invalid cpio entry bounds")
        name = record[110:110 + name_size - 1].tobytes().decode("utf-8").removeprefix("./")
        contents = record[start:start + file_size]
        if prefix == b"070702":
            require(sum(contents) & 0xffffffff == checksum, "cpio checksum mismatch")
        require(name == "TRAILER!!!" or name == "." or relative_path(name), "unsafe cpio name")
        match = re.fullmatch(r"(?:usr/)?lib/modules/([^/]+)/(.+\.ko(?:\.(?:xz|gz|zst))?)", name)
        if match:
            key = re.sub(r"\.(xz|gz|zst)$", "", match[2])
            require(match[1] == RELEASE and key in by_key and key not in seen and stat.S_ISREG(mode),
                    "foreign/duplicate/nonregular module in initrd")
            # Compare ELF bytes even if initramfs-tools changed compression.
            with tempfile.NamedTemporaryFile(prefix="e87n-initrd-module-", suffix=Path(name).suffix) as module:
                module.write(contents)
                module.flush()
                require(module_bytes(Path(module.name)) == module_bytes(base / by_key[key]),
                        "initrd module bytes differ from rootfs: " + name)
            seen.add(key)
        firmware = re.fullmatch(r"(?:usr/)?lib/firmware/(mediatek/mt7987/i2p5ge-phy-(?:pmb|DSPBitTb)\.bin)(?:\.(?:xz|zst))?", name)
        if firmware:
            key = firmware[1]
            require(key not in seen_firmware and stat.S_ISREG(mode), "duplicate/nonregular PHY firmware in initrd")
            with tempfile.NamedTemporaryFile(prefix="e87n-initrd-firmware-", suffix=Path(name).suffix) as blob:
                blob.write(contents)
                blob.flush()
                require(module_bytes(Path(blob.name)) == regular(rooted(root, "usr/lib/firmware/" + key)).read_bytes(),
                        "initrd PHY firmware differs from audited rootfs: " + name)
            seen_firmware.add(key)
        consumed += end
        require(consumed <= limit, "oversize expanded initrd")
        position += end
        last_name = name
    require(last_name == "TRAILER!!!", "initrd missing cpio trailer")
    present = {by_key[key] for key in seen}
    for module in present:
        require(deps[module] <= present, "initrd module content dependency missing: " + module)


def collect_evidence(root):
    """Read final rootfs: no extlinux assumptions, target execution or device access."""
    def read(name, limit=CONTROL_LIMIT):
        return regular(rooted(root, name), limit).read_bytes()
    boot = "boot/"
    config_bytes = read(boot + "config-" + RELEASE)
    config = {}
    for line in config_bytes.decode().splitlines():
        match = re.fullmatch(r"CONFIG_([A-Z0-9_]+)=(.+)", line)
        if match:
            require(match[1] not in config, "duplicate kernel config symbol")
            config[match[1]] = match[2]
    for name in "ARM64 MODULES MMC MMC_BLOCK MMC_MTK EFI_PARTITION EXT4_FS BLK_DEV_INITRD DEVTMPFS FW_LOADER".split():
        require(config.get(name) == "y", "required factory boot config missing: " + name)
    version = read("etc/debian_version", 256).decode().strip()
    require(version == BUILD["debian_point"], "actual Debian point release differs from build policy")
    components = {name: file_record(rooted(root, boot + path)) for name, path in {
        "kernel": "vmlinuz-" + RELEASE, "config": "config-" + RELEASE,
        "dtb": "dtb-" + RELEASE + "/mediatek/mt7987a-edgepi-e87n.dtb",
        "initrd": "initrd.img-" + RELEASE}.items()}
    kernel = read(boot + "vmlinuz-" + RELEASE, KERNEL_LIMIT)
    require(kernel[56:60] == b"ARM\x64" and ("Linux version " + RELEASE + " ").encode() in kernel,
            "actual Image does not identify the expected ARM64 kernel release")
    os_release = read("etc/os-release").decode()
    fields = {}
    for line in os_release.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(separator and key not in fields, "invalid/duplicate os-release field")
        parsed = shlex.split(value)
        require(len(parsed) <= 1, "invalid os-release value")
        fields[key] = parsed[0] if parsed else ""
    require(all(fields.get(key) == value for key, value in {
        "ID": "debian", "VERSION_ID": BUILD["debian_version"], "VERSION_CODENAME": BUILD["release"]}.items()),
        "actual rootfs is not reviewed Debian Trixie")
    provenance_bytes = read(PROVENANCE)
    provenance = read_json(provenance_bytes)
    require(provenance.get("schema") == 1 and provenance.get("hardware_validation") == "pending" and
            provenance.get("build") == BUILD, "build provenance differs from reviewed build policy")
    sources = provenance.get("source_files")
    require(isinstance(sources, dict) and sources, "missing actual recipe source files")
    for path, value in sources.items():
        relative_path(path)
        require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "invalid recipe source hash")
    require(provenance.get("recipe_sha256") == digest(canonical(sources)), "recipe source digest mismatch")
    recipe_bytes = read(BUILD_RECIPE)
    recipe = read_json(recipe_bytes)
    require(recipe.get("schema") == 1 and type(recipe.get("source_dirty")) is bool and
            re.fullmatch(r"[0-9a-f]{40}", recipe.get("source_commit", "")) and
            recipe.get("recipe_sha256") == provenance["recipe_sha256"], "invalid compiled build recipe identity")
    require(provenance.get("source_commit") == recipe["source_commit"] and
            type(provenance.get("source_dirty")) is bool and provenance["source_dirty"] == recipe["source_dirty"],
            "source commit/dirty state differs between receipts")
    for key in ("patched_kernel_commit", "patched_kernel_tree"):
        if key in recipe:
            require(re.fullmatch(r"[0-9a-f]{40}", recipe[key]), "invalid patched kernel identity")
    for key in ("kernel_release", "kernel_source", "kernel_commit", "armbian_commit"):
        require(recipe.get(key) == BUILD[key], "compiled build recipe mismatch: " + key)
    require(recipe.get("kernel_sha256") == components["kernel"]["sha256"] and
            recipe.get("kernel_config_sha256") == components["config"]["sha256"],
            "compiled recipe Image/config does not match actual files")
    patches = recipe.get("patches")
    require(isinstance(patches, list), "missing actual applied patch list")
    seen = set()
    for patch in patches:
        require(isinstance(patch, dict) and set(patch) == {"path", "sha256"}, "invalid patch record")
        path = relative_path(patch["path"])
        source_path = path if path.startswith("userpatches/") else "userpatches/" + path
        require(source_path not in seen and sources.get(source_path) == patch["sha256"], "patch not bound to recipe sources")
        seen.add(source_path)
    # The installed blobs must match the reviewed repository, not just hashes
    # supplied by the same rootfs under audit.
    reviewed = {}
    for line in (REPO / "firmware/SHA256SUMS").read_text().splitlines():
        value, path = line.split("  ", 1)
        require(path not in reviewed and re.fullmatch(r"[0-9a-f]{64}", value), "invalid reviewed firmware manifest")
        reviewed[path] = value
    firmware = {}
    for path, expected_size in (("mediatek/mt7987/i2p5ge-phy-pmb.bin", 98304),
                                ("mediatek/mt7987/i2p5ge-phy-DSPBitTb.bin", 28672)):
        record = file_record(rooted(root, "usr/lib/firmware/" + path))
        require(record == {"bytes": expected_size, "sha256": reviewed[path]}, "PHY firmware hash/size mismatch: " + path)
        firmware[path] = record
    license_hash = digest(read("usr/share/doc/e87n-phy-firmware/LICENCE.mediatek"))
    require(license_hash == reviewed["LICENCE.mediatek"], "PHY firmware licence hash mismatch")
    modules, deps = audit_modules(root, config)
    components.update(modules=modules, firmware={"required_phy": firmware, "licence_sha256": license_hash,
                      "tree": tree_record(rooted(root, "usr/lib/firmware"))})
    return {"schema": 1, "debian_version": version, "packages": installed_packages(root),
            "os_release_sha256": digest(os_release.encode()),
            "dpkg_status_sha256": digest(read("var/lib/dpkg/status", 32 * MIB)),
            "build_provenance_sha256": digest(provenance_bytes), "build_recipe_sha256": digest(recipe_bytes),
            "recipe_sha256": provenance["recipe_sha256"], "source": recipe,
            "applied_patches_sha256": digest(canonical(patches)), "components": components}, deps


def build_id(control):
    """Content identity, not a signature. Root payload hash prevents circular identity."""
    return digest(canonical({key: value for key, value in control.items() if key != "build_id"}))


def module_closure(deps, start):
    visited, active = set(), set()
    def visit(name):
        require(name not in active, "cyclic module dependency: " + name)
        if name in visited:
            return
        active.add(name)
        for dependency in sorted(deps[name]):
            visit(dependency)
        active.remove(name)
        visited.add(name)
    visit(start)
    return visited


def verify_module_indexes(root, deps):
    """Ask host kmod to resolve the shipped binary indexes; --show-depends never loads."""
    root = Path(root).resolve(strict=True)
    base = rooted(root, "lib/modules/" + RELEASE)
    if not base.is_dir():
        base = rooted(root, "usr/lib/modules/" + RELEASE)
    # kmod's --dirname is not chroot: an absolute /lib symlink in the image
    # would otherwise resolve in the host. Only expose the already-resolved tree.
    with tempfile.TemporaryDirectory(prefix="e87n-kmod-index-") as temporary:
        stage = Path(temporary).resolve()
        linked = stage / "lib/modules" / RELEASE
        linked.parent.mkdir(parents=True)
        linked.symlink_to(base, target_is_directory=True)
        for module in sorted(deps):
            if module_name(module) not in ("mtk_2p5ge", "mtk_phy_lib"):
                continue
            expected = module_closure(deps, module)
            result = run("modprobe", "--show-depends", "--ignore-install", "--config", "/dev/null",
                         "--dirname", stage, "--set-version", RELEASE, module_name(module),
                         capture_output=True, text=True, timeout=30).stdout
            actual = set()
            for line in result.splitlines():
                fields = line.split()
                if fields and fields[0] == "builtin":
                    continue
                require(len(fields) == 2 and fields[0] == "insmod", "unexpected host modprobe output")
                path = Path(fields[1])
                require(path.is_absolute() and linked in path.parents, "module index points outside active release")
                actual.add(relative_path(path.relative_to(linked).as_posix()))
            require(actual == expected, "binary module index/dependency closure mismatch: " + module)


def bootargs(root_uuid):
    return ("console=ttyS0,115200n8 earlycon=uart8250,mmio32,0x11000000 "
            f"root=UUID={root_uuid} rootwait rootfstype=ext4 rw "
            "fsck.repair=yes net.ifnames=0 consoleblank=0 panic=0")


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
            limit = KERNEL_LIMIT if name == "kernel" else (CONTROL_LIMIT if name == "CONTROL" else ROOT_LIMIT)
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
    control = read_json(paths["CONTROL"].read_bytes())
    require(type(control.get("headless", False)) is bool, "invalid headless firmware profile")
    require(control.get("format") == FORMAT and control.get("layout") == LAYOUT and
            control.get("kernel_release") == RELEASE and control.get("hardware_validation") == "pending",
            "wrong firmware manifest contract")
    for name in ("kernel", "root"):
        require(control["payloads"][name] == {"bytes": paths[name].stat().st_size, "sha256": sha(paths[name])},
                "firmware payload checksum/size mismatch: " + name)
    require(isinstance(control.get("evidence"), dict) and control["evidence"].get("schema") == 1,
            "missing final rootfs evidence")
    require(control.get("debian_version") == control["evidence"].get("debian_version") and
            control.get("debian_version") == BUILD["debian_point"], "wrong actual Debian version")
    require(control.get("build_id") == build_id(control), "firmware build ID mismatch")
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
