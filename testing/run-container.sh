#!/usr/bin/env bash
# Public CI interface. Inputs are read-only; QEMU only opens private copies.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
usage() {
    printf '%s\n' 'Usage: bash testing/run-container.sh --firmware TAR --display-deb DEB --output DIR --source-commit SHA --run-id ID --run-attempt ATTEMPT'
    printf '%s\n' 'Optional environment: E87N_BOOT_TIMEOUT=900 E87N_COMMAND_TIMEOUT=1200 E87N_TOTAL_TIMEOUT=7200 E87N_ROOT_GROW_MIB=1024 E87N_KEEP_DISKS=0'
}
firmware='' display='' output='' source_commit='' run_id='' run_attempt=''
while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --firmware|--display-deb|--output|--source-commit|--run-id|--run-attempt)
            [[ $# -ge 2 && -n $2 && $2 != --* ]] || { usage >&2; exit 2; }
            case "$1" in
                --firmware) firmware=$2 ;; --display-deb) display=$2 ;;
                --output) output=$2 ;; --source-commit) source_commit=$2 ;;
                --run-id) run_id=$2 ;; --run-attempt) run_attempt=$2 ;;
            esac
            shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -f $firmware && ! -L $firmware && -f $display && ! -L $display && -n $output &&
   $source_commit =~ ^[0-9a-f]{40}$ && $run_id =~ ^[0-9]+$ && $run_attempt =~ ^[1-9][0-9]*$ ]] || { usage >&2; exit 2; }
firmware=$(cd -- "$(dirname -- "$firmware")" && printf '%s/%s' "$PWD" "${firmware##*/}")
display=$(cd -- "$(dirname -- "$display")" && printf '%s/%s' "$PWD" "${display##*/}")
mkdir -p -- "$output"
output=$(cd -- "$output" && pwd -P)
[[ ! -e $output/result.json && ! -L $output/result.json ]] || { printf 'Refusing an existing result.json; use a fresh output directory.\n' >&2; exit 2; }
# Docker --mount uses comma-delimited options, so reject ambiguous paths.
[[ $firmware != *,* && $display != *,* && $output != *,* ]] || exit 2
container_id=
cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    if [[ -n $container_id ]]; then docker rm -f "$container_id" >/dev/null 2>&1 || true; fi
    if (( rc != 0 )) && [[ ! -e $output/result.json ]]; then
        printf '{"schema_version":1,"status":"FAIL","error":"container setup interrupted or failed; see container-build.log and runner.log"}\n' > "$output/result.json"
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
command -v docker >/dev/null
# Limit context to actual dependencies, not firmware or native kernel outputs.
tag="e87n-qemu-test:run-${run_id}-${run_attempt}-$$"
tar -C "$repo" -cf - testing scripts/factory_firmware.py scripts/build_config.py userpatches/config \
    | docker build --platform linux/arm64 -t "$tag" -f testing/Dockerfile - \
        2>&1 | tee "$output/container-build.log"
container_id=$(docker create --platform linux/arm64 --init \
    --mount "type=bind,src=$firmware,dst=/input/firmware.tar,readonly" \
    --mount "type=bind,src=$display,dst=/input/display.deb,readonly" \
    --mount "type=bind,src=$output,dst=/output" \
    -e E87N_BOOT_TIMEOUT -e E87N_COMMAND_TIMEOUT -e E87N_TOTAL_TIMEOUT \
    -e E87N_ROOT_GROW_MIB -e E87N_KEEP_DISKS \
    "$tag" --firmware /input/firmware.tar --display-deb /input/display.deb \
    --output /output --source-commit "$source_commit" --run-id "$run_id" --run-attempt "$run_attempt")
docker start -a "$container_id" 2>&1 | tee "$output/runner.log"
rc=$(docker inspect --format '{{.State.ExitCode}}' "$container_id")
[[ $rc == 0 ]] || exit "$rc"
[[ -s $output/result.json ]]
