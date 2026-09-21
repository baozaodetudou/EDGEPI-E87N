#!/usr/bin/env python3
"""Derive stable release tags, titles and run-scoped artifact names."""
import argparse
from pathlib import Path
import re

from build_config import BUILD


REPO = Path(__file__).resolve().parents[1]
DISPLAY_VERSION_PATH = REPO / "packaging/e87n-display/VERSION"
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def display_version(path=DISPLAY_VERSION_PATH):
    version = Path(path).read_text(encoding="utf-8").strip()
    require(re.fullmatch(r"[0-9][A-Za-z0-9.+~\-]*", version) is not None,
            "invalid display package version")
    return version


def tag_version(version):
    value = version.replace("+", ".plus.").replace("~", ".tilde.")
    require(len(value) <= 48 and TAG.fullmatch(value) is not None,
            "version cannot be represented safely in a release tag")
    return value


def release_tag(kind, *, build=BUILD, version=None):
    if kind == "image":
        value = f"e87n-image-v{tag_version(build['firmware_version'])}"
    elif kind == "display":
        value = f"e87n-display-v{tag_version(display_version() if version is None else version)}"
    else:
        raise ValueError("invalid release kind")
    require(TAG.fullmatch(value) is not None and ".." not in value,
            "derived release tag is invalid")
    return value


def is_current_release_version(kind, tag, *, build=BUILD, version=None):
    """Match the stable tag and legacy run-scoped display tags for one version."""
    if tag == release_tag(kind, build=build, version=version):
        return True
    if kind != "display":
        return False
    encoded = tag_version(display_version() if version is None else version)
    return re.fullmatch(rf"e87n-display-{re.escape(encoded)}-[0-9]+-[0-9]+", tag) is not None


def release_title(kind, *, build=BUILD, version=None):
    if kind == "image":
        return (f"E87N Image | {build['firmware_version']} | "
                f"Debian {build['debian_point']} | Linux {build['kernel_version']}")
    if kind == "display":
        return f"E87N Display | {display_version() if version is None else version}"
    raise ValueError("invalid release kind")


def image_filename(*, build=BUILD):
    version = tag_version(build["firmware_version"])
    return f"edgepi-e87n-debian_{version}_arm64-uboot-firmware.tar"


def display_filename(*, version=None):
    return f"e87n-display_{display_version() if version is None else version}_all.deb"


def candidate_artifact(kind, run_id, run_attempt, *, build=BUILD, version=None):
    require(re.fullmatch(r"[0-9]+", str(run_id)) is not None, "invalid run id")
    require(re.fullmatch(r"[0-9]+", str(run_attempt)) is not None, "invalid run attempt")
    return (f"{release_tag(kind, build=build, version=version)}-candidate-"
            f"{run_id}-{run_attempt}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("field", choices=("tag", "title", "artifact", "version"))
    parser.add_argument("--kind", required=True, choices=("image", "display"))
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    args = parser.parse_args(argv)
    try:
        if args.field == "version":
            require(args.kind == "display", "version is only defined for display releases")
            value = display_version()
        elif args.field == "tag":
            value = release_tag(args.kind)
        elif args.field == "title":
            value = release_title(args.kind)
        else:
            require(args.run_id is not None and args.run_attempt is not None,
                    "artifact requires --run-id and --run-attempt")
            value = candidate_artifact(args.kind, args.run_id, args.run_attempt)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
