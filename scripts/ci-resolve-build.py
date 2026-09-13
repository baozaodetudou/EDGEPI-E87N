#!/usr/bin/env python3
"""Resolve an existing successful main build using read-only, fixed GitHub endpoints."""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import sys

SPEC = importlib.util.spec_from_file_location(
    "e87n_publisher", Path(__file__).with_name("ci-publish-release.py"))
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
require = publisher.require
WORKFLOW = ".github/workflows/build-e87n.yml"
LATEST = "actions/workflows/build-e87n.yml/runs?branch=main&status=success&per_page=1"
MAX_JOB_PAGES = 10


def positive(value):
    return type(value) is int and 0 < value <= 9223372036854775807


def resolve(repository, build_run_id=""):
    require(isinstance(repository, str) and len(repository.split("/")) == 2 and
            all(publisher.safe(part) for part in repository.split("/")), "Invalid repository")
    require(isinstance(build_run_id, str) and (build_run_id == "" or
            (re.fullmatch(r"[1-9][0-9]{0,18}", build_run_id) and positive(int(build_run_id)))),
            "Invalid build run ID")
    publisher.gh("auth", "status", "--hostname", "github.com")
    if not build_run_id:
        latest = publisher.api(repository, LATEST)
        require(isinstance(latest, dict) and isinstance(latest.get("workflow_runs"), list) and
                len(latest["workflow_runs"]) == 1 and isinstance(latest["workflow_runs"][0], dict),
                "No unique latest successful main build")
        run_id = latest["workflow_runs"][0].get("id")
        require(positive(run_id), "Invalid latest build run ID")
        build_run_id = str(run_id)
    run = publisher.api(repository, f"actions/runs/{build_run_id}")
    require(isinstance(run, dict) and positive(run.get("id")) and str(run["id"]) == build_run_id,
            "Build run identity mismatch")
    require(run.get("status") == "completed" and run.get("conclusion") == "success" and
            run.get("head_branch") == "main", "Build must be completed and successful on main")
    require(run.get("path") == WORKFLOW and positive(run.get("workflow_id")), "Wrong build workflow")
    require(isinstance(run.get("head_sha"), str) and re.fullmatch(publisher.SHA, run["head_sha"]),
            "Invalid source commit")
    require(positive(run.get("run_attempt")), "Invalid build run attempt")
    for key in ("repository", "head_repository"):
        repo = run.get(key)
        require(isinstance(repo, dict) and isinstance(repo.get("full_name"), str) and
                repo["full_name"].casefold() == repository.casefold() and repo.get("fork") is False and
                positive(repo.get("id")), "Build repository mismatch or fork")
    require(run["repository"]["id"] == run["head_repository"]["id"], "Source repository ID mismatch")
    workflow = publisher.api(repository, "actions/workflows/build-e87n.yml")
    require(isinstance(workflow, dict) and positive(workflow.get("id")) and
            workflow["id"] == run["workflow_id"] and workflow.get("path") == WORKFLOW,
            "Build workflow identity mismatch")
    expected = {"validate", "image", "display"}
    found, seen, total = set(), set(), None
    for page in range(1, MAX_JOB_PAGES + 1):
        jobs = publisher.api(repository,
            f"actions/runs/{build_run_id}/attempts/{run['run_attempt']}/jobs?per_page=100&page={page}")
        require(isinstance(jobs, dict) and type(jobs.get("total_count")) is int and
                0 <= jobs["total_count"] <= MAX_JOB_PAGES * 100 and isinstance(jobs.get("jobs"), list),
                "Invalid or oversized build jobs response")
        if total is None:
            total = jobs["total_count"]
        require(jobs["total_count"] == total and
                len(jobs["jobs"]) == min(100, total - len(seen)), "Incomplete or changing build jobs")
        for job in jobs["jobs"]:
            require(isinstance(job, dict) and positive(job.get("id")) and job["id"] not in seen and
                    positive(job.get("run_id")) and job["run_id"] == run["id"] and
                    positive(job.get("run_attempt")) and job["run_attempt"] == run["run_attempt"] and
                    job.get("head_sha") == run["head_sha"] and isinstance(job.get("name"), str),
                    "Build job identity or source mismatch")
            seen.add(job["id"])
            if job["name"] in expected:
                require(job["name"] not in found and job.get("status") == "completed" and
                        job.get("conclusion") == "success", "Required build job is not uniquely successful")
                found.add(job["name"])
        if len(seen) == total:
            break
    require(len(seen) == total and found == expected, "Missing successful validate/image/display jobs")
    return {"source_commit": run["head_sha"], "source_run_id": build_run_id,
            "source_run_attempt": str(run["run_attempt"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--build-run-id", default="")
    args = parser.parse_args(argv)
    try:
        outputs = resolve(args.repository, args.build_run_id)
        # Only constant keys and already validated hexadecimal/decimal values reach the env file.
        text = "".join(f"{key}={value}\n" for key, value in outputs.items())
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as target:
                target.write(text)
        print(text, end="")
    except (OSError, ValueError) as error:
        print("FAIL: " + (str(error) if isinstance(error, ValueError) else
                         "Local I/O or gh execution failed"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
