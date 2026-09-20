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
# Opt in with ./build.sh E87N_EXTRA_STORAGE=yes; see docs/OPTIONAL-STORAGE.md.
# Only an unset option defaults to no; empty/unknown values are errors.
E87N_EXTRA_STORAGE="${E87N_EXTRA_STORAGE-no}"

function add_host_dependencies__edgepi_e87n_image_validation() {
	# Supplies host-side lsinitramfs for the final read-only image audit.
	EXTRA_BUILD_DEPS+=("core::initramfs-tools-core")
}

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
	# Validate before changing any requests (including during artifact hashing).
	case "${E87N_EXTRA_STORAGE-no}" in
		yes|no) ;;
		*)
			exit_with_error 'E87N_EXTRA_STORAGE must be yes or no' "${E87N_EXTRA_STORAGE}"
			return 1
			;;
	esac
	# Armbian applies opts_m after opts_y. Keep boot/thermal drivers built-in.
	# Only mutate the hook arrays: Armbian also calls this before .config exists.
	local option
	local -a remaining_modules=()
	local -a remaining_builtin=()
	for option in "${opts_y[@]}"; do
		case "${option#CONFIG_}" in
			CPU_FREQ|CPU_THERMAL|FRAMEBUFFER_CONSOLE|PANIC_ON_OOPS|WATCHDOG_HANDLE_BOOT_ENABLED|WATCHDOG_NOWAYOUT|WATCHDOG_PRETIMEOUT_GOV_PANIC|WATCHDOG_PRETIMEOUT_DEFAULT_GOV_PANIC) ;;
			*) remaining_builtin+=("${option}") ;;
		esac
	done
	opts_y=("${remaining_builtin[@]}")
	for option in "${opts_m[@]}"; do
		case "${option#CONFIG_}" in
			EXT4_FS|THERMAL|THERMAL_OF|MTK_THERMAL|MTK_LVTS_THERMAL|HWMON|PWM|PWM_MEDIATEK|SENSORS_PWM_FAN|NVMEM|NVMEM_MTK_EFUSE|CPU_FREQ|CPU_THERMAL|SPI|SPI_MASTER|SPI_MT65XX|STAGING|FB|FB_DEVICE|BACKLIGHT_CLASS_DEVICE|BACKLIGHT_PWM|FRAMEBUFFER_CONSOLE|PANIC_ON_OOPS|WATCHDOG_HANDLE_BOOT_ENABLED|WATCHDOG_NOWAYOUT|WATCHDOG_PRETIMEOUT_GOV_PANIC|WATCHDOG_PRETIMEOUT_DEFAULT_GOV_PANIC) ;;
			*) remaining_modules+=("${option}") ;;
		esac
	done
	opts_m=("${remaining_modules[@]}")
	opts_y+=(
		ARCH_MEDIATEK OF PINCTRL_MT7987 COMMON_CLK_MT7987
		REGULATOR_FIXED_VOLTAGE
		WATCHDOG_HANDLE_BOOT_ENABLED
		WATCHDOG MEDIATEK_WATCHDOG MFD_SYSCON NVMEM NVMEM_MTK_EFUSE
		MMC MMC_BLOCK MMC_MTK PARTITION_ADVANCED EFI_PARTITION
		SERIAL_8250 SERIAL_8250_CONSOLE SERIAL_8250_MT6577
		SERIAL_OF_PLATFORM SERIAL_EARLYCON
		DEVTMPFS DEVTMPFS_MOUNT BLK_DEV_INITRD RD_GZIP RD_ZSTD
		EXT4_FS EXT4_FS_POSIX_ACL EXT4_FS_SECURITY
		FW_LOADER
		THERMAL THERMAL_OF THERMAL_GOV_STEP_WISE THERMAL_DEFAULT_GOV_STEP_WISE
		MTK_THERMAL MTK_LVTS_THERMAL HWMON PWM PWM_MEDIATEK SENSORS_PWM_FAN
		SPI SPI_MASTER SPI_MT65XX STAGING FB FB_DEVICE
		BACKLIGHT_CLASS_DEVICE BACKLIGHT_PWM
	)
	# No validated MT7987 voltage/OPP data: retain the firmware CPU rate.
	opts_n+=(CPU_FREQ CPU_THERMAL FRAMEBUFFER_CONSOLE)
	# Do not hide a first-boot driver fault behind an automatic reset.  The
	# factory watchdog may be left enabled, but its pretimeout governor must not
	# turn a warning into a panic while the board is being brought up.
	opts_n+=(PANIC_ON_OOPS WATCHDOG_NOWAYOUT WATCHDOG_PRETIMEOUT_GOV_PANIC WATCHDOG_PRETIMEOUT_DEFAULT_GOV_PANIC)
	opts_y+=(WATCHDOG_PRETIMEOUT_GOV_NOOP WATCHDOG_PRETIMEOUT_DEFAULT_GOV_NOOP)
	opts_val["PANIC_TIMEOUT"]="0"
	# The factory U-Boot can leave the MTK watchdog running while Linux starts.
	# Let the watchdog core take ownership and ping it before userspace exists;
	# without this, a healthy kernel can be reset during early boot.
	opts_val["WATCHDOG_OPEN_TIMEOUT"]="0"
	# The serial console remains available; fbcon must not overwrite the dashboard.
	# udev and modules-load load the board-specific SPI panel after rootfs is ready.
	opts_m+=(FB_TFT FB_TFT_NV3007)
	# Upstream 6.18 uses the mediatek/ PHY subdirectory and the 2P5GE symbol.
	# Firmware is installed in rootfs; keep the matching PHY driver modular.
	opts_m+=("MEDIATEK_2P5GE_PHY")

	# Linux 6.18: optional data-volume targets, not a new rootfs path.
	local -a storage_y=() storage_m=()
	local -a storage_drivers=(
		BLK_DEV_DM DM_CRYPT DM_SNAPSHOT DM_THIN_PROVISIONING
		DM_MIRROR DM_ZERO DM_MULTIPATH BLK_DEV_MD
		MD_LINEAR MD_RAID0 MD_RAID1 MD_RAID10 MD_RAID456
	)
	local -a storage_n=(MD_AUTODETECT DM_INIT)
	if [[ "${E87N_EXTRA_STORAGE-no}" == yes ]]; then
		# Preserve generic crypto built-ins; add LUKS/legacy-volume modes
		# and the explicit XFRM userspace/interface requests only on opt-in.
		storage_y=(
			MODULES BLOCK MD NET INET IPV6 XFRM CRYPTO DM_UEVENT
			CRYPTO_AES CRYPTO_CBC CRYPTO_HMAC CRYPTO_SHA256 CRYPTO_SHA512
		)
		storage_m=("${storage_drivers[@]}" CRYPTO_XTS CRYPTO_ESSIV XFRM_USER XFRM_INTERFACE)
	else
		# Override enabled seed/family requests too: merely omitting opts_m
		# would leave unwanted DM/RAID drivers in the final kernel config.
		storage_n+=(MD DM_UEVENT "${storage_drivers[@]}" XFRM_INTERFACE)
		# Keep inherited networking and crypto (including XFRM_USER, XTS,
		# ESSIV and WireGuard). This switch does not prune the entire base.
	fi
	local storage_options=" ${storage_y[*]} ${storage_m[*]} ${storage_n[*]} "
	local -a remaining_disabled=()
	remaining_builtin=()
	remaining_modules=()
	# Normalize all three inherited arrays (including CONFIG_ aliases) so
	# repeated hooks have one unambiguous request per storage/VPN option.
	for option in "${opts_y[@]}"; do
		[[ "$storage_options" == *" ${option#CONFIG_} "* ]] || remaining_builtin+=("$option")
	done
	for option in "${opts_m[@]}"; do
		[[ "$storage_options" == *" ${option#CONFIG_} "* ]] || remaining_modules+=("$option")
	done
	for option in "${opts_n[@]}"; do
		[[ "$storage_options" == *" ${option#CONFIG_} "* ]] || remaining_disabled+=("$option")
	done
	opts_y=("${remaining_builtin[@]}" "${storage_y[@]}")
	opts_m=("${remaining_modules[@]}" "${storage_m[@]}")
	# No kernel RAID autodetection or command-line DM device creation.
	opts_n=("${remaining_disabled[@]}" "${storage_n[@]}")
}
