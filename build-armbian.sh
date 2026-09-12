#!/usr/bin/env bash
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ARMBIAN_DIR="$ROOT_DIR/source/armbian-build"
ARMBIAN_REF=${ARMBIAN_REF:-7c1bb29eb0e7bd75b0703d86fe654b2680e646da}

# Check before replacing the overlay: a detached build may outlive its launcher.
# These E87N containers share the same named cache/temp volumes.
if [ "$(uname -s)" = Darwin ]; then
	ACTIVE_BUILDS=$(docker ps --filter name=e87n-armbian- --filter status=running --format '{{.Names}}')
	if [ -n "$ACTIVE_BUILDS" ]; then
		printf 'An E87N build is already running; inspect it before retrying:\n%s\n' "$ACTIVE_BUILDS" >&2
		exit 75
	fi
fi

mkdir -p "$ROOT_DIR/source"
if [ ! -d "$ARMBIAN_DIR/.git" ]; then
	git init "$ARMBIAN_DIR"
	git -C "$ARMBIAN_DIR" remote add origin https://github.com/armbian/build.git
	git -C "$ARMBIAN_DIR" fetch --depth 1 origin "$ARMBIAN_REF"
	git -C "$ARMBIAN_DIR" checkout --detach FETCH_HEAD
fi
ARMBIAN_ACTUAL=$(git -C "$ARMBIAN_DIR" rev-parse HEAD)
if [[ "$ARMBIAN_REF" =~ ^[0-9a-f]{40}$ && "$ARMBIAN_ACTUAL" != "$ARMBIAN_REF" ]]; then
	printf 'Armbian checkout mismatch: expected %s, found %s. Existing checkout left unchanged.\n' \
		"$ARMBIAN_REF" "$ARMBIAN_ACTUAL" >&2
	exit 1
fi
printf 'Armbian source: %s\n' "$ARMBIAN_ACTUAL"
if [ "$(git -C "$ARMBIAN_DIR" config --get core.sparseCheckout || true)" = true ]; then
	git -C "$ARMBIAN_DIR" sparse-checkout disable
fi
bash "$ROOT_DIR/scripts/prepare-framework.sh" "$ARMBIAN_DIR"

# Keep the previous overlay recoverable, including any local build adjustments.
if [ -d "$ARMBIAN_DIR/userpatches" ]; then
	OVERLAY_BACKUP=$(mktemp -d "$ARMBIAN_DIR/userpatches-backup.XXXXXX")
	mv "$ARMBIAN_DIR/userpatches" "$OVERLAY_BACKUP/userpatches"
fi
cp -a "$ROOT_DIR/userpatches" "$ARMBIAN_DIR/userpatches"
mkdir -p "$ARMBIAN_DIR/userpatches/overlay"
cp -a "$ROOT_DIR/firmware" "$ARMBIAN_DIR/userpatches/overlay/e87n-firmware"

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
	USE_TMPFS=no
	KERNEL_GIT=shallow
	EXTRAWIFI=no
	CPUTHREADS=4
)
ARGS+=("$@")

if [ "$(uname -s)" = Darwin ]; then
	mkdir -p "$ARMBIAN_DIR/output/logs"
	BUILD_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
	BUILD_CONTAINER="e87n-armbian-$BUILD_RUN_ID"
	BUILD_LOG="$ARMBIAN_DIR/output/logs/launcher-$BUILD_RUN_ID.log"
	BUILD_IMAGE="${E87N_BUILD_IMAGE:-ghcr.io/armbian/docker-armbian-build:armbian-debian-trixie-latest}"
	# Native Linux volumes are necessary for case-sensitive kernel sources,
	# symlinks, device nodes, and loop-mounted root filesystems on macOS.
	# Retain the named container for diagnosis if the client disconnects.
	docker run --detach --name "$BUILD_CONTAINER" --privileged \
		--cpus "${E87N_BUILD_CPUS:-4}" --memory "${E87N_BUILD_MEMORY:-4g}" \
		-e ARMBIAN_RUNNING_IN_CONTAINER=yes \
		-e GIT_TERMINAL_PROMPT=0 -e DEBIAN_FRONTEND=noninteractive \
		-v "$ROOT_DIR:/workspace" \
		-v e87n-armbian-cache:/workspace/source/armbian-build/cache \
		-v e87n-armbian-tmp:/workspace/source/armbian-build/.tmp \
		-v /dev:/tmp/dev:ro \
		-w /workspace/source/armbian-build \
		--entrypoint /bin/bash "$BUILD_IMAGE" \
		/workspace/scripts/container-entrypoint.sh "${ARGS[@]}"
	printf 'Container: %s\nLog: %s\n' "$BUILD_CONTAINER" "$BUILD_LOG"
	docker logs --follow "$BUILD_CONTAINER" 2>&1 | tee "$BUILD_LOG"
	BUILD_EXIT=$(docker wait "$BUILD_CONTAINER")
	printf 'Build exit code: %s (container retained: %s)\n' "$BUILD_EXIT" "$BUILD_CONTAINER"
	exit "$BUILD_EXIT"
else
	exec ./compile.sh "${ARGS[@]}"
fi
