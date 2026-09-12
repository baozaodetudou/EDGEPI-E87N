#!/usr/bin/env bash
set -Eeuo pipefail

# Armbian runs this inside the newly-created Debian filesystem.
[[ "${3:-}" == edgepi-e87n ]] || { echo 'Unexpected target board' >&2; exit 1; }
. /etc/os-release
[[ "$ID" == debian ]] || { echo 'E87N expects a Debian root filesystem' >&2; exit 1; }
cd /tmp/overlay/e87n-firmware
sha256sum --check SHA256SUMS
[[ "$(stat -c %s mediatek/mt7987/i2p5ge-phy-pmb.bin)" == 98304 ]]
[[ "$(stat -c %s mediatek/mt7987/i2p5ge-phy-DSPBitTb.bin)" == 28672 ]]
install -d /usr/lib/firmware/mediatek/mt7987 /usr/share/doc/e87n-phy-firmware
install -m 0644 mediatek/mt7987/*.bin /usr/lib/firmware/mediatek/mt7987/
install -m 0644 LICENCE.mediatek README.md SHA256SUMS /usr/share/doc/e87n-phy-firmware/
