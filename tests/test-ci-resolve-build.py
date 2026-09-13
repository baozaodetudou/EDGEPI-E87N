#!/usr/bin/env python3
"""Read-only resolver and manual workflow contracts; all subprocesses are intercepted."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("resolver", ROOT / "scripts/ci-resolve-build.py")
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)
REPOSITORY, RUN, ATTEMPT, SHA = "owner/repo", 34737922588, 2, "a1" * 20
SECRET = "ghp_fixture_do_not_log"
RUN_ENDPOINT = f"actions/runs/{RUN}"
WORKFLOW_ENDPOINT = "actions/workflows/build-e87n.yml"
JOBS_ENDPOINT = f"actions/runs/{RUN}/attempts/{ATTEMPT}/jobs?per_page=100&page=1"


def response(data, code=200):
    return subprocess.CompletedProcess([], int(code >= 400),
        f"HTTP/2.0 {code} Test\nContent-Type: application/json\n\n" + json.dumps(data), SECRET)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="e87n-resolve-test-")
        self.addCleanup(temp.cleanup)
        self.output_file = Path(temp.name) / "output"
        self.output_file.write_text("sentinel=preserve\n")
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, GITHUB_OUTPUT=str(self.output_file), GH_DEBUG="api",
                        GH_HOST="untrusted.example", GH_TOKEN=SECRET).start()
        mock.patch.object(resolver.publisher.subprocess, "run", side_effect=self.gh).start()
        self.reset()

    def reset(self):
        repo = {"id": 123, "full_name": REPOSITORY, "fork": False}
        self.run = {"id": RUN, "workflow_id": 456, "run_attempt": ATTEMPT,
                    "head_sha": SHA, "head_branch": "main", "path": resolver.WORKFLOW,
                    "status": "completed", "conclusion": "success", "repository": repo,
                    "head_repository": copy.deepcopy(repo)}
        self.jobs = [{"id": n, "name": name, "run_id": RUN, "run_attempt": ATTEMPT,
                      "head_sha": SHA, "status": "completed", "conclusion": "success"}
                     for n, name in enumerate(("validate", "image", "display"), 1)]
        self.workflow = {"id": 456, "path": resolver.WORKFLOW}
        self.latest = {"workflow_runs": [{"id": RUN}]}
        self.calls, self.overrides, self.auth_failed = [], {}, False

    def gh(self, command, **kwargs):
        self.assertEqual(command[0], "gh")
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 120)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["env"]["GH_HOST"], "github.com")
        self.assertEqual(kwargs["env"]["GH_PROMPT_DISABLED"], "1")
        self.assertNotIn("GH_DEBUG", kwargs["env"])
        self.calls.append(command)
        if command[1] == "auth":
            self.assertEqual(command, ["gh", "auth", "status", "--hostname", "github.com"])
            return subprocess.CompletedProcess(command, int(self.auth_failed), SECRET, SECRET)
        self.assertEqual(self.calls[0][1:3], ["auth", "status"])
        self.assertEqual(command[1], "api")
        self.assertEqual(command[command.index("--method") + 1], "GET")
        self.assertEqual(command[command.index("--hostname") + 1], "github.com")
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertTrue(command[-1].startswith(f"repos/{REPOSITORY}/"))
        endpoint = command[-1].removeprefix(f"repos/{REPOSITORY}/")
        if endpoint in self.overrides:
            return self.overrides[endpoint]
        data = {RUN_ENDPOINT: self.run, WORKFLOW_ENDPOINT: self.workflow,
                JOBS_ENDPOINT: {"total_count": len(self.jobs), "jobs": self.jobs},
                resolver.LATEST: self.latest}
        self.assertIn(endpoint, data, "No response-supplied or unexpected URL may be requested")
        return response(data[endpoint])

    def cli(self, run_id=str(RUN), repository=REPOSITORY):
        before = self.output_file.read_text()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = resolver.main([f"--repository={repository}", f"--build-run-id={run_id}"])
        self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
        if result:
            self.assertEqual(self.output_file.read_text(), before, "Failure must emit no env outputs")
            self.assertEqual(out.getvalue(), "")
        return result

    def test_explicit_and_latest_success_emit_only_validated_outputs(self):
        expected = f"source_commit={SHA}\nsource_run_id={RUN}\nsource_run_attempt={ATTEMPT}\n"
        for run_id in (str(RUN), ""):
            with self.subTest(run_id=run_id):
                self.reset()
                self.assertEqual(self.cli(run_id), 0)
                self.assertTrue(self.output_file.read_text().endswith(expected))
                endpoints = [c[-1].removeprefix(f"repos/{REPOSITORY}/") for c in self.calls[1:]]
                self.assertEqual(endpoints, ([resolver.LATEST] if not run_id else []) +
                                 [RUN_ENDPOINT, WORKFLOW_ENDPOINT, JOBS_ENDPOINT])

    def test_invalid_inputs_never_authenticate(self):
        for value in ("0", "-1", "01", "1.0", " 123", "1\n", "1\nx=y", "$(id)",
                      "`id`", "1/attempts/2", "９", "9" * 20, "9223372036854775808"):
            with self.subTest(run_id=value):
                self.assertNotEqual(self.cli(value), 0)
                self.assertEqual(self.calls, [])
        for value in ("", "owner", "../repo", "owner/repo/x", "owner/repo?x=1", "owner/repo\nx=y"):
            with self.subTest(repository=value):
                self.assertNotEqual(self.cli(repository=value), 0)
                self.assertEqual(self.calls, [])

    def test_bad_run_metadata_and_unsafe_outputs(self):
        cases = {"id": [RUN + 1, str(RUN), True, None], "status": ["queued", "in_progress", None],
                 "conclusion": ["failure", "cancelled", "skipped", None],
                 "head_branch": ["feature", "main\nx=y", None],
                 "path": [".github/workflows/other.yml", resolver.WORKFLOW + "@main", None],
                 "head_sha": ["a" * 39, "g" * 40, SHA + "\nx=y", None],
                 "run_attempt": [0, -1, True, "2", "2\nx=y", None],
                 "workflow_id": [0, True, "456", 789, None]}
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.reset()
                    self.run[key] = value
                    self.assertNotEqual(self.cli(), 0)

    def test_forks_repository_mismatch_and_missing_identity(self):
        for key in ("repository", "head_repository"):
            for field, value in (("full_name", "fork/repo"), ("full_name", None),
                                 ("fork", True), ("fork", None), ("id", 999), ("id", True)):
                with self.subTest(key=key, field=field, value=value):
                    self.reset()
                    self.run[key][field] = value
                    self.assertNotEqual(self.cli(), 0)

    def test_wrong_workflow_identity(self):
        for value in ({}, {"id": 457, "path": resolver.WORKFLOW},
                      {"id": 456, "path": ".github/workflows/other.yml"}):
            self.reset()
            self.workflow = value
            self.assertNotEqual(self.cli(), 0)

    def test_latest_missing_malformed_and_selected_run_mismatch(self):
        for value in ({}, {"workflow_runs": []}, {"workflow_runs": [{"id": RUN}] * 2},
                      {"workflow_runs": [{"id": True}]}, {"workflow_runs": [{"id": "1\nx=y"}]}):
            self.reset()
            self.latest = value
            self.assertNotEqual(self.cli(""), 0)
        self.reset()
        self.run["id"] = RUN + 1
        self.assertNotEqual(self.cli(""), 0)

    def test_each_required_job_must_succeed_for_exact_run_attempt_and_sha(self):
        for index in range(3):
            for key, value in (("status", "in_progress"), ("conclusion", "failure"),
                               ("conclusion", "cancelled"), ("conclusion", "skipped"),
                               ("run_id", RUN + 1), ("run_attempt", 1), ("run_attempt", True),
                               ("head_sha", "b" * 40), ("name", "other"), ("id", None)):
                with self.subTest(index=index, key=key, value=value):
                    self.reset()
                    self.jobs[index][key] = value
                    self.assertNotEqual(self.cli(), 0)
        self.reset()
        self.jobs.pop()
        self.assertNotEqual(self.cli(), 0)
        self.reset()
        self.jobs.append(dict(self.jobs[0], id=4))
        self.assertNotEqual(self.cli(), 0)

    def test_bounded_jobs_pagination(self):
        extras = [dict(self.jobs[0], id=n, name=f"extra-{n}") for n in range(4, 102)]
        jobs = self.jobs + extras
        self.overrides[JOBS_ENDPOINT] = response({"total_count": 101, "jobs": jobs[:100]})
        self.overrides[JOBS_ENDPOINT.replace("&page=1", "&page=2")] = response(
            {"total_count": 101, "jobs": jobs[100:]})
        self.assertEqual(self.cli(), 0)
        for data in ({"total_count": 1001, "jobs": []}, {"total_count": 4, "jobs": self.jobs},
                     {"total_count": True, "jobs": []}, {"total_count": 0, "jobs": []}):
            self.reset()
            self.overrides[JOBS_ENDPOINT] = response(data)
            self.assertNotEqual(self.cli(), 0)

    def test_api_errors_fail_closed_at_every_endpoint(self):
        for endpoint in (resolver.LATEST, RUN_ENDPOINT, WORKFLOW_ENDPOINT, JOBS_ENDPOINT):
            for code in (400, 401, 403, 404, 429, 500, 502, 503):
                with self.subTest(endpoint=endpoint, code=code):
                    self.reset()
                    self.overrides[endpoint] = response({"message": SECRET}, code)
                    self.assertNotEqual(self.cli(""), 0)

    def test_malformed_api_and_untrusted_response_urls(self):
        for body in ("not JSON", "[]", "null", "true"):
            self.overrides[RUN_ENDPOINT] = subprocess.CompletedProcess(
                [], 0, "HTTP/2 200 OK\n\n" + body, SECRET)
            self.assertNotEqual(self.cli(), 0)
        self.reset()
        self.run.update(url="https://evil.example/", jobs_url="https://evil.example/jobs",
                        workflow_url="https://evil.example/workflow")
        self.assertEqual(self.cli(), 0)

    def test_auth_failure_missing_gh_and_timeout_do_not_emit_outputs(self):
        self.auth_failed = True
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual(len(self.calls), 1)
        for error in (FileNotFoundError(SECRET), subprocess.TimeoutExpired("gh", 120, output=SECRET)):
            with mock.patch.object(resolver.publisher.subprocess, "run", side_effect=error):
                self.assertNotEqual(self.cli(), 0)


class WorkflowTests(unittest.TestCase):
    def test_manual_main_only_and_pinned_actions(self):
        workflow = yaml.load((ROOT / ".github/workflows/publish-e87n.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["name"], "Publish existing E87N build")
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"build_run_id", "release_tag"})
        for item in inputs.values():
            self.assertEqual(item["type"], "string")
            self.assertEqual(item["required"], "false")
        self.assertEqual(set(workflow["jobs"]), {"publish"})
        job = workflow["jobs"]["publish"]
        self.assertIn("github.event_name == 'workflow_dispatch'", job["if"])
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(job["timeout-minutes"], "30")
        self.assertEqual(job["permissions"], {"contents": "write", "actions": "read"})
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertNotIn("GH_TOKEN", workflow.get("env", {}))
        self.assertNotIn("GH_TOKEN", job.get("env", {}))
        steps = job["steps"]
        actions = [s for s in steps if "uses" in s]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["uses"], "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
        self.assertEqual(actions[0]["with"]["persist-credentials"], "false")
        self.assertNotIn("ref", actions[0]["with"])
        for step in steps:
            script = step.get("run", "")
            self.assertNotIn("${{", script, "Dynamic values must be passed through env")
            self.assertNotIn("GITHUB_SHA", script)
            self.assertNotIn("ci-build.sh", script)
            if "--source-commit" in script:
                self.assertIn('--source-commit "$SOURCE_COMMIT"', script)
                self.assertEqual(step["env"]["SOURCE_COMMIT"], "${{ steps.source.outputs.source_commit }}")
        scripts = [s.get("run", "") for s in steps]
        download = next(s for s in scripts if "gh run download" in s)
        self.assertEqual(download.count('gh run download "$SOURCE_RUN_ID" --repo "$GITHUB_REPOSITORY"'), 2)
        for name in ("e87n-trixie-6.18.51-candidate", "e87n-display-candidate"):
            self.assertIn(f'--name "{name}-${{SOURCE_RUN_ID}}-${{SOURCE_RUN_ATTEMPT}}"', download)
        preflight = next(i for i, s in enumerate(scripts) if "ci-publish-release.py preflight" in s)
        self.assertLess(preflight, scripts.index(download))
        self.assertIn("e87n-trixie-6.18.51-${SOURCE_RUN_ID}", scripts[preflight])
        self.assertLess(scripts[preflight].index("ci-publish-release.py preflight"),
                        scripts[preflight].index("printf 'release_tag="))
        prepare = next(s for s in scripts if "ci-prepare-release.py" in s)
        self.assertIn('--run-id "$SOURCE_RUN_ID" --run-attempt "$SOURCE_RUN_ATTEMPT"', prepare)


if __name__ == "__main__":
    unittest.main()
