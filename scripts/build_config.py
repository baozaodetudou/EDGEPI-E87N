"""One reviewed set of E87N build inputs shared by builders and auditors."""
from pathlib import Path
import argparse
import json
import re

CONFIG_PATH = Path(__file__).resolve().parents[1] / "userpatches/config/e87n-build.json"


def load_config(path=CONFIG_PATH):
    config = json.loads(Path(path).read_text())
    if config.get("schema") != 1:
        raise ValueError("unsupported E87N build configuration schema")
    for field in ("kernel_commit", "armbian_commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", config.get(field, "")):
            raise ValueError(f"{field} must pin a full source commit")
    version = config["kernel_version"]
    if not re.fullmatch(r"6\.18\.\d+", version):
        raise ValueError("E87N requires the reviewed 6.18 LTS series")
    if config["kernel_release"] != version + "-current-" + config["linux_family"]:
        raise ValueError("kernel package release does not match the source version/family")
    if config["release"] != "trixie" or config["architecture"] != "arm64":
        raise ValueError("this profile requires Debian Trixie ARM64")
    return config


BUILD = load_config()
KERNEL_VERSION = BUILD["kernel_version"]
KERNEL_RELEASE = BUILD["kernel_release"]
TARGET = {"debian": BUILD["debian_version"], "release": BUILD["release"],
          "kernel": KERNEL_VERSION, "extra_storage": BUILD["extra_storage"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", nargs="?", choices=list(BUILD))
    args = parser.parse_args()
    print(BUILD[args.key] if args.key else json.dumps(BUILD, indent=2))
