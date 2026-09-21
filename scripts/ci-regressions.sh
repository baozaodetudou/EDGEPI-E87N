#!/usr/bin/env bash
# Linux fixture suite only. Never run the live smoke/evidence scripts here.
set -Eeuo pipefail
umask 022
export PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
[[ $(uname -s) == Linux ]] || { printf 'Full CI regression suite requires Linux.\n' >&2; exit 1; }
for tool in python3 cc dtc fdtget fdtput patch zstd xz dpkg dpkg-deb md5sum \
	deb-systemd-helper deb-systemd-invoke py3clean mkimage dumpimage \
	mkfs.ext4 e2fsck resize2fs debugfs blkid sfdisk losetup mount umount findmnt lsinitramfs; do
	command -v "$tool" >/dev/null || { printf 'Missing regression dependency: %s\n' "$tool" >&2; exit 1; }
done
python3 -c 'import PIL, yaml'
patch --version | head -n 1
[[ -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf && \
	-f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf && \
	-f /usr/share/fonts/truetype/wqy/wqy-microhei.ttc ]]
sudo -n true
regression_tmp=$(mktemp -d "${RUNNER_TEMP:-/var/tmp}/e87n-regressions.XXXXXXXX")
# Retained large fixtures must not fill a Debian RAM-backed /tmp.
export TMPDIR="$regression_tmp"
mkdir -p output/ci/logs
failed=0
run_fixture() {
	local name=$1
	shift
	if "$@" 2>&1 | tee "output/ci/logs/$name.log"; then
		printf 'PASS: %s\n' "$name"
	else
		printf 'FAIL: %s (see saved log)\n' "$name" >&2
		failed=1
	fi
}
for suite in hardware display verify-display-fan doctor network-policy; do
	run_fixture "$suite" python3 -B "tests/test-$suite.py"
done
# Both suites require real root-owned temporary directories. Package lifecycle
# uses dpkg --root=<unique fixture> and stub runtime commands, never host install.
run_fixture verify-system sudo -n python3 -B tests/test-verify-system.py
run_fixture display-package sudo -n python3 -B tests/test-display-package.py
run_fixture factory-firmware sudo -n python3 -B tests/test-factory-firmware.py
run_fixture factory-rootfs sudo -n python3 -B tests/test-factory-rootfs.py
run_fixture ramdiag python3 -B tests/test-ramdiag.py
run_fixture board-hook bash tests/test-board-config.sh --hook-only
for suite in launcher lima-launcher verify-artifacts verify-initramfs verify-image \
	verify-lts-platform collect-board-evidence; do
	run_fixture "$suite" bash "tests/test-$suite.sh"
done
# Pick up the image-defaults fixture when added by the recipe implementation.
for suite in tests/test-image-defaults.py tests/test-image-defaults.sh; do
	[[ -f $suite ]] || continue
	case $suite in
		*.py) run_fixture image-defaults-python sudo -n python3 -B "$suite" ;;
		*.sh) run_fixture image-defaults-shell bash "$suite" ;;
	esac
done
exit "$failed"
