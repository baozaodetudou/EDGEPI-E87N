#!/usr/bin/env bash
set -Ee -o pipefail
hook_only=no
if [[ "${1:-}" == --hook-only ]]; then
	# Native macOS Bash can test the pure hook without loading Bash 5 Armbian.
	hook_only=yes
	shift
fi
if (( BASH_VERSINFO[0] < 5 )) && [[ "$hook_only" == no ]]; then
	echo 'This test requires Bash 5, like the Armbian builder.' >&2
	exit 2
fi
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC="${1:-$repo_dir/source/armbian-build}"
USERPATCHES_PATH="$repo_dir/userpatches"
exit_with_error() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
BUILD_JSON="$USERPATCHES_PATH/config/e87n-build.json"
[[ -f "$BUILD_JSON" ]] || { printf 'ERROR: missing central build manifest: %s\n' "$BUILD_JSON" >&2; exit 1; }

json_value() {
	python3 - "$BUILD_JSON" "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)[sys.argv[2]]
if isinstance(value, bool):
    print("yes" if value else "no")
else:
    print(value)
PY
}

build_board=$(json_value board)
build_family=$(json_value linux_family)
build_kernel_source=$(json_value kernel_source)
build_kernel_commit=$(json_value kernel_commit)
build_kernel_series=$(json_value kernel_series)
build_kernel_version=$(json_value kernel_version)
build_kernel_release=$(json_value kernel_release)
[[ "$build_board" == edgepi-e87n && "$build_family" == edgepi-e87n ]] ||
	exit_with_error 'Central manifest must describe the isolated E87N family'
[[ "$build_kernel_release" == "$build_kernel_version-current-$build_family" ]] ||
	exit_with_error 'Central manifest kernel_release is not version/family derived'
export BRANCH=current
export BOARD="$build_board"
display_alert() { :; }
track_general_config_variables() { :; }

if [[ "$hook_only" == no ]]; then
	source "$SRC/lib/functions/configuration/main-config.sh"
fi
# Test the public default independently of the caller's environment.
unset E87N_EXTRA_STORAGE
source "$USERPATCHES_PATH/config/boards/edgepi-e87n.csc"
[[ "$E87N_EXTRA_STORAGE" == no ]] || exit_with_error 'Extra storage must default to no'
if [[ "$hook_only" == no ]]; then
	export LINUXFAMILY="$build_family"
	# Use the real Armbian family/architecture loader to catch early source errors.
	source_family_config_and_arch
	[[ "$LINUXFAMILY" == "$build_family" ]] || exit_with_error 'LINUXFAMILY must stay isolated from filogic'
	[[ "$KERNELSOURCE" == "$build_kernel_source" ]] || exit_with_error 'Unexpected kernel source'
	[[ "$KERNELBRANCH" == "commit:$build_kernel_commit" ]] || exit_with_error 'Unexpected kernel commit pin'
	post_family_config__edgepi_e87n_existing_uboot
	[[ "$ARCH" == arm64 && "$BOOT_SOC" == mt7987 ]]
	[[ "$BOOTCONFIG" == none && "$ATF_COMPILE" == no ]]
	[[ "$BOOT_FDT_FILE" == mediatek/mt7987a-edgepi-e87n.dtb ]]
	[[ "$BOOTFS_TYPE" == ext4 && "$ROOTFS_TYPE" == ext4 ]]
	[[ "$KERNELPATCHDIR" == edgepi-e87n-6.18 ]]
	[[ "$KERNEL_MAJOR_MINOR" == "$build_kernel_series" ]]
	[[ "$LINUXCONFIG" == linux-edgepi-e87n-lts ]]
	[[ -f "$USERPATCHES_PATH/config/kernel/$LINUXCONFIG.config" ]]
fi
# Armbian's patching.py uses USERPATCHES_PATH/kernel, not userpatches/patch/kernel.
# Check the inventory even in native hook-only mode.  MT7987 pinctrl, clock,
# Ethernet, PHY, PWM and LVTS support is already in Frank-W's 6.18.52 tree;
# these old ports must not be carried into the isolated E87N patchset again.
patch_dir="$USERPATCHES_PATH/kernel/edgepi-e87n-6.18"
shopt -s nullglob
patch_files=("$patch_dir/"*.patch)
[[ ${#patch_files[@]} -gt 0 ]] || exit_with_error 'No Linux 6.18 E87N patches found'
required_prefixes=(0000 790 791 831 832 843 900 901 902)
for prefix in "${required_prefixes[@]}"; do
	matches=("$patch_dir/"${prefix}-*.patch)
	[[ ${#matches[@]} == 1 ]] || exit_with_error 'Expected exactly one E87N patch prefix' "$prefix"
done
for prefix in 360 361 740 750 752 821 830; do
	matches=("$patch_dir/"${prefix}-*.patch)
	[[ ${#matches[@]} == 0 ]] || exit_with_error 'Upstream MT7987 support must not be duplicated' "${matches[*]}"
done
shopt -u nullglob
[[ "$BOOTFS_TYPE" == ext4 && "$ROOTFS_TYPE" == ext4 && "$BOOTCONFIG" == none ]]
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
	VIRTIO VIRTIO_MMIO VIRTIO_BLK VIRTIO_NET SERIAL_AMBA_PL011 SERIAL_AMBA_PL011_CONSOLE
	SPI SPI_MASTER SPI_MT65XX STAGING FB FB_DEVICE BACKLIGHT_CLASS_DEVICE BACKLIGHT_PWM
	COMMON_CLK_MT7987 COMMON_CLK_MT7987_ETHSYS PINCTRL_MT7987 MMC_MTK SERIAL_8250_MT6577 EXT4_FS
	MEDIATEK_WATCHDOG MFD_SYSCON NVMEM NVMEM_MTK_EFUSE WATCHDOG_HANDLE_BOOT_ENABLED
	HWMON THERMAL THERMAL_OF THERMAL_GOV_STEP_WISE THERMAL_DEFAULT_GOV_STEP_WISE
	MTK_THERMAL MTK_LVTS_THERMAL PWM PWM_MEDIATEK SENSORS_PWM_FAN
)
module_conflicts=(
	COMMON_CLK_MT7987_ETHSYS VIRTIO VIRTIO_MMIO VIRTIO_BLK VIRTIO_NET SERIAL_AMBA_PL011 SERIAL_AMBA_PL011_CONSOLE
	SPI SPI_MASTER SPI_MT65XX STAGING FB FB_DEVICE BACKLIGHT_CLASS_DEVICE BACKLIGHT_PWM
	EXT4_FS NVMEM NVMEM_MTK_EFUSE HWMON THERMAL THERMAL_OF
	MTK_THERMAL MTK_LVTS_THERMAL PWM PWM_MEDIATEK SENSORS_PWM_FAN
)
disabled_options=(CPU_FREQ CPU_THERMAL FRAMEBUFFER_CONSOLE PANIC_ON_OOPS
	WATCHDOG_NOWAYOUT WATCHDOG_PRETIMEOUT_GOV_PANIC WATCHDOG_PRETIMEOUT_DEFAULT_GOV_PANIC)
# Independent expectations, not obtained from the board's option lists.
storage_builtins=(
	MODULES BLOCK MD NET INET IPV6 XFRM CRYPTO DM_UEVENT
	CRYPTO_AES CRYPTO_CBC CRYPTO_HMAC CRYPTO_SHA256 CRYPTO_SHA512
)
storage_modules=(
	BLK_DEV_DM DM_CRYPT DM_SNAPSHOT DM_THIN_PROVISIONING
	DM_MIRROR DM_ZERO DM_MULTIPATH BLK_DEV_MD
	MD_LINEAR MD_RAID0 MD_RAID1 MD_RAID10 MD_RAID456
	CRYPTO_XTS CRYPTO_ESSIV XFRM_USER XFRM_INTERFACE
)
storage_disabled=(MD_AUTODETECT DM_INIT)
minimal_disabled=(
	MD DM_UEVENT BLK_DEV_DM DM_CRYPT DM_SNAPSHOT DM_THIN_PROVISIONING
	DM_MIRROR DM_ZERO DM_MULTIPATH BLK_DEV_MD
	MD_LINEAR MD_RAID0 MD_RAID1 MD_RAID10 MD_RAID456 XFRM_INTERFACE
	MD_AUTODETECT DM_INIT
)
shared_prerequisites=(
	MODULES BLOCK NET INET IPV6 XFRM CRYPTO
	CRYPTO_AES CRYPTO_CBC CRYPTO_HMAC CRYPTO_SHA256 CRYPTO_SHA512
	CRYPTO_XTS CRYPTO_ESSIV XFRM_USER
)
baseline_options=(
	NETDEVICES NET_MEDIATEK_SOC BLK_DEV_NVME NVME_HWMON SCSI BLK_DEV_SD
	USB USB_XHCI_HCD USB_XHCI_MTK USB_STORAGE USB_UAS
	TUN BRIDGE BRIDGE_VLAN_FILTERING NETFILTER NF_TABLES NFT_CT NFT_NAT
	WIREGUARD CRYPTO_CHACHA20POLY1305 CRYPTO_LIB_CURVE25519
)

has_option() {
	local wanted=$1 option
	shift
	for option in "$@"; do
		if [[ "${option#CONFIG_}" == "$wanted" ]]; then return 0; fi
	done
	return 1
}

count_option() {
	local wanted=$1 option count=0
	shift
	for option in "$@"; do
		if [[ "${option#CONFIG_}" == "$wanted" ]]; then count=$((count + 1)); fi
	done
	printf '%s' "$count"
}

# Exact requests, including aliases/duplicates, for options the hook must leave
# alone. Used for shared crypto/network policy as well as unrelated sentinels.
requests_for() {
	local wanted=" $* " option
	for option in "${opts_y[@]}"; do
		[[ "$wanted" != *" ${option#CONFIG_} "* ]] || printf '%s=y\n' "$option"
	done
	for option in "${opts_m[@]}"; do
		[[ "$wanted" != *" ${option#CONFIG_} "* ]] || printf '%s=m\n' "$option"
	done
	for option in "${opts_n[@]}"; do
		[[ "$wanted" != *" ${option#CONFIG_} "* ]] || printf '%s=n\n' "$option"
	done
	return 0
}

# Model Armbian's n -> y -> m precedence over the actual checked-in seed.
# No .config is created or edited; this deliberately does not model Kconfig's
# dependency resolution/olddefconfig or claim a kernel build was tested.
effective_config() {
	{
		cat "$USERPATCHES_PATH/config/kernel/linux-edgepi-e87n-lts.config"
		local option
		for option in "${opts_n[@]}"; do printf 'CONFIG_%s=n\n' "${option#CONFIG_}"; done
		for option in "${opts_y[@]}"; do printf 'CONFIG_%s=y\n' "${option#CONFIG_}"; done
		for option in "${opts_m[@]}"; do printf 'CONFIG_%s=m\n' "${option#CONFIG_}"; done
	} | awk '
		/^CONFIG_[A-Za-z0-9_]+=/ {
			key = substr($0, 1, index($0, "=") - 1)
			values[key] = substr($0, index($0, "=") + 1)
		}
		/^# CONFIG_[A-Za-z0-9_]+ is not set$/ { values[$2] = "n" }
		END { for (key in values) print key "=" values[key] }
	' | LC_ALL=C sort
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
	[[ "$BOOTFS_TYPE" == ext4 && "$ROOTFS_TYPE" == ext4 && "$BOOTCONFIG" == none ]]
	[[ "$SRC_CMDLINE" != *root=* && "$SRC_CMDLINE" != *dm-mod.create=* && "$SRC_CMDLINE" != *md=* ]]
}

assert_storage_requests() {
	local required
	if [[ "${E87N_EXTRA_STORAGE-no}" == no ]]; then
		for required in "${minimal_disabled[@]}"; do
			[[ "$(count_option "$required" "${opts_n[@]}")" == 1 ]] || exit_with_error 'Expected one minimal disable request' "$required"
			! has_option "$required" "${opts_y[@]}" "${opts_m[@]}" || exit_with_error 'Extra storage/VPN still enabled' "$required"
		done
		return 0
	fi
	for required in "${storage_builtins[@]}"; do
		[[ "$(count_option "$required" "${opts_y[@]}")" == 1 ]] || exit_with_error 'Expected one storage built-in request' "$required"
		! has_option "$required" "${opts_m[@]}" "${opts_n[@]}" || exit_with_error 'Conflicting storage built-in request' "$required"
	done
	for required in "${storage_modules[@]}"; do
		[[ "$(count_option "$required" "${opts_m[@]}")" == 1 ]] || exit_with_error 'Expected one optional storage/VPN module request' "$required"
		! has_option "$required" "${opts_y[@]}" "${opts_n[@]}" || exit_with_error 'Conflicting storage/VPN module request' "$required"
	done
	for required in "${storage_disabled[@]}"; do
		[[ "$(count_option "$required" "${opts_n[@]}")" == 1 ]] || exit_with_error 'Expected one early-assembly disable request' "$required"
		! has_option "$required" "${opts_y[@]}" "${opts_m[@]}" || exit_with_error 'Early assembly enabled' "$required"
	done
}

default_configs=()
for mode in default no yes; do
	if [[ "$mode" == default ]]; then
		unset E87N_EXTRA_STORAGE
	else
		E87N_EXTRA_STORAGE=$mode
		# Re-sourcing the board must respect an explicit setting.
		source "$USERPATCHES_PATH/config/boards/edgepi-e87n.csc"
		[[ "$E87N_EXTRA_STORAGE" == "$mode" ]] || exit_with_error 'Explicit storage option overwritten'
	fi
	scenario_index=0
	for scenario in empty inherited_conflicts enabled_seed; do
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
			for required in "${storage_builtins[@]}" "${storage_modules[@]}" "${storage_disabled[@]}"; do
				opts_y+=("$required" "CONFIG_$required" "$required" "CONFIG_$required")
				opts_m+=("$required" "CONFIG_$required" "$required" "CONFIG_$required")
				opts_n+=("$required" "CONFIG_$required" "$required" "CONFIG_$required")
			done
		elif [[ "$scenario" == enabled_seed ]]; then
			# Exercise the actual seed plus an inherited configuration with every
			# optional driver enabled. The minimal policy must override both.
			while IFS= read -r line; do
				case "$line" in
					CONFIG_*=y) opts_y+=("${line%=y}") ;;
					CONFIG_*=m) opts_m+=("${line%=m}") ;;
					'# CONFIG_'*' is not set')
						line=${line#\# }
						opts_n+=("${line% is not set}")
						;;
				esac
			done < "$USERPATCHES_PATH/config/kernel/linux-edgepi-e87n-lts.config"
			opts_y+=("${minimal_disabled[@]}")
			opts_m+=("${minimal_disabled[@]}")
		fi
		untouched_before=$(requests_for "${baseline_options[@]}" BTRFS_FS BINFMT_MISC DEBUG_INFO)
		shared_before=$(requests_for "${shared_prerequisites[@]}")
		# Repeated hook calls must preserve the same effective configuration.
		for pass in 1 2; do
			custom_kernel_config__edgepi_e87n_first_boot
			assert_first_boot_requests
			assert_storage_requests
			[[ "$(requests_for "${baseline_options[@]}" BTRFS_FS BINFMT_MISC DEBUG_INFO)" == "$untouched_before" ]] ||
				exit_with_error 'Unrelated/base driver requests changed' "$mode" "$scenario"
			if [[ "$mode" != yes ]]; then
				[[ "$(requests_for "${shared_prerequisites[@]}")" == "$shared_before" ]] ||
					exit_with_error 'Minimal mode changed inherited networking/crypto' "$scenario"
			fi
			current_config=$(effective_config)
			if [[ "$pass" == 2 ]]; then
				[[ "$current_config" == "$previous_config" ]] || exit_with_error 'Hook is not effectively idempotent' "$mode" "$scenario"
			fi
			previous_config=$current_config
			if [[ "$mode" != yes ]]; then
				for required in "${minimal_disabled[@]}"; do
					[[ $'\n'"$current_config"$'\n' == *$'\n'"CONFIG_$required=n"$'\n'* ]] ||
						exit_with_error 'Enabled seed defeated minimal policy' "$required"
				done
			fi
			# These values are pinned independently of the seed and hook lists.
			for setting in EXT4_FS=y MMC_MTK=y NET_MEDIATEK_SOC=y BLK_DEV_NVME=y \
				USB=y USB_XHCI_MTK=y USB_STORAGE=y USB_UAS=y TUN=m BRIDGE=y \
				NF_TABLES=m NFT_CT=m NFT_NAT=m WIREGUARD=m; do
				[[ $'\n'"$current_config"$'\n' == *$'\n'"CONFIG_$setting"$'\n'* ]] ||
					exit_with_error 'Essential/base capability changed' "$setting" "$mode" "$scenario"
			done
			for setting in WATCHDOG_PRETIMEOUT_GOV_NOOP=y WATCHDOG_PRETIMEOUT_DEFAULT_GOV_NOOP=y \
				PANIC_TIMEOUT=0; do
				[[ $'\n'"$current_config"$'\n' == *$'\n'"CONFIG_$setting"$'\n'* ]] ||
					exit_with_error 'Bring-up observability setting changed' "$setting" "$mode" "$scenario"
			done
			printf 'PASS: first-boot hook %s / %s, pass %s\n' "$mode" "$scenario" "$pass"
		done
		if [[ "$mode" == default ]]; then
			default_configs+=("$current_config")
		elif [[ "$mode" == no ]]; then
			[[ "$current_config" == "${default_configs[$scenario_index]}" ]] || exit_with_error 'Default differs from explicit no' "$scenario"
		fi
		scenario_index=$((scenario_index + 1))
	done
done

# Invalid values must fail clearly before mutating arrays, even when an error
# handler returns rather than exits (and when errexit is suppressed by if).
for invalid in '' YES NO true false 1 0 auto ' yes' 'no '; do
	(
		E87N_EXTRA_STORAGE=$invalid
		source "$USERPATCHES_PATH/config/boards/edgepi-e87n.csc"
		[[ "$E87N_EXTRA_STORAGE" == "$invalid" ]] || exit_with_error 'Invalid option silently defaulted'
		opts_y=(EXT4_FS)
		opts_m=(WIREGUARD)
		opts_n=(DEBUG_INFO)
		before=$(declare -p opts_y opts_m opts_n)
		# Called indirectly by the sourced board hook.
		# shellcheck disable=SC2329
		exit_with_error() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
		if custom_kernel_config__edgepi_e87n_first_boot; then
			printf 'Invalid storage option accepted: <%s>\n' "$invalid" >&2
			exit 1
		fi
		[[ "$(declare -p opts_y opts_m opts_n)" == "$before" ]] || exit 1
	) 2>&1 | {
		message=$(cat)
		[[ "$message" == *'E87N_EXTRA_STORAGE must be yes or no'* ]] || exit_with_error 'Missing invalid-option diagnostic' "$invalid"
	}
done
printf 'PASS: unset equals no; yes preserved on source; invalid options fail before mutation\n'
if [[ "$hook_only" == no ]]; then
	printf 'PASS: pinned Linux 6.18 family, no bootloader writer\n'
else
	printf 'SCOPE: hook-only; Bash 5 Armbian family loader NOT tested\n'
fi
printf 'PASS: %s board patches; native MT7987 providers, QEMU boot devices and storage opt-in verified\n' "${#patch_files[@]}"
printf 'SCOPE: request arrays and seed overlay tested; Kconfig resolution, kernel/image build and hardware NOT tested\n'
