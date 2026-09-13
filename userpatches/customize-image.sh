#!/usr/bin/env bash
set -Eeuo pipefail

# Armbian runs this inside the newly-created Debian filesystem.
[[ "${3:-}" == edgepi-e87n ]] || { echo 'Unexpected target board' >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" == debian ]] || { echo 'E87N expects a Debian root filesystem' >&2; exit 1; }
case "${1:-}" in
	trixie) expected_version=13 ;;
	bookworm) expected_version=12 ;;
	*) echo 'Unsupported E87N Debian release' >&2; exit 1 ;;
esac
[[ "${VERSION_CODENAME:-}" == "$1" && "${VERSION_ID:-}" == "$expected_version" ]] || {
	echo 'Debian rootfs version does not match the requested release' >&2; exit 1;
}

# This hook runs in Armbian's target chroot, not on the build host. Refresh a
# cached rootfs against the chosen Debian release's signed repositories so the
# new image includes available stable/security updates, without changing release.
export DEBIAN_FRONTEND=noninteractive
apt-get -o APT::Update::Error-Mode=any update
apt-get -y --with-new-pkgs -o Dpkg::Options::=--force-confdef \
	-o Dpkg::Options::=--force-confold upgrade

cd /tmp/overlay/e87n-firmware
sha256sum --check SHA256SUMS
[[ "$(stat -c %s mediatek/mt7987/i2p5ge-phy-pmb.bin)" == 98304 ]]
[[ "$(stat -c %s mediatek/mt7987/i2p5ge-phy-DSPBitTb.bin)" == 28672 ]]
install -d /usr/lib/firmware/mediatek/mt7987 /usr/share/doc/e87n-phy-firmware
install -m 0644 mediatek/mt7987/*.bin /usr/lib/firmware/mediatek/mt7987/
install -m 0644 LICENCE.mediatek README.md SHA256SUMS /usr/share/doc/e87n-phy-firmware/

# Debian-native dashboard/backlight support. No musl binary, LuCI, UCI or
# userspace fan-control daemon is installed: Linux is the sole fan controller.
apt-get -y --no-install-recommends install python3 python3-pil fonts-dejavu-core
cd /tmp/overlay/e87n-board-support
install -d /usr/lib/python3/dist-packages/e87n /etc/e87n /usr/lib/systemd/system
install -m 0644 e87n/*.py /usr/lib/python3/dist-packages/e87n/
install -m 0755 e87nctl /usr/bin/e87nctl
install -m 0644 display.json /etc/e87n/display.json
install -m 0644 systemd/e87n-display.service /usr/lib/systemd/system/
install -d /etc/modules-load.d /etc/systemd/system/multi-user.target.wants
install -m 0644 e87n-display.conf /etc/modules-load.d/
install -d /usr/share/doc/e87n-display
install -m 0644 README.md /usr/share/doc/e87n-display/
ln -s /usr/lib/systemd/system/e87n-display.service /etc/systemd/system/multi-user.target.wants/e87n-display.service
# Do not start this service in the image-building chroot.
