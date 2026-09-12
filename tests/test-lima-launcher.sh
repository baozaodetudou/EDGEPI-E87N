#!/usr/bin/env bash
# No real VM/systemd/Docker/build calls: only the remote shell protocol and host fixtures.
set -Eeuo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/e87n-lima-tests.XXXXXXXX")
test_dir=$(cd "$test_dir" && pwd)
export repo_dir test_dir
printf 'MOCK ONLY; fixtures retained at %s\n' "$test_dir"
record() { printf '%s\n' "$*" >> "$E87N_LIMA_DIR/calls"; }
unexpected() { printf 'UNEXPECTED MOCK: %s\n' "$*" >&2; return 99; }
limactl() {
	if [[ ${mock_default:-no} == yes ]]; then
		[[ "$*" == 'shell --tty=false --workdir=/srv/e87n e87n-armbian sudo -n bash -s -- status /srv/e87n' ]] || return 99
		command cat >/dev/null
		return 3
	fi
	record limactl "$@"
	case "$1" in
		shell)
			[[ "$2 $3 $4 $5 $6 $7 $8 $9" == "--tty=false --workdir=$E87N_LIMA_DIR ${E87N_LIMA_VM:-e87n-armbian} sudo -n bash -s --" && "${11}" == "$E87N_LIMA_DIR" ]] || unexpected limactl "$@" || return
			command bash -s -- "${@:10}" ;;
		copy)
			[[ $# == 5 && "$2 $3" == '--tty=false --backend=scp' && "$4" == "${E87N_LIMA_VM:-e87n-armbian}:$E87N_LIMA_DIR"/.lima-export.*/output.tar ]] || unexpected limactl "$@" || return
			case "$mode" in copy_fail) return 67 ;; copy_empty) : > "$5" ;; *) command cp "${4#*:}" "$5" ;; esac ;;
		*) unexpected limactl "$@" ;;
	esac
}
systemctl() {
	record systemctl "$@"
	[[ "$1 $2" == 'show e87n-armbian-build.service' && $# == 3 && "$3" == --property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp ]] || unexpected systemctl "$@" || return
	[[ "$mode" != query_fail ]] || { printf 'LoadState=error\n'; return 5; }
	if [[ ! -f "$E87N_LIMA_DIR/unit-state" ]]; then printf 'LoadState=not-found\nActiveState=inactive\nSubState=dead\n'; return 4; fi
	command cat "$E87N_LIMA_DIR/unit-state"
}
journalctl() {
	record journalctl "$@"
	[[ "$*" == '--unit=e87n-armbian-build.service --no-pager --lines=200' ]] || unexpected journalctl "$@" || return
	printf 'MOCK JOURNAL: existing manual conventional unit, no receipt needed\n'
}
flock() {
	record flock "$@"
	if [[ "$*" == '--unlock 9' ]]; then return 0; fi
	[[ "$*" == '--nonblock --conflict-exit-code 75 9' ]] || unexpected flock "$@" || return
	python3 - "$E87N_LIMA_DIR/.build.lock" <<'PY'
import os, sys
assert os.fstat(9).st_ino == os.stat(sys.argv[1]).st_ino
PY
	[[ "$mode" != lock_busy ]] || return 75
}
pgrep() {
	record pgrep "$@"
	[[ $# == 2 && "$1" == -f ]] || unexpected pgrep "$@" || return
	case "$mode" in manual_process) printf '12345\n'; return 0 ;; process_query_fail) return 2 ;; *) return 1 ;; esac
}
find() {
	record find "$@"
	[[ $# == 12 && "$1" == "$E87N_LIMA_DIR/source/armbian-build/output/images" &&
		"$2 $3 $4 $5 $6 $7 $8 $9 ${10} ${12}" == '-maxdepth 1 -type f -name Armbian*.img* -size +0c -newermt -print' ]] || unexpected find "$@" || return
	[[ "$mode" != bad_timestamp ]] || return 1
	python3 - "$1" "${11}" <<'PY'
import datetime, pathlib, sys
since = datetime.datetime.strptime(sys.argv[2], "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=datetime.timezone.utc).timestamp()
for file in pathlib.Path(sys.argv[1]).glob("Armbian*.img*"):
    if file.is_file() and not file.is_symlink() and file.stat().st_size > 0 and file.stat().st_mtime > since:
        print(file)
PY
}
systemd-run() {
	record systemd-run "$@"
	[[ $# -ge 15 && "$1 $2 $3" == '--unit=e87n-armbian-build --uid=root --service-type=exec' &&
		"$4" == "--property=WorkingDirectory=$E87N_LIMA_DIR" && "$5 $6 $7 $8" == '--property=RemainAfterExit=yes --setenv=ALLOW_ROOT=yes --setenv=DEBIAN_FRONTEND=noninteractive --setenv=GIT_TERMINAL_PROMPT=0' &&
		"$9 ${10} ${11} ${12} ${13} ${14} ${15}" == "/usr/bin/flock --nonblock --conflict-exit-code 75 $E87N_LIMA_DIR/.build.lock /bin/bash $E87N_LIMA_DIR/build.sh" ]] || unexpected systemd-run "$@" || return
	printf '%s\n' "${@:16}" > "$E87N_LIMA_DIR/build-args"
	[[ "$mode" != launch_fail ]] || return 46
	printf 'LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nExecMainCode=0\nExecMainStatus=0\n' > "$E87N_LIMA_DIR/unit-state"
}
export -f record unexpected limactl systemctl journalctl flock pgrep find systemd-run
fixture() {
	mode=$1 E87N_LIMA_DIR="$test_dir/$1"
	export mode E87N_LIMA_DIR
	mkdir -p "$E87N_LIMA_DIR/source/armbian-build/output/images"
	printf 'MOCK BUILD - MUST NEVER BE EXECUTED\n' > "$E87N_LIMA_DIR/build.sh"
}
unit_state() {
	printf 'LoadState=loaded\nActiveState=%s\nSubState=%s\nResult=%s\nExecMainCode=%s\nExecMainStatus=%s\nExecMainStartTimestamp=2020-01-01 00:00:00 UTC\n' "$@" > "$E87N_LIMA_DIR/unit-state"
}
image_fixture() {
	python3 - "$E87N_LIMA_DIR" "$1" <<'PY'
import os, pathlib, sys
root, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
name = "Armbian-fixture.img.sha" if mode == "checksum" else "Armbian-fixture.img.xz"
file = root / "source/armbian-build/output/images" / name
file.write_bytes(b"" if mode == "empty" else b"SYNTHETIC MOCK; NOT AN ARMBIAN IMAGE\n")
stamp = 1 if mode == "stale" else 1700000000
os.utime(file, (stamp, stamp))
PY
}
count=0
check() {
	local label=$1 expected=$2 result output
	shift 2
	output="$test_dir/result-$count"
	if command bash "$repo_dir/build-lima.sh" "$@" > "$output" 2>&1; then result=0; else result=$?; fi
	if [[ "$result" != "$expected" || "$(< "$output")" == *'UNEXPECTED MOCK'* ]]; then
		printf 'FAIL %s: expected %s, got %s\n' "$label" "$expected" "$result" >&2
		command cat "$output" >&2; exit 1
	fi
	count=$((count + 1))
	printf 'PASS mock %02d: %s (exit %s)\n' "$count" "$label" "$result"
}
check help 0 --help
export mock_default=yes
check 'default VM/workdir/status protocol' 3
export mock_default=no
fixture initial
check 'unknown command' 2 reset
check 'unit not found is not success' 3 status
check 'journal works without private receipts' 0 logs
unit_state active running success 0 0
check 'manual active/running with Result=success' 75 status
check 'active unit prevents build' 75 build
check 'active unit prevents export' 75 export "$test_dir/no-active-export"
[[ ! -f "$E87N_LIMA_DIR/.build.lock" && ! -e "$test_dir/no-active-export" ]]
unit_state active exited success 0 0
check 'exited with default ExecMainCode is not proof' 3 status
unit_state failed failed exit-code 1 23
check 'actual process failure is propagated' 23 status
check 'failed manual run refuses export' 23 export "$test_dir/no-failed-export"
unit_state failed failed signal 2 9
check 'signal is not normal success' 3 status
unit_state inactive dead success 0 0
check 'inactive defaults are not completion' 3 status
fixture query_fail
check 'failed unit query is not idle' 1 build
fixture lock_busy
check 'shared seed/build lock conflict' 75 build
fixture manual_process
check 'untracked compiler blocks build' 75 build
fixture process_query_fail
check 'process query error fails closed' 2 build
fixture missing_build
command rm "$E87N_LIMA_DIR/build.sh"
check 'VM build.sh must exist' 2 build
for kind in missing empty stale checksum; do
	fixture "$kind"
	unit_state active exited success 1 0
	if [[ "$kind" != missing ]]; then image_fixture "$kind"; fi
	check "$kind image fails status readiness" 4 status
	check "$kind image refuses export" 4 export "$test_dir/no-$kind-export"
	[[ ! -e "$test_dir/no-$kind-export" ]]
done
fixture bad_timestamp
unit_state active exited success 1 0
image_fixture fresh
check 'timestamp parser failure refuses export' 3 export "$test_dir/no-time-export"
fixture success
unit_state active exited success 1 0
image_fixture fresh
export E87N_LIMA_VM=custom-e87n
check 'manual active(exited), real success, fresh image' 0 status
check 'completed existing unit retained, never stopped' 2 build
check 'manual unit output snapshot export' 0 export "$test_dir/export-one"
[[ -s "$test_dir/export-one/output.tar" ]]
command tar -tf "$test_dir/export-one/output.tar" > "$test_dir/members"
[[ "$(< "$test_dir/members")" == *'images/Armbian-fixture.img.xz'* ]]
check 'never overwrite existing host export' 2 export "$test_dir/export-one"
export E87N_LIMA_EXPORT_DIR="$test_dir/default-export"
check 'default export uses a new child directory' 0 export
mode=copy_fail
check 'Lima copy failure propagates' 67 export "$test_dir/export-failed"
[[ ! -e "$test_dir/export-failed/output.tar" ]]
mode=copy_empty
check 'empty copy is rejected' 4 export "$test_dir/export-empty"
[[ ! -e "$test_dir/export-empty/output.tar" ]]
fixture future_build
# shellcheck disable=SC2016 # This argument must remain literal through the launcher.
check 'fixed conventional unit/shared lock/literal args' 0 build 'NAME=value with spaces' 'LITERAL=$(false)'
[[ "$(< "$E87N_LIMA_DIR/build-args")" == $'NAME=value with spaces\nLITERAL=$(false)' ]]
check 'accepted service is not completed' 75 status
fixture launch_fail
check 'service launch failure propagates' 46 build 'TEST=yes'
printf 'PASS: %s mock tests. No VM connection/service change, Docker, real build, image execution or flashing occurred.\n' "$count"
