#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ARMBIAN_DIR="$ROOT_DIR/source/armbian-build"
ARMBIAN_REF=${ARMBIAN_REF:-main}

mkdir -p "$ROOT_DIR/source"
if [ ! -d "$ARMBIAN_DIR/.git" ]; then
	git clone --depth 1 --branch "$ARMBIAN_REF" https://github.com/armbian/build.git "$ARMBIAN_DIR"
fi

rm -rf "$ARMBIAN_DIR/userpatches"
cp -a "$ROOT_DIR/userpatches" "$ARMBIAN_DIR/userpatches"

cd "$ARMBIAN_DIR"
ARGS=(
	BOARD=edgepi-e87n
	BRANCH=current
	RELEASE=bookworm
	BUILD_DESKTOP=no
	BUILD_MINIMAL=yes
	KERNEL_BTF=no
	KERNEL_CONFIGURE=no
	SHOW_LOG=yes
)

if [ "$(uname -s)" = Darwin ]; then
	docker run --rm --privileged \
		-e ARMBIAN_RUNNING_IN_CONTAINER=yes \
		-v "$ROOT_DIR:/workspace" \
		-w /workspace/source/armbian-build \
		ghcr.io/armbian/docker-armbian-build:armbian-debian-trixie-latest \
		bash ./compile.sh "${ARGS[@]}"
else
	exec ./compile.sh "${ARGS[@]}"
fi
