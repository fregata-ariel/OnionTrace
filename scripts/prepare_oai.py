"""Prepare a new, isolated OAI analysis checkout; never reuse a build directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from onion.build_inputs import LOCK, git_provenance, query


def prepare(workspace, *, fetch_only=False, dependency_lock=None):
    lock = json.loads(LOCK.read_text())
    workspace = Path(workspace).resolve()
    if not fetch_only:
        # Readable before build; newly downloaded CPM paths may not exist yet.
        json.loads(Path(dependency_lock).read_text())
    # Fail before touching an existing directory, including an operational build.
    workspace.mkdir(parents=True, exist_ok=False)
    source = workspace / "source"
    build = source / "cmake_targets/oniontrace-analysis/build"
    subprocess.run(["git", "clone", "--depth", "1", "--branch", lock["tag"],
                    lock["origin"], str(source)], check=True)
    provenance = git_provenance(source, lock)
    arguments = list(lock["build_oai_arguments"])
    # Keep helper's eval-based --cmake-opt value free of workspace interpolation.
    # The dedicated CPM cache is supplied through the supported environment var.
    command = ["bash", "./build_oai", *arguments, "-d", "oniontrace-analysis"]
    environment = dict(os.environ, CPM_SOURCE_CACHE=str(workspace / "cpm-cache"))
    plan = {"source": provenance, "build": str(build), "build_oai_command": command,
            "cwd": str(source / "cmake_targets"), "fetch_only": fetch_only,
            "environment": {"CPM_SOURCE_CACHE": environment["CPM_SOURCE_CACHE"]}}
    (workspace / "preparation.json").write_text(json.dumps(plan, indent=2) + "\n")
    if fetch_only:
        return
    query(build)
    with (workspace / "build.log").open("w") as log:
        subprocess.run(command, cwd=source / "cmake_targets", stdout=log,
                       stderr=subprocess.STDOUT, env=environment, check=True)
    subprocess.run([sys.executable, "-m", "onion.build_inputs", "capture", "--build", str(build),
                    "--target", "nr-softmodem", "--target", "nr-uesoftmodem",
                    "--dependency-lock", str(Path(dependency_lock).resolve()),
                    "--out", str(workspace / "manifest")], cwd=ROOT, env=environment, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--fetch-only", action="store_true")
    parser.add_argument("--dependency-lock", type=Path)
    options = parser.parse_args()
    if not options.fetch_only and options.dependency_lock is None:
        parser.error("build/capture requires --dependency-lock; see docs/oai-build-inputs.md")
    prepare(options.workspace, fetch_only=options.fetch_only, dependency_lock=options.dependency_lock)


if __name__ == "__main__":
    main()
