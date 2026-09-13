#!/usr/bin/env bash
# Intended exclusively for a disposable GitHub-hosted Ubuntu ARM64 job.
set -Eeuo pipefail
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[[ ${GITHUB_ACTIONS:-} == true && ${E87N_RUNNER_ENVIRONMENT:-} == github-hosted ]] || \
	fail 'cleanup is restricted to GitHub-hosted runners'
[[ ${E87N_REPOSITORY_VISIBILITY:-} == public ]] || fail 'this image job targets public repositories'
[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || fail 'requires native Linux aarch64'
# shellcheck source=/dev/null
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || fail 'requires Ubuntu 24.04'
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ $(realpath -- "${GITHUB_WORKSPACE:?}") == "$repo_dir" ]] || fail 'workspace mismatch'
sudo -n true
df -h "$repo_dir"
free -h

# A fixed SDK allowlist only. Never touch Docker storage, the complete toolcache,
# runner state, swap, source/output directories, or a user-supplied cleanup path.
# Refuse symlinks and mounts, including nested mounts, before removing an SDK.
for sdk in /usr/share/dotnet /usr/local/lib/android /opt/hostedtoolcache/CodeQL; do
	[[ -e $sdk || -L $sdk ]] || continue
	[[ -d $sdk && ! -L $sdk && $(realpath -- "$sdk") == "$sdk" ]] || fail "unexpected SDK path: $sdk"
	mounts=$(findmnt --raw --noheadings --output TARGET)
	while IFS= read -r target; do
		[[ $target != "$sdk" && $target != "$sdk/"* ]] || fail "SDK contains a mount: $target"
	done <<< "$mounts"
	sudo -n du -sh -- "$sdk"
	sudo -n rm --recursive --force --one-file-system -- "$sdk"
done
sudo -n apt-get clean
sudo -n apt-get update
sudo -n apt-get install -y --no-install-recommends \
	bash ca-certificates git sudo curl xz-utils procps util-linux python3 \
	device-tree-compiler u-boot-tools initramfs-tools-core fdisk gdisk e2fsprogs zstd libcrypt1
sudo -n apt-get clean
df -h "$repo_dir"
available_kib=$(df -Pk "$repo_dir" | awk 'END { print $4 }')
[[ $available_kib =~ ^[0-9]+$ ]] || fail 'could not measure available workspace disk'
# Headroom for shallow kernel sources, object files, rootfs and compression.
# This is a preflight floor, not a guarantee of peak disk consumption.
(( available_kib >= 30 * 1024 * 1024 )) || fail 'need at least 30 GiB free after preparation; inspect runner.log'
printf 'PASS: native runner ready; Armbian will install the remaining host dependencies.\n'
