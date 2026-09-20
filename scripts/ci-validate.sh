#!/usr/bin/env bash
# Static checks and isolated fixtures only: no package/image build or sudo.
set -Eeuo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
for tool in actionlint shellcheck python3; do
	command -v "$tool" >/dev/null || { printf 'Missing validation tool: %s\n' "$tool" >&2; exit 1; }
done
actionlint .github/workflows/*.yml
for script in scripts/ci-*.sh; do bash -n "$script"; done
shellcheck scripts/ci-*.sh
python3 -B - scripts/ci-*.py scripts/build-factory-firmware.py \
	scripts/verify-factory-firmware.py scripts/factory_firmware.py \
	scripts/prepare-factory-rootfs.py board-support/factory-boot/factory_boot.py \
	scripts/ramdiag/*.py tests/test-ci-*.py tests/test-ramdiag.py \
	tests/test-factory-firmware.py tests/test-factory-rootfs.py <<'PY'
from pathlib import Path
import sys

# Compile in memory so validation cannot leave bytecode in the source tree.
for name in sys.argv[1:]:
    compile(Path(name).read_bytes(), name, "exec")
print("PASS: CI and factory firmware Python compilation")
PY
python3 tests/test-ci-workflow.py
python3 tests/test-ci-prepare-release.py
python3 tests/test-ci-publish-release.py
