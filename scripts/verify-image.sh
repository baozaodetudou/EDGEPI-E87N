#!/usr/bin/env bash
# Linux host audit only. Never repair, flash, execute target code or reuse a loop.
set -Eeuo pipefail
export LC_ALL=C
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
usage() {
	printf '%s\n' \
		'Usage: sudo bash scripts/verify-image.sh [--] existing.img' \
		'Linux/root only; accepts a regular, uncompressed .img, never a device.' \
		'Checks GPT (16 MiB offset, 256 MiB boot, root at 272 MiB), ext4 and fsck -fn.' \
		'Creates a NEW read-only loop; mounts only in a private mktemp directory with' \
		'ro,noload,nodev,nosuid,noexec. Reuses verify-artifacts.sh with the real root UUID.' \
		'Lists initramfs contents using host lsinitramfs; never executes target code.' \
		'Requires python3, util-linux tools, sgdisk, e2fsck, dumpimage, lsinitramfs and mount privilege.' \
		'Use a trusted, idle image: do not modify it during this audit. Audit files are retained.' \
		'Only the mounts/loop owned by this run are released; no force/lazy/global cleanup.' \
		'PASS is static image validation, NOT board/U-Boot validation or safe eMMC installation.'
}
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
if [[ ${1:-} == -- ]]; then shift; fi
[[ $# == 1 ]] || { usage >&2; exit 2; }
image=$1
[[ ! -b "$image" && ! -c "$image" ]] || fail 'block/character devices are explicitly forbidden'
[[ -f "$image" && ! -L "$image" && "$image" == *.img ]] || fail 'need an existing regular non-symlink .img file'
[[ $(uname -s) == Linux ]] || fail 'requires a Linux host; no Docker is started by this script'
[[ $(id -u) == 0 ]] || fail 'requires root and loop/mount privileges on the Linux host'
for tool in python3 sfdisk sgdisk losetup blockdev blkid e2fsck mount umount mountpoint findmnt dumpimage lsinitramfs mktemp mkdir bash; do
	command -v "$tool" >/dev/null 2>&1 || fail "missing host tool: $tool"
done
[[ -f "$script_dir/verify-artifacts.sh" ]] || fail 'sibling verify-artifacts.sh missing'

# Keep the same opened inode through GPT inspection and loop allocation.
exec 3<"$image"
python3 - <<'PY'
import os, stat, sys
info = os.fstat(3)
if not stat.S_ISREG(info.st_mode) or info.st_size <= 272 * 1024 * 1024:
    sys.exit("FAIL: opened input must be a regular disk image larger than 272 MiB")
magic = os.pread(3, 8, 0)
if magic.startswith((b"\x1f\x8b", b"\xfd7zXZ\0", b"BZh", b"\x28\xb5\x2f\xfd", b"\x04\x22\x4d\x18", b"PK\x03\x04")):
    sys.exit("FAIL: compressed/archive input is forbidden, even when renamed .img")
PY
image_fd="/proc/$$/fd/3"
audit_dir=$(mktemp -d "${TMPDIR:-/tmp}/e87n-image-audit.XXXXXXXX")
loopdev=''
boot_attempted=0
root_attempted=0

release_mount() {
	local target=$1 expected=$2 actual
	if mountpoint -q -- "$target"; then
		actual=$(findmnt --noheadings --output SOURCE --mountpoint "$target") || return 1
		if [[ "$actual" != "$expected" ]]; then
			printf 'FAIL: refusing to unmount unexpected source %s at %s\n' "$actual" "$target" >&2
			return 1
		fi
		umount -- "$target" || return 1
	fi
}
release_resources() {
	local failed=0
	if (( root_attempted )); then
		if release_mount "$audit_dir/root" "${loopdev}p2"; then root_attempted=0; else failed=1; fi
	fi
	if (( boot_attempted )); then
		if release_mount "$audit_dir/boot" "${loopdev}p1"; then boot_attempted=0; else failed=1; fi
	fi
	if (( failed )); then
		printf 'FAIL: cleanup incomplete; retaining owned loop %s and audit directory %s\n' "$loopdev" "$audit_dir" >&2
		return 1 # Never detach a loop whose mounts could not be released.
	fi
	if [[ -n "$loopdev" ]]; then
		losetup --detach "$loopdev" || return 1
		loopdev=''
	fi
}
on_exit() {
	local result=$?
	trap - EXIT INT TERM HUP
	if ! release_resources; then
		printf 'FAIL: release resources manually using the owned paths above; no forced cleanup attempted\n' >&2
		if (( result == 0 )); then result=1; fi
	fi
	exit "$result"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
printf 'Audit directory (retained): %s\n' "$audit_dir"
sfdisk --json "$image_fd" > "$audit_dir/gpt.json"
sgdisk --verify "$image_fd" > "$audit_dir/gpt-check.txt" 2>&1
python3 - "$audit_dir" <<'PY'
import json, os, pathlib, re, sys
work = pathlib.Path(sys.argv[1])
try:
    report = (work / "gpt-check.txt").read_text()
    if not re.search(r"^No problems found\.", report, re.M) or re.search(
            r"warning|caution|invalid|corrupt|mismatch|identified [1-9]", report, re.I):
        raise ValueError("sgdisk did not report a clean GPT: " + report.strip())
    table = json.loads((work / "gpt.json").read_text())["partitiontable"]
    parts = table["partitions"]
    if table["label"] != "gpt" or table["unit"] != "sectors" or table["sectorsize"] != 512 or len(parts) != 2:
        raise ValueError("need GPT, 512-byte sectors and exactly two partitions")
    boot, root = parts
    if (boot["start"], boot["size"], root["start"]) != (32768, 524288, 557056):
        raise ValueError("layout must be boot at 16 MiB, boot size 256 MiB, root at 272 MiB")
    if root["size"] <= 0 or root["start"] + root["size"] - 1 > table["lastlba"] or (
            table["lastlba"] > os.fstat(3).st_size // 512 - 34):
        raise ValueError("root/GPT extends outside image bounds")
except (ValueError, KeyError, TypeError, OSError) as error:
    sys.exit("FAIL: " + str(error))
print("OK: GPT, 16 MiB / 256 MiB boot / 272 MiB root layout")
PY

# --nooverlap is intentionally NOT used: it may reuse a pre-existing loop.
allocated=$(losetup --find --show --read-only --partscan "$image_fd")
[[ "$allocated" =~ ^/dev/loop[0-9]+$ ]] || fail "unexpected newly allocated loop response: $allocated"
loopdev=$allocated
printf 'Owned read-only loop: %s\n' "$loopdev"
for device in "$loopdev" "${loopdev}p1" "${loopdev}p2"; do
	[[ $(blockdev --getro "$device") == 1 ]] || fail "device is not read-only: $device"
done
for device in "${loopdev}p1" "${loopdev}p2"; do
	[[ $(blkid -p -s TYPE -o value "$device") == ext4 ]] || fail "partition is not ext4: $device"
	# Never repair; nonzero (including uncorrected errors) propagates to EXIT.
	e2fsck -fn "$device"
done
root_uuid=$(blkid -p -s UUID -o value "${loopdev}p2")
[[ "$root_uuid" =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ ]] || fail 'root filesystem UUID missing/invalid'
mkdir "$audit_dir/boot" "$audit_dir/root"
boot_attempted=1
mount -t ext4 -o ro,noload,nodev,nosuid,noexec "${loopdev}p1" "$audit_dir/boot"
root_attempted=1
mount -t ext4 -o ro,noload,nodev,nosuid,noexec "${loopdev}p2" "$audit_dir/root"
for part in boot root; do
	if [[ "$part" == boot ]]; then expected="${loopdev}p1"; else expected="${loopdev}p2"; fi
	[[ $(findmnt --noheadings --output SOURCE --mountpoint "$audit_dir/$part") == "$expected" ]] || fail 'unexpected mounted source'
	options=$(findmnt --noheadings --output OPTIONS --mountpoint "$audit_dir/$part")
	[[ ",$options," == *,ro,* && ",$options," != *,rw,* ]] || fail 'mount is not read-only'
done
bash "$script_dir/verify-artifacts.sh" --extracted-rootfs "$audit_dir/root" --boot-dir "$audit_dir/boot" --expected-root-uuid "$root_uuid"

# Inspect every extlinux-selected initrd, resolving symlinks within the bootfs.
# uInitrd's legacy U-Boot wrapper must not be passed to lsinitramfs as raw cpio.
python3 - "$audit_dir" <<'PY'
import os, pathlib, stat, sys
work = pathlib.Path(sys.argv[1])
boot = work / "boot"
def resolve(name):
    pending, parts, hops = name.split("/"), [], 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("initrd path escapes bootfs")
            parts.pop()
            continue
        path = boot.joinpath(*parts, part)
        if path.is_symlink():
            hops += 1
            if hops > 40:
                raise ValueError("bootfs symlink loop")
            target = os.readlink(path)
            if target.startswith("/"):
                parts = []
            pending = target.split("/") + pending
        else:
            parts.append(part)
    path = boot.joinpath(*parts)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("not a regular bootfs file: " + name)
    return path
try:
    names = []
    for line in resolve("extlinux/extlinux.conf").read_text().splitlines():
        entry = line.strip().split(None, 1)
        if len(entry) == 2 and entry[0].lower() == "initrd" and entry[1] not in names:
            names.append(entry[1])
    if not names:
        raise ValueError("no extlinux initrd selected")
    (work / "initrd-files.txt").write_text("".join(str(resolve(name)) + "\n" for name in names))
except (ValueError, OSError) as error:
    sys.exit("FAIL: " + str(error))
PY
number=0
while IFS= read -r initrd; do
	# dumpimage recognizes supported U-Boot images; raw initrd.img is read directly.
	if dumpimage -l "$initrd" > "$audit_dir/initrd-$number.header" 2>&1; then
		dumpimage -T ramdisk -p 0 -o "$audit_dir/initrd-$number.raw" "$initrd"
		initrd="$audit_dir/initrd-$number.raw"
	fi
	lsinitramfs "$initrd" > "$audit_dir/initrd-$number.list"
	python3 - "$audit_dir/initrd-$number.list" <<'PY'
import pathlib, sys
names = {line[2:] if line.startswith("./") else line.lstrip("/")
         for line in pathlib.Path(sys.argv[1]).read_text().splitlines()}
if "init" not in names:
    sys.exit("FAIL: initramfs listing lacks /init")
print("OK: host lsinitramfs parsed selected initrd and found /init (not executed)")
PY
	number=$((number + 1))
done < "$audit_dir/initrd-files.txt"
release_resources || fail 'cleanup incomplete; image validation is not successful'
printf '%s\n' \
	'PASS: static image checks, real root UUID and initramfs listing only.' \
	'Experimental: NOT board/U-Boot validated; no fixed-MAC or bootloader preservation guarantee.' \
	'This does NOT authorize or make a whole-image eMMC write safe.'
