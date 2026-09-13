#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "GitPython==3.1.62", "unidiff==1.0.0", "Unidecode==1.4.0",
#   "rich==15.0.0", "PyYAML==6.0.3",
# ]
# ///
"""Use the real Armbian patch discovery/parser without modifying a kernel tree.

Run with uv run --script tests/test-patch-discovery.py, or the builder's Python
environment. Optional args: Armbian source directory, userpatches directory.
This tests discovery and parsing only, not application or compilation.
"""

import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]) if len(sys.argv) > 1 else repo / "source/armbian-build"
overlay = Path(sys.argv[2]) if len(sys.argv) > 2 else repo / "userpatches"
sys.path.insert(0, str(source / "lib/tools"))

from common.patching_utils import PatchDir, PatchRootDir, PatchSubDir

patch_dir = PatchDir(
    PatchRootDir(str(overlay / "kernel/edgepi-e87n-6.18"), "user", "kernel", str(overlay)),
    PatchSubDir("", "common"),
    str(source),
)
files = patch_dir.find_files_patch_files()
expected_prefixes = ["0000", "360", "361", "740", "750", "752", "790", "791", "792", "821", "830", "843", "900", "901"]
files = sorted(files, key=lambda item: item.file_name)
if len(files) != len(expected_prefixes):
    raise SystemExit(f"FAIL: Armbian discovered {len(files)} patches, expected {len(expected_prefixes)}")
prefixes = [Path(item.file_name).name.split("-", 1)[0] for item in files]
if prefixes != expected_prefixes:
    raise SystemExit(f"FAIL: missing, duplicate or unexpected patch prefixes: {prefixes}; expected {expected_prefixes}")
touched = set()
for patch_file in files:
    patches = patch_file.split_patches_from_file()
    if len(patches) != 1:
        raise SystemExit(f"FAIL: expected one fragment in {patch_file.file_name}")
    for patch in patches:
        patch.parse_patch()
        if patch.failed_to_parse:
            raise SystemExit(f"FAIL: Armbian cannot parse {patch_file.file_name}")
        if not patch.all_file_names_touched:
            raise SystemExit(f"FAIL: empty patch: {patch_file.file_name}")
        touched.update(patch.all_file_names_touched)
    print(f"Parsed: {patch_file.file_name}")
if "arch/arm64/boot/dts/mediatek/mt7987a-edgepi-e87n.dts" not in touched:
    raise SystemExit("FAIL: E87N DTS was not discovered in the patchset")
for required in ("drivers/net/phy/mediatek/mtk-2p5ge.c", "drivers/net/phy/realtek/realtek_main.c"):
    if required not in touched:
        raise SystemExit(f"FAIL: Linux 6.18 PHY patch target not discovered: {required}")
legacy = touched & {"drivers/net/phy/mtk-2p5ge.c", "drivers/net/phy/realtek.c"}
if legacy:
    raise SystemExit(f"FAIL: legacy flat PHY paths in Linux 6.18 patchset: {sorted(legacy)}")
print(f"PASS: Armbian discovered and parsed all {len(expected_prefixes)} Linux 6.18 patches; no compilation performed.")
