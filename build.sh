#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
JOBS=${JOBS:-4}

mkdir -p "$ROOT_DIR/build" "$ROOT_DIR/output"

docker build -t e87n-armbian-builder -f "$ROOT_DIR/docker/Dockerfile" "$ROOT_DIR"

docker run --rm --privileged \
  -e JOBS="$JOBS" \
  -e ROOT_PASSWORD="${ROOT_PASSWORD:-change-me}" \
  -v "$ROOT_DIR:/workspace" \
  -w /workspace \
  e87n-armbian-builder \
  bash /workspace/scripts/container-build.sh

