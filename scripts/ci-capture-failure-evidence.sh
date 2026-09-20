#!/usr/bin/env bash
# Capture bounded, non-secret build context before artifact collection.
# This never reads credentials or arbitrary environment variables.
set -Eeuo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
[[ $# == 2 ]] || { printf 'Usage: %s validate|image|display status\n' "$0" >&2; exit 2; }
kind=$1
status=$2
case "$kind" in
	validate|image|display) ;;
	*) printf 'FAIL: unsupported evidence kind: %s\n' "$kind" >&2; exit 2 ;;
esac

evidence_dir="output/ci/failure-evidence/$kind"
mkdir -p "$evidence_dir/framework-tail"
umask 077

cat > "$evidence_dir/run.txt" <<EOF
kind=$kind
build_step_outcome=$status
captured_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
github_sha=${GITHUB_SHA:-unknown}
github_run_id=${GITHUB_RUN_ID:-unknown}
github_run_attempt=${GITHUB_RUN_ATTEMPT:-unknown}
runner_os=${RUNNER_OS:-unknown}
runner_arch=${RUNNER_ARCH:-unknown}
runner_environment=${RUNNER_ENVIRONMENT:-unknown}
EOF

capture() {
	local name=$1
	shift
	if "$@" >"$evidence_dir/$name" 2>&1; then
		return 0
	fi
	printf 'command failed: %q\n' "$*" >>"$evidence_dir/$name"
}

capture uname.txt uname -a
capture disk-space.txt df -h
capture inode-space.txt df -i
capture memory.txt free -h
capture mounts.txt mount
capture findmnt.txt findmnt -R
capture git-status.txt git status --short --branch
capture git-head.txt git show -s --format=fuller HEAD

{
	printf '%s\n' '--- image output files ---'
	if [[ -d source/armbian-build/output ]]; then
		find source/armbian-build/output -xdev -type f -printf '%s %p\n' | sort -n | tail -n 200
	else
		printf '%s\n' '(missing source/armbian-build/output)'
	fi
	printf '%s\n' '--- temporary build directories ---'
	if [[ -d source/armbian-build/.tmp ]]; then
		find source/armbian-build/.tmp -xdev -maxdepth 3 -printf '%y %s %p\n' | sort | head -n 500
	else
		printf '%s\n' '(missing source/armbian-build/.tmp)'
	fi
} >"$evidence_dir/workspace-inventory.txt"

# Keep only bounded tails. Full allowlisted logs are collected separately when
# available; this copy remains useful when the framework stops before producing
# its normal output tree.
if [[ -d source/armbian-build/output/logs ]]; then
	while IFS= read -r -d '' log; do
		rel=${log#source/armbian-build/output/logs/}
		target="$evidence_dir/framework-tail/$rel.tail"
		mkdir -p "$(dirname "$target")"
		if ! tail -c 262144 "$log" >"$target" 2>&1; then
			printf 'tail failed for %s\n' "$log" >"$target"
		fi
	done < <(find source/armbian-build/output/logs -xdev -type f \( \
		-name '*.log' -o -name '*.txt' -o -name '*.json' -o -name '*.html' -o \
		-name '*.gz' -o -name '*.xz' -o -name '*.zst' \) -print0 | sort -z)
fi

printf 'PASS: bounded %s failure evidence captured in %s\n' "$kind" "$evidence_dir"
