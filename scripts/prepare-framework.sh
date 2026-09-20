#!/usr/bin/env bash
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
framework_dir=${1:-"$repo_dir/source/armbian-build"}
framework_pin=$(python3 "$repo_dir/scripts/build_config.py" armbian_commit)
[[ $(git -C "$framework_dir" rev-parse HEAD) == "$framework_pin" ]] || {
	printf 'Framework fixes only verified against %s\n' "$framework_pin" >&2; exit 2;
}
for fix in "$repo_dir/patches/armbian-build/"*.patch; do
	if git -C "$framework_dir" apply --reverse --check "$fix" >/dev/null 2>&1; then
		printf 'Framework fix already present: %s\n' "${fix##*/}"
	else
		git -C "$framework_dir" apply --check "$fix"
		git -C "$framework_dir" apply "$fix"
		printf 'Applied framework fix: %s\n' "${fix##*/}"
	fi
done
