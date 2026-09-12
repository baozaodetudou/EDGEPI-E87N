#!/usr/bin/env bash
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
kernel_repo=${1:?Usage: bash scripts/check-kernel-patches.sh /path/to/kernel/git [revision]}
kernel_ref=${2:-b864732ee285e7868fb0857d69a8ff349e37003e}
patch_bin=${PATCH_BIN:-patch}
patch_dir="$repo_dir/userpatches/patch/kernel/edgepi-e87n-6.12"

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
