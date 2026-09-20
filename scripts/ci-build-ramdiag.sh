#!/usr/bin/env bash
# Build RAM-only diagnostic FITs from the just-built image. This is a CI
# helper, not an installer: it never writes the input image or any board disk.
set -Eeuo pipefail
export LC_ALL=C
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
usage() { printf 'Usage: %s --image IMAGE.img --output-dir OUTPUT\n' "$0" >&2; }
image=''
output=''
while (( $# )); do
  case "$1" in
    --image) [[ $# -ge 2 ]] || { usage; exit 2; }; image=$2; shift 2 ;;
    --output-dir) [[ $# -ge 2 ]] || { usage; exit 2; }; output=$2; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done
[[ -n $image && -n $output ]] || { usage; exit 2; }
[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || {
  printf 'FAIL: RAM diagnostic assembly requires native Linux aarch64\n' >&2
  exit 1
}
[[ $(id -u) == 0 ]] || { printf 'FAIL: RAM diagnostic assembly requires root\n' >&2; exit 1; }
[[ -f $image && ! -L $image && ! -b $image && ! -c $image ]] || {
  printf 'FAIL: diagnostic input must be a regular image file\n' >&2
  exit 1
}
for tool in mount umount findmnt python3 mkimage fdtget fdtput cpio chroot; do
  command -v "$tool" >/dev/null 2>&1 || { printf 'FAIL: missing host tool: %s\n' "$tool" >&2; exit 1; }
done
[[ ! -e $output && ! -L $output ]] || {
  printf 'FAIL: refusing to overwrite diagnostic output: %s\n' "$output" >&2
  exit 1
}
mkdir -p "$output"
work=$(mktemp -d "${RUNNER_TEMP:-/var/tmp}/e87n-ramdiag-image.XXXXXXXX")
boot="$work/boot"
root="$work/root"
mkdir "$boot" "$root"
boot_mounted=no
root_mounted=no
cleanup() {
  local result=$?
  trap - EXIT
  if [[ $root_mounted == yes ]]; then umount "$root" || result=1; fi
  if [[ $boot_mounted == yes ]]; then umount "$boot" || result=1; fi
  exit "$result"
}
trap cleanup EXIT INT TERM HUP

# The E87N image layout is fixed and is checked by verify-image.sh immediately
# before this helper runs: p1 starts at 16 MiB and p2 at 272 MiB.
mount -t ext4 -o ro,noload,loop,offset=$((16 * 1024 * 1024)),sizelimit=$((256 * 1024 * 1024)) \
  -- "$image" "$boot"
boot_mounted=yes
mount -t ext4 -o ro,noload,loop,offset=$((272 * 1024 * 1024)) -- "$image" "$root"
root_mounted=yes

release=6.18.51-current-filogic
kernel="$work/Image"
dtb="$work/board.dtb"
boot_initrd="$work/production-initrd"
cp -L -- "$boot/vmlinuz-$release" "$kernel"
cp -L -- "$boot/dtb-$release/mediatek/mt7987a-edgepi-e87n.dtb" "$dtb"
cp -L -- "$boot/initrd.img-$release" "$boot_initrd"
[[ -s $kernel && -s $dtb && -s $boot_initrd ]] || {
  printf 'FAIL: built image is missing the expected E87N boot payloads\n' >&2
  exit 1
}

input="$work/input"
mkdir "$input"
# The checked-in RAMDIAG helper currently names the PID-1 asset
# `init-network-first` but its asset contract still checks for `init`.  Stage
# an isolated copy and add the compatibility name there; never mutate the
# checkout or the source RAMDIAG tree in a CI job.
ramdiag_source="$work/ramdiag"
cp -a -- "$repo_dir/scripts/ramdiag" "$ramdiag_source"
if [[ ! -e "$ramdiag_source/assets/init" ]]; then
  [[ -f "$ramdiag_source/assets/init-network-first" ]] || {
    printf 'FAIL: RAMDIAG source is missing both assets/init and assets/init-network-first\n' >&2
    exit 1
  }
  ln -s init-network-first "$ramdiag_source/assets/init"
fi
python3 "$ramdiag_source/build-initrd.py" \
	--root "$root" --boot "$boot" --output-dir "$input"
python3 "$ramdiag_source/build-fits.py" \
	--kernel "$kernel" --dtb "$dtb" --initrd "$input/ramdisk.payload" \
	--output-dir "$output"
cat >"$output/README.txt" <<'EOF'
E87N RAM-only diagnostics (not installation firmware)

First test E87N-ramdiag-40000000-initrd.itb. It disables eMMC in its DTB,
boots a custom initrd entirely from RAM, configures eth0 as 192.168.1.1,
emits one-way UDP status to 192.168.1.2:6666, and starts root/doumao SSH.
Do not upload these FIT files to any permanent firmware/upgrade field.
EOF
printf 'PASS: RAM-only diagnostic FIT matrix built in %s\n' "$output"
