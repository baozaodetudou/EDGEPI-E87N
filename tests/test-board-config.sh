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
[[ "$KERNELPATCHDIR" == edgepi-e87n-6.12 ]]
[[ "$SRC_CMDLINE" != *root=* ]]
[[ "$SRC_CMDLINE" != *squashfs* ]]
if declare -F write_uboot_platform >/dev/null; then
	echo 'Unexpected raw bootloader writer in E87N family' >&2
	exit 1
fi

opts_y=()
opts_m=(EXT4_FS CONFIG_EXT4_FS BTRFS_FS)
custom_kernel_config__edgepi_e87n_first_boot
[[ " ${opts_m[*]} " != *' EXT4_FS '* && " ${opts_m[*]} " != *' CONFIG_EXT4_FS '* ]]
[[ " ${opts_m[*]} " == *' BTRFS_FS '* && " ${opts_m[*]} " == *' MEDIATEK_2P5G_PHY '* ]]
for required in COMMON_CLK_MT7987 PINCTRL_MT7987 MMC_MTK SERIAL_8250_MT6577 EXT4_FS; do
	[[ " ${opts_y[*]} " == *" $required "* ]] || exit 1
done
printf 'PASS: Armbian family loader, no bootloader writer, root cmdline, first-boot driver requests\n'
