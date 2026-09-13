#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/e87n-launcher-test.XXXXXX")
printf 'Test files retained at %s\n' "$test_dir"
mkdir -p "$test_dir/source/armbian-build/.git" "$test_dir/source/armbian-build/userpatches"
cp "$repo_dir/build.sh" "$repo_dir/build-armbian.sh" "$test_dir/"
mkdir -p "$test_dir/scripts" "$test_dir/patches"
cp "$repo_dir/scripts/prepare-framework.sh" "$test_dir/scripts/"
cp -a "$repo_dir/patches/armbian-build" "$test_dir/patches/"
cp -a "$repo_dir/userpatches" "$test_dir/userpatches"
cp -a "$repo_dir/firmware" "$test_dir/firmware"
cp -a "$repo_dir/board-support" "$test_dir/board-support"
cp "$repo_dir/README.md" "$test_dir/source/armbian-build/userpatches/local-marker"

# Exported shell functions mock the external runtime; no Docker/git changes.
uname() { printf 'Darwin\n'; }
git() {
	# Existing non-sparse checkout; no network allowed in test.
	case "$*" in
		*'rev-parse HEAD') printf '%s\n' "${mock_revision:-7c1bb29eb0e7bd75b0703d86fe654b2680e646da}" ;;
		*'config --get core.sparseCheckout') return 1 ;;
		*'apply --reverse --check '*) return 1 ;;
		*'apply --check '*|*'apply '*) return 0 ;;
		*) printf 'Unexpected git command: %s\n' "$*" >&2; return 99 ;;
	esac
}
docker() {
	case "$1" in
		ps) if [[ ${mock_active:-no} == yes ]]; then printf 'e87n-armbian-existing\n'; fi ;;
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
grep -qx RELEASE=trixie "$test_dir/docker-args"
grep -qx BSPFREEZE=yes "$test_dir/docker-args"
if grep -qx RELEASE=bookworm "$test_dir/docker-args"; then
	printf 'FAIL: launcher still defaults to Debian 12\n' >&2
	exit 1
fi
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

# The running-container check must happen before backing up/replacing the overlay.
cp "$repo_dir/README.md" "$test_dir/source/armbian-build/userpatches/do-not-replace"
export mock_active=yes
if bash "$test_dir/build.sh"; then
	printf 'FAIL: launcher allowed a second build\n' >&2
	exit 1
else
	result=$?
	[[ "$result" == 75 ]] || exit 1
fi
cmp "$repo_dir/README.md" "$test_dir/source/armbian-build/userpatches/do-not-replace"
export mock_active=no mock_revision=1111111111111111111111111111111111111111
if bash "$test_dir/build.sh"; then
	printf 'FAIL: launcher accepted an unexpected Armbian revision\n' >&2
	exit 1
fi
cmp "$repo_dir/README.md" "$test_dir/source/armbian-build/userpatches/do-not-replace"
printf 'PASS: forwarding, volumes, overlay preservation, failure propagation, active-build guard, source pin\n'
