#!/usr/bin/env bash
set -Ee -o pipefail
if (( BASH_VERSINFO[0] < 5 )); then
	echo 'This test requires Bash 5, like the Armbian builder.' >&2
	exit 2
fi
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC="${1:-$repo_dir/source/armbian-build}"
USERPATCHES_PATH="$repo_dir/userpatches"
export BRANCH=current
export BOARD=edgepi-e87n
display_alert() { :; }
track_general_config_variables() { :; }
exit_with_error() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

source "$SRC/lib/functions/configuration/main-config.sh"
source "$USERPATCHES_PATH/config/boards/edgepi-e87n.csc"
export LINUXFAMILY="$BOARDFAMILY"
# Use the real Armbian family/architecture loader to catch early source errors.
source_family_config_and_arch
post_family_config__edgepi_e87n_existing_uboot
[[ "$ARCH" == arm64 && "$BOOT_SOC" == mt7987 ]]
[[ "$BOOTCONFIG" == none && "$ATF_COMPILE" == no ]]
[[ "$BOOT_FDT_FILE" == mediatek/mt7987a-edgepi-e87n.dtb ]]
[[ "$BOOTFS_TYPE" == ext4 && "$ROOTFS_TYPE" == ext4 ]]
[[ "$KERNELPATCHDIR" == edgepi-e87n-6.18 ]]
[[ "$KERNEL_MAJOR_MINOR" == 6.18 ]]
[[ "$KERNELBRANCH" == commit:f6388029ea9e2c9e807d73827658738ea131faee ]]
[[ "$LINUXCONFIG" == linux-edgepi-e87n-lts ]]
[[ -f "$USERPATCHES_PATH/config/kernel/$LINUXCONFIG.config" ]]
# Armbian's patching.py uses USERPATCHES_PATH/kernel, not userpatches/patch/kernel.
patch_files=("$USERPATCHES_PATH/kernel/$KERNELPATCHDIR/"*.patch)
expected_prefixes=(0000 360 361 740 750 752 790 791 792 821 830 843 900)
[[ ${#patch_files[@]} == "${#expected_prefixes[@]}" ]] || exit_with_error 'Expected exactly 13 Linux 6.18 patches'
for index in "${!expected_prefixes[@]}"; do
	patch_name=${patch_files[$index]##*/}
	[[ -f "${patch_files[$index]}" && "${patch_name%%-*}" == "${expected_prefixes[$index]}" ]] ||
		exit_with_error 'Missing, duplicate or unexpected patch prefix' "$patch_name"
done
[[ "$SRC_CMDLINE" != *root=* ]]
[[ "$SRC_CMDLINE" != *squashfs* ]]
EXTRA_BUILD_DEPS=()
add_host_dependencies__edgepi_e87n_image_validation
[[ " ${EXTRA_BUILD_DEPS[*]} " == *' core::initramfs-tools-core '* ]]
if declare -F write_uboot_platform >/dev/null; then
	echo 'Unexpected raw bootloader writer in E87N family' >&2
	exit 1
fi

required_builtins=(
	SPI SPI_MASTER SPI_MT65XX STAGING FB FB_DEVICE BACKLIGHT_CLASS_DEVICE BACKLIGHT_PWM
	COMMON_CLK_MT7987 PINCTRL_MT7987 MMC_MTK SERIAL_8250_MT6577 EXT4_FS
	MEDIATEK_WATCHDOG MFD_SYSCON NVMEM NVMEM_MTK_EFUSE
	HWMON THERMAL THERMAL_OF THERMAL_GOV_STEP_WISE THERMAL_DEFAULT_GOV_STEP_WISE
	MTK_THERMAL MTK_LVTS_THERMAL PWM PWM_MEDIATEK SENSORS_PWM_FAN
)
module_conflicts=(
	SPI SPI_MASTER SPI_MT65XX STAGING FB FB_DEVICE BACKLIGHT_CLASS_DEVICE BACKLIGHT_PWM
	EXT4_FS NVMEM NVMEM_MTK_EFUSE HWMON THERMAL THERMAL_OF
	MTK_THERMAL MTK_LVTS_THERMAL PWM PWM_MEDIATEK SENSORS_PWM_FAN
)
disabled_options=(CPU_FREQ CPU_THERMAL FRAMEBUFFER_CONSOLE)

has_option() {
	local wanted=$1 option
	shift
	for option in "$@"; do
		if [[ "${option#CONFIG_}" == "$wanted" ]]; then return 0; fi
	done
	return 1
}

assert_first_boot_requests() {
	local required
	for required in "${required_builtins[@]}"; do
		has_option "$required" "${opts_y[@]}" || exit_with_error 'Missing built-in request' "$required"
		if has_option "$required" "${opts_m[@]}"; then
			exit_with_error 'Module request overrides required built-in' "$required"
		fi
	done
	# Armbian applies n, then y, then m: disabling DVFS requires removing
	# every enabled request from both arrays, not just adding opts_n entries.
	for required in "${disabled_options[@]}"; do
		has_option "$required" "${opts_n[@]}" || exit_with_error 'Missing disable request' "$required"
		if has_option "$required" "${opts_y[@]}" "${opts_m[@]}"; then
			exit_with_error 'Enabled request overrides opts_n' "$required"
		fi
	done
	has_option MEDIATEK_2P5GE_PHY "${opts_m[@]}" || exit_with_error 'Linux 6.18 PHY must be modular'
	if has_option MEDIATEK_2P5G_PHY "${opts_y[@]}" "${opts_m[@]}"; then
		exit_with_error 'Legacy Linux 6.12 PHY symbol requested'
	fi
}

for scenario in empty inherited_conflicts; do
	opts_y=()
	opts_m=()
	opts_n=()
	if [[ "$scenario" == inherited_conflicts ]]; then
		# Both spellings and repeated requests must be removed completely.
		opts_y=(BINFMT_MISC MEDIATEK_2P5GE_PHY CONFIG_MEDIATEK_2P5GE_PHY)
		opts_m=(BTRFS_FS CONFIG_BTRFS_FS MEDIATEK_2P5GE_PHY CONFIG_MEDIATEK_2P5GE_PHY)
		opts_n=(DEBUG_INFO)
		for required in "${module_conflicts[@]}" "${disabled_options[@]}"; do
			opts_m+=("$required" "CONFIG_$required" "$required" "CONFIG_$required")
		done
		for required in "${disabled_options[@]}"; do
			opts_y+=("$required" "CONFIG_$required" "$required" "CONFIG_$required")
		done
	fi
	# Repeated hook calls must preserve the same effective configuration.
	for pass in 1 2; do
		custom_kernel_config__edgepi_e87n_first_boot
		assert_first_boot_requests
		if [[ "$scenario" == inherited_conflicts ]]; then
			has_option BTRFS_FS "${opts_m[@]}" || exit_with_error 'Unrelated module request lost'
			has_option BINFMT_MISC "${opts_y[@]}" || exit_with_error 'Unrelated built-in request lost'
			has_option DEBUG_INFO "${opts_n[@]}" || exit_with_error 'Unrelated disable request lost'
		fi
		printf 'PASS: first-boot hook %s, pass %s\n' "$scenario" "$pass"
	done
done
printf 'PASS: pinned Linux 6.18 family, 13 patches, no bootloader writer, thermal/fan/display built-ins, DVFS/fbcon disabled, modular PHY\n'
