#!/usr/bin/env bash
# CI adapter only; the board recipe and display package builder own their policy.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0 DEBIAN_FRONTEND=noninteractive
# Keep each package build deterministic; Firmware and Display releases run independently.
export SOURCE_DATE_EPOCH=0
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

kernel_version=$(python3 scripts/build_config.py kernel_version)
[[ ${E87N_KERNEL_VERSION:-$kernel_version} == "$kernel_version" ]] || {
	printf 'FAIL: kernel version must be the reviewed pin %s\n' "$kernel_version" >&2
	exit 2
}
release=$(python3 scripts/build_config.py release)
board=$(python3 scripts/build_config.py board)
extra_storage=$(python3 scripts/build_config.py extra_storage)

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
   ! -e output/ci/firmware && ! -L output/ci/firmware &&
   ! -e output/ci/ramdiag && ! -L output/ci/ramdiag &&
   ! -e output/ci/simulation-display-debs && ! -L output/ci/simulation-display-debs &&
   ! -e output/ci/simulation && ! -L output/ci/simulation ]] || {
	printf 'FAIL: existing Armbian or firmware output; use a fresh job, do not reuse candidates\n' >&2
	exit 1
}
sudo -n true
# Explicit `build` bypasses the framework's undecided/interactive command.
# PREFER_DOCKER=no makes the framework use native sudo even with Docker present.
# Empty CARD_DEVICE/SEND_TO_SERVER disable device writing and external publishing.
bash build-armbian.sh build \
	BOARD="$board" BRANCH=current RELEASE="$release" \
	BUILD_DESKTOP=no BUILD_MINIMAL=yes BSPFREEZE=yes \
	E87N_EXTRA_STORAGE="$extra_storage" \
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
sudo -n bash scripts/verify-image.sh --release "$release" --require-usb-root \
	--require-display-fan --require-system "$audit_dir/candidate.img" \
	2>&1 | tee output/ci/logs/image-audit-1.log

# Build a disposable RAM-only network/SSH diagnostic matrix from the same
# image. Assembly alone does not prove boot or early userspace execution.
sudo -n bash scripts/ci-build-ramdiag.sh \
	--image "$audit_dir/candidate.img" --output-dir "$repo_dir/output/ci/ramdiag"

image_basename=${candidate##*/}
firmware_output="$repo_dir/output/ci/firmware/${image_basename%.img.xz}-uboot-firmware.tar"
mkdir -p "$repo_dir/output/ci/firmware"
sudo -n python3 scripts/build-factory-firmware.py \
	--image "$audit_dir/candidate.img" --output "$firmware_output"
# The runner owns this log directory; only the verifier needs root privileges.
# shellcheck disable=SC2024
sudo -n python3 scripts/verify-factory-firmware.py "$firmware_output" \
	> output/ci/logs/factory-firmware-audit-1.log 2>&1
# Build the current source locally for the final-firmware guest's package
# lifecycle test. The independent Display workflow owns public deb releases.
bash scripts/build-display-deb.sh --output-dir "$repo_dir/output/ci/simulation-display-debs"
packages=(output/ci/simulation-display-debs/e87n-display_*.deb)
[[ ${#packages[@]} == 1 && -s ${packages[0]} && ! -L ${packages[0]} ]] || {
	printf 'FAIL: expected one standalone display package for simulation\n' >&2
	exit 1
}
bash testing/run-container.sh \
	--firmware "$firmware_output" --display-deb "$repo_dir/${packages[0]}" \
	--output "$repo_dir/output/ci/simulation" \
	--source-commit "${GITHUB_SHA:?}" --run-id "${GITHUB_RUN_ID:?}" \
	--run-attempt "${GITHUB_RUN_ATTEMPT:?}" \
	2>&1 | tee output/ci/logs/simulation.log
printf 'PASS: static audits and container/QEMU runner completed; collection must verify same-build evidence. Hardware validation is pending.\n'
