"""Capture CMake/compile DB provenance for later extraction (no CFG claims).

Commands from a compile DB are trusted build inputs and are executed without a
shell. A successful entry means syntax/dependency validation, not extraction.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/oai-2026.w07.json"
QUERIES = ("codemodel-v2", "cache-v2", "cmakeFiles-v1", "toolchains-v1")
ENVIRONMENT = ("PATH", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
               "SDKROOT", "COMPILER_PATH", "GCC_EXEC_PREFIX", "LIBRARY_PATH", "SOURCE_DATE_EPOCH",
               "CPM_SOURCE_CACHE", "SIMDE_VERSION", "LD_LIBRARY_PATH", "LANG", "LC_ALL")


def digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=True).encode()).hexdigest()


def file_record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": "sha256:" + sha256(path.read_bytes()).hexdigest()}


def run(arguments, cwd=None):
    return subprocess.run(arguments, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, timeout=180)


def query(build):
    """Request the File API before configure; never overwrite CMakeCache.txt."""
    directory = Path(build) / ".cmake/api/v1/query/client-oniontrace"
    directory.mkdir(parents=True, exist_ok=True)
    for name in QUERIES:
        (directory / name).touch()


def cmake_model(build, configuration=None):
    reply = build / ".cmake/api/v1/reply"
    indices = sorted(reply.glob("index-*.json"))
    if not indices:
        raise ValueError("CMake File API reply missing; run query before configure")
    index_path = indices[-1]
    index = json.loads(index_path.read_text())
    responses = index["reply"]["client-oniontrace"]
    objects = {}
    records = [file_record(index_path)]
    for name in QUERIES:
        response = responses.get(name, {})
        if "jsonFile" not in response:
            raise ValueError(f"missing File API response: {name}: {response}")
        path = reply / response["jsonFile"]
        records.append(file_record(path))
        objects[name] = json.loads(path.read_text())
    model = objects["codemodel-v2"]
    if Path(model["paths"]["build"]).resolve() != build:
        raise ValueError("File API build path mismatch")
    configurations = model["configurations"]
    if len(configurations) != 1:
        raise ValueError("multi-config generators unsupported; use a separate single-config build")
    matches = [c for c in configurations if configuration is None or c["name"] == configuration]
    if len(matches) != 1:
        raise ValueError("select exactly one CMake configuration")
    targets = {}
    for ref in matches[0]["targets"]:
        path = reply / ref["jsonFile"]
        records.append(file_record(path))
        target = json.loads(path.read_text())
        target["json_file"] = str(path)
        targets[target["id"]] = target
    # CMake configure inputs include toolchain and package discovery files.
    for entry in objects["cmakeFiles-v1"]["inputs"]:
        path = Path(entry["path"])
        if not path.is_absolute():
            path = Path(model["paths"]["source"]) / path
        if not path.is_file():
            raise ValueError(f"CMake input missing: {path}")
        records.append(file_record(path))
    records += [file_record(build / "CMakeCache.txt"), file_record(build / "compile_commands.json")]
    return index, model, matches[0], targets, records, objects["cache-v2"]


def selection(targets, names):
    roots = {}
    for name in names:
        matches = [t["id"] for t in targets.values() if t["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"target not found or ambiguous: {name}")
        roots[name] = matches[0]
    membership = {}
    for name, root in roots.items():
        pending, seen = [root], set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            if key not in targets:
                raise ValueError(f"unknown CMake dependency: {key}")
            seen.add(key)
            membership.setdefault(key, []).append(name)
            pending.extend(dep["id"] for dep in targets[key].get("dependencies", []))
    return membership


def command(entry):
    args = entry.get("arguments")
    if args is None:
        args = shlex.split(entry["command"])
    if not isinstance(args, list) or not args or any(not isinstance(a, str) for a in args):
        raise ValueError("invalid compile command arguments")
    return args


def absolute(path, directory):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (directory / path).resolve()


def output_path(entry, args, directory):
    if "output" in entry:
        return absolute(entry["output"], directory)
    positions = [i for i, arg in enumerate(args) if arg == "-o"]
    if len(positions) != 1 or positions[0] + 1 == len(args):
        raise ValueError("compile DB entry needs one explicit -o/output")
    return absolute(args[positions[0] + 1], directory)


def owner_for(entry, args, directory, source, model, build, targets):
    output = output_path(entry, args, directory)
    owners = []
    for target in targets.values():
        # File API is the authority for both target build directory and source
        # membership. The object output disambiguates shared sources/variants.
        prefix = build / target["paths"]["build"] / "CMakeFiles" / (target["name"] + ".dir")
        if not output.is_relative_to(prefix.resolve()):
            continue
        matching = [s for s in target.get("sources", []) if "compileGroupIndex" in s
                    and absolute(s["path"], Path(model["paths"]["source"])) == source]
        if len(matching) == 1:
            owners.append((target, matching[0]))
    if len(owners) != 1:
        raise ValueError("compile DB output/source cannot be matched to one CMake target")
    return owners[0]


def executable_path(executable, directory):
    path = shutil.which(executable) if "/" not in executable else str(absolute(executable, directory))
    if not path:
        raise ValueError(f"compiler not found: {executable}")
    return Path(path).resolve()


def compiler_record(executable, directory):
    path = str(executable_path(executable, directory))
    version = run([path, "--version"], directory).stdout
    return {**file_record(path), "version": version}


def validation_args(args):
    # Only remove compile-output/dependency-output switches. All semantic flags
    # remain, including unknown ones, so the compiler must diagnose them.
    result, i = [], 1
    while i < len(args):
        arg = args[i]
        if arg.startswith("@") or arg in ("-Xclang", "-load", "-fplugin") or arg.startswith("-fplugin="):
            raise ValueError(f"unsupported response/plugin argument: {arg}")
        if arg in ("-o", "-MF", "-MT", "-MQ", "-MJ"):
            if i + 1 == len(args):
                raise ValueError(f"missing argument for {arg}")
            i += 2
            continue
        if arg not in ("-c", "-MD", "-MMD", "-MP"):
            result.append(arg)
        i += 1
    return result


def validate_entry(args, directory, compiler, source):
    flags = validation_args(args)
    # The generic profile relaxes OAI's version/repository checks, not driver
    # identification. Clang's -MJ records the actual primary input; a depfile
    # alone cannot distinguish a primary source from a forced include.
    if not re.search(r"clang version \d+\.", compiler["version"]):
        raise ValueError("unsupported direct compiler: primary-input validation requires Clang")
    flags += ["-Werror=unknown-warning-option", "-Werror=unused-command-line-argument",
              "-Werror=ignored-optimization-argument"]
    with tempfile.TemporaryDirectory(prefix="onion-deps-") as temporary:
        depfile = Path(temporary) / "inputs.d"
        compilation_file = Path(temporary) / "compilations.json"
        invocation = [compiler["path"], *flags, "-fsyntax-only", "-MD", "-MF", str(depfile),
                      "-MT", "onion", "-MJ", str(compilation_file)]
        completed = run(invocation, directory)
        if not compilation_file.is_file():
            raise ValueError("compiler primary input cannot be verified: missing -MJ output")
        fragments = compilation_file.read_text().strip().rstrip(",")
        compilations = json.loads("[" + fragments + "]")
        if len(compilations) != 1:
            raise ValueError("expected exactly one compiler primary input")
        compilation = compilations[0]
        if (not isinstance(compilation, dict) or
                not isinstance(compilation.get("directory"), str) or not compilation["directory"] or
                not isinstance(compilation.get("file"), str) or not compilation["file"] or
                not isinstance(compilation.get("arguments"), list) or not compilation["arguments"] or
                any(not isinstance(arg, str) for arg in compilation["arguments"]) or
                not compilation["arguments"][0]):
            raise ValueError("compiler primary input cannot be verified: malformed -MJ record")
        actual_directory = Path(compilation["directory"]).resolve()
        actual_source = absolute(compilation["file"], actual_directory)
        if actual_directory != directory or actual_source != source:
            raise ValueError("compiler primary input does not match compile DB file/directory")
        actual_compiler = executable_path(compilation["arguments"][0], actual_directory)
        if actual_compiler != Path(compiler["path"]):
            raise ValueError("compiler launcher unsupported: emitted driver differs from recorded compiler")
        if not depfile.is_file():
            raise ValueError("compiler did not emit dependency file")
        contents = depfile.read_text().replace("\\\n", "")
        if not contents.startswith("onion:"):
            raise ValueError("unsupported dependency file format")
        dependencies = sorted({str(absolute(p.replace("$$", "$"), directory))
                               for p in shlex.split(contents[len("onion:"):])})
        if not dependencies:
            raise ValueError("empty dependency inventory")
        if str(source) not in dependencies:
            raise ValueError("compiler primary input missing from dependency inventory")
        records = [file_record(path) for path in dependencies]
        main_input = next(row for row in records if row["path"] == str(source))
        return records, completed.stderr, {"main_input": main_input, "compiler_path": str(actual_compiler)}


def git_provenance(source, lock):
    revision = run(["git", "rev-parse", "HEAD"], source).stdout.strip()
    tag_revision = run(["git", "rev-parse", lock["tag"] + "^{commit}"], source).stdout.strip()
    origin = run(["git", "remote", "get-url", "origin"], source).stdout.strip()
    if revision != lock["commit"] or tag_revision != revision or origin != lock["origin"]:
        raise ValueError("OAI origin/tag/commit does not match lock")
    dirty = run(["git", "status", "--porcelain", "--untracked-files=normal"], source).stdout
    # The dedicated build tree is ignored by OAI. Any source change is rejected.
    if dirty:
        raise ValueError("OAI source checkout is dirty")
    return {"origin": origin, "tag": lock["tag"], "commit": revision, "clean": True,
            "lock": file_record(LOCK)}


def dependency_inventory(lock_path):
    """Explicit versions for dependencies installed outside the OS package DB.

    The lock is supplied by the environment builder, not guessed from a path.
    Hash the actual tree as well, including headers used by code generation.
    """
    lock_path = Path(lock_path).resolve()
    records = []
    for item in json.loads(lock_path.read_text()):
        if set(item) != {"name", "version", "path"} or not item["name"] or not item["version"]:
            raise ValueError("dependency lock needs name/version/path records")
        path = Path(item["path"])
        if not path.is_absolute() or not path.exists():
            raise ValueError(f"dependency path missing or not absolute: {path}")
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts)
        if not files:
            raise ValueError(f"empty dependency tree: {path}")
        inputs = [file_record(p) for p in files]
        records.append({**item, "files": inputs, "input_digest": digest(inputs)})
    if not records:
        raise ValueError("empty dependency lock")
    return {"lock": file_record(lock_path), "dependencies": records}


def collect(build, names, *, configuration=None, oai=False, dependency_lock=None, sources=None):
    build = Path(build).resolve()
    index, model, config, targets, provenance, cache = cmake_model(build, configuration)
    source = Path(model["paths"]["source"]).resolve()
    requested = None if sources is None else {absolute(str(p), source) for p in sources}
    if requested is not None:
        if not requested or any(not p.is_file() or not p.is_relative_to(source) for p in requested):
            raise ValueError("analysis sources must be a nonempty list of files inside the CMake source tree")
    lock = json.loads(LOCK.read_text())
    if oai and sorted(names) != sorted(lock["targets"]):
        raise ValueError("OAI profile requires the two locked targets")
    source_info = git_provenance(source, lock) if oai else {"path": str(source), "profile": "fixture/generic"}
    if oai and dependency_lock is None:
        raise ValueError("OAI profile requires --dependency-lock for non-package dependencies")
    dependencies = dependency_inventory(dependency_lock) if dependency_lock else None
    cache_values = {e["name"]: e["value"] for e in cache["entries"]}
    if oai and cache_values.get("OAI_SIMU", "").upper() not in ("ON", "TRUE", "1"):
        raise ValueError("OAI profile requires OAI_SIMU=ON in actual CMake cache")
    cmake_tool = compiler_record(index["cmake"]["paths"]["cmake"], build)
    packages = None
    if shutil.which("dpkg-query"):
        listing = run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\n"]).stdout
        packages = {"format": "dpkg-query name/version/architecture", "listing": listing,
                    "sha256": "sha256:" + sha256(listing.encode()).hexdigest()}
    members = selection(targets, names)
    environment = {key: os.environ.get(key) for key in ENVIRONMENT}
    context = {"source": source_info, "cmake": index["cmake"], "configuration": config["name"],
               "environment": environment, "platform": platform.platform(), "inputs": provenance,
               "collector": file_record(Path(__file__)), "cmake_tool": cmake_tool,
               "dependency_inventory": dependencies, "packages": packages}
    if requested is not None:
        context['analysis_scope'] = {'kind': 'explicit_source_subset',
            'sources': [file_record(p) for p in sorted(requested)],
            'scope': 'only listed TUs are analyzed; other target TUs remain excluded with reasons'}
    cache_context = digest(context)
    entries, compilers, matched, selected_sources = [], {}, set(), set()
    for number, entry in enumerate(json.loads((build / "compile_commands.json").read_text())):
        row = {"index": number, "raw": entry, "status": "failed", "reason": None}
        try:
            args = command(entry)
            directory = Path(entry["directory"]).resolve()
            path = absolute(entry["file"], directory)
            target, target_source = owner_for(entry, args, directory, path, model, build, targets)
            matched.add((target["id"], target_source["path"]))
            group = target["compileGroups"][target_source["compileGroupIndex"]]
            row.update(source=str(path), directory=str(directory), arguments=args,
                       target={"id": target["id"], "name": target["name"], "json_file": target["json_file"],
                               "source": target_source, "compile_group": group},
                       selected_by=members.get(target["id"], []))
            if target["id"] not in members:
                row.update(status="excluded", reason="outside selected target dependency closure")
                entries.append(row)
                continue
            selected_sources.add(path)
            if requested is not None and path not in requested:
                row.update(status="excluded", reason="outside explicit analysis source subset; not analyzed")
                entries.append(row)
                continue
            if group["language"] not in ("C", "CXX"):
                raise ValueError(f"unsupported language: {group['language']}")
            configured_compiler = cache_values.get("CMAKE_" + group["language"] + "_COMPILER")
            if not configured_compiler or executable_path(args[0], directory) != executable_path(configured_compiler, build):
                raise ValueError("compiler launcher or mismatch unsupported: expected CMake's direct compiler")
            compiler_key = (args[0], str(directory))
            if compiler_key not in compilers:
                compilers[compiler_key] = compiler_record(args[0], directory)
            compiler = compilers[compiler_key]
            row["compiler"] = compiler
            if oai and not re.search(r"clang version 18\.", compiler["version"]):
                raise ValueError("OAI analysis requires Clang 18")
            dependencies, diagnostics, validation = validate_entry(args, directory, compiler, path)
            row.update(status="validated", reason="syntax and dependencies validated; extraction not run",
                       dependencies=dependencies, diagnostics=diagnostics, validation=validation)
            row["cache_key"] = digest({"context": cache_context, "source": row["source"],
                                       "directory": row["directory"], "arguments": args,
                                       "compiler": compiler, "dependencies": dependencies,
                                       "target": row["target"], "validation": validation})
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            row.update(status="failed", reason=str(error))
            if isinstance(error, subprocess.CalledProcessError):
                row["diagnostics"] = error.stderr
        entries.append(row)
    # Do not silently lose a selected CMake translation unit absent from DB.
    for key in sorted(members):
        for item in targets[key].get("sources", []):
            if "compileGroupIndex" in item and (key, item["path"]) not in matched:
                entries.append({"status": "failed", "source": item["path"], "target_id": key,
                                "reason": "selected CMake translation unit missing from compile DB"})
    if requested is not None:
        for path in sorted(requested - selected_sources):
            entries.append({'status': 'failed', 'source': str(path),
                            'reason': 'requested analysis source absent from selected target compile DB closure'})
    summary = {status: sum(r["status"] == status for r in entries)
               for status in ("validated", "excluded", "failed")}
    manifest = {"schema_version": 1, "kind": "build_input_manifest", "profile": "oai" if oai else "generic",
                "validation_scope": "syntax_and_dependency_scan", "context": context,
                "targets": [{"id": key, "name": target["name"], "type": target["type"],
                             "selected_by": members.get(key, []),
                             "dependencies": target.get("dependencies", [])} for key, target in sorted(targets.items())],
                "entries": entries, "summary": summary,
                "ready": summary["failed"] == 0 and summary["validated"] > 0}
    manifest["manifest_id"] = digest(manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    request = sub.add_parser("query")
    request.add_argument("--build", required=True, type=Path)
    capture = sub.add_parser("capture")
    capture.add_argument("--build", required=True, type=Path)
    capture.add_argument("--target", action="append", required=True)
    capture.add_argument("--configuration")
    capture.add_argument("--profile", choices=("oai", "generic"), default="oai")
    capture.add_argument("--out", required=True, type=Path)
    capture.add_argument("--dependency-lock", type=Path)
    capture.add_argument("--source", type=Path, action="append", help="Explicit TU subset, relative to CMake source root; repeatable")
    options = parser.parse_args()
    if options.action == "query":
        query(options.build)
        return 0
    options.out.mkdir(parents=True, exist_ok=True)
    try:
        manifest = collect(options.build, options.target, configuration=options.configuration,
                           oai=options.profile == "oai", dependency_lock=options.dependency_lock, sources=options.source)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        manifest = {"schema_version": 1, "kind": "build_input_manifest", "ready": False,
                    "entries": [], "failures": [{"reason": str(error)}]}
    (options.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for status, name in (("validated", "targets"), ("excluded", "excluded"), ("failed", "failures")):
        rows = [r for r in manifest["entries"] if r["status"] == status]
        if name == "failures":
            rows += manifest.get("failures", [])
        (options.out / (name + ".json")).write_text(json.dumps(rows, indent=2) + "\n")
    return 0 if manifest["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
