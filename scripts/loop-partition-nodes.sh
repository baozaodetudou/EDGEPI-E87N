#!/usr/bin/env bash
# Materialize only kernel-registered partitions of a caller-owned loop device.
# This changes private /dev entries, never the backing image or partition table.
e87n_loop_partition_node() {
	local device=$1 device_major device_minor expected actual
	[[ $device =~ ^/dev/loop[0-9]+p[0-9]+$ ]] || return 0
	[[ -r /sys/class/block/${device##*/}/dev ]] || return 0
	IFS=: read -r device_major device_minor < "/sys/class/block/${device##*/}/dev" || return 1
	[[ $device_major =~ ^[0-9]+$ && $device_minor =~ ^[0-9]+$ ]] || return 1
	[[ ! -L $device && ( ! -e $device || -b $device ) ]] || return 1
	printf -v expected '%x:%x' "$device_major" "$device_minor"
	actual=$(stat -c '%t:%T' "$device" 2>/dev/null || true)
	if [[ $actual != "$expected" ]]; then
		[[ ! -b $device ]] || rm -- "$device"
		mknod -m0660 "$device" b "$device_major" "$device_minor"
	fi
}
