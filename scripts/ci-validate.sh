#!/usr/bin/env bash
# Static checks and isolated fixtures only: no package/image build or sudo.
set -Eeuo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
for tool in actionlint shellcheck python3; do
	command -v "$tool" >/dev/null || { printf 'Missing validation tool: %s\n' "$tool" >&2; exit 1; }
done
actionlint .github/workflows/build-e87n.yml
for script in scripts/ci-*.sh; do bash -n "$script"; done
shellcheck scripts/ci-*.sh
python3 tests/test-ci-workflow.py
