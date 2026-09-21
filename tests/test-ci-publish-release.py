#!/usr/bin/env python3
"""Isolated publisher fixtures: every subprocess is intercepted; no network."""
import contextlib
import copy
import hashlib
from importlib import import_module
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci-publish-release.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("publisher", SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
fixture_report = import_module("test-ci-simulation").fixture_report
SOURCE, TAG, REPOSITORY = "a1" * 20, publisher.release_tag("image"), "owner/repo"
IMAGE, DEB = publisher.image_filename(), publisher.display_filename()
SECRET = "ghp_fixture_never_log_this"


def response(code=200, data=None, *, raw=None, protocol="HTTP/2.0", newline="\n", returncode=None):
    body = json.dumps(data) if raw is None else raw
    stdout = newline.join((f"{protocol} {code} Test", "Content-Type: application/json", "", body))
    return subprocess.CompletedProcess([], int(code >= 400) if returncode is None else returncode, stdout, SECRET)


class FakeGh:
    def __init__(self, test, tag=TAG):
        self.test, self.tag, self.calls, self.overrides = test, tag, [], {}
        self.created = self.published = self.tag_exists = False
        self.fail = None
        self.release = {"id": 42, "tag_name": self.tag, "draft": True, "prerelease": True,
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
            endpoint = command[-1].removeprefix(f"repos/{REPOSITORY}/")
            if endpoint in self.overrides:
                override = self.overrides[endpoint]
                return override() if callable(override) else override
            if endpoint == f"commits/{SOURCE}":
                return response(data={"sha": SOURCE})
            if endpoint == f"git/ref/tags/{self.tag}":
                return response(data={"ref": f"refs/tags/{self.tag}", "object": self.tag_object}) if self.tag_exists else response(404, {})
            if endpoint == f"releases/tags/{self.tag}":
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
        self.test.assertEqual(command[3], self.tag)
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
            self.assets = [{"name": Path(path).name, "size": Path(path).stat().st_size, "state": "uploaded",
                            "digest": "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                           for path in paths]
            self.after_upload(self.assets)
            if self.fail == "upload":
                self.assets = []
        elif action == "edit":
            self.test.assertTrue(self.created and not self.published)
            self.test.assertTrue({"--draft=false", "--prerelease", "--latest=false"} <= set(command))
            if self.fail != "edit":
                self.published = self.tag_exists = True
                self.release.update(draft=False, published_at="2026-09-21T00:00:00Z")
                self.after_edit()
        else:
            self.test.fail("Unexpected write: " + action)
        return subprocess.CompletedProcess(command, int(self.fail == action), SECRET, SECRET)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="e87n-release-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.kind = "image"
        self.build_assets(self.kind)
        self.fake = FakeGh(self, publisher.release_tag(self.kind))
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(publisher.subprocess, "run", side_effect=lambda *args, **kwargs: self.fake(*args, **kwargs)).start()
        mock.patch.dict(os.environ, GH_HOST="untrusted.example", GH_TOKEN=SECRET, GH_DEBUG="api").start()

    def metadata(self, kind):
        data = {"kind": kind, "build_step_outcome": "success", "source_commit": SOURCE,
                "run_id": "123", "run_attempt": "1", "target": dict(publisher.TARGET),
                "collection_errors": [], "display_version_source": publisher.display_version(),
                "image_static_audit": "passed" if kind == "image" else "not applicable"}
        data.update({key: publisher.BUILD[key] for key in
                     ("armbian_commit", "kernel_commit", "kernel_source", "kernel_release")})
        if kind == "image":
            data.update(factory_format=publisher.FORMAT, factory_static_audit="passed",
                        simulation_validation="passed")
        return data

    def build_assets(self, kind):
        for path in self.assets.iterdir():
            path.unlink()
        self.kind = kind
        for name in ("build-evidence.tar.xz", "RELEASE-NOTES.md"):
            (self.assets / name).write_bytes(("fixture " + name).encode())
        deb = self.assets / DEB
        deb.write_bytes(b"display package")
        (self.assets / f"{kind}-build-metadata.json").write_text(json.dumps(self.metadata(kind)))
        if kind == "image":
            firmware = self.assets / IMAGE
            firmware.write_bytes(b"firmware tar")
            (self.assets / "kernel-packages.tar.xz").write_bytes(b"kernel packages")
            (self.assets / "simulation-result.json").write_text(json.dumps(fixture_report(
                firmware.read_bytes(), deb.read_bytes(), SOURCE, "123", "1")))
        self.manifest()

    def manifest(self):
        (self.assets / "SHA256SUMS").write_text("".join(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
            for path in sorted(self.assets.iterdir()) if path.name != "SHA256SUMS"))

    def reset_channel(self, kind):
        self.build_assets(kind)
        self.fake = FakeGh(self, publisher.release_tag(kind))
        self.output, self.errors = io.StringIO(), io.StringIO()

    def run_cli(self, command="publish", kind=None, **values):
        options = {"kind": self.kind if kind is None else kind, "repository": REPOSITORY,
                   "source-commit": SOURCE, "tag": publisher.release_tag(self.kind)}
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
        return [call[2] for call in self.fake.calls if call[1] == "release"]

    def test_preflight_is_read_only_for_both_kinds(self):
        for kind in ("image", "display"):
            with self.subTest(kind=kind):
                self.reset_channel(kind)
                self.assertEqual(self.run_cli("preflight"), 0, self.errors.getvalue())
                self.assertEqual(self.actions(), [])
                self.assertEqual(len(self.fake.calls), 5)

    def test_each_kind_publishes_exactly_one_public_payload(self):
        expected = {"image": IMAGE, "display": DEB}
        for kind, payload in expected.items():
            with self.subTest(kind=kind):
                self.reset_channel(kind)
                self.assertEqual(self.run_cli(), 0, self.errors.getvalue())
                self.assertEqual(self.actions(), ["create", "upload", "edit"])
                self.assertEqual([asset["name"] for asset in self.fake.assets], [payload])
                upload = next(call for call in self.fake.calls if call[1:3] == ["release", "upload"])
                self.assertEqual(upload[upload.index("--") + 1:], [str(self.assets / payload)])
                create = next(call for call in self.fake.calls if call[1:3] == ["release", "create"])
                self.assertEqual(create[create.index("--title") + 1], publisher.release_title(kind))

    def test_image_staging_requires_tested_deb_but_never_uploads_it(self):
        files = publisher.validate_assets(self.assets, SOURCE, "image")
        self.assertIn(DEB, files)
        self.assertEqual(self.run_cli(), 0, self.errors.getvalue())
        self.assertEqual({asset["name"] for asset in self.fake.assets}, {IMAGE})

    def test_display_staging_rejects_image_payload_and_evidence(self):
        self.reset_channel("display")
        for name in ("unexpected-uboot-firmware.tar", "kernel-packages.tar.xz",
                     "simulation-result.json", "image-build-metadata.json"):
            with self.subTest(name=name):
                path = self.assets / name
                path.write_bytes(b"unexpected")
                self.manifest()
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.fake.calls, [])
                path.unlink()
                self.manifest()

    def test_exact_whitelist_manifest_and_metadata_for_both_kinds(self):
        for kind in ("image", "display"):
            self.reset_channel(kind)
            extra = self.assets / "extra.txt"
            extra.write_text("unexpected")
            self.manifest()
            self.assertNotEqual(self.run_cli(), 0)
            self.assertEqual(self.fake.calls, [])
            extra.unlink()
            self.manifest()
            metadata_path = self.assets / f"{kind}-build-metadata.json"
            original = json.loads(metadata_path.read_text())
            cases = [{}, [], {**original, "kind": "wrong"},
                     {**original, "build_step_outcome": "failure"},
                     {**original, "source_commit": "b" * 40},
                     {**original, "collection_errors": ["failed"]},
                     {**original, "display_version_source": "9.9-1"},
                     {**original, "target": {**publisher.TARGET, "debian": "12"}},
                     {**original, "kernel_commit": "wrong"},
                     {**original, "image_static_audit": "not applicable" if kind == "image" else "passed"}]
            if kind == "image":
                cases.extend(({**original, "factory_format": "raw-gpt"},
                              {**original, "factory_static_audit": "failed"},
                              {**original, "simulation_validation": "failed"}))
            for metadata in cases:
                metadata_path.write_text(json.dumps(metadata))
                self.manifest()
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.fake.calls, [])
            serialized = json.dumps(original)
            metadata_path.write_text('{"kind":"duplicate",' + serialized.lstrip()[1:])
            self.manifest()
            self.assertNotEqual(self.run_cli(), 0)
            self.assertEqual(self.fake.calls, [])
            metadata_path.write_text(json.dumps(original))
            self.manifest()
            checksum_path = self.assets / "SHA256SUMS"
            checksum = checksum_path.read_text()
            checksum_path.write_text(checksum + checksum.splitlines()[0] + "\n")
            self.assertNotEqual(self.run_cli(), 0)

    def test_image_simulation_binds_source_run_firmware_and_tested_deb(self):
        path = self.assets / "simulation-result.json"
        original = json.loads(path.read_text())
        for section, key, value in (
                ("binding", "source_commit", "b" * 40), ("binding", "run_id", "124"),
                ("binding", "run_attempt", "2"), ("artifacts", "firmware_tar_sha256", "b" * 64),
                ("artifacts", "display_deb_sha256", "b" * 64), ("guest", "display_package", False)):
            report = copy.deepcopy(original)
            report[section][key] = value
            path.write_text(json.dumps(report))
            self.manifest()
            with self.subTest(section=section, key=key):
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.fake.calls, [])

    def test_invalid_inputs_and_kind_make_no_subprocess_calls(self):
        for key, values in {
            "kind": ["", "firmware", "image;id"],
            "tag": ["", "-x", "x/y", "x..y", "x.lock", "x;touch pwned", "x\n",
                    publisher.release_tag("display"), "e87n-image-v2026.09.1-run-123"],
            "repository": ["owner", "owner/repo/other", "../repo", "owner/repo?x=1"],
            "source-commit": ["a" * 39, "g" * 40, SOURCE + ";id", "--help"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.fake = FakeGh(self, publisher.release_tag(self.kind))
                    self.output, self.errors = io.StringIO(), io.StringIO()
                    self.assertNotEqual(self.run_cli("preflight", **{key: value}), 0)
                    self.assertEqual(self.fake.calls, [])
        self.fake = FakeGh(self, publisher.release_tag(self.kind))
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.assertNotEqual(self.run_cli(assets=None), 0)
        self.fake = FakeGh(self, publisher.release_tag(self.kind))
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.assertNotEqual(self.run_cli("preflight", assets=str(self.assets)), 0)
        self.assertEqual(self.fake.calls, [])

    def test_auth_api_and_missing_gh_fail_without_publish_or_secret_leak(self):
        self.fake.fail = "auth"
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(len(self.fake.calls), 1)
        self.fake = FakeGh(self, publisher.release_tag(self.kind))
        self.fake.overrides[f"commits/{SOURCE}"] = response(503, {})
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.actions(), [])
        self.fake = FakeGh(self, publisher.release_tag(self.kind))
        with mock.patch.object(publisher.subprocess, "run", side_effect=FileNotFoundError(SECRET)):
            self.assertNotEqual(self.run_cli(), 0)

    def test_tag_release_and_draft_collisions_prevent_create(self):
        self.fake.tag_exists = True
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.actions(), [])
        self.fake = FakeGh(self)
        self.fake.published = True
        self.assertNotEqual(self.run_cli(), 0)
        self.fake = FakeGh(self)
        self.fake.other_releases = [{"tag_name": f"other-{index}"} for index in range(100)] + [self.fake.release]
        self.assertNotEqual(self.run_cli(), 0)
        self.assertTrue(any(call[-1].endswith("page=2") for call in self.fake.calls))

    def test_legacy_display_release_blocks_republishing_same_debian_version(self):
        self.reset_channel("display")
        encoded = publisher.display_version().replace("+", ".plus.").replace("~", ".tilde.")
        self.fake.other_releases = [{"tag_name": f"e87n-display-{encoded}-35586533613-1"}]
        self.assertNotEqual(self.run_cli("preflight"), 0)
        self.assertEqual(self.actions(), [])
        self.assertIn("Release version already exists", self.errors.getvalue())

    def test_partial_remote_failures_preserve_draft_and_never_clobber(self):
        for stage, actions in (("create", ["create"]), ("upload", ["create", "upload"]),
                               ("edit", ["create", "upload", "edit"])):
            self.fake = FakeGh(self, publisher.release_tag(self.kind))
            self.fake.fail = stage
            with self.subTest(stage=stage):
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.actions(), actions)
                self.assertIn("retained without cleanup", self.errors.getvalue())

    def test_remote_asset_integrity_is_verified_before_publish(self):
        mutations = [lambda assets: assets[0].update(digest="sha256:" + "0" * 64),
                     lambda assets: assets[0].pop("digest"),
                     lambda assets: assets[0].update(size=assets[0]["size"] + 1),
                     lambda assets: assets[0].update(state="starter"),
                     lambda assets: assets.append(dict(assets[0])),
                     lambda assets: assets[0].update(name="unexpected")]
        for mutation in mutations:
            self.fake = FakeGh(self, publisher.release_tag(self.kind))
            self.fake.after_upload = mutation
            with self.subTest(mutation=mutation):
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.actions(), ["create", "upload"])
                self.assertFalse(self.fake.published)

    def test_final_state_and_tag_target_are_verified(self):
        for mutate in (lambda: self.fake.release.update(draft=True),
                       lambda: self.fake.release.update(published_at=None),
                       lambda: self.fake.release.update(prerelease=False),
                       lambda: self.fake.tag_object.update(sha="b" * 40)):
            self.fake = FakeGh(self, publisher.release_tag(self.kind))
            self.fake.after_edit = mutate
            with self.subTest(mutate=mutate):
                self.assertNotEqual(self.run_cli(), 0)
                self.assertEqual(self.actions(), ["create", "upload", "edit"])

    def test_symlink_and_hardlink_payloads_are_rejected(self):
        link = self.root / "alias"
        link.symlink_to(self.assets, target_is_directory=True)
        self.assertNotEqual(self.run_cli(assets=str(link)), 0)
        path = self.assets / "linked-uboot-firmware.tar"
        path.symlink_to(self.assets / IMAGE)
        self.assertNotEqual(self.run_cli(), 0)
        path.unlink()
        os.link(self.assets / IMAGE, path)
        self.assertNotEqual(self.run_cli(), 0)
        self.assertEqual(self.fake.calls, [])
        path.unlink()


if __name__ == "__main__":
    unittest.main()
