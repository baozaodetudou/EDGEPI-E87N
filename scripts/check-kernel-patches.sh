#!/usr/bin/env bash
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
kernel_repo=${1:?Usage: bash scripts/check-kernel-patches.sh /path/to/kernel/git [revision] [patchset]}
[[ $# -le 3 ]] || { printf 'Too many arguments\n' >&2; exit 2; }
export BRANCH=current
exit_with_error() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
# shellcheck source=/dev/null
source "$repo_dir/userpatches/config/sources/families/edgepi-e87n.conf"
kernel_ref=${2:-${KERNELBRANCH#commit:}}
patch_bin=${PATCH_BIN:-patch}
patchset=${3:-$KERNELPATCHDIR}
[[ "$patchset" =~ ^edgepi-e87n-[0-9]+\.[0-9]+$ ]] || { printf 'Invalid patchset\n' >&2; exit 2; }
patch_dir="$repo_dir/userpatches/kernel/$patchset"
[[ -d "$patch_dir" ]] || { printf 'Missing patch directory: %s\n' "$patch_dir" >&2; exit 2; }

if ! "$patch_bin" --version | head -1 | grep -q 'GNU patch'; then
	echo 'GNU patch is required. On macOS set PATCH_BIN to gpatch.' >&2
	exit 2
fi
audit_dir=$(mktemp -d "${TMPDIR:-/tmp}/e87n-patch-check.XXXXXX")
mkdir -p "$audit_dir/tree" "$audit_dir/logs"
printf 'Isolated audit directory: %s\n' "$audit_dir"
git -C "$kernel_repo" rev-parse --verify "$kernel_ref^{commit}"

# Extract only existing paths touched by the series. New files are supplied by
# the patches themselves. The user's working tree is never changed.
changed_paths=()
while IFS= read -r changed_path; do
	changed_paths+=("$changed_path")
done < <(awk '/^--- a\// { sub(/^--- a\//, ""); print $1 }' "$patch_dir"/*.patch | LC_ALL=C sort -u)
baseline_paths=()
while IFS= read -r baseline_path; do
	baseline_paths+=("$baseline_path")
done < <(git -C "$kernel_repo" ls-tree -r --name-only "$kernel_ref" -- "${changed_paths[@]}")
[[ ${#baseline_paths[@]} -gt 0 ]] || { echo 'No baseline paths found' >&2; exit 1; }
# git archive can eagerly fetch the entire tree of a blob-filtered clone.
# Read only these blobs, preserving each failure and avoiding a full checkout.
for baseline_path in "${baseline_paths[@]}"; do
	mkdir -p "$audit_dir/tree/$(dirname "$baseline_path")"
	git -C "$kernel_repo" show "$kernel_ref:$baseline_path" > "$audit_dir/tree/$baseline_path"
done

count=0
for patch_file in "$patch_dir"/*.patch; do
	patch_name=${patch_file##*/}
	printf 'Checking %s\n' "$patch_name"
	# Stop on the first failure; never continue on a partially-applied series.
	if ! "$patch_bin" --batch --forward --fuzz=0 -d "$audit_dir/tree" -p1 \
		-i "$patch_file" > "$audit_dir/logs/$patch_name.log" 2>&1; then
		cat "$audit_dir/logs/$patch_name.log" >&2
		printf 'FAIL: %s (audit retained at %s)\n' "$patch_name" "$audit_dir" >&2
		exit 1
	fi
	count=$((count + 1))
done
printf 'PASS: %s patches applied with fuzz=0. This is not a compilation test.\n' "$count"

# Optional device-tree compile independent of the slow container bootstrap.
# Fetch only these unmodified binding headers, never the full partial-clone tree.
if [[ ${CHECK_DTB:-no} == yes ]]; then
	command -v dtc >/dev/null
	command -v fdtget >/dev/null
	for header in \
		interrupt-controller/irq.h interrupt-controller/arm-gic.h phy/phy.h \
		reset/ti-syscon.h pinctrl/mt65xx.h gpio/gpio.h thermal/thermal.h \
		leds/common.h input/input.h; do
		mkdir -p "$audit_dir/tree/include/dt-bindings/$(dirname "$header")"
		git -C "$kernel_repo" show "$kernel_ref:include/dt-bindings/$header" \
			> "$audit_dir/tree/include/dt-bindings/$header"
	done
	# linux-event-codes.h is a relative symlink in the kernel; materialize its target.
	git -C "$kernel_repo" show "$kernel_ref:include/uapi/linux/input-event-codes.h" \
		> "$audit_dir/tree/include/dt-bindings/input/linux-event-codes.h"
	"${CPP_BIN:-cc}" -E -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
		-I "$audit_dir/tree/include" \
		"$audit_dir/tree/arch/arm64/boot/dts/mediatek/mt7987a-edgepi-e87n.dts" \
		-o "$audit_dir/e87n.preprocessed.dts"
	dtc -I dts -O dtb -o "$audit_dir/mt7987a-edgepi-e87n.dtb" \
		"$audit_dir/e87n.preprocessed.dts" 2> "$audit_dir/logs/dtc.log"
	fdtget -t s "$audit_dir/mt7987a-edgepi-e87n.dtb" / compatible | grep -w 'edgepi,e87n'
	printf 'PASS: E87N DTB compiled. Warnings: %s/logs/dtc.log; not a kernel or boot test.\n' "$audit_dir"
fi
