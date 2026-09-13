#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Workflow contracts and isolated CI fixtures; never invoke real builders/sudo."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/build-e87n.yml"


class WorkflowPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader preserves GitHub's `on` key (YAML 1.1 calls it a boolean).
        cls.workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    def test_triggers_and_pins(self):
        events = self.workflow["on"]
        self.assertEqual(set(events), {"workflow_dispatch"})
        inputs = events["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"kernel_version", "release_tag"})
        self.assertEqual(inputs["release_tag"]["type"], "string")
        self.assertEqual(inputs["release_tag"]["required"], "false")
        self.assertEqual(inputs["kernel_version"]["options"], ["6.18.51"])
        self.assertEqual(inputs["kernel_version"]["type"], "choice")
        self.assertEqual(inputs["kernel_version"]["default"], "6.18.51")
        for needle, file in (
            ("commit:f6388029ea9e2c9e807d73827658738ea131faee", "userpatches/config/sources/families/edgepi-e87n.conf"),
            ("7c1bb29eb0e7bd75b0703d86fe654b2680e646da", "build-armbian.sh"),
        ):
            self.assertIn(needle, (REPO / file).read_text())

    def test_permissions_runners_and_actions(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.workflow["concurrency"]["cancel-in-progress"], "false")
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["image"]["runs-on"], "ubuntu-24.04-arm")
        self.assertEqual(jobs["image"]["needs"], "validate")
        self.assertEqual(jobs["display"]["needs"], "validate")
        self.assertEqual(jobs["release"]["permissions"], {"contents": "write", "actions": "read"})
        for name in ("validate", "image", "display"):
            self.assertNotIn("permissions", jobs[name])
        reviewed = {
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        }
        for job in jobs.values():
            self.assertLessEqual(int(job["timeout-minutes"]), 360)
            for step in job["steps"]:
                self.assertNotIn("continue-on-error", step)
                if "uses" in step:
                    self.assertIn(step["uses"], reviewed)
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertEqual(step["with"]["persist-credentials"], "false")
                if "run" in step:
                    self.assertNotIn("${{ inputs.", step["run"])
        steps = jobs["image"]["steps"]
        build = next(step for step in steps if step.get("id") == "build")
        self.assertLess(int(build["timeout-minutes"]), int(jobs["image"]["timeout-minutes"]) - 45)

    def test_release_is_manual_main_only_after_all_success(self):
        job = self.workflow["jobs"]["release"]
        self.assertEqual(job["needs"], ["validate", "display", "image"])
        for condition in ("github.event_name == 'workflow_dispatch'", "github.ref == 'refs/heads/main'",
                          "needs.validate.result == 'success'", "needs.display.result == 'success'",
                          "needs.image.result == 'success'"):
            self.assertIn(condition, job["if"])
        self.assertNotIn("always()", job["if"])
        preflight = next(s for s in self.workflow["jobs"]["validate"]["steps"]
                         if s.get("id") == "release_tag")
        self.assertIn('"$GITHUB_EVENT_NAME" == workflow_dispatch', preflight["run"])
        self.assertIn('"$GITHUB_REF" == refs/heads/main', preflight["run"])
        self.assertIn("ci-publish-release.py preflight", preflight["run"])
        self.assertEqual(preflight["env"]["INPUT_RELEASE_TAG"], "${{ inputs.release_tag }}")

    def test_release_uses_exact_current_attempt_and_verified_assets(self):
        steps = self.workflow["jobs"]["release"]["steps"]
        download = next(s for s in steps if 'gh run download' in s.get("run", ""))
        self.assertEqual(download["run"].count('gh run download "$GITHUB_RUN_ID"'), 2)
        for name in ("e87n-trixie-6.18.51-candidate", "e87n-display-candidate"):
            self.assertIn(name + '-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}', download["run"])
        self.assertNotIn("--pattern", download["run"])
        prepare = next(s for s in steps if "ci-prepare-release.py" in s.get("run", ""))
        publish = next(s for s in steps if "ci-publish-release.py publish" in s.get("run", ""))
        self.assertLess(steps.index(prepare), steps.index(publish))
        self.assertIn('--source-commit "$GITHUB_SHA"', prepare["run"])
        self.assertIn('--run-attempt "$GITHUB_RUN_ATTEMPT"', prepare["run"])
        self.assertIn('--source-commit "$GITHUB_SHA"', publish["run"])
        self.assertNotIn("--clobber", publish["run"])
        self.assertEqual(publish["env"]["RELEASE_TAG"], "${{ needs.validate.outputs.release_tag }}")

    def test_failure_uploads(self):
        for kind in ("image", "display"):
            steps = self.workflow["jobs"][kind]["steps"]
            uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
            self.assertEqual(len(uploads), 2)
            for upload in uploads:
                self.assertEqual(upload["if"], "${{ always() }}")
                self.assertIn("github.run_attempt", upload["with"]["name"])
                self.assertEqual(upload["with"]["retention-days"], "14")
            collect = next(s for s in steps if "ci-collect-artifacts.py" in s.get("run", ""))
            self.assertEqual(collect["if"], "${{ always() }}")
            self.assertIn("steps.build.outcome", collect["env"]["BUILD_OUTCOME"])


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e87n-ci-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        for name in ("ci-build.sh", "ci-collect-artifacts.py", "ci-prepare-runner.sh"):
            shutil.copyfile(REPO / "scripts" / name, self.root / "scripts" / name)
        self.env = {**os.environ, "GITHUB_SHA": "fixture-commit", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
        self.env["RUNNER_TEMP"] = str(self.root)
        for key in ("BASH_ENV", "E87N_KERNEL_VERSION", "GITHUB_ACTIONS", "E87N_RUNNER_ENVIRONMENT"):
            self.env.pop(key, None)

    def put(self, name, contents="fixture\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def run_script(self, script, *args):
        return subprocess.run(["bash", str(self.root / "scripts" / script), *args], cwd=self.root, env=self.env, text=True, capture_output=True)

    def collect(self, kind, status):
        import sys
        return subprocess.run([sys.executable, str(self.root / "scripts/ci-collect-artifacts.py"), "--kind", kind, "--status", status], cwd=self.root, env=self.env, text=True, capture_output=True)

    def prepare_mocks(self, result=0, image=True):
        self.put("packaging/e87n-display/VERSION", "1.2.3\n")
        self.put("userpatches/config/sources/families/edgepi-e87n.conf", "# commit:f6388029ea9e2c9e807d73827658738ea131faee\n")
        self.put("build-armbian.sh", "#!/bin/bash\n# 7c1bb29eb0e7bd75b0703d86fe654b2680e646da\nprintf '%s\\n' \"$@\" > image-args\n" +
                 ("mkdir -p source/armbian-build/output/images\nprintf 'fixture image' > source/armbian-build/output/images/test.img.xz\n" if image else "") + f"exit {result}\n")
        self.put("scripts/build-display-deb.sh", "#!/bin/bash\nprintf '%s\\n' \"$@\" > display-args\nmkdir -p \"$2\"\nprintf 'fixture deb' > \"$2/e87n-display_1.2.3_all.deb\"\n" + f"exit {result}\n")
        mocks = self.put("mocks.sh", """uname() { if [[ $1 == -s ]]; then echo Linux; else echo aarch64; fi; }
sudo() {
    if [[ $* == '-n true' ]]; then return 0; fi
    [[ $1 == -n && $2 == bash && $3 == scripts/verify-image.sh ]] || return 99
    printf '%s\\n' "$@" > audit-args
    printf 'fixture audit result\\n'
    return "${E87N_MOCK_AUDIT_EXIT:-0}"
}
xz() {
    [[ $1 == -dc && $2 == -- ]] || return 99
    printf 'fixture raw image'
    return "${E87N_MOCK_XZ_EXIT:-0}"
}
dpkg-deb() { echo 'fixture package metadata'; }
export -f uname sudo xz dpkg-deb
""")
        self.env["BASH_ENV"] = str(mocks)

    def test_native_image_arguments(self):
        self.prepare_mocks()
        result = self.run_script("ci-build.sh", "image")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = (self.root / "image-args").read_text().splitlines()
        self.assertEqual(args[0], "build")
        for required in ("RELEASE=trixie", "BUILD_MINIMAL=yes", "BUILD_DESKTOP=no", "PREFER_DOCKER=no", "E87N_EXTRA_STORAGE=no", "COMPRESS_OUTPUTIMAGE=xz", "CARD_DEVICE=", "SEND_TO_SERVER="):
            self.assertIn(required, args)
        audit = (self.root / "audit-args").read_text().splitlines()
        self.assertEqual(audit[:-1], ["-n", "bash", "scripts/verify-image.sh", "--release", "trixie", "--require-usb-root", "--require-display-fan", "--require-system"])
        raw = Path(audit[-1])
        self.assertEqual(raw.read_text(), "fixture raw image")
        self.assertTrue(raw.parent.name.startswith("e87n-ci-audit."))
        self.assertNotIn("output", raw.parts)

    def test_audit_failure_marks_build_and_collection_failed(self):
        self.prepare_mocks()
        self.env["E87N_MOCK_AUDIT_EXIT"] = "42"
        result = self.run_script("ci-build.sh", "image")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertEqual((self.root / "output/ci/logs/image.exit-code").read_text(), "42\n")
        self.assertIn("fixture audit result", (self.root / "output/ci/logs/image-audit-1.log").read_text())
        self.assertEqual(self.collect("image", "failure").returncode, 0)
        dest = self.root / "output/ci/artifacts"
        metadata = json.loads((dest / "build-metadata.json").read_text())
        self.assertEqual(metadata["build_step_outcome"], "failure")
        self.assertEqual(metadata["image_static_audit"], "not proven")
        self.assertFalse(list(dest.rglob("candidate.img")))

    def test_decompression_failure_prevents_audit(self):
        self.prepare_mocks()
        self.env["E87N_MOCK_XZ_EXIT"] = "31"
        self.assertEqual(self.run_script("ci-build.sh", "image").returncode, 31)
        self.assertFalse((self.root / "audit-args").exists())

    def test_package_builder_contract(self):
        self.prepare_mocks()
        result = self.run_script("ci-build.sh", "display")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "display-args").read_text().splitlines(), ["--output-dir", str(self.root / "output/ci/display-debs")])

    def test_builder_failures_propagate_and_logs_survive(self):
        self.prepare_mocks(result=23)
        for kind in ("display", "image"):
            with self.subTest(kind=kind):
                result = self.run_script("ci-build.sh", kind)
                self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                self.assertEqual((self.root / f"output/ci/logs/{kind}.exit-code").read_text(), "23\n")
                self.assertTrue((self.root / f"output/ci/logs/{kind}.log").is_file())

    def test_no_image_is_failure(self):
        self.prepare_mocks(image=False)
        self.assertNotEqual(self.run_script("ci-build.sh", "image").returncode, 0)

    def test_arbitrary_kernel_is_rejected_before_build(self):
        self.prepare_mocks()
        self.env["E87N_KERNEL_VERSION"] = "6.19; touch should-not-exist"
        self.assertEqual(self.run_script("ci-build.sh", "image").returncode, 2)
        self.assertFalse((self.root / "image-args").exists())
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_stale_output_is_preserved_and_rejected(self):
        self.prepare_mocks()
        sentinel = self.put("source/armbian-build/output/images/old.img", "keep me")
        self.assertNotEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep me")
        self.assertFalse((self.root / "image-args").exists())

    def test_cleanup_refuses_non_hosted_environment(self):
        # No mocks: the guard MUST fire before sudo or any host filesystem work.
        result = self.run_script("ci-prepare-runner.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restricted to GitHub-hosted", result.stderr)

    def test_failed_image_collection_and_checksums(self):
        image = self.put("source/armbian-build/output/images/candidate.img.xz", "partial image")
        self.put("source/armbian-build/output/debs/kernel/fixture.deb")
        self.put("source/armbian-build/output/logs/build.log")
        self.put("output/ci/logs/image.exit-code", "23\n")
        self.put("source/armbian-build/.tmp/rootfs/etc/shadow", "never upload")
        self.put("source/armbian-build/output/images/private.key", "never upload")
        result = self.collect("image", "failure")
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.root / "output/ci/artifacts"
        metadata = json.loads((dest / "build-metadata.json").read_text())
        self.assertEqual(metadata["build_step_outcome"], "failure")
        self.assertEqual(metadata["source_commit"], "fixture-commit")
        self.assertEqual(image.stat().st_ino, (dest / "images/candidate.img.xz").stat().st_ino)
        for line in (dest / "SHA256SUMS").read_text().splitlines():
            digest, relative = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((dest / relative).read_bytes()).hexdigest())
        self.assertNotIn("never upload", "".join(p.read_text() for p in dest.rglob("*") if p.is_file()))

    def test_missing_payload_fails_successful_build(self):
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.root / "output/ci/artifacts/build-metadata.json").is_file())

    def test_early_failure_still_has_metadata_and_checksums(self):
        result = self.collect("display", "skipped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "output/ci/artifacts/SHA256SUMS").is_file())

    def test_symlink_payload_is_rejected_and_log_collected(self):
        secret = self.put("private.txt", "private key fixture")
        directory = self.root / "output/ci/display-debs"
        directory.mkdir(parents=True)
        (directory / "e87n-display_1_all.deb").symlink_to(secret)
        self.put("output/ci/logs/display.log", "failure log")
        result = self.collect("display", "failure")
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.root / "output/ci/artifacts/logs/ci/display.log").is_file())
        self.assertFalse((self.root / "output/ci/artifacts/packages/display/e87n-display_1_all.deb").exists())

    def test_symlink_output_directory_is_rejected(self):
        other = self.root / "unrelated"
        other.mkdir()
        (self.root / "output").symlink_to(other, target_is_directory=True)
        result = self.collect("display", "skipped")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
