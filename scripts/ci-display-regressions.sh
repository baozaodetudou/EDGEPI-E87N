#!/usr/bin/env bash
# Focused Linux fixtures for the independently released display package.
set -Eeuo pipefail
umask 022
export PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

[[ $(uname -s) == Linux ]] || {
	printf 'Display regression suite requires Linux.\n' >&2
	exit 1
}
for tool in python3 dpkg dpkg-query dpkg-deb md5sum deb-systemd-helper \
	deb-systemd-invoke py3clean dtc fdtget xz; do
	command -v "$tool" >/dev/null || {
		printf 'Missing display regression dependency: %s\n' "$tool" >&2
		exit 1
	}
done
python3 -c 'import PIL'
[[ -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf && \
	-f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf && \
	-f /usr/share/fonts/truetype/wqy/wqy-microhei.ttc ]]
sudo -n true

regression_tmp=$(mktemp -d "${RUNNER_TEMP:-/var/tmp}/e87n-display-regressions.XXXXXXXX")
export TMPDIR="$regression_tmp"
mkdir -p output/ci/logs
failed=0
run_fixture() {
	local name=$1
	shift
	if "$@" 2>&1 | tee "output/ci/logs/display-$name.log"; then
		printf 'PASS: display %s\n' "$name"
	else
		printf 'FAIL: display %s (see saved log)\n' "$name" >&2
		failed=1
	fi
}

for suite in hardware display doctor verify-display-fan; do
	run_fixture "$suite" python3 -B "tests/test-$suite.py"
done
# Exercise the real package archive and isolated dpkg lifecycle as root without
# installing into, or starting services on, the host system.
run_fixture display-package sudo -n python3 -B tests/test-display-package.py

exit "$failed"
