#!/usr/bin/env bash
# CI adapter only; the board recipe and display package builder own their policy.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0 DEBIAN_FRONTEND=noninteractive
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
[[ $# == 1 && ( $1 == image || $1 == display ) ]] || {
	printf 'Usage: bash scripts/ci-build.sh image|display\n' >&2
	exit 2
}
kind=$1
mkdir -p output/ci/logs
exec > >(tee "output/ci/logs/$kind.log") 2>&1
record_exit() {
	local result=$?
	printf '%s\n' "$result" > "output/ci/logs/$kind.exit-code"
}
trap record_exit EXIT

[[ ${E87N_KERNEL_VERSION:-6.18.51} == 6.18.51 ]] || {
	printf 'FAIL: kernel version must be the reviewed pin 6.18.51\n' >&2
	exit 2
}

if [[ $kind == display ]]; then
	# Do not duplicate VERSION parsing or invent a CI-only Debian version.
	[[ -s packaging/e87n-display/VERSION && -f scripts/build-display-deb.sh ]] || {
		printf 'FAIL: display packaging implementation or VERSION is missing\n' >&2
		exit 1
	}
	bash scripts/build-display-deb.sh --output-dir "$repo_dir/output/ci/display-debs"
	shopt -s nullglob
	packages=(output/ci/display-debs/e87n-display_*.deb)
	[[ ${#packages[@]} == 1 && -s ${packages[0]} && ! -L ${packages[0]} ]] || {
		printf 'FAIL: expected one nonempty versioned e87n-display .deb\n' >&2
		exit 1
	}
	dpkg-deb --info "${packages[0]}"
	exit 0
fi

[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || {
	printf 'FAIL: image CI requires a native Linux aarch64 runner\n' >&2
	exit 1
}
# Reject stale products before invoking the existing launcher. Hosted jobs use
# fresh checkouts; retaining these directories makes a failed manual retry clear.
[[ ! -e source/armbian-build/output && ! -L source/armbian-build/output &&
   ! -e output/ci/firmware && ! -L output/ci/firmware ]] || {
	printf 'FAIL: existing Armbian or firmware output; use a fresh job, do not reuse candidates\n' >&2
	exit 1
}
grep -Fq "commit:f6388029ea9e2c9e807d73827658738ea131faee" \
	userpatches/config/sources/families/edgepi-e87n.conf
grep -Fq '7c1bb29eb0e7bd75b0703d86fe654b2680e646da' build-armbian.sh
sudo -n true
# Explicit `build` bypasses the framework's undecided/interactive command.
# PREFER_DOCKER=no makes the framework use native sudo even with Docker present.
# Empty CARD_DEVICE/SEND_TO_SERVER disable device writing and external publishing.
bash build-armbian.sh build \
	BOARD=edgepi-e87n BRANCH=current RELEASE=trixie \
	BUILD_DESKTOP=no BUILD_MINIMAL=yes BSPFREEZE=yes \
	E87N_EXTRA_STORAGE=no \
	KERNEL_CONFIGURE=no KERNEL_BTF=no EXTRAWIFI=no \
	CPUTHREADS=4 USE_TMPFS=no KERNEL_GIT=shallow \
	PREFER_DOCKER=no COMPRESS_OUTPUTIMAGE=xz \
	CARD_DEVICE= SEND_TO_SERVER=

shopt -s nullglob
images=(source/armbian-build/output/images/*.img.xz)
[[ ${#images[@]} == 1 ]] || { printf 'FAIL: expected exactly one .img.xz build image\n' >&2; exit 1; }
candidate=${images[0]}
[[ -f $candidate && -s $candidate && ! -L $candidate ]] || { printf 'FAIL: invalid image %s\n' "$candidate" >&2; exit 1; }
audit_dir=$(mktemp -d "${RUNNER_TEMP:?}/e87n-ci-audit.XXXXXXXX")
printf 'Read-only image audit: %s -> %s/candidate.img\n' "$candidate" "$audit_dir"
# Decompression verifies xz integrity; raw scratch never enters the artifact
# tree. Retain it for the remainder of this disposable job, even on failure.
xz -dc -- "$candidate" > "$audit_dir/candidate.img"
sudo -n bash scripts/verify-image.sh --release trixie --require-usb-root \
	--headless --require-system "$audit_dir/candidate.img" \
	2>&1 | tee output/ci/logs/image-audit-1.log

image_basename=${candidate##*/}
firmware_output="$repo_dir/output/ci/firmware/${image_basename%.img.xz}-uboot-firmware.tar"
mkdir -p "$repo_dir/output/ci/firmware"
sudo -n python3 scripts/build-factory-firmware.py \
	--image "$audit_dir/candidate.img" --output "$firmware_output" --headless
# The runner owns this log directory; only the verifier needs root privileges.
# shellcheck disable=SC2024
sudo -n python3 scripts/verify-factory-firmware.py "$firmware_output" --headless \
	> output/ci/logs/factory-firmware-audit-1.log 2>&1
printf 'PASS: image and factory firmware static audits passed; hardware validation is pending.\n'
