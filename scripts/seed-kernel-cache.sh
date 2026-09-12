#!/usr/bin/env bash
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0

# Optional cold-cache seed for the pinned Armbian framework. Its generic shallow
# gitball can otherwise become a full-history fetch for a vendor kernel commit.
# No checkout/patch/build is performed here. Existing caches are never replaced.
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
framework_dir="$repo_dir/source/armbian-build"
framework_pin=7c1bb29eb0e7bd75b0703d86fe654b2680e646da
kernel_pin=b864732ee285e7868fb0857d69a8ff349e37003e
cache_dir="$framework_dir/cache/git-bare/shallow-kernel-6.12"

[[ $(uname -s) == Linux ]] || { printf 'Run inside the Linux build host.\n' >&2; exit 2; }
[[ $(git -C "$framework_dir" rev-parse HEAD) == "$framework_pin" ]] || {
	printf 'Unexpected framework revision; cache layout not verified.\n' >&2; exit 2;
}
mkdir -p "$framework_dir/cache/git-bare"
# Same lock as build-lima.sh; use this helper before starting the build.
exec 9>"$repo_dir/.build.lock"
flock --nonblock --conflict-exit-code 75 9
exec 8>"$framework_dir/cache/e87n-build.lock"
flock --nonblock --conflict-exit-code 75 8

if [[ -e "$cache_dir" ]]; then
	[[ -f "$cache_dir/.git/armbian-bare-tree-done" ]] || {
		printf 'Unfinished cache retained at %s; inspect before proceeding.\n' "$cache_dir" >&2; exit 2;
	}
	git -C "$cache_dir" cat-file -e "$kernel_pin^{commit}"
	printf 'Pinned kernel commit already cached: %s\n' "$kernel_pin"
	exit 0
fi

seed_dir=$(mktemp -d "$framework_dir/cache/git-bare/e87n-kernel-seed.XXXXXX")
printf 'Preparing %s (retained if interrupted)\n' "$seed_dir"
git init --initial-branch=master "$seed_dir"
git -C "$seed_dir" remote add origin https://github.com/frank-w/BPI-Router-Linux.git
git -C "$seed_dir" fetch --no-tags --depth=1 origin "$kernel_pin"
[[ $(git -C "$seed_dir" rev-parse 'FETCH_HEAD^{commit}') == "$kernel_pin" ]]
git -C "$seed_dir" update-ref refs/heads/master "$kernel_pin"
git -C "$seed_dir" fsck --full --no-reflogs
[[ $(git -C "$seed_dir" rev-parse --is-shallow-repository) == true ]]
# Armbian uses a .git-containing repository as a worktree source despite calling
# it a "bare tree". Its readiness marker is valid only after object validation.
touch "$seed_dir/.git/armbian-bare-tree-done"
mv "$seed_dir" "$cache_dir"
printf 'Validated kernel cache: %s @ %s\n' "$cache_dir" "$kernel_pin"
