#!/usr/bin/env bash
# Orchestration tests only: EVERY device/mount/initrd tool is a shell-function mock.
# No actual loop, mount, fsck or artifact validation runs, even under Linux/root.
set -Eeuo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/e87n-image-mocks.XXXXXXXX")
test_dir=$(cd "$test_dir" && pwd)
export repo_dir test_dir
printf 'MOCK ONLY: no real image/device validation. Test files retained: %s\n' "$test_dir"
python3 - "$test_dir" <<'PY'
import pathlib, sys
work = pathlib.Path(sys.argv[1])
for name, magic in (("fixture.img", b"MOCK DISK - NOT AN ARMBIAN IMAGE"), ("compressed.img", b"\x1f\x8b")):
    with (work / name).open("wb") as file:
        file.write(magic)
        file.truncate(280 * 1024 * 1024) # Sparse synthetic file; GPT is entirely mocked.
(work / "tiny.img").write_bytes(b"MOCK")
(work / "wrong.img.xz").write_bytes(b"MOCK")
(work / "link.img").symlink_to(work / "fixture.img")
PY
mock_record() { printf '%s\n' "$*" >> "$mock_dir/calls"; }
mock_error() { printf 'UNEXPECTED MOCK INVOCATION: %s\n' "$*" >&2; return 99; }
uname() { if [[ "$mode" == nonlinux ]]; then printf 'Darwin\n'; else printf 'Linux\n'; fi; }
id() { [[ "$*" == -u ]] || return 99; if [[ "$mode" == nonroot ]]; then printf '1000\n'; else printf '0\n'; fi; }
sfdisk() {
	mock_record sfdisk "$@"
	[[ $# == 2 && "$1" == --json && "$2" =~ ^/proc/[0-9]+/fd/3$ ]] || mock_error sfdisk "$@" || return
	python3 - "$mode" <<'PY'
import json, sys
mode = sys.argv[1]
table = {"partitiontable": {"label": "gpt", "unit": "sectors", "sectorsize": 512,
    "lastlba": 573406, "partitions": [{"start": 32768, "size": 524288}, {"start": 557056, "size": 16351}]}}
p = table["partitiontable"]
if mode == "wrong_offset": p["partitions"][0]["start"] = 2048
if mode == "wrong_boot_size": p["partitions"][0]["size"] = 262144
if mode == "wrong_root_offset": p["partitions"][1]["start"] += 1
if mode == "not_gpt": p["label"] = "dos"
if mode == "extra_partition": p["partitions"].append({"start": 1, "size": 1})
if mode == "outside_image": p["partitions"][1]["size"] = 999999
print(json.dumps(table))
PY
}
sgdisk() {
	mock_record sgdisk "$@"
	[[ $# == 2 && "$1" == --verify && "$2" =~ ^/proc/[0-9]+/fd/3$ ]] || mock_error sgdisk "$@" || return
	if [[ "$mode" == gpt_command_fail ]]; then return 70; fi
	printf 'No problems found. (MOCK REPORT)\n'
	if [[ "$mode" == corrupt_gpt ]]; then printf 'Warning: invalid main GPT header\n'; fi
}
losetup() {
	mock_record losetup "$@"
	if [[ $# == 5 && "$1 $2 $3 $4" == '--find --show --read-only --partscan' && "$5" =~ ^/proc/[0-9]+/fd/3$ ]]; then
		if [[ "$mode" == loop_fail ]]; then return 66; fi
		printf 'MOCK OWNERSHIP ONLY\n' > "$mock_dir/loop-owned"
		printf '/dev/loop770077\n' # No such device is created or accessed.
	elif [[ "$*" == '--detach /dev/loop770077' && -f "$mock_dir/loop-owned" ]]; then
		[[ ! -f "$mock_dir/boot-mounted" && ! -f "$mock_dir/root-mounted" ]] || return 99
		command rm "$mock_dir/loop-owned"
	else mock_error losetup "$@"; fi
}
blockdev() {
	mock_record blockdev "$@"
	[[ $# == 2 && "$1" == --getro && "$2" =~ ^/dev/loop770077(p[12])?$ ]] || mock_error blockdev "$@" || return
	if [[ "$mode" == writable_loop ]]; then printf '0\n'; else printf '1\n'; fi
}
blkid() {
	mock_record blkid "$@"
	[[ $# == 6 && "$1 $2 $4 $5" == '-p -s -o value' && "$6" =~ ^/dev/loop770077p[12]$ ]] || mock_error blkid "$@" || return
	case "$3" in
		TYPE) if [[ "$mode" == not_ext4 ]]; then printf 'squashfs\n'; else printf 'ext4\n'; fi ;;
		UUID) if [[ "$mode" == bad_uuid ]]; then printf 'NOT-A-UUID\n'; else printf '12345678-1234-4abc-8def-123456789abc\n'; fi ;;
		*) mock_error blkid "$@" ;;
	esac
}
e2fsck() {
	mock_record e2fsck "$@"
	[[ $# == 2 && "$1" == -fn && "$2" =~ ^/dev/loop770077p[12]$ ]] || mock_error e2fsck "$@" || return
	if [[ "$mode" == fsck_fail ]]; then return 4; fi
}
mount() {
	mock_record mount "$@"
	[[ $# == 6 && "$1 $2 $3 $4" == '-t ext4 -o ro,noload,nodev,nosuid,noexec' && "$6" == "$mock_dir"/e87n-image-audit.*/boot ||
		$# == 6 && "$1 $2 $3 $4" == '-t ext4 -o ro,noload,nodev,nosuid,noexec' && "$6" == "$mock_dir"/e87n-image-audit.*/root ]] || mock_error mount "$@" || return
	local part=${6##*/}
	if [[ "$part" == boot ]]; then [[ "$5" == /dev/loop770077p1 ]] || return 99
	else [[ "$5" == /dev/loop770077p2 ]] || return 99; fi
	if [[ "$mode" == mount_fail && "$part" == root ]]; then return 32; fi
	printf '%s\n' "$5" > "$mock_dir/$part-mounted"
	if [[ "$part" == boot ]]; then
		command mkdir "$6/extlinux"
		local initrd=uInitrd
		if [[ "$mode" == raw_initrd ]]; then initrd=initrd.img-fixture; fi
		printf 'label MOCK\n initrd /%s\n' "$initrd" > "$6/extlinux/extlinux.conf"
		printf 'MOCK INITRD; NOT BOOTABLE\n' > "$6/$initrd"
		if [[ "$mode" == usb_root_builtin || "$mode" == usb_root_missing_module ]]; then
			local symbol
			for symbol in MODULES USB_COMMON USB USB_XHCI_HCD USB_XHCI_MTK USB_STORAGE SCSI_COMMON SCSI BLK_DEV_SD PHY_MTK_TPHY; do
				printf 'CONFIG_%s=y\n' "$symbol"
			done > "$6/config-6.12.108-fixture"
			if [[ "$mode" == usb_root_builtin ]]; then printf 'CONFIG_USB_UAS=y\n'
			else printf 'CONFIG_USB_UAS=m\n'; fi >> "$6/config-6.12.108-fixture"
		fi
	fi
	if [[ "$mode" == signal && "$part" == root ]]; then kill -TERM "$$"; fi
}
mountpoint() {
	[[ $# == 3 && "$1 $2" == '-q --' && "$3" == "$mock_dir"/e87n-image-audit.*/* ]] || mock_error mountpoint "$@" || return
	[[ -f "$mock_dir/${3##*/}-mounted" ]]
}
findmnt() {
	[[ $# == 5 && "$1 $2 $4" == '--noheadings --output --mountpoint' && "$5" == "$mock_dir"/e87n-image-audit.*/* ]] || mock_error findmnt "$@" || return
	case "$3" in
		SOURCE)
			if [[ "$mode" == foreign_mount && "${5##*/}" == boot ]]; then printf '/dev/foreign-device\n'
			else printf '%s\n' "$(< "$mock_dir/${5##*/}-mounted")"; fi ;;
		OPTIONS) if [[ "$mode" == writable_mount ]]; then printf 'rw,nodev\n'; else printf 'ro,noload,nodev,nosuid,noexec\n'; fi ;;
		*) mock_error findmnt "$@" ;;
	esac
}
umount() {
	mock_record umount "$@"
	[[ $# == 2 && "$1" == -- && "$2" == "$mock_dir"/e87n-image-audit.*/* ]] || mock_error umount "$@" || return
	if [[ "$mode" == cleanup_fail && "${2##*/}" == root ]]; then return 44; fi
	command rm "$mock_dir/${2##*/}-mounted"
}
bash() {
	mock_record verify-artifacts "$@"
	[[ $# == 7 && "$1" == "$repo_dir/scripts/verify-artifacts.sh" &&
		"$2 $4 $6 $7" == '--extracted-rootfs --boot-dir --expected-root-uuid 12345678-1234-4abc-8def-123456789abc' &&
		"$3" == "$mock_dir"/e87n-image-audit.*/root && "$5" == "$mock_dir"/e87n-image-audit.*/boot ]] || mock_error bash "$@" || return
	if [[ "$mode" == artifacts_fail ]]; then return 23; fi
	printf 'MOCK: artifact verifier delegation only; real verifier was NOT run\n'
}
dumpimage() {
	mock_record dumpimage "$@"
	if [[ $# == 2 && "$1" == -l ]]; then
		[[ "$2" == */uInitrd ]] # Raw initrd is not a U-Boot image.
	elif [[ $# == 7 && "$1 $2 $3 $4 $5" == '-T ramdisk -p 0 -o' && "$6" == "$mock_dir"/e87n-image-audit.*/initrd-0.raw && "$7" == */uInitrd ]]; then
		if [[ "$mode" == dump_fail ]]; then return 51; fi
		printf 'MOCK UNWRAPPED CPIO\n' > "$6"
	else mock_error dumpimage "$@"; fi
}
lsinitramfs() {
	mock_record lsinitramfs "$@"
	[[ $# == 1 && ( "$1" == "$mock_dir"/e87n-image-audit.*/initrd-0.raw || "$1" == "$mock_dir"/e87n-image-audit.*/boot/initrd.img-fixture ) ]] || mock_error lsinitramfs "$@" || return
	if [[ "$mode" == initrd_fail ]]; then return 52; fi
	if [[ "$mode" != missing_init ]]; then printf 'init\n'; fi
	printf 'scripts/local\nbin/sh\n'
}
export -f mock_record mock_error uname id sfdisk sgdisk losetup blockdev blkid e2fsck mount mountpoint findmnt umount bash dumpimage lsinitramfs
count=0
run_case() {
	mode=$1
	local expected=$2 result calls output line
	shift 2
	mock_dir="$test_dir/$mode"
	command mkdir "$mock_dir"
	export mode mock_dir TMPDIR="$mock_dir"
	if command bash "$repo_dir/scripts/verify-image.sh" "$@" > "$mock_dir/output" 2>&1; then result=0; else result=$?; fi
	if [[ "$result" != "$expected" ]]; then
		printf 'FAIL mock %s: expected %s, got %s\n' "$mode" "$expected" "$result" >&2
		command sed -n '1,160p' "$mock_dir/output" >&2
		exit 1
	fi
	output=$(< "$mock_dir/output")
	[[ "$output" != *'UNEXPECTED MOCK INVOCATION'* && "$output" != *Traceback* ]]
	if [[ "$expected" != 0 ]]; then [[ "$output" != *'PASS: static image checks'* ]]; fi
	calls=''
	if [[ -f "$mock_dir/calls" ]]; then calls=$(< "$mock_dir/calls"); fi
	case "$mode" in
		help|no_args|missing|character_device|symlink|wrong_extension|tiny|compressed|nonlinux|nonroot|not_gpt|wrong_offset|wrong_boot_size|wrong_root_offset|extra_partition|outside_image|corrupt_gpt|gpt_command_fail)
			[[ "$calls" != *'losetup '* ]] ;;
		wrapped_initrd|raw_initrd)
			[[ "$calls" == *'verify-artifacts '* && "$calls" == *'lsinitramfs '* && "$calls" == *'losetup --detach /dev/loop770077'* ]] ;;
		cleanup_fail|foreign_mount)
			[[ "$calls" != *'losetup --detach'* && -f "$mock_dir/loop-owned" ]] ;;
	esac
	if [[ "$calls" == *'losetup --detach'* ]]; then
		[[ ! -f "$mock_dir/loop-owned" && ! -f "$mock_dir/boot-mounted" && ! -f "$mock_dir/root-mounted" ]]
	fi
	if [[ "$calls" == *'mount -t ext4'* && "$mode" != cleanup_fail && "$mode" != foreign_mount ]]; then
		[[ "$calls" == *'losetup --detach /dev/loop770077'* ]] # Includes partial mount and signal failures.
	fi
	if [[ "$mode" == foreign_mount ]]; then
		while IFS= read -r line; do [[ "$line" != 'umount -- '*/boot ]]; done < "$mock_dir/calls"
	fi
	if [[ "$mode" == raw_initrd ]]; then [[ "$calls" != *'dumpimage -T '* ]]; fi
	count=$((count + 1))
	printf 'PASS mock %02d: %s (exit %s)\n' "$count" "$mode" "$result"
}
image="$test_dir/fixture.img"
run_case help 0 --help
run_case no_args 2
run_case missing 1 "$test_dir/missing.img"
run_case character_device 1 /dev/null
run_case symlink 1 "$test_dir/link.img"
run_case wrong_extension 1 "$test_dir/wrong.img.xz"
run_case tiny 1 "$test_dir/tiny.img"
run_case compressed 1 "$test_dir/compressed.img"
run_case nonlinux 1 "$image"
run_case nonroot 1 "$image"
for mode in not_gpt wrong_offset wrong_boot_size wrong_root_offset extra_partition outside_image corrupt_gpt; do run_case "$mode" 1 "$image"; done
run_case gpt_command_fail 70 "$image"
run_case loop_fail 66 "$image"
run_case writable_loop 1 "$image"
run_case not_ext4 1 "$image"
run_case fsck_fail 4 "$image"
run_case bad_uuid 1 "$image"
run_case mount_fail 32 "$image"
run_case writable_mount 1 "$image"
run_case artifacts_fail 23 "$image"
run_case dump_fail 51 "$image"
run_case initrd_fail 52 "$image"
run_case missing_init 1 "$image"
run_case signal 143 "$image"
run_case cleanup_fail 1 "$image"
run_case foreign_mount 1 "$image"
run_case wrapped_initrd 0 "$image"
run_case raw_initrd 0 "$image"
run_case usb_root_no_image 2 --require-usb-root
run_case usb_root_no_config 1 --require-usb-root "$image"
run_case usb_root_builtin 0 --require-usb-root "$image"
run_case usb_root_missing_module 1 --require-usb-root "$image"
printf 'PASS: %s MOCK tests. No real loop/mount/fsck/dumpimage/artifact validation or hardware checks performed.\n' "$count"
