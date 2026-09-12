#!/usr/bin/env bash
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0 DEBIAN_FRONTEND=noninteractive ALLOW_ROOT=yes

# The official prebuilt Armbian image already has these. A Debian trixie
# base image may also be used; Armbian installs its remaining host tools.
if ! command -v git >/dev/null || ! command -v sudo >/dev/null || ! command -v curl >/dev/null; then
	apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update
	apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
		ca-certificates git sudo curl xz-utils procps
fi
# Prevent concurrent compilers from changing the same source/cache volumes.
exec flock --nonblock --conflict-exit-code 75 cache/e87n-build.lock bash ./compile.sh "$@"
