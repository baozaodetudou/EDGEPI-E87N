#!/usr/bin/env python3
"""Publish verified CI assets through a draft; image releases may be replaced."""
import argparse
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from build_config import BUILD, TARGET
from factory_firmware import FORMAT
from release_identity import (display_filename, display_version, image_filename,
                              release_tag, release_title)

simulation = import_module("ci-simulation")

SAFE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}"
SHA = r"[0-9a-fA-F]{40}"
FIXED = {
    "image": {"image-build-metadata.json", "kernel-packages.tar.xz",
              "build-evidence.tar.xz", "RELEASE-NOTES.md", "SHA256SUMS",
              "simulation-result.json"},
    "display": {"display-build-metadata.json", "build-evidence.tar.xz",
                "RELEASE-NOTES.md", "SHA256SUMS"},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate metadata key: " + key)
        result[key] = value
    return result


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
    ref, release = inspect_existing(args)
    require(args.replace_existing or (ref is None and release is None),
            "Tag or release already exists")


def delete_api(repository, endpoint):
    gh("api", "--hostname", "github.com", "--method", "DELETE",
       "-H", "Accept: application/vnd.github+json",
       "-H", "X-GitHub-Api-Version: 2022-11-28", f"repos/{repository}/{endpoint}")


def inspect_existing(args):
    ref = api(args.repository, f"git/ref/tags/{args.tag}", absent=True)
    published = api(args.repository, f"releases/tags/{args.tag}", absent=True)
    listed = find_release(args)
    releases = [release for release in (published, listed) if release is not None]
    release = None
    if releases:
        require(all(isinstance(release, dict) for release in releases),
                "Existing release identity mismatch")
        ids = {release.get("id") for release in releases}
        require(len(ids) == 1 and all(type(value) is int and value > 0 for value in ids) and
                all(release.get("tag_name") == args.tag for release in releases),
                "Existing release identity mismatch")
        release = releases[0]
        require(release.get("immutable") is False,
                "Existing release immutability state is unsafe")
    if ref is not None:
        require(isinstance(ref, dict) and ref.get("ref") == f"refs/tags/{args.tag}",
                "Existing tag identity mismatch")
    return ref, release


def delete_identity(args, ref, release):
    if release is not None:
        release_id = release["id"]
        delete_api(args.repository, f"releases/{release_id}")
        require(api(args.repository, f"releases/{release_id}", absent=True) is None,
                "Existing release deletion was not confirmed")
    if ref is not None:
        delete_api(args.repository, f"git/refs/tags/{args.tag}")
        require(api(args.repository, f"git/ref/tags/{args.tag}", absent=True) is None,
                "Existing tag deletion was not confirmed")


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def validate_assets(directory, source_commit, kind):
    require(kind in FIXED, "Invalid release kind")
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
    images = {name for name in files if name.endswith(".tar")}
    debs = {name for name in files if re.fullmatch(r"e87n-display_.+_all\.deb", name)}
    expected_payloads = images | debs
    expected_deb = display_filename()
    if kind == "image":
        require(images == {image_filename()} and debs == {expected_deb},
                "Image staging requires the versioned firmware tar and current tested display package")
    else:
        require(not images and debs == {expected_deb},
                "Display staging requires exactly the current versioned display package")
    require(set(files) == FIXED[kind] | expected_payloads,
            "Missing, duplicate, or unexpected release assets")
    require(all(files[name][1] > 0 for name in expected_payloads), "Empty release payload")
    manifest = {}
    for line in files["SHA256SUMS"][0].read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]([A-Za-z0-9][A-Za-z0-9._+~-]*)", line)
        require(match is not None and ".." not in match[2], "Invalid checksum manifest entry")
        require(match[2] not in manifest, "Duplicate checksum manifest entry")
        manifest[match[2]] = match[1].lower()
    require(set(manifest) == set(files) - {"SHA256SUMS"}, "Checksum manifest coverage mismatch")
    require(all(files[name][2] == sha for name, sha in manifest.items()), "Asset checksum mismatch")
    metadata = json.loads(files[f"{kind}-build-metadata.json"][0].read_text(encoding="utf-8"),
                          object_pairs_hook=unique_object)
    require(isinstance(metadata, dict) and metadata.get("kind") == kind and
            metadata.get("build_step_outcome") == "success" and metadata.get("source_commit") == source_commit
            and isinstance(metadata.get("target"), dict) and
            all(metadata["target"].get(k) == v for k, v in TARGET.items()) and
            metadata.get("collection_errors") == [], "Build metadata is not a successful current-source target")
    require(metadata.get("display_version_source") == display_version(),
            "Build metadata display version mismatch")
    require(kind != "image" or metadata.get("image_static_audit") == "passed", "Image audit has not passed")
    require(kind != "display" or metadata.get("image_static_audit") == "not applicable",
            "Display metadata has an invalid image audit state")
    require(all(metadata.get(key) == BUILD[key] for key in
                ("armbian_commit", "kernel_commit", "kernel_source", "kernel_release")),
            "Build source configuration mismatch")
    require(kind != "image" or metadata.get("factory_format") == FORMAT,
            "Factory firmware format mismatch")
    require(kind != "image" or metadata.get("factory_static_audit") == "passed",
            "Factory firmware audit has not passed")
    require(kind != "image" or metadata.get("simulation_validation") == "passed",
            "Same-build simulation has not passed")
    if kind == "image":
        simulation.verify_report(
            simulation.load_report(files["simulation-result.json"][0]),
            firmware_sha256=files[next(iter(images))][2], display_sha256=files[next(iter(debs))][2],
            source_commit=source_commit, run_id=metadata.get("run_id"),
            run_attempt=metadata.get("run_attempt"))
    return files


def check_release(release, args, draft, release_id=None):
    # GitHub may normalize target_commitish to the repository's default branch
    # when a release is created with a commit SHA. The tag check below remains
    # authoritative for the exact source commit after publication.
    target = release.get("target_commitish") if isinstance(release, dict) else None
    require(isinstance(release, dict) and type(release.get("id")) is int and release["id"] > 0 and
            (release_id is None or release["id"] == release_id) and release.get("tag_name") == args.tag and
            release.get("draft") is draft and release.get("prerelease") is True and
            isinstance(target, str) and (target == args.source_commit or target == "main") and
            (release.get("published_at") is None if draft else bool(release.get("published_at"))),
            "Release identity, source, or publication state mismatch")
    return release["id"]


def view_release(args):
    """Read a draft that GitHub omits from the authenticated REST list."""
    result = gh("release", "view", args.tag, "--repo", args.repository,
                "--json", "databaseId,tagName,isDraft,isPrerelease,targetCommitish,publishedAt",
                capture=True)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return {"id": data["databaseId"], "tag_name": data["tagName"],
                "draft": data["isDraft"], "prerelease": data["isPrerelease"],
                "target_commitish": data["targetCommitish"],
                "published_at": data["publishedAt"]}
    except (KeyError, TypeError, ValueError):
        raise ValueError("gh release view returned invalid release JSON") from None


def find_created_release(args):
    release = find_release(args)
    return release if release is not None else view_release(args)


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


def create_verified_draft(args, files, downloads):
    gh("release", "create", args.tag, "--repo", args.repository, "--target", args.source_commit,
       "--title", release_title(args.kind), "--notes-file", files["RELEASE-NOTES.md"][0],
       "--draft", "--prerelease", "--latest=false")
    release_id = check_release(find_created_release(args), args, True)
    check_remote_assets(args, release_id, {})
    gh("release", "upload", args.tag, "--repo", args.repository, "--",
       *(value[0] for value in downloads.values()))
    check_release(api(args.repository, f"releases/{release_id}"), args, True, release_id)
    check_remote_assets(args, release_id, downloads)
    check_tag(args, pending=True)
    return release_id


def publish_draft(args, release_id, downloads):
    gh("release", "edit", args.tag, "--repo", args.repository,
       "--draft=false", "--prerelease", "--latest=false")
    check_release(api(args.repository, f"releases/{release_id}"), args, False, release_id)
    check_remote_assets(args, release_id, downloads)
    check_tag(args)


def replacement_args(args):
    result = argparse.Namespace(**vars(args))
    result.tag = f"{args.tag}-replacement-{args.replacement_id}"
    require(safe(result.tag), "Invalid replacement release tag")
    return result


def publish_replacement(args, files, downloads):
    replacement = replacement_args(args)
    replacement_ref, replacement_release = inspect_existing(replacement)
    require(replacement_ref is None and replacement_release is None,
            "Replacement release identity already exists")
    release_id = create_verified_draft(replacement, files, downloads)

    # Re-read the public identity only after the replacement draft and its
    # remote SHA-256 have been verified. A failed build or upload therefore
    # leaves the current public release untouched.
    current_ref, current_release = inspect_existing(args)
    delete_identity(args, current_ref, current_release)
    gh("release", "edit", replacement.tag, "--repo", args.repository,
       "--tag", args.tag, "--target", args.source_commit,
       "--title", release_title(args.kind), "--draft=false", "--prerelease", "--latest=false")
    check_release(api(args.repository, f"releases/{release_id}"), args, False, release_id)
    check_remote_assets(args, release_id, downloads)
    check_tag(args)

    # A draft normally has no ref until publication. Remove a stale temporary
    # ref if GitHub created one while retagging the verified draft.
    temporary_ref = api(args.repository, f"git/ref/tags/{replacement.tag}", absent=True)
    if temporary_ref is not None:
        delete_identity(replacement, temporary_ref, None)


def publish(args, files):
    # Staging includes validation evidence, but each release channel exposes
    # exactly one end-user download.
    downloads = {name: value for name, value in files.items() if
                 (args.kind == "image" and name.endswith(".tar")) or
                 (args.kind == "display" and re.fullmatch(r"e87n-display_.+_all\.deb", name))}
    require(len(downloads) == 1, "Release must contain exactly one public payload")
    try:
        if args.replace_existing:
            publish_replacement(args, files, downloads)
        else:
            release_id = create_verified_draft(args, files, downloads)
            publish_draft(args, release_id, downloads)
    except (OSError, ValueError):
        print("Release publication was attempted; remote state was retained without automatic rollback. "
              "Inspect the official and replacement tags before retrying.", file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("preflight", "publish"))
    parser.add_argument("--kind", required=True, choices=("image", "display"))
    for option in ("repository", "source-commit", "tag"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--assets")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--replacement-id")
    args = parser.parse_args(argv)
    try:
        require(safe(args.tag), "Invalid release tag")
        require(args.tag == release_tag(args.kind),
                "Release tag does not match the current channel version")
        require(len(args.repository.split("/")) == 2 and all(safe(p) for p in args.repository.split("/")), "Invalid repository")
        require(re.fullmatch(SHA, args.source_commit), "Invalid source commit")
        args.source_commit = args.source_commit.lower()
        require(bool(args.assets) == (args.command == "publish"), "--assets is required only for publish")
        require(not args.replace_existing or args.kind == "image",
                "Replacement is supported only for image releases")
        require(bool(args.replacement_id) == (args.command == "publish" and args.replace_existing),
                "--replacement-id is required only for replacement publish")
        require(args.replacement_id is None or safe(args.replacement_id), "Invalid replacement id")
        files = validate_assets(args.assets, args.source_commit, args.kind) if args.command == "publish" else None
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
