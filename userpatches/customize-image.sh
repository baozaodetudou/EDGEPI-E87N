#!/usr/bin/env bash
set -Eeuo pipefail
# Armbian runs this inside the NEW image, never on the live board.
[[ "${3:-}" == edgepi-e87n ]] || { echo 'Unexpected target board' >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" == debian && "${1:-}" == trixie && "${VERSION_CODENAME:-}" == trixie && "${VERSION_ID:-}" == 13 ]] || {
  echo 'E87N minimal profile requires Debian 13 Trixie' >&2; exit 1;
}
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

# Minimal OS essentials. The current production profile is headless because
# the NV3007 SPI driver is not yet hardware-validated. The display package is
# built and released separately for later installation.
apt-get -y --no-install-recommends install openssh-server ca-certificates \
  iproute2 netplan.io systemd-resolved systemd-timesyncd tzdata locales
if [[ ! -e /tmp/overlay/e87n-board-support/DISPLAY_DISABLED ]]; then
  bash /tmp/overlay/e87n-package/scripts/build-display-deb.sh --output-dir /tmp/e87n-debs
  display_debs=(/tmp/e87n-debs/e87n-display_*_all.deb)
  [[ ${#display_debs[@]} == 1 && -f "${display_debs[0]}" ]] || exit 1
  apt-get -y --no-install-recommends install "${display_debs[0]}"
else
  install -d /etc/modprobe.d
  install -m 0644 /tmp/overlay/e87n-board-support/e87n-headless.conf /etc/modprobe.d/e87n-headless.conf
fi
bash /tmp/overlay/e87n-board-support/image-defaults.sh --target-chroot
apt-get clean
