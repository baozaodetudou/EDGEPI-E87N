#!/usr/bin/env python3
"""Isolated publisher fixtures: every subprocess is intercepted; no network."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci-publish-release.py"
SPEC = importlib.util.spec_from_file_location("publisher", SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
SOURCE, TAG, REPOSITORY = "a1" * 20, "e87n-v1.0", "owner/repo"
SECRET = "ghp_fixture_never_log_this"


def response(code=200, data=None, *, raw=None, protocol="HTTP/2.0", newline="\n", returncode=None):
    body = json.dumps(data) if raw is None else raw
    stdout = newline.join((f"{protocol} {code} Test", "Content-Type: application/json", "", body))
    return subprocess.CompletedProcess([], int(code >= 400) if returncode is None else returncode, stdout, SECRET)


class FakeGh:
    def __init__(self, test):
        self.test, self.calls, self.overrides = test, [], {}
        self.created = self.published = self.tag_exists = False
        self.fail = None
        self.release = {"id": 42, "tag_name": TAG, "draft": True, "prerelease": True,
                        "target_commitish": SOURCE, "published_at": None}
        self.other_releases, self.assets = [], []
        self.after_upload = lambda assets: None
        self.after_edit = lambda: None
        self.tag_object = {"type": "commit", "sha": SOURCE}

    def __call__(self, command, **kwargs):
        self.test.assertIsInstance(command, list)
        self.test.assertEqual(command[0], "gh")
        self.test.assertNotIn("shell", kwargs)
        self.test.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.test.assertEqual(kwargs["env"]["GH_HOST"], "github.com")
        self.test.assertEqual(kwargs["env"]["GH_PROMPT_DISABLED"], "1")
        self.test.assertNotIn("GH_DEBUG", kwargs["env"])
        self.test.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.test.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.test.assertEqual(kwargs["stdout"], subprocess.PIPE if command[1] == "api" else subprocess.DEVNULL)
        self.test.assertEqual(kwargs["timeout"], 900 if command[1:3] == ["release", "upload"] else 120)
        self.calls.append(command)
        if command[1] == "auth":
            self.test.assertEqual(command, ["gh", "auth", "status", "--hostname", "github.com"])
            return subprocess.CompletedProcess(command, int(self.fail == "auth"), SECRET, SECRET)
        self.test.assertEqual(self.calls[0][1:3], ["auth", "status"])
        if command[1] == "api":
            self.test.assertEqual(command[command.index("--method") + 1], "GET")
            self.test.assertEqual(command[command.index("--hostname") + 1], "github.com")
            self.test.assertIn("--include", command)
            self.test.assertTrue(command[-1].startswith(f"repos/{REPOSITORY}/"))
            endpoint = command[-1].removeprefix(f"repos/{REPOSITORY}/")
            if endpoint in self.overrides:
                override = self.overrides[endpoint]
                return override() if callable(override) else override
            if endpoint == f"commits/{SOURCE}":
                return response(data={"sha": SOURCE})
            if endpoint == f"git/ref/tags/{TAG}":
                return response(data={"ref": f"refs/tags/{TAG}", "object": self.tag_object}) if self.tag_exists else response(404, {})
            if endpoint == f"releases/tags/{TAG}":
                return response(data=self.release) if self.published else response(404, {})
            if endpoint.startswith("releases?per_page=100&page="):
                page = int(endpoint.rsplit("=", 1)[1])
                releases = self.other_releases + ([self.release] if self.created else [])
                return response(data=releases[(page - 1) * 100:page * 100])
            if endpoint == "releases/42/assets?per_page=100":
                return response(data=self.assets)
            if endpoint == "releases/42":
                return response(data=self.release)
            self.test.fail("Unexpected API endpoint: " + endpoint)
        self.test.assertEqual(command[1], "release")
        action = command[2]
        self.test.assertEqual(command[3], TAG)
        self.test.assertEqual(command[command.index("--repo") + 1], REPOSITORY)
        self.test.assertNotIn("--clobber", command)
        if action == "create":
            self.created = True
            self.test.assertFalse(self.published)
            self.test.assertEqual(command[command.index("--target") + 1], SOURCE)
            self.test.assertTrue({"--draft", "--prerelease", "--latest=false"} <= set(command))
        elif action == "upload":
            self.test.assertTrue(self.created and not self.published)
            paths = command[command.index("--") + 1:]
            self.assets = [{"name": Path(p).name, "size": Path(p).stat().st_size, "state": "uploaded",
                            "digest": "sha256:" + hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in paths]
            self.after_upload(self.assets)
            if self.fail == "upload":
                self.assets = self.assets[:1]
        elif action == "edit":
            self.test.assertTrue(self.created and not self.published)
            self.test.assertTrue({"--draft=false", "--prerelease", "--latest=false"} <= set(command))
            if self.fail != "edit":
                self.published = self.tag_exists = True
                self.release.update(draft=False, published_at="2026-09-13T00:00:00Z")
                self.after_edit()
        else:
            self.test.fail("Unexpected write: " + action)
        return subprocess.CompletedProcess(command, int(self.fail == action), SECRET, SECRET)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="e87n-release-fixture-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.assets = self.root / "assets"
        self.assets.mkdir()
        for name in ("e87n-trixie.img.xz", "e87n-display_1.2.3_all.deb", "kernel-packages.tar.xz",
                     "build-evidence.tar.xz", "RELEASE-NOTES.md"):
            (self.assets / name).write_bytes(("fixture " + name).encode())
        for kind in ("image", "display"):
            metadata = {"kind": kind, "build_step_outcome": "success", "source_commit": SOURCE,
                        "target": dict(publisher.TARGET), "collection_errors": [],
                        "image_static_audit": "passed" if kind == "image" else "not applicable"}
            (self.assets / f"{kind}-build-metadata.json").write_text(json.dumps(metadata))
        self.manifest()
        self.fake = FakeGh(self)
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(publisher.subprocess, "run", side_effect=lambda *a, **k: self.fake(*a, **k)).start()
        mock.patch.dict(os.environ, GH_HOST="untrusted.example", GH_TOKEN=SECRET, GH_DEBUG="api").start()

    def manifest(self):
        (self.assets / "SHA256SUMS").write_text("".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
            for p in sorted(self.assets.iterdir()) if p.name != "SHA256SUMS"))

    def run_cli(self, command="publish", **values):
        options = {"repository": REPOSITORY, "source-commit": SOURCE, "tag": TAG}
        if command == "publish":
            options["assets"] = str(self.assets)
        options.update(values)
        args = [command] + [f"--{key}={value}" for key, value in options.items() if value is not None]
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            try:
                result = publisher.main(args)
            except SystemExit as error:
                result = error.code
        self.assertNotIn(SECRET, self.output.getvalue() + self.errors.getvalue())
        return result

    def actions(self):
        return [c[2] for c in self.fake.calls if c[1] == "release"]

    def test_preflight_is_read_only_and_authenticates_first(self):
        self.assertEqual(self.run_cli("preflight"), 0, self.errors.getvalue())
        self.assertEqual(self.actions(), [])
        self.assertEqual(len(self.fake.calls), 5)
        self.assertEqual(self.run_cli("preflight", **{"source-commit": SOURCE.upper()}), 0)

    def test_input_injection_and_invalid_values_make_no_subprocess_calls(self):
        for key, values in {
            "tag": ["", "-x", ".x", "x/y", "x..y", "x.", "x.lock", "x~1", "x;touch pwned", "$(id)", "`id`", "x\n", "x" * 97],
            "repository": ["owner", "owner/repo/other", "../repo", "owner/repo..bad", "owner/repo?x=1", "-x/repo", "owner/repo;id"],
            "source-commit": ["a" * 39, "g" * 40, SOURCE + ";id", SOURCE + "\n", "--help"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertNotEqual(self.run_cli("preflight", **{key: value}), 0)
                    self.assertEqual(self.fake.calls, [])
        self.assertNotEqual(self.run_cli(assets=None), 0)
        self.assertNotEqual(self.run_cli("preflight", assets=str(self.assets)), 0)
        self.assertEqual(self.fake.calls, [])

    def test_auth_failure_stops_before_api(self):
        self.fake.fail = "auth"
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(len(self.fake.calls), 1)

    def test_missing_gh_fails_cleanly(self):
        with mock.patch.object(publisher.subprocess, "run", side_effect=FileNotFoundError(SECRET)):
            self.assertNotEqual(self.run_cli(), 0)

    def test_timeouts_fail_without_credentials_or_cleanup(self):
        for stage in ("auth", "api", "create", "upload", "edit"):
            with self.subTest(stage=stage):
                self.fake = FakeGh(self)

                def timeout(command, **kwargs):
                    result = self.fake(command, **kwargs)
                    action = command[2] if command[1] == "release" else command[1]
                    if action == stage:
                        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=SECRET, stderr=SECRET)
                    return result

                with mock.patch.object(publisher.subprocess, "run", side_effect=timeout):
                    self.assertNotEqual(self.run_cli(), 0)
                self.assertIn("timed out", self.errors.getvalue())
                if stage in ("create", "upload", "edit"):
                    self.assertTrue(self.fake.created)
                    self.assertIn("retained without cleanup", self.errors.getvalue())

    def test_http_status_parser_and_fail_closed_errors(self):
        endpoint = f"git/ref/tags/{TAG}"
        for protocol in ("HTTP/1.1", "HTTP/2", "HTTP/2.0"):
            for newline in ("\n", "\r\n"):
                with self.subTest(protocol=protocol, newline=newline):
                    self.fake.overrides[endpoint] = response(404, {"message": "Not Found"}, protocol=protocol, newline=newline)
                    self.assertEqual(self.run_cli("preflight"), 0, self.errors.getvalue())
        cases = [response(code, {"message": "Not Found"}) for code in (301, 401, 403, 429, 500, 502, 503)]
        cases += [response(200, {}, returncode=1), response(404, {}, returncode=2),
                  response(404, raw="not JSON"), response(404, data=None), response(200, data=True),
                  response(200, raw='{}\nHTTP/2.0 404 Not Found\n\n{}'),
                  subprocess.CompletedProcess([], 1, "", "HTTP 404 " + SECRET),
                  subprocess.CompletedProcess([], 1, '{"message":"HTTP 404"}', SECRET),
                  subprocess.CompletedProcess([], 0, "HTTP/2.0 200 OK\n{}", SECRET),
                  subprocess.CompletedProcess([], 0, "HTTP/1.1 100 Continue\n\nHTTP/2.0 200 OK\n\n{}", SECRET)]
        for result in cases:
            with self.subTest(stdout=result.stdout, returncode=result.returncode):
                self.fake.overrides[endpoint] = result
                self.assertNotEqual(self.run_cli("preflight"), 0)
                self.assertEqual(self.actions(), [])

    def test_read_errors_at_every_preflight_endpoint_prevent_create(self):
        for endpoint in (f"commits/{SOURCE}", f"git/ref/tags/{TAG}", f"releases/tags/{TAG}", "releases?per_page=100&page=1"):
            for code in (403, 503):
                with self.subTest(endpoint=endpoint, code=code):
                    self.fake.overrides = {endpoint: response(code, {"message": "Not Found"})}
                    self.assertNotEqual(self.run_cli(), 0)
                    self.assertEqual(self.actions(), [])

    def test_source_commit_must_exist_and_resolve_exactly(self):
        for result in (response(404, {}), response(data={"sha": "b" * 40}), response(data=[])):
            self.fake.overrides[f"commits/{SOURCE}"] = result
            self.assertNotEqual(self.run_cli(), 0)
            self.assertEqual(self.actions(), [])

    def test_tag_and_published_release_collisions_prevent_create(self):
        self.fake.tag_exists = True
        self.assertNotEqual(self.run_cli(), 0)
        self.fake.tag_exists, self.fake.published = False, True
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.actions(), [])

    def test_draft_collision_on_later_page_is_not_ignored(self):
        self.fake.other_releases = [{"tag_name": f"other-{i}"} for i in range(100)] + [self.fake.release]
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.actions(), [])
        self.assertTrue(any(c[-1].endswith("page=2") for c in self.fake.calls))

    def test_release_pagination_is_bounded(self):
        args = mock.Mock(repository=REPOSITORY, tag=TAG)
        with mock.patch.object(publisher, "api", return_value=[{"tag_name": "other"}] * 100) as api:
            with self.assertRaisesRegex(ValueError, "pagination limit"):
                publisher.find_release(args)
        self.assertEqual(api.call_count, 20)

    def test_debian_version_tilde_is_valid_in_asset_and_manifest(self):
        (self.assets / "e87n-display_1.2.3_all.deb").rename(self.assets / "e87n-display_1.2.3~rc1_all.deb")
        self.manifest()
        self.assertEqual(self.run_cli(), 0, self.errors.getvalue())

    def test_success_order_and_all_assets_verified_while_draft(self):
        self.assertEqual(self.run_cli(), 0, self.errors.getvalue())
        self.assertEqual(self.actions(), ["create", "upload", "edit"])
        calls = self.fake.calls
        upload_index = next(i for i, c in enumerate(calls) if c[1:3] == ["release", "upload"])
        edit_index = next(i for i, c in enumerate(calls) if c[1:3] == ["release", "edit"])
        self.assertTrue(any(c[-1].endswith("releases/42/assets?per_page=100") for c in calls[upload_index + 1:edit_index]))
        upload = calls[upload_index]
        self.assertEqual(set(upload[upload.index("--") + 1:]), {str(p) for p in self.assets.iterdir()})
        self.assertEqual(len(self.fake.assets), 8)
        self.assertTrue(self.fake.published and self.fake.tag_exists)
        self.assertTrue(calls[-1][-1].endswith(f"git/ref/tags/{TAG}"))

    def test_publish_repeats_preflight_and_rejects_new_collision(self):
        self.assertEqual(self.run_cli("preflight"), 0)
        self.fake.tag_exists = True
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.actions(), [])
        self.assertEqual(sum(c[1] == "auth" for c in self.fake.calls), 2)

    def test_partial_create_upload_and_edit_failures_preserve_remote_state(self):
        for stage, actions in (("create", ["create"]), ("upload", ["create", "upload"]), ("edit", ["create", "upload", "edit"])):
            with self.subTest(stage=stage):
                self.fake = FakeGh(self)
                self.fake.fail = stage
                self.assertNotEqual(self.run_cli(), 0)
                self.assertTrue(self.fake.created)
                self.assertFalse(self.fake.published)
                self.assertEqual(self.actions(), actions)
                self.assertIn("retained without cleanup", self.errors.getvalue())

    def test_existing_draft_assets_are_preserved_without_upload_or_clobber(self):
        self.fake.assets = [{"name": "old.img.xz", "size": 3, "state": "uploaded"}]
        before = copy.deepcopy(self.fake.assets)
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.actions(), ["create"])
        self.assertEqual(self.fake.assets, before)

    def test_remote_asset_integrity_failures_prevent_edit(self):
        mutations = [lambda a: a[0].update(digest="sha256:" + "0" * 64), lambda a: a[0].pop("digest"),
                     lambda a: a[0].update(digest=None), lambda a: a[0].update(digest="md5:invalid"),
                     lambda a: a[0].update(size=a[0]["size"] + 1), lambda a: a[0].update(size=True),
                     lambda a: a[0].update(state="starter"), lambda a: a.pop(),
                     lambda a: a.append(dict(a[0])), lambda a: a[0].update(name=a[1]["name"]),
                     lambda a: a[0].update(name="unexpected"), lambda a: a.__setitem__(0, None)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.fake = FakeGh(self)
                self.fake.after_upload = mutation
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.actions(), ["create", "upload"])
                self.assertFalse(self.fake.published)

    def test_remote_read_failure_after_upload_preserves_draft(self):
        self.fake.after_upload = lambda a: self.fake.overrides.update({"releases/42/assets?per_page=100": response(503, {})})
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.actions(), ["create", "upload"])

    def test_wrong_draft_identity_or_state_prevents_upload(self):
        for key, value in (("id", "42"), ("draft", False), ("prerelease", False), ("target_commitish", "main"),
                           ("published_at", "already published")):
            with self.subTest(key=key):
                self.fake = FakeGh(self)
                self.fake.release[key] = value
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.actions(), ["create"])

    def test_final_release_state_and_tag_target_are_verified(self):
        for mutate in (lambda: self.fake.release.update(draft=True), lambda: self.fake.release.update(published_at=None),
                       lambda: self.fake.release.update(id=99), lambda: self.fake.release.update(prerelease=False),
                       lambda: self.fake.tag_object.update(sha="b" * 40)):
            self.fake = FakeGh(self)
            self.fake.after_edit = mutate
            self.assertNotEqual(self.run_cli(), 0)
            self.assertEqual(self.actions(), ["create", "upload", "edit"])

    def test_annotated_tag_resolves_using_validated_sha_not_remote_url(self):
        annotated = "b" * 40
        self.fake.tag_object = {"type": "tag", "sha": annotated, "url": "https://untrusted.example/secret"}
        self.fake.overrides[f"git/tags/{annotated}"] = response(data={"sha": annotated, "object": {"type": "commit", "sha": SOURCE}})
        self.assertEqual(self.run_cli(), 0, self.errors.getvalue())
        self.assertTrue(self.fake.calls[-1][-1].endswith(f"git/tags/{annotated}"))

    def test_tag_race_before_publication_and_invalid_annotated_tags_fail_closed(self):
        annotated = "b" * 40
        objects = [{"type": "commit", "sha": "c" * 40}, {"type": "blob", "sha": annotated},
                   {"type": "tag", "sha": "../../secret"}, {"type": "tag", "sha": annotated}]
        for obj in objects:
            with self.subTest(obj=obj):
                self.fake = FakeGh(self)
                self.fake.tag_object = obj
                self.fake.after_upload = lambda a: setattr(self.fake, "tag_exists", True)
                self.fake.overrides[f"git/tags/{annotated}"] = response(data={"sha": annotated, "object": obj})
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.actions(), ["create", "upload"])

    def test_wrong_metadata_is_rejected_before_auth(self):
        for kind in ("image", "display"):
            path = self.assets / f"{kind}-build-metadata.json"
            original = json.loads(path.read_text())
            cases = [{}, [], {**original, "kind": "wrong"}, {**original, "build_step_outcome": "failure"},
                     {**original, "source_commit": "b" * 40}, {**original, "collection_errors": ["failed"]}]
            cases += [{**original, "target": target} for target in (None, [], {},
                      {**publisher.TARGET, "debian": "12"}, {**publisher.TARGET, "release": "bookworm"},
                      {**publisher.TARGET, "kernel": "6.18.50"}, {**publisher.TARGET, "extra_storage": "yes"})]
            if kind == "image":
                cases.append({**original, "image_static_audit": "not proven"})
            for metadata in cases:
                with self.subTest(kind=kind, metadata=metadata):
                    path.write_text(json.dumps(metadata))
                    self.manifest()
                    self.assertNotEqual(self.run_cli(), 0)
                    self.assertEqual(self.fake.calls, [])
            path.write_text(json.dumps(original))
            self.manifest()

    def test_checksum_manifest_exact_coverage_and_safe_unique_paths(self):
        path = self.assets / "SHA256SUMS"
        original = path.read_text()
        first = original.splitlines()[0]
        invalid = ["", original + first + "\n", "\n".join(original.splitlines()[1:]) + "\n",
                   original + "0" * 64 + "  SHA256SUMS\n", original + "0" * 64 + "  unexpected.txt\n",
                   original.replace("  ", "  ../", 1), original.replace("  ", "  /", 1),
                   original.replace("  ", "  sub/", 1), original.replace("  ", "  ./", 1),
                   original.replace("  ", "  bad\\", 1), original.replace("  ", "  -", 1),
                   "g" + original[1:], "0" * 64 + original[64:]]
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                path.write_text(manifest)
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.fake.calls, [])
        path.write_text(original.upper()[:64] + original[64:])
        self.assertEqual(self.run_cli(), 0)

    def test_unexpected_missing_and_duplicate_required_files_rejected_locally(self):
        for name in ("another.img.xz", "e87n-display_2_all.deb", "extra.txt", "evil;id.img.xz", "image#label.img.xz"):
            path = self.assets / name
            path.write_text("fixture")
            self.manifest()
            self.assertNotEqual(self.run_cli(), 0)
            self.assertEqual(self.fake.calls, [])
            path.unlink()
        self.manifest()
        for path in list(self.assets.iterdir()):
            saved = path.read_bytes()
            path.unlink()
            self.assertNotEqual(self.run_cli(), 0)
            self.assertEqual(self.fake.calls, [])
            path.write_bytes(saved)

    def test_symlink_directories_files_and_nonregular_files_rejected_locally(self):
        link = self.root / "alias"
        link.symlink_to(self.assets, target_is_directory=True)
        self.assertNotEqual(self.run_cli(assets=str(link)), 0)
        parent = self.root / "parent"
        parent.symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.run_cli(assets=str(parent / "assets")), 0)
        path = self.assets / "linked.img.xz"
        path.symlink_to(self.assets / "e87n-trixie.img.xz")
        self.assertNotEqual(self.run_cli(), 0)
        path.unlink()
        path.mkdir()
        self.assertNotEqual(self.run_cli(), 0)
        path.rmdir()
        os.mkfifo(path)
        self.assertNotEqual(self.run_cli(), 0)
        path.unlink()
        os.link(self.assets / "e87n-trixie.img.xz", path)
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.fake.calls, [])

    def test_corrupt_payload_is_rejected_before_auth_and_hashes_are_streamed(self):
        (self.assets / "e87n-trixie.img.xz").write_bytes(b"changed payload")
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.fake.calls, [])
        self.manifest()
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("Payload must stream")):
            files = publisher.validate_assets(self.assets, SOURCE)
        self.assertEqual(len(files), 8)
        payload = b"x" * (2 * 1024 * 1024 + 7)
        reader = mock.MagicMock(wraps=io.BytesIO(payload))
        context = mock.MagicMock()
        context.__enter__.return_value = reader
        with mock.patch.object(Path, "open", return_value=context):
            self.assertEqual(publisher.digest(Path("fixture")), hashlib.sha256(payload).hexdigest())
        self.assertEqual(reader.read.call_args_list, [mock.call(1024 * 1024)] * 4)


if __name__ == "__main__":
    unittest.main()
