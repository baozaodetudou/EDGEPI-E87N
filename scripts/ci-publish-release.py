#!/usr/bin/env python3
"""Publish verified CI assets through a draft; failures never trigger cleanup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

SAFE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}"
SHA = r"[0-9a-fA-F]{40}"
FIXED = {"image-build-metadata.json", "display-build-metadata.json",
         "kernel-packages.tar.xz", "build-evidence.tar.xz", "RELEASE-NOTES.md", "SHA256SUMS"}
TARGET = {"debian": "13", "release": "trixie", "kernel": "6.18.51", "extra_storage": "no"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe(value):
    return re.fullmatch(SAFE, value) and ".." not in value and not value.endswith((".", ".lock"))


def gh(*arguments, capture=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_HOST="github.com", GH_PROMPT_DISABLED="1")
    env.pop("GH_DEBUG", None)
    try:
        result = subprocess.run(["gh", *map(str, arguments)], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, text=True, check=False,
                                timeout=900 if arguments[:2] == ("release", "upload") else 120)
    except subprocess.TimeoutExpired:
        raise ValueError("gh " + " ".join(arguments[:2]) + " timed out (output suppressed)") from None
    if not capture:
        require(result.returncode == 0, "gh " + " ".join(arguments[:2]) + " failed (output suppressed)")
    return result


def api(repository, endpoint, absent=False):
    result = gh("api", "--hostname", "github.com", "--method", "GET", "--include",
                "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28",
                f"repos/{repository}/{endpoint}", capture=True)
    # gh --include emits one HTTP status/header block followed by JSON, on errors too.
    header, separator, body = result.stdout.replace("\r\n", "\n").partition("\n\n")
    status = re.fullmatch(r"HTTP/\d(?:\.\d)? ([1-5]\d{2})(?: [^\n]*)?", header.split("\n")[0])
    require(separator and status, "API response has no valid HTTP status/header block")
    code = int(status[1])
    require((code == 200 and result.returncode == 0) or
            (absent and code == 404 and result.returncode in (0, 1)), f"API GET failed (HTTP {code})")
    try:
        data = json.loads(body)
    except ValueError:
        raise ValueError("API response has invalid JSON") from None
    require(isinstance(data, (dict, list)), "API response must be a JSON object or array")
    return None if code == 404 else data


def find_release(args):
    # REST /releases/tags only finds published releases; list also exposes drafts.
    matches = []
    for page in range(1, 21):
        releases = api(args.repository, f"releases?per_page=100&page={page}")
        require(isinstance(releases, list) and all(isinstance(r, dict) and
                isinstance(r.get("tag_name"), str) for r in releases), "Invalid release list")
        matches.extend(r for r in releases if r["tag_name"] == args.tag)
        require(len(matches) <= 1, "Duplicate releases for tag")
        if len(releases) < 100:
            return matches[0] if matches else None
    raise ValueError("Release pagination limit exceeded (20 pages)")


def preflight(args):
    gh("auth", "status", "--hostname", "github.com")
    commit = api(args.repository, f"commits/{args.source_commit}")
    require(isinstance(commit, dict) and commit.get("sha") == args.source_commit, "Source commit mismatch")
    for endpoint in (f"git/ref/tags/{args.tag}", f"releases/tags/{args.tag}"):
        require(api(args.repository, endpoint, absent=True) is None, "Tag or release already exists")
    require(find_release(args) is None, "Release already exists (including drafts)")


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def validate_assets(directory, source_commit):
    root = Path(directory).absolute()
    require(not any(p.is_symlink() for p in (root, *root.parents)), "Asset directory contains a symlink")
    require(root.is_dir() and not any(c in str(root) for c in "#\r\n*?[]"), "Invalid asset directory")
    files, inodes = {}, set()
    for path in sorted(root.iterdir()):
        info = path.lstat()
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]*", path.name) and ".." not in path.name
                and stat.S_ISREG(info.st_mode), "Assets must be safe, regular top-level files")
        require((info.st_dev, info.st_ino) not in inodes, "Duplicate asset file")
        inodes.add((info.st_dev, info.st_ino))
        files[path.name] = (path, info.st_size, digest(path))
    images = {name for name in files if name.endswith(".img.xz")}
    debs = {name for name in files if re.fullmatch(r"e87n-display_.+_all\.deb", name)}
    require(len(images) == len(debs) == 1 and set(files) == FIXED | images | debs,
            "Missing, duplicate, or unexpected release assets")
    manifest = {}
    for line in files["SHA256SUMS"][0].read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]([A-Za-z0-9][A-Za-z0-9._+~-]*)", line)
        require(match is not None and ".." not in match[2], "Invalid checksum manifest entry")
        require(match[2] not in manifest, "Duplicate checksum manifest entry")
        manifest[match[2]] = match[1].lower()
    require(set(manifest) == set(files) - {"SHA256SUMS"}, "Checksum manifest coverage mismatch")
    require(all(files[name][2] == sha for name, sha in manifest.items()), "Asset checksum mismatch")
    for kind in ("image", "display"):
        metadata = json.loads(files[f"{kind}-build-metadata.json"][0].read_text(encoding="utf-8"))
        require(isinstance(metadata, dict) and metadata.get("kind") == kind and
                metadata.get("build_step_outcome") == "success" and metadata.get("source_commit") == source_commit
                and isinstance(metadata.get("target"), dict) and
                all(metadata["target"].get(k) == v for k, v in TARGET.items()) and
                metadata.get("collection_errors") == [], "Build metadata is not a successful current-source target")
        require(kind != "image" or metadata.get("image_static_audit") == "passed", "Image audit has not passed")
    return files


def check_release(release, args, draft, release_id=None):
    require(isinstance(release, dict) and type(release.get("id")) is int and release["id"] > 0 and
            (release_id is None or release["id"] == release_id) and release.get("tag_name") == args.tag and
            release.get("draft") is draft and release.get("prerelease") is True and
            release.get("target_commitish") == args.source_commit and
            (release.get("published_at") is None if draft else bool(release.get("published_at"))),
            "Release identity, source, or publication state mismatch")
    return release["id"]


def check_remote_assets(args, release_id, files):
    assets = api(args.repository, f"releases/{release_id}/assets?per_page=100")
    require(isinstance(assets, list) and len(assets) == len(files), "Remote asset count mismatch")
    seen = set()
    for asset in assets:
        require(isinstance(asset, dict) and isinstance(asset.get("name"), str), "Invalid remote asset")
        name = asset["name"]
        require(name in files and name not in seen, "Unexpected or duplicate remote asset")
        seen.add(name)
        require(asset.get("state") == "uploaded" and type(asset.get("size")) is int and
                asset["size"] == files[name][1], "Remote asset state or size mismatch")
        # Strict on github.com: a missing digest cannot establish content integrity.
        require(asset.get("digest") == "sha256:" + files[name][2], "Remote asset SHA-256 digest missing or mismatched")


def check_tag(args, pending=False):
    ref = api(args.repository, f"git/ref/tags/{args.tag}", absent=pending)
    if ref is None and pending:
        return  # GitHub may create the tag only when the draft is published.
    require(isinstance(ref, dict) and ref.get("ref") == f"refs/tags/{args.tag}", "Tag reference mismatch")
    obj, seen = ref.get("object"), set()
    for _ in range(16):
        require(isinstance(obj, dict) and isinstance(obj.get("sha"), str) and
                re.fullmatch(SHA, obj["sha"]), "Invalid tag object SHA")
        if obj.get("type") == "commit":
            require(obj["sha"] == args.source_commit, "Tag source commit mismatch")
            return
        require(obj.get("type") == "tag" and obj["sha"] not in seen, "Invalid or cyclic annotated tag")
        seen.add(obj["sha"])
        tag = api(args.repository, f"git/tags/{obj['sha']}")
        require(isinstance(tag, dict) and tag.get("sha") == obj["sha"], "Annotated tag identity mismatch")
        obj = tag.get("object")
    raise ValueError("Annotated tag nesting limit exceeded")


def publish(args, files):
    try:
        gh("release", "create", args.tag, "--repo", args.repository, "--target", args.source_commit,
           "--title", f"E87N {args.tag}", "--notes-file", files["RELEASE-NOTES.md"][0],
           "--draft", "--prerelease", "--latest=false")
        release_id = check_release(find_release(args), args, True)
        check_remote_assets(args, release_id, {})  # Never adopt or overwrite preexisting assets.
        gh("release", "upload", args.tag, "--repo", args.repository, "--", *(v[0] for v in files.values()))
        check_release(api(args.repository, f"releases/{release_id}"), args, True, release_id)
        check_remote_assets(args, release_id, files)
        check_tag(args, pending=True)
        gh("release", "edit", args.tag, "--repo", args.repository, "--draft=false", "--prerelease", "--latest=false")
        check_release(api(args.repository, f"releases/{release_id}"), args, False, release_id)
        check_tag(args)
    except (OSError, ValueError):
        print("Release creation was attempted; remote release/tag/assets were retained without cleanup. "
              "Inspect manually before retrying; publication may already have occurred.", file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("preflight", "publish"))
    for option in ("repository", "source-commit", "tag"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--assets")
    args = parser.parse_args(argv)
    try:
        require(safe(args.tag), "Invalid release tag")
        require(len(args.repository.split("/")) == 2 and all(safe(p) for p in args.repository.split("/")), "Invalid repository")
        require(re.fullmatch(SHA, args.source_commit), "Invalid source commit")
        args.source_commit = args.source_commit.lower()
        require(bool(args.assets) == (args.command == "publish"), "--assets is required only for publish")
        files = validate_assets(args.assets, args.source_commit) if args.command == "publish" else None
        preflight(args)
        if files is not None:
            publish(args, files)
    except (OSError, ValueError) as error:
        print("FAIL: " + (str(error) if isinstance(error, ValueError) else "Local I/O or gh execution failed"), file=sys.stderr)
        return 1
    print(f"PASS: release {args.command} verified for {args.repository} {args.tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
