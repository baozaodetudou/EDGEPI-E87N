# SPDX-License-Identifier: GPL-2.0
# This hook runs after the real kernel build, in the host packaging stage.
# The receipt lives in the kernel .deb, so cached packages retain their actual
# build provenance rather than being relabelled with the current recipe.
function artifact_kernel_version_parts__e87n_receipt_identity() {
	[[ ${BOARD} == edgepi-e87n ]] || return 0
	local receipt_hash
	receipt_hash=$(python3 - "${SRC}/userpatches/overlay/e87n-board-support/build-provenance.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text())
identity = [value["source_commit"], value["source_dirty"], value["recipe_sha256"]]
print(hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:24])
PY
	)
	artifact_version_parts["E"]="$receipt_hash"
	artifact_version_part_order+=("0090-E")
}

function pre_package_kernel_image__e87n_receipt() {
	[[ ${BOARD} == edgepi-e87n ]] || return 0
	python3 - "${SRC}/userpatches/config/e87n-build.json" \
		"${SRC}/userpatches/overlay/e87n-board-support/build-provenance.json" \
		"${kernel_work_dir}" "${kernel_git_revision}" \
		"${kernel_image_pre_package_path}" "${kernel_version_family}" \
		"${package_directory}/usr/share/e87n/build-recipe.json" \
		"${SRC}" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys

config_file, source_receipt, tree, base, image, release, output, framework = sys.argv[1:]
config = json.loads(Path(config_file).read_text())
source = json.loads(Path(source_receipt).read_text())
def git(directory, *args):
    return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert base == config["kernel_commit"], "compiled kernel source pin mismatch"
assert release == config["kernel_release"], "compiled kernel release mismatch"
assert git(framework, "rev-parse", "HEAD") == config["armbian_commit"]
assert git(tree, "rev-parse", base) == base
subprocess.run(["git", "-C", tree, "merge-base", "--is-ancestor", base, "HEAD"], check=True)
config_path = Path(tree) / ".config"
patch_dir = Path(framework) / "userpatches/kernel/edgepi-e87n-6.18"
patches = [{"path": str(p.relative_to(Path(framework) / "userpatches")),
            "sha256": digest(p)} for p in sorted(patch_dir.glob("*.patch"))]
receipt = {
    "schema": 1, "kernel_release": release,
    "kernel_source": config["kernel_source"], "kernel_commit": base,
    "armbian_commit": git(framework, "rev-parse", "HEAD"),
    "source_commit": source["source_commit"],
    "source_dirty": source["source_dirty"],
    "recipe_sha256": source["recipe_sha256"],
    "patches": patches, "kernel_config_sha256": digest(config_path),
    "kernel_sha256": digest(image),
    "patched_kernel_commit": git(tree, "rev-parse", "HEAD"),
    "patched_kernel_tree": git(tree, "rev-parse", "HEAD^{tree}"),
}
destination = Path(output)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
print("E87N compiled kernel receipt:", destination, receipt["kernel_sha256"])
PY
}
