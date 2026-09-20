#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Workflow contracts and isolated CI fixtures; never invoke real builders/sudo."""
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_config import BUILD, TARGET
from factory_firmware import FORMAT

fixture_report = import_module("test-ci-simulation").fixture_report

WORKFLOW = REPO / ".github/workflows/build-e87n.yml"


class WorkflowPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader preserves GitHub's `on` key (YAML 1.1 calls it a boolean).
        cls.workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    def test_triggers_and_pins(self):
        events = self.workflow["on"]
        self.assertEqual(set(events), {"workflow_dispatch"})
        self.assertIn(events["workflow_dispatch"], (None, ""))
        self.assertNotIn("E87N_KERNEL_VERSION", self.workflow["env"])
        self.assertEqual(set(WORKFLOW.parent.glob("*.yml")) | set(WORKFLOW.parent.glob("*.yaml")), {WORKFLOW})
        self.assertNotIn("inputs.", WORKFLOW.read_text())
        for path in [WORKFLOW, *(REPO / "scripts").glob("ci-*")]:
            for pin in (BUILD["kernel_version"], BUILD["kernel_commit"], BUILD["armbian_commit"]):
                self.assertNotIn(pin, path.read_text(), str(path))

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
        ramdiag = (REPO / "scripts/ci-build.sh").read_text()
        self.assertIn("scripts/ci-build-ramdiag.sh", ramdiag)
        ramdiag_builder = (REPO / "scripts/ci-build-ramdiag.sh").read_text()
        self.assertIn('ramdiag_source="$repo_dir/scripts/ramdiag"', ramdiag_builder)
        self.assertNotIn("cp -a", ramdiag_builder)
        self.assertNotIn("ln -s", ramdiag_builder)
        self.assertIn('scripts/build_config.py" kernel_release)', ramdiag_builder)

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
        self.assertEqual(preflight["env"], {"GH_TOKEN": "${{ github.token }}"})
        self.assertIn('release_tag="e87n-${release}-${kernel_version}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"', preflight["run"])

    def test_zero_input_tag_generation_and_preflight_failure(self):
        step = next(s for s in self.workflow["jobs"]["validate"]["steps"] if s.get("id") == "release_tag")
        with tempfile.TemporaryDirectory(prefix="e87n-auto-tag-") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "userpatches/config").mkdir(parents=True)
            shutil.copyfile(REPO / "scripts/build_config.py", root / "scripts/build_config.py")
            # A future reviewed config must flow into the tag and artifact name.
            config = {**BUILD, "kernel_version": "6.18.99",
                      "kernel_release": "6.18.99-current-" + BUILD["linux_family"]}
            (root / "userpatches/config/e87n-build.json").write_text(json.dumps(config))
            mocks = root / "mocks.sh"
            mocks.write_text('''python3() {
    if [[ $1 == scripts/build_config.py ]]; then
        command "$E87N_TEST_PYTHON" "$@"
        return $?
    fi
    printf '%s\\n' "$@" >> "$E87N_PREFLIGHT_ARGS"
    return "${E87N_PREFLIGHT_EXIT:-0}"
}
export -f python3
''')
            env = {**os.environ, "BASH_ENV": str(mocks), "GITHUB_EVENT_NAME": "workflow_dispatch",
                   "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": "a" * 40,
                   "GITHUB_REPOSITORY": "fixture/repo", "GITHUB_RUN_ATTEMPT": "1",
                   "GIT_TERMINAL_PROMPT": "0", "E87N_TEST_PYTHON": sys.executable,
                   "INPUT_RELEASE_TAG": "must-not-affect-tag", "E87N_PREFLIGHT_EXIT": "0"}
            # Run the real config CLI; GitHub preflight is intercepted.
            for run_id in ("34737922588", "34737922589"):
                output, arguments = root / (run_id + ".output"), root / (run_id + ".args")
                env.update(GITHUB_RUN_ID=run_id, GITHUB_OUTPUT=str(output), E87N_PREFLIGHT_ARGS=str(arguments))
                result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], env=env,
                                        cwd=root, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                tag = "e87n-trixie-6.18.99-" + run_id + "-1"
                artifact = "e87n-trixie-6.18.99-candidate-" + run_id + "-1"
                self.assertEqual(output.read_text(), "release_tag=" + tag + "\nimage_artifact=" + artifact + "\n")
                self.assertEqual(arguments.read_text().splitlines(), ["scripts/ci-publish-release.py", "preflight",
                    "--repository", "fixture/repo", "--source-commit", "a" * 40, "--tag", tag])
            for index, changes in enumerate(({"E87N_PREFLIGHT_EXIT": "42"},
                                              {"GITHUB_REF": "refs/heads/feature"},
                                              {"GITHUB_EVENT_NAME": "push"})):
                output = root / f"failed-{index}.output"
                result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]],
                    env={**env, **changes, "GITHUB_OUTPUT": str(output)}, cwd=root, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists(), "A failed preflight must not emit a release tag")

    def test_release_uses_exact_current_attempt_and_verified_assets(self):
        steps = self.workflow["jobs"]["release"]["steps"]
        download = next(s for s in steps if 'gh run download' in s.get("run", ""))
        self.assertEqual(download["run"].count('gh run download "$GITHUB_RUN_ID"'), 2)
        self.assertIn('e87n-display-candidate-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}', download["run"])
        self.assertIn('--name "$IMAGE_ARTIFACT"', download["run"])
        self.assertEqual(download["env"]["IMAGE_ARTIFACT"], "${{ needs.validate.outputs.image_artifact }}")
        upload = next(s for s in self.workflow["jobs"]["image"]["steps"]
                      if s.get("with", {}).get("path") == "output/ci/artifacts/")
        self.assertEqual(upload["with"]["name"], download["env"]["IMAGE_ARTIFACT"])
        self.assertEqual(self.workflow["jobs"]["validate"]["outputs"]["image_artifact"],
                         "${{ steps.release_tag.outputs.image_artifact }}")
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
                if upload["with"]["name"] != "${{ needs.validate.outputs.image_artifact }}":
                    self.assertIn("github.run_attempt", upload["with"]["name"])
                self.assertEqual(upload["with"]["retention-days"], "14")
            collect = next(s for s in steps if "ci-collect-artifacts.py" in s.get("run", ""))
            self.assertEqual(collect["if"], "${{ always() }}")
            self.assertIn("steps.build.outcome", collect["env"]["BUILD_OUTCOME"])
            evidence = next(s for s in steps if "ci-capture-failure-evidence.sh" in s.get("run", ""))
            self.assertEqual(evidence["if"], "${{ always() }}")
            self.assertIn("steps.build.outcome", evidence["env"]["BUILD_OUTCOME"])
            log_upload = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")
                          and "failure-evidence" in s["with"].get("path", "")]
            self.assertEqual(len(log_upload), 1)

    def test_validation_failure_evidence(self):
        steps = self.workflow["jobs"]["validate"]["steps"]
        evidence = next(s for s in steps if "ci-capture-failure-evidence.sh" in s.get("run", ""))
        self.assertEqual(evidence["if"], "${{ always() }}")
        self.assertIn("job.status", evidence["env"]["BUILD_OUTCOME"])
        self.assertIn("output/ci/failure-evidence/", next(
            s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")
        )["with"]["path"])
        capture = (REPO / "scripts/ci-capture-failure-evidence.sh").read_text()
        self.assertIn("validate|image|display", capture)

    def test_failure_evidence_is_collected(self):
        collector = (REPO / "scripts/ci-collect-artifacts.py").read_text()
        self.assertIn('"failure_evidence"', collector)
        self.assertIn('"logs/failure"', collector)

    def test_factory_firmware_dependencies(self):
        install = next(s["run"] for s in self.workflow["jobs"]["validate"]["steps"]
                       if s.get("name") == "Install validation tools")
        runner = (REPO / "scripts/ci-prepare-runner.sh").read_text()
        for package in ("u-boot-tools", "e2fsprogs", "util-linux", "device-tree-compiler", "python3",
                        "initramfs-tools-core", "fdisk", "kmod", "zstd", "libcrypt1", "cpio"):
            with self.subTest(package=package):
                self.assertIn(package, install.split())
                self.assertIn(package, runner.split())
        regressions = (REPO / "scripts/ci-regressions.sh").read_text()
        for tool in ("resize2fs", "fdtput", "mkimage", "lsinitramfs"):
            self.assertIn(tool, regressions.replace(";", " ").split())

    def test_factory_compile_checks_and_regression_suites_are_mandatory(self):
        validate = (REPO / "scripts/ci-validate.sh").read_text()
        regressions = (REPO / "scripts/ci-regressions.sh").read_text()
        for name in ("scripts/build-factory-firmware.py", "scripts/verify-factory-firmware.py",
                     "scripts/prepare-factory-rootfs.py", "board-support/factory-boot/factory_boot.py",
                     "scripts/factory_firmware.py", "tests/test-factory-firmware.py", "tests/test-factory-rootfs.py"):
            self.assertIn(name, validate)
        for suite in ("factory-firmware", "factory-rootfs"):
            self.assertIn(f'run_fixture {suite} sudo -n python3 -B tests/test-{suite}.py', regressions)
        self.assertIn('run_fixture ramdiag python3 -B tests/test-ramdiag.py', regressions)
        self.assertIn('scripts/ramdiag/*.py', validate)

    def test_firmware_candidate_upload_does_not_compress_again(self):
        upload = next(s for s in self.workflow["jobs"]["image"]["steps"]
                      if s.get("uses", "").startswith("actions/upload-artifact@")
                      and s["with"]["path"] == "output/ci/artifacts/")
        self.assertEqual(upload["with"]["compression-level"], "0")


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e87n-ci-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        for name in ("ci-build.sh", "ci-build-ramdiag.sh", "ci-collect-artifacts.py",
                     "ci-prepare-runner.sh", "ci-validate.sh", "ci-simulation.py",
                     "factory_firmware.py", "build_config.py"):
            shutil.copyfile(REPO / "scripts" / name, self.root / "scripts" / name)
        shutil.copytree(REPO / "scripts/ramdiag", self.root / "scripts/ramdiag",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.put("userpatches/config/e87n-build.json", (REPO / "userpatches/config/e87n-build.json").read_text())
        self.put("testing/validate.py", (REPO / "testing/validate.py").read_text())
        self.env = {**os.environ, "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
        self.env["RUNNER_TEMP"] = str(self.root)
        for key in list(self.env):
            if key.startswith("E87N_MOCK_") or key in ("BASH_ENV", "E87N_KERNEL_VERSION", "GITHUB_ACTIONS", "E87N_RUNNER_ENVIRONMENT"):
                self.env.pop(key, None)

    def put(self, name, contents="fixture\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def run_script(self, script, *args):
        return subprocess.run(["bash", str(self.root / "scripts" / script), *args], cwd=self.root, env=self.env, text=True, capture_output=True)

    def collect(self, kind, status):
        return subprocess.run([sys.executable, str(self.root / "scripts/ci-collect-artifacts.py"), "--kind", kind, "--status", status], cwd=self.root, env=self.env, text=True, capture_output=True)

    def test_factory_compile_checks_fail_on_missing_or_invalid_python(self):
        # Real Python compiler, isolated inputs; factory suites must not execute
        # during static validation (their Linux execution belongs to regressions).
        names = ("scripts/build-factory-firmware.py", "scripts/verify-factory-firmware.py",
                 "scripts/prepare-factory-rootfs.py", "board-support/factory-boot/factory_boot.py",
                 "scripts/factory_firmware.py", "tests/test-factory-firmware.py", "tests/test-factory-rootfs.py")
        source = 'raise AssertionError("factory code must only be compiled here")\n'
        for name in names:
            self.put(name, source)
        self.put("tests/test-ramdiag.py", (REPO / "tests/test-ramdiag.py").read_text())
        for name in ("test-ci-workflow.py", "test-ci-prepare-release.py", "test-ci-publish-release.py",
                     "test-build-config.py", "test-ci-simulation.py"):
            self.put("tests/" + name, "pass\n")
        mocks = self.put("validation-mocks.sh", '''actionlint() { return 0; }
shellcheck() { return 0; }
python3() { command "$E87N_TEST_PYTHON" "$@"; }
export -f actionlint shellcheck python3
''')
        self.env.update(BASH_ENV=str(mocks), E87N_TEST_PYTHON=sys.executable)
        result = self.run_script("ci-validate.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: CI and factory firmware Python compilation", result.stdout)
        self.assertFalse(list(self.root.rglob("__pycache__")))
        for name in names:
            with self.subTest(name=name):
                path = self.root / name
                path.unlink()
                result = self.run_script("ci-validate.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FileNotFoundError", result.stderr)
                path.write_text("def broken(:\n")
                result = self.run_script("ci-validate.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SyntaxError", result.stderr)
                path.write_text(source)

    def prepare_mocks(self, result=0, image=True):
        self.put("packaging/e87n-display/VERSION", "1.2.3\n")
        self.put("build-armbian.sh", "#!/bin/bash\nprintf '%s\\n' \"$@\" > image-args\n" +
                 ("mkdir -p source/armbian-build/output/images\nprintf 'fixture image' > source/armbian-build/output/images/test.img.xz\n" if image else "") + f"exit {result}\n")
        self.put("scripts/build-display-deb.sh", "#!/bin/bash\nprintf '%s\\n' \"$@\" > display-args\nmkdir -p \"$2\"\nprintf 'fixture deb' > \"$2/e87n-display_1.2.3_all.deb\"\n" + f"exit {result}\n")
        report = fixture_report(b"fixture firmware", b"fixture deb", "a" * 40, "123", "2")
        self.put("testing/mock-runner.py", "import json, pathlib, sys\n"
                 "args = sys.argv[1:]\n"
                 "output = pathlib.Path(args[args.index('--output') + 1])\n"
                 "output.mkdir(parents=True)\n"
                 f"(output / 'result.json').write_text({json.dumps(report)!r})\n")
        self.put("testing/run-container.sh", '#!/bin/bash\nset -euo pipefail\n'
                 'printf "simulation\\n" >> build-events\n'
                 'printf "%s\\n" "$@" > simulation-args\n'
                 'python3 testing/mock-runner.py "$@"\n'
                 'exit "${E87N_MOCK_SIMULATION_EXIT:-0}"\n')
        mocks = self.put("mocks.sh", """uname() { if [[ $1 == -s ]]; then echo Linux; else echo aarch64; fi; }
sudo() {
    if [[ $* == '-n true' ]]; then return 0; fi
    if [[ $1 == -n && $2 == bash && $3 == scripts/verify-image.sh ]]; then
        printf 'image-audit\\n' >> build-events
        printf '%s\\n' "$@" > audit-args
        printf 'fixture audit result\\n'
        return "${E87N_MOCK_AUDIT_EXIT:-0}"
    fi
    if [[ $1 == -n && $2 == bash && $3 == scripts/ci-build-ramdiag.sh ]]; then
        printf 'ramdiag-build\\n' >> build-events
        mkdir -p "$7"
        printf 'fixture diagnostic\\n' > "$7/E87N-ramdiag-40000000-initrd.itb"
        printf '{}\\n' > "$7/E87N-ramdiag-40000000-initrd.itb.json"
        printf 'fixture diagnostic\\n' > "$7/E87N-ramdiag-40080000-initrd.itb"
        printf '{}\\n' > "$7/E87N-ramdiag-40080000-initrd.itb.json"
        printf 'fixture diagnostic\\n' > "$7/E87N-ramdiag-40000000-no-initrd.itb"
        printf '{}\\n' > "$7/E87N-ramdiag-40000000-no-initrd.itb.json"
        printf 'fixture manifest\\n' > "$7/MANIFEST.json"
        printf 'fixture readme\\n' > "$7/README.txt"
        return "${E87N_MOCK_RAMDIAG_EXIT:-0}"
    fi
    [[ $1 == -n && $2 == python3 ]] || return 99
    case $3 in
        scripts/build-factory-firmware.py)
            [[ $# == 8 && $4 == --image && $6 == --output && $8 == --headless ]] || return 99
            printf 'firmware-build\\n' >> build-events
            printf '%s\\n' "$@" > factory-build-args
            printf 'fixture firmware' > "$7"
            return "${E87N_MOCK_FACTORY_BUILD_EXIT:-0}"
            ;;
        scripts/verify-factory-firmware.py)
            [[ $# == 5 && -s $4 && $5 == --headless ]] || return 99
            printf 'firmware-audit\\n' >> build-events
            printf '%s\\n' "$@" > factory-audit-args
            printf '%s\\n' "${E87N_MOCK_FACTORY_AUDIT_OUTPUT:-PASS: fixture factory firmware audit}"
            return "${E87N_MOCK_FACTORY_AUDIT_EXIT:-0}"
            ;;
        *) return 99 ;;
    esac
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
        self.assertEqual(audit[:-1], ["-n", "bash", "scripts/verify-image.sh", "--release", "trixie", "--require-usb-root", "--headless", "--require-system"])
        raw = Path(audit[-1])
        self.assertEqual(raw.read_text(), "fixture raw image")
        self.assertTrue(raw.parent.name.startswith("e87n-ci-audit."))
        self.assertNotIn("output", raw.parts)
        firmware = self.root / "output/ci/firmware/test-uboot-firmware.tar"
        self.assertEqual((self.root / "factory-build-args").read_text().splitlines(),
                         ["-n", "python3", "scripts/build-factory-firmware.py", "--image", str(raw), "--output", str(firmware), "--headless"])
        self.assertEqual((self.root / "factory-audit-args").read_text().splitlines(),
                         ["-n", "python3", "scripts/verify-factory-firmware.py", str(firmware), "--headless"])
        self.assertEqual(firmware.read_text(), "fixture firmware")
        self.assertEqual((self.root / "source/armbian-build/output/images/test.img.xz").read_text(), "fixture image")
        self.assertEqual((self.root / "build-events").read_text().splitlines(),
                         ["image-audit", "ramdiag-build", "firmware-build", "firmware-audit", "simulation"])
        self.assertEqual((self.root / "display-args").read_text().splitlines(),
                         ["--output-dir", str(self.root / "output/ci/simulation-display-debs")])
        self.assertEqual((self.root / "simulation-args").read_text().splitlines(), [
            "--firmware", str(firmware), "--display-deb",
            str(self.root / "output/ci/simulation-display-debs/e87n-display_1.2.3_all.deb"),
            "--output", str(self.root / "output/ci/simulation"), "--source-commit", "a" * 40,
            "--run-id", "123", "--run-attempt", "2"])
        self.assertIn("PASS", (self.root / "output/ci/logs/factory-firmware-audit-1.log").read_text())
        self.put("source/armbian-build/output/debs/kernel.deb")
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata = json.loads((self.root / "output/ci/artifacts/build-metadata.json").read_text())
        self.assertEqual(metadata["factory_format"], FORMAT)
        self.assertEqual(metadata["factory_static_audit"], "passed")
        self.assertEqual(metadata["simulation_validation"], "passed")
        self.assertEqual(metadata["files_collected"]["images"], 1)
        self.assertEqual(metadata["files_collected"]["diagnostics"], 8)
        self.assertEqual([p.name for p in (self.root / "output/ci/artifacts/images").iterdir()], [firmware.name])

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
        self.assertEqual(metadata["factory_static_audit"], "not proven")
        self.assertFalse((self.root / "factory-build-args").exists())
        self.assertFalse(list(dest.rglob("candidate.img")))

    def test_simulation_failure_preserves_report_and_blocks_success(self):
        self.prepare_mocks()
        self.env["E87N_MOCK_SIMULATION_EXIT"] = "45"
        result = self.run_script("ci-build.sh", "image")
        self.assertEqual(result.returncode, 45, result.stdout + result.stderr)
        self.assertEqual(self.collect("image", "failure").returncode, 0)
        dest = self.root / "output/ci/artifacts"
        self.assertTrue((dest / "validation/qemu/result.json").is_file())
        metadata = json.loads((dest / "build-metadata.json").read_text())
        self.assertEqual(metadata["simulation_validation"], "not proven")

    def test_missing_simulation_report_blocks_successful_collection(self):
        self.prepare_mocks()
        self.assertEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        (self.root / "output/ci/simulation/result.json").unlink()
        self.put("source/armbian-build/output/debs/kernel.deb")
        result = self.collect("image", "success")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required same-build simulation evidence", result.stdout)

    def test_simulation_from_another_attempt_is_rejected(self):
        self.prepare_mocks()
        self.assertEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        path = self.root / "output/ci/simulation/result.json"
        report = json.loads(path.read_text())
        report["binding"]["run_attempt"] = "1"
        path.write_text(json.dumps(report))
        self.put("source/armbian-build/output/debs/kernel.deb")
        result = self.collect("image", "success")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binding mismatch", result.stdout)

    def test_factory_packager_failure_stops_verification_and_preserves_evidence(self):
        self.prepare_mocks()
        self.env["E87N_MOCK_FACTORY_BUILD_EXIT"] = "43"
        result = self.run_script("ci-build.sh", "image")
        self.assertEqual(result.returncode, 43, result.stdout + result.stderr)
        self.assertEqual((self.root / "output/ci/logs/image.exit-code").read_text(), "43\n")
        self.assertFalse((self.root / "factory-audit-args").exists())
        self.assertEqual(self.collect("image", "failure").returncode, 0)
        dest = self.root / "output/ci/artifacts"
        metadata = json.loads((dest / "build-metadata.json").read_text())
        self.assertEqual(metadata["factory_static_audit"], "not proven")
        self.assertTrue((dest / "logs/ci/image-audit-1.log").is_file())

    def test_factory_verifier_failure_marks_build_failed_even_with_pass_output(self):
        self.prepare_mocks()
        self.env["E87N_MOCK_FACTORY_AUDIT_EXIT"] = "44"
        result = self.run_script("ci-build.sh", "image")
        self.assertEqual(result.returncode, 44, result.stdout + result.stderr)
        self.assertEqual((self.root / "output/ci/logs/image.exit-code").read_text(), "44\n")
        self.assertEqual(self.collect("image", "failure").returncode, 0)
        dest = self.root / "output/ci/artifacts"
        metadata = json.loads((dest / "build-metadata.json").read_text())
        self.assertEqual(metadata["factory_static_audit"], "not proven")
        self.assertIn("PASS", (dest / "logs/ci/factory-firmware-audit-1.log").read_text())

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

    def test_multiple_images_stop_before_audit_and_packaging(self):
        self.prepare_mocks()
        builder = self.root / "build-armbian.sh"
        builder.write_text(builder.read_text().replace("exit 0\n",
            "cp source/armbian-build/output/images/test.img.xz source/armbian-build/output/images/second.img.xz\nexit 0\n"))
        result = self.run_script("ci-build.sh", "image")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one", result.stdout + result.stderr)
        self.assertFalse((self.root / "audit-args").exists())
        self.assertFalse((self.root / "factory-build-args").exists())

    def test_arbitrary_kernel_is_rejected_before_build(self):
        self.prepare_mocks()
        self.env["E87N_KERNEL_VERSION"] = "6.19; touch should-not-exist"
        self.assertEqual(self.run_script("ci-build.sh", "image").returncode, 2)
        self.assertFalse((self.root / "image-args").exists())
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_shell_and_collector_follow_changed_reviewed_config(self):
        self.prepare_mocks()
        config = {**BUILD, "kernel_version": "6.18.99",
                  "kernel_release": "6.18.99-current-" + BUILD["linux_family"],
                  "kernel_commit": "b" * 40, "armbian_commit": "c" * 40}
        self.put("userpatches/config/e87n-build.json", json.dumps(config))
        self.env["E87N_KERNEL_VERSION"] = config["kernel_version"]
        result = self.run_script("ci-build.sh", "display")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.collect("display", "success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata = json.loads((self.root / "output/ci/artifacts/build-metadata.json").read_text())
        self.assertEqual(metadata["target"]["kernel"], config["kernel_version"])
        for key in ("kernel_commit", "kernel_release", "armbian_commit"):
            self.assertEqual(metadata[key], config[key])

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

    def test_stale_factory_output_is_preserved_and_rejected(self):
        self.prepare_mocks()
        sentinel = self.put("output/ci/firmware/old-uboot-firmware.tar", "keep me")
        self.assertNotEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep me")
        self.assertFalse((self.root / "image-args").exists())

    def test_stale_ramdiag_output_is_preserved_and_rejected(self):
        self.prepare_mocks()
        sentinel = self.put("output/ci/ramdiag/old.itb", "keep me")
        self.assertNotEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep me")
        self.assertFalse((self.root / "image-args").exists())

    def test_stale_simulation_package_is_preserved_and_rejected(self):
        self.prepare_mocks()
        sentinel = self.put("output/ci/simulation-display-debs/old.deb", "keep me")
        self.assertNotEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep me")
        self.assertFalse((self.root / "image-args").exists())

    def test_successful_collection_requires_exact_ramdiag_matrix(self):
        self.prepare_mocks()
        self.assertEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        (self.root / "output/ci/ramdiag/README.txt").unlink()
        self.put("source/armbian-build/output/debs/kernel.deb")
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertIn("incomplete RAM diagnostic matrix", result.stdout)

    def test_successful_collection_rejects_empty_ramdiag_file(self):
        self.prepare_mocks()
        self.assertEqual(self.run_script("ci-build.sh", "image").returncode, 0)
        (self.root / "output/ci/ramdiag/MANIFEST.json").write_bytes(b"")
        self.put("source/armbian-build/output/debs/kernel.deb")
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertIn("empty file", result.stdout)

    def test_failed_image_collection_and_checksums(self):
        image = self.put("output/ci/firmware/candidate-uboot-firmware.tar", "partial firmware")
        for name in ("candidate.img", "candidate.img.xz", "candidate.img.gz", "candidate.img.zst", "wrong.tar"):
            self.put("source/armbian-build/output/images/" + name, "never upload")
        for name in ("candidate.img", "candidate.img.xz", "candidate.tar.xz"):
            self.put("output/ci/firmware/" + name, "never upload")
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
        self.assertEqual(metadata["source_commit"], "a" * 40)
        self.assertEqual(metadata["target"], TARGET)
        for key in ("kernel_source", "kernel_release", "kernel_commit", "armbian_commit"):
            self.assertEqual(metadata[key], BUILD[key])
        self.assertEqual(metadata["simulation_validation"], "not proven")
        self.assertEqual(metadata["factory_format"], FORMAT)
        self.assertEqual(metadata["factory_static_audit"], "not proven")
        self.assertEqual(image.stat().st_ino, (dest / "images/candidate-uboot-firmware.tar").stat().st_ino)
        for line in (dest / "SHA256SUMS").read_text().splitlines():
            digest, relative = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((dest / relative).read_bytes()).hexdigest())
        self.assertNotIn("never upload", "".join(p.read_text() for p in dest.rglob("*") if p.is_file()))

    def test_missing_payload_fails_successful_build(self):
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.root / "output/ci/artifacts/build-metadata.json").is_file())

    def test_multiple_factory_tars_fail_successful_collection(self):
        self.put("output/ci/firmware/first-uboot-firmware.tar")
        self.put("output/ci/firmware/nested/second-uboot-firmware.tar")
        self.put("source/armbian-build/output/debs/kernel.deb")
        self.put("output/ci/logs/factory-firmware-audit-1.log", "PASS\n")
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly one", result.stdout)

    def test_missing_factory_audit_log_fails_successful_collection(self):
        self.put("output/ci/firmware/candidate-uboot-firmware.tar")
        self.put("source/armbian-build/output/debs/kernel.deb")
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertIn("required factory firmware audit log", result.stdout)
        metadata = json.loads((self.root / "output/ci/artifacts/build-metadata.json").read_text())
        self.assertEqual(metadata["factory_static_audit"], "not proven")

    def test_factory_audit_without_pass_fails_successful_collection(self):
        self.put("output/ci/firmware/candidate-uboot-firmware.tar")
        self.put("source/armbian-build/output/debs/kernel.deb")
        self.put("output/ci/logs/factory-firmware-audit-1.log", "FAIL: malformed firmware\n")
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing PASS", result.stdout)

    def test_legacy_image_alone_cannot_satisfy_successful_collection(self):
        self.put("source/armbian-build/output/images/candidate.img.xz")
        self.put("source/armbian-build/output/debs/kernel.deb")
        self.put("output/ci/logs/factory-firmware-audit-1.log", "PASS\n")
        result = self.collect("image", "success")
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly one", result.stdout)
        self.assertFalse((self.root / "output/ci/artifacts/images").exists())

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
