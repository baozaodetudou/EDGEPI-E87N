#!/usr/bin/env bash
# Existing Lima VM / conventional unit only. Never start/reset a VM or stop a service.
set -Eeuo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
vm=${E87N_LIMA_VM:-e87n-armbian}
work=${E87N_LIMA_DIR:-/srv/e87n}
usage() {
	printf '%s\n' \
		'Usage: ./build-lima.sh [status|logs|build [BUILD_ARGS...]|export [NEW_DIRECTORY]]' \
		'Default: status. E87N_LIMA_VM=e87n-armbian; E87N_LIMA_DIR=/srv/e87n.' \
		'Always follows e87n-armbian-build.service, including the existing manual build.' \
		'status: 75=running; actual nonzero process exit is propagated; 3=not proven completed;' \
		'4=process succeeded but no fresh nonempty image. Unit query success is not build success.' \
		'logs: last 200 journal lines. No launcher receipt is required.' \
		'build: only submits the conventional unit if it does not exist; never stops/resets an existing unit.' \
		'Prepare/copy VM inputs and optional kernel cache manually; no automatic seed/install/sync.' \
		'Uses the shared VM .build.lock; an active unit or lock conflict returns 75.' \
		'export: exit 0 + fresh nonempty Armbian*.img[.xz/.gz/.zst/.bz2] required.' \
		'Copies an output.tar snapshot to a NEW directory under host output/lima by default.' \
		'E87N_LIMA_EXPORT_DIR overrides that parent. Images are never run or flashed.' \
		'If unit state/start time is unavailable, export refuses; arrange manual export separately.'
}
[[ ${1:-} != --help && ${1:-} != -h ]] || { usage; exit 0; }
action=${1:-status}
if (( $# )); then shift; fi
case "$action" in
	status|logs) [[ $# == 0 ]] || { usage >&2; exit 2; } ;;
	build) ;;
	export) [[ $# -le 1 ]] || { usage >&2; exit 2; } ;;
	*) usage >&2; exit 2 ;;
esac
[[ "$vm" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { printf 'Invalid VM name\n' >&2; exit 2; }
[[ "$work" =~ ^/[A-Za-z0-9_./-]+$ && "$work" != / && "/${work#/}/" != */../* ]] || { printf 'Invalid absolute VM directory\n' >&2; exit 2; }
work=${work%/}
command -v limactl >/dev/null 2>&1 || { printf 'limactl is required\n' >&2; exit 2; }
remote() {
	limactl shell --tty=false --workdir="$work" "$vm" sudo -n bash -s -- "$1" "$work" "${@:2}" <<'REMOTE'
set -Eeuo pipefail
export LC_ALL=C TZ=UTC
action=$1 work=$2
shift 2
unit=e87n-armbian-build.service
cd "$work"
read_unit() {
	local info result=0 key value
	load='' active='' sub='' real_result='' main_code='' main_status='' started=''
	info=$(systemctl show "$unit" --property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp) || result=$?
	while IFS='=' read -r key value; do
		case "$key" in
			LoadState) load=$value ;; ActiveState) active=$value ;; SubState) sub=$value ;;
			Result) real_result=$value ;; ExecMainCode) main_code=$value ;;
			ExecMainStatus) main_status=$value ;; ExecMainStartTimestamp) started=$value ;;
		esac
	done <<< "$info"
	if [[ "$load" == not-found ]]; then return 0; fi
	if (( result != 0 )) || [[ "$load" != loaded || -z "$active" || -z "$sub" ]]; then
		printf 'Cannot establish unit state:\n%s\n' "$info" >&2; return 1
	fi
}
not_running() {
	case "$active/$sub" in
		inactive/*|failed/*|active/exited|/) return 0 ;;
		*) printf 'Active unit: %s %s(%s); Result=%s is NOT completion\n' "$unit" "$active" "$sub" "$real_result" >&2; return 75 ;;
	esac
}
completed() {
	printf 'Unit: %s\nState: %s(%s)\nResult: %s\nExecMainCode: %s\nExecMainStatus: %s\n' \
		"$unit" "$active" "$sub" "$real_result" "$main_code" "$main_status"
	if [[ "$load" == not-found ]]; then printf 'No conventional unit; no completion proof\n' >&2; return 3; fi
	not_running
	if [[ "$main_code" == 1 && "$main_status" =~ ^[1-9][0-9]{0,2}$ && "$main_status" -le 255 ]]; then return "$main_status"; fi
	if [[ "$active/$sub" != active/exited || "$real_result" != success || "$main_code" != 1 || "$main_status" != 0 ]]; then
		printf 'Not a proven normal successful exit (including signals/unknown state)\n' >&2; return 3
	fi
}
check_images() {
	local found file
	[[ -n "$started" && "$started" != n/a ]] || { printf 'Start timestamp unavailable; use manual export\n' >&2; return 3; }
	[[ -d "$work/source/armbian-build/output/images" ]] || { printf 'No output/images directory\n' >&2; return 4; }
	# GNU find on Debian accepts the systemd timestamp. Errors fail closed.
	found=$(find "$work/source/armbian-build/output/images" -maxdepth 1 -type f -name 'Armbian*.img*' -size +0c -newermt "$started" -print) || return 3
	while IFS= read -r file; do
		case "$file" in *.img|*.img.xz|*.img.gz|*.img.zst|*.img.bz2) printf 'Fresh nonempty image (not boot-validated): %s\n' "$file"; return 0 ;; esac
	done <<< "$found"
	printf 'No fresh nonempty image; process success alone is insufficient\n' >&2; return 4
}
if [[ "$action" == logs ]]; then
	# GCC's ANSI diagnostics otherwise become "[NNNB blob data]" in the journal.
	journalctl --all --unit="$unit" --no-pager --lines=200
	exit $?
fi
read_unit
if [[ "$action" == status ]]; then completed; check_images; exit 0; fi
not_running
# Shared with the current manual build and seed-kernel-cache.sh.
exec 9> "$work/.build.lock"
flock --nonblock --conflict-exit-code 75 9
read_unit
not_running
if [[ "$action" == export ]]; then
	completed >&2
	check_images >&2
	snapshot=$(mktemp -d "$work/.lima-export.XXXXXXXX")
	tar -C "$work/source/armbian-build/output" -cf "$snapshot/output.tar" .
	[[ -s "$snapshot/output.tar" ]] || exit 4
	chmod 755 "$snapshot"
	chmod 644 "$snapshot/output.tar"
	printf '%s\n' "$snapshot/output.tar"
	exit 0
fi
[[ -f "$work/build.sh" ]] || { printf 'Missing VM build.sh; copy/prepare inputs manually\n' >&2; exit 2; }
[[ "$load" == not-found ]] || { printf 'Existing unit retained unchanged. Inspect its logs; release it manually before a new build.\n' >&2; exit 2; }
# Keep this deliberately narrow: do not disturb untracked native build processes.
if pids=$(pgrep -f '(^|[ /])(build(-armbian)?|compile)\.sh([[:space:]]|$)'); then
	printf 'Existing build/compile processes: %s\n' "$pids" >&2; exit 75
else result=$?; [[ "$result" == 1 ]] || exit "$result"; fi
# systemd enforces the fixed unit name against concurrent dispatchers. The service
# independently takes the same lock; if another owner wins, its real exit is 75.
flock --unlock 9
systemd-run --unit=e87n-armbian-build --uid=root --service-type=exec \
	--property="WorkingDirectory=$work" --property=RemainAfterExit=yes \
	--setenv=ALLOW_ROOT=yes --setenv=DEBIAN_FRONTEND=noninteractive --setenv=GIT_TERMINAL_PROMPT=0 \
	/usr/bin/flock --nonblock --conflict-exit-code 75 "$work/.build.lock" /bin/bash "$work/build.sh" "$@"
printf 'Build accepted, NOT completed. Use status/logs for e87n-armbian-build.service.\n'
REMOTE
}
if [[ "$action" != export ]]; then remote "$action" "$@"; exit $?; fi
if [[ $# == 1 && ( -e "$1" || -L "$1" ) ]]; then printf 'Export destination must not exist\n' >&2; exit 2; fi
snapshot=$(remote export)
[[ "$snapshot" == "$work"/.lima-export.*/output.tar && "$snapshot" != *$'\n'* ]] || { printf 'Invalid snapshot response\n' >&2; exit 1; }
if (( $# )); then destination=$1; mkdir -- "$destination"
else
	parent=${E87N_LIMA_EXPORT_DIR:-$repo_dir/output/lima}
	mkdir -p -- "$parent"
	destination=$(mktemp -d "$parent/export.XXXXXXXX")
fi
limactl copy --tty=false --backend=scp "$vm:$snapshot" "$destination/output.tar.partial"
[[ -f "$destination/output.tar.partial" && -s "$destination/output.tar.partial" && ! -L "$destination/output.tar.partial" ]] || { printf 'Empty/missing export; not successful\n' >&2; exit 4; }
mv "$destination/output.tar.partial" "$destination/output.tar"
printf 'Exported output snapshot: %s/output.tar\nNo image execution, flashing or board validation performed.\n' "$destination"
