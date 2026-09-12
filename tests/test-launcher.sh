#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/e87n-launcher-test.XXXXXX")
printf 'Test files retained at %s\n' "$test_dir"
mkdir -p "$test_dir/source/armbian-build/.git" "$test_dir/source/armbian-build/userpatches"
cp "$repo_dir/build.sh" "$repo_dir/build-armbian.sh" "$test_dir/"
cp -a "$repo_dir/userpatches" "$test_dir/userpatches"
cp -a "$repo_dir/firmware" "$test_dir/firmware"
cp "$repo_dir/README.md" "$test_dir/source/armbian-build/userpatches/local-marker"

# Exported shell functions mock the external runtime; no Docker/git changes.
uname() { printf 'Darwin\n'; }
git() { return 1; } # Existing non-sparse checkout; no network allowed in test.
docker() {
	case "$1" in
		run)
			printf '%s\n' "$@" > "$test_dir/docker-args"
			printf 'test-container-id\n'
			;;
		logs) printf 'mock build log\n' ;;
		wait) printf '%s\n' "${mock_exit:-0}" ;;
		*) printf 'Unexpected docker subcommand: %s\n' "$1" >&2; return 99 ;;
	esac
}
export -f uname docker git
export test_dir

bash "$test_dir/build.sh" kernel TEST_MARKER=passed
grep -qx kernel "$test_dir/docker-args"
grep -qx TEST_MARKER=passed "$test_dir/docker-args"
grep -qx 'e87n-armbian-cache:/workspace/source/armbian-build/cache' "$test_dir/docker-args"
grep -qx 'e87n-armbian-tmp:/workspace/source/armbian-build/.tmp' "$test_dir/docker-args"
grep -qx -- --detach "$test_dir/docker-args"
cmp "$repo_dir/README.md" "$test_dir"/source/armbian-build/userpatches-backup.*/userpatches/local-marker

export mock_exit=23
if bash "$test_dir/build.sh"; then
	printf 'FAIL: launcher masked a failed build\n' >&2
	exit 1
else
	result=$?
	[[ "$result" == 23 ]] || { printf 'FAIL: expected exit 23, got %s\n' "$result" >&2; exit 1; }
fi
printf 'PASS: forwarding, Linux volumes, overlay preservation, detached run, failure propagation\n'
