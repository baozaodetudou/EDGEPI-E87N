# MT7987 Ethernet PHY firmware

Unmodified files from [linux-firmware commit c0af6c70df291701fdecf6402e47dd4564e6b718](https://gitlab.com/kernel-firmware/linux-firmware/-/commit/c0af6c70df291701fdecf6402e47dd4564e6b718), version 7.1 (2025-08-22).

The files are redistributed separately from the GPL kernel under `LICENCE.mediatek`. They are installed into `/usr/lib/firmware/mediatek/mt7987/` in the Debian image, not linked into the kernel. `customize-image.sh` checks SHA-256 and file sizes before installation.

- `i2p5ge-phy-pmb.bin`: 98304 bytes
- `i2p5ge-phy-DSPBitTb.bin`: 28672 bytes

These are PHY microcode blobs, not an OpenWrt root filesystem or bootloader.
