# EdgePi E87N: MediaTek MT7987A, eMMC, dual 2.5GbE, NVMe and USB3.
# Experimental: the factory bootloader and first boot are not board-validated.
BOARD_NAME="EdgePi E87N"
BOARD_VENDOR="EdgePi"
BOARDFAMILY="edgepi-e87n"
BOARD_MAINTAINER="local"
INTRODUCED="2026"
KERNEL_TARGET="current"
KERNEL_TEST_TARGET="current"
BOOT_SOC="mt7987"
BOOTCONFIG="none"
BOOT_FDT_FILE="mediatek/mt7987a-edgepi-e87n.dtb"
SRC_EXTLINUX="yes"
BOOTFS_TYPE="ext4"
ROOTFS_TYPE="ext4"
BOOTSIZE=256
IMAGE_PARTITION_TABLE="gpt"
SERIALCON="ttyS0:115200"
HAS_VIDEO_OUTPUT="no"
# Armbian supplies root=UUID=... when it completes extlinux.conf.
SRC_CMDLINE="console=ttyS0,115200n8 earlycon=uart8250,mmio32,0x11000000 rootwait rootfstype=ext4"

function post_family_config__edgepi_e87n_existing_uboot() {
	# Disable bootloader artifacts only. Writing the whole image to eMMC
	# still replaces its GPT and user-area contents; see docs/first-boot.md.
	# The isolated edgepi-e87n family keeps LINUXFAMILY=filogic for packaging.
	declare -g ATF_COMPILE="no"
	declare -g BOOTCONFIG="none"
	declare -g UBOOT_TARGET_MAP=";;"
	declare -g BOOT_FDT_FILE="mediatek/mt7987a-edgepi-e87n.dtb"
}

function custom_kernel_config__edgepi_e87n_first_boot() {
	# Armbian applies opts_m after opts_y. Remove any EXT4_FS module request
	# before forcing the root filesystem driver built-in.
	# Only mutate the hook arrays: Armbian also calls this before .config exists.
	local option
	local -a remaining_modules=()
	for option in "${opts_m[@]}"; do
		case "${option#CONFIG_}" in
			EXT4_FS) ;;
			*) remaining_modules+=("${option}") ;;
		esac
	done
	opts_m=("${remaining_modules[@]}")
	opts_y+=(
		ARCH_MEDIATEK OF PINCTRL_MT7987 COMMON_CLK_MT7987
		REGULATOR_FIXED_VOLTAGE
		WATCHDOG MEDIATEK_WATCHDOG MFD_SYSCON NVMEM NVMEM_MTK_EFUSE
		MMC MMC_BLOCK MMC_MTK PARTITION_ADVANCED EFI_PARTITION
		SERIAL_8250 SERIAL_8250_CONSOLE SERIAL_8250_MT6577
		SERIAL_OF_PLATFORM SERIAL_EARLYCON
		DEVTMPFS DEVTMPFS_MOUNT BLK_DEV_INITRD RD_GZIP RD_ZSTD
		EXT4_FS EXT4_FS_POSIX_ACL EXT4_FS_SECURITY
		FW_LOADER
	)
	# The MT7987 PHY firmware is installed in rootfs; keep its driver modular.
	opts_m+=("MEDIATEK_2P5G_PHY")
}
