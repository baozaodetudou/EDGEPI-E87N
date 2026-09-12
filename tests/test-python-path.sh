#!/usr/bin/env bash
set -Ee -o pipefail
(( BASH_VERSINFO[0] >= 5 )) || { printf 'Bash 5 required\n' >&2; exit 2; }
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
framework_dir=${1:-"$repo_dir/source/armbian-build"}
# Consumed by the real assignment evaluated below.
# shellcheck disable=SC2034
declare -A PYTHON3_INFO=(
	[USERBASE]='/tmp/e87n unused pip directory'
	[PYCACHEPREFIX]='/tmp/e87n unused pycache directory'
)
# Evaluate the real framework assignment, not a duplicate implementation.
assignment=$(sed -n '/declare -r -g -a PYTHON3_VARS=(/,/^[[:space:]]*)/p' \
	"$framework_dir/lib/functions/general/python-tools.sh")
[[ -n "$assignment" ]]
eval "$assignment"
# Keep the actual bash -c expansion; omit only unrelated logging/error handlers.
# shellcheck disable=SC1091
source "$framework_dir/lib/functions/logging/runners.sh"
run_host_command_logged_raw() { "$@"; }
python_code='import os, shutil, subprocess; assert "\x27" not in os.environ["PATH"], os.environ["PATH"]; assert shutil.which("git"), os.environ["PATH"]; subprocess.run(["git", "--version"], check=True)'
run_host_command_logged env -i "${PYTHON3_VARS[@]@Q}" /usr/bin/python3 -c "${python_code@Q}"
printf 'PASS: actual Armbian Python environment finds Git without literal PATH quotes\n'
