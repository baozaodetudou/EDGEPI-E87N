# EdgePi E87N: MediaTek MT7987A, eMMC, dual 2.5GbE, NVMe and USB3.
BOARD_NAME="EdgePi E87N"
BOARD_VENDOR="EdgePi"
BOARDFAMILY="filogic"
BOARD_MAINTAINER="local"
INTRODUCED="2026"
KERNEL_TARGET="current"
KERNEL_TEST_TARGET="current"
BOOT_SOC="mt7987"
BOOTCONFIG="none"
BOOT_FDT_FILE="mediatek/mt7987a-edgepi-e87n.dtb"
SRC_EXTLINUX="yes"
BOOTFS_TYPE="ext4"
BOOTSIZE=256
IMAGE_PARTITION_TABLE="gpt"
SERIALCON="ttyS0:115200"
HAS_VIDEO_OUTPUT="no"
SRC_CMDLINE="console=ttyS0,115200n8 root=PARTLABEL=rootfs rootwait rootfstype=ext4 rw"

function post_family_config__edgepi_e87n_existing_uboot() {
	# The factory U-Boot is retained. Armbian produces the Debian image and
	# kernel artifacts without writing BL2/FIP/U-Boot into the image.
	declare -g ATF_COMPILE="no"
	declare -g BOOTCONFIG="none"
	declare -g UBOOT_TARGET_MAP=";;"
	declare -g BOOT_FDT_FILE="mediatek/mt7987a-edgepi-e87n.dtb"
}

