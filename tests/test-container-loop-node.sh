#!/usr/bin/env bash
# Real Linux/container integration with an exclusively allocated scratch loop.
set -Eeuo pipefail
[[ $(uname -s) == Linux && $(id -u) == 0 && $# == 1 ]]
framework=$1
scratch=$(mktemp -d "${TMPDIR:-/var/tmp}/e87n-loop-node.XXXXXXXX")
loop_device=
cleanup() {
    local result=$?
    trap - EXIT
    if [[ -n $loop_device ]]; then losetup --detach "$loop_device" || result=1; fi
    exit "$result"
}
trap cleanup EXIT
truncate -s 32M "$scratch/disk.img"
sfdisk "$scratch/disk.img" <<'GPT'
label: gpt
start=2048,size=8192,type=L
start=10240,size=8192,type=L
GPT
loop_device=$(losetup --find --show --partscan "$scratch/disk.img")
[[ $loop_device =~ ^/dev/loop[0-9]+$ ]]
display_alert() { :; }
run_host_command_logged() { "$@"; }
export CONTAINER_COMPAT=yes CHECK_LOOP_FOR_SIZE=yes RETRY_RUNS=1
# shellcheck source=/dev/null
source "$framework/lib/functions/image/loop.sh"
for part in 1 2; do
    node="${loop_device}p${part}"
    check_loop_device_internal "$node"
    [[ -b $node && $(blockdev --getsize64 "$node") == 4194304 ]]
    IFS=: read -r device_major device_minor < "/sys/class/block/${node##*/}/dev"
    printf -v expected '%x:%x' "$device_major" "$device_minor"
    [[ $(stat -c '%t:%T' "$node") == "$expected" ]]
    check_loop_device_internal "$node"
done
# The standalone image auditor uses the same kernel-number policy without
# requiring the Armbian source tree to be present.
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/loop-partition-nodes.sh
source "$repo/scripts/loop-partition-nodes.sh"
e87n_loop_partition_node "${loop_device}p1"
[[ $(blockdev --getsize64 "${loop_device}p1") == 4194304 ]]
printf 'PASS: real loop partitions materialized from sysfs with correct identity and size; repeat is idempotent.\n'
