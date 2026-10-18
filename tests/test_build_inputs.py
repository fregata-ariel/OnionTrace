"""Real CMake File API/compile DB regressions; no external OAI checkout."""
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from onion.build_inputs import collect, dependency_inventory, git_provenance, query, validation_args
from onion.build_inputs import cmake_model, ownership_index, owner_for, command, absolute

ROOT = Path(__file__).resolve().parents[1]


class BuildInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="onion-build-inputs-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source with spaces"
        shutil.copytree(ROOT / "fixture/compilation", self.source)
        self.build = self.root / "build with spaces"
        self.cc = os.environ.get("ONION_CLANG", "clang-18")
        query(self.build)
        self.configure()
        self.db_path = self.build / "compile_commands.json"

    def configure(self, *extra):
        subprocess.run(["cmake", "-S", str(self.source), "-B", str(self.build), "-G", "Ninja",
                        "-DCMAKE_C_COMPILER=" + self.cc, *extra], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def capture(self):
        return collect(self.build, ["nr-softmodem", "nr-uesoftmodem"])

    def mutate_db(self, mutate):
        rows = json.loads(self.db_path.read_text())
        mutate(rows)
        self.db_path.write_text(json.dumps(rows))

    def test_target_closure_and_shared_source_variants(self):
        manifest = self.capture()
        self.assertTrue(manifest["ready"], manifest)
        self.assertEqual(manifest["summary"], {"validated": 6, "excluded": 1, "failed": 0})
        shared = next(r for r in manifest["entries"] if r["source"].endswith("shared.c"))
        self.assertEqual(shared["selected_by"], ["nr-softmodem", "nr-uesoftmodem"])
        variants = [r for r in manifest["entries"] if r["source"].endswith("variant.c")]
        self.assertEqual(len(variants), 2)
        self.assertNotEqual(variants[0]["cache_key"], variants[1]["cache_key"])
        self.assertEqual({r["target"]["name"] for r in variants}, {"variant_a", "variant_b"})
        self.assertTrue(any(r["source"].endswith("worker.c") for r in manifest["entries"]))
        self.assertEqual(self.capture(), manifest)

    def test_explicit_source_subset_keeps_variants_and_records_unanalyzed_entries(self):
        whole = self.capture()
        subset = collect(self.build, ['nr-softmodem', 'nr-uesoftmodem'], sources=['variant.c'])
        self.assertTrue(subset['ready'], subset)
        self.assertEqual(subset['summary'], {'validated': 2, 'excluded': 5, 'failed': 0})
        self.assertEqual(len(subset['entries']), len(whole['entries']))
        rows = [r for r in subset['entries'] if r['status'] == 'validated']
        self.assertEqual({r['target']['name'] for r in rows}, {'variant_a', 'variant_b'})
        self.assertNotEqual(rows[0]['cache_key'], rows[1]['cache_key'])
        self.assertNotEqual(rows[0]['cache_key'], next(r['cache_key'] for r in whole['entries']
                                                     if r.get('target') == rows[0]['target']))
        self.assertEqual(subset['context']['analysis_scope']['kind'], 'explicit_source_subset')
        self.assertTrue(all('not analyzed' in r['reason'] for r in subset['entries']
                            if r['status'] == 'excluded' and r['selected_by']))
        for row in subset['entries']:
            if row['status'] != 'excluded':
                continue
            self.assertIn(row['raw'], [e['raw'] for e in whole['entries']])
            reference = row['target']['compile_group_reference']
            target = json.loads(Path(reference['json_file']).read_text())
            self.assertEqual(target['compileGroups'][reference['index']],
                             next(e['target']['compile_group'] for e in whole['entries']
                                  if e['raw'] == row['raw']) if row['selected_by'] else
                             target['compileGroups'][reference['index']])

    def test_requested_source_outside_target_closure_or_missing_db_is_failure(self):
        result = collect(self.build, ['nr-softmodem', 'nr-uesoftmodem'], sources=['unrelated.c'])
        self.assertFalse(result['ready'])
        self.assertTrue(any('requested analysis source absent' in r['reason'] for r in result['entries']))
        self.mutate_db(lambda rows: rows.__setitem__(slice(None), [r for r in rows if not r['file'].endswith('variant.c')]))
        result = collect(self.build, ['nr-softmodem', 'nr-uesoftmodem'], sources=['variant.c'])
        self.assertFalse(result['ready'])
        self.assertTrue(any('missing from compile DB' in r['reason'] for r in result['entries']))

    def test_empty_or_outside_source_subset_is_rejected(self):
        for sources in ([], ['missing.c'], [ROOT / 'fixture/compilation/main.c']):
            with self.subTest(sources=sources), self.assertRaisesRegex(ValueError, 'analysis sources'):
                collect(self.build, ['nr-softmodem'], sources=sources)

    def test_indexed_ownership_rejects_ambiguous_source_membership_and_prefixes(self):
        _, model, _, targets, _, _ = cmake_model(self.build)
        row = json.loads(self.db_path.read_text())[0]
        args, directory = command(row), Path(row['directory'])
        source = absolute(row['file'], directory)
        target, item = owner_for(row, args, directory, source, model, self.build, targets,
                                 index=ownership_index(model, self.build, targets))
        for kind in ('source', 'prefix'):
            broken = copy.deepcopy(targets)
            if kind == 'source':
                broken[target['id']]['sources'].append(copy.deepcopy(item))
            else:
                broken['duplicate-prefix'] = copy.deepcopy(target)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'one CMake target'):
                owner_for(row, args, directory, source, model, self.build, broken,
                          index=ownership_index(model, self.build, broken))

    def test_generated_and_system_headers_are_hashed(self):
        row = next(r for r in self.capture()["entries"] if r["source"].endswith("shared.c"))
        self.assertTrue(any(d["path"].endswith("generated/config.h") for d in row["dependencies"]))
        self.assertTrue(any(d["path"].endswith("stddef.h") for d in row["dependencies"]))
        self.assertTrue(all(d["sha256"].startswith("sha256:") for d in row["dependencies"]))
        self.assertIn("version", row["compiler"])
        self.assertIn("sha256", row["compiler"])
        self.assertIn("compile_group", row["target"])

    def test_definition_change_invalidates_cache(self):
        before = self.capture()
        self.configure("-DVARIANT=7")
        after = self.capture()
        self.assertTrue(after["ready"])
        self.assertNotEqual(before["manifest_id"], after["manifest_id"])
        self.assertNotEqual(before["entries"][0]["cache_key"], after["entries"][0]["cache_key"])

    def test_native_cpu_expansion_is_recorded_and_invalidates_cache(self):
        from onion.build_inputs import run as real_run
        self.configure('-DCMAKE_C_FLAGS=-march=native -mno-avx512f')
        before = self.capture()
        self.assertTrue(before['ready'], before)
        row = next(r for r in before['entries'] if r['status'] == 'validated')
        self.assertIn('-target-cpu', row['validation']['native_driver_jobs'])
        self.assertIn('-target-feature', row['validation']['native_driver_jobs'])
        def changed_cpu(arguments, cwd=None):
            result = real_run(arguments, cwd)
            if '-###' in arguments:
                result.stderr += '\nchanged native CPU feature set\n'
            return result
        with patch('onion.build_inputs.run', side_effect=changed_cpu):
            after = self.capture()
        self.assertTrue(after['ready'])
        other = next(r for r in after['entries'] if r['status'] == 'validated')
        self.assertNotEqual(row['cache_key'], other['cache_key'])

    def test_header_content_change_invalidates_cache(self):
        before = self.capture()
        header = self.build / "generated/config.h"
        header.write_text(header.read_text() + "\n/* changed generated input */\n")
        after = self.capture()
        self.assertTrue(after["ready"])
        a = next(r for r in before["entries"] if r["source"].endswith("shared.c"))
        b = next(r for r in after["entries"] if r["source"].endswith("shared.c"))
        self.assertNotEqual(a["cache_key"], b["cache_key"])

    def test_missing_generated_header_is_failed_without_cache_key(self):
        (self.build / "generated/config.h").unlink()
        manifest = self.capture()
        self.assertFalse(manifest["ready"])
        failed = [r for r in manifest["entries"] if r["status"] == "failed"]
        self.assertEqual(len(failed), 3)
        self.assertTrue(all("config.h" in r["diagnostics"] for r in failed))
        self.assertTrue(all("cache_key" not in r for r in failed))

    def test_unknown_argument_is_failed(self):
        self.mutate_db(lambda rows: rows[0].update(command=rows[0]["command"] + " -fonion-unsupported"))
        manifest = self.capture()
        self.assertFalse(manifest["ready"])
        self.assertEqual(manifest["summary"]["failed"], 1)
        self.assertIn("onion-unsupported", manifest["entries"][0]["diagnostics"])

    def test_missing_db_entry_and_unmapped_output_are_failed(self):
        original = self.db_path.read_text()
        self.mutate_db(lambda rows: rows.pop(0))
        result = self.capture()
        self.assertFalse(result["ready"])
        self.assertTrue(any("missing from compile DB" in r["reason"] for r in result["entries"]))
        self.db_path.write_text(original)
        self.mutate_db(lambda rows: rows[0].update(output="unmapped.o"))
        result = self.capture()
        self.assertFalse(result["ready"])
        self.assertTrue(any("cannot be matched" in r["reason"] for r in result["entries"]))

    def test_arguments_array_preserves_command_conditions(self):
        before = self.capture()
        def convert(rows):
            for row in rows:
                row["arguments"] = shlex.split(row.pop("command"))
        self.mutate_db(convert)
        after = self.capture()
        self.assertTrue(after["ready"])
        self.assertEqual([r.get("arguments") for r in before["entries"]],
                         [r.get("arguments") for r in after["entries"]])

    def test_different_primary_input_is_rejected_even_when_declared_source_is_included(self):
        original = self.db_path.read_text()
        for form in ("command", "arguments", "forced_include"):
            with self.subTest(form=form):
                self.db_path.write_text(original)
                def replace_source(rows):
                    entry = rows[0]
                    args = shlex.split(entry["command"])
                    args = [str(self.source / "unrelated.c") if a == entry["file"] else a for a in args]
                    if form == "forced_include":
                        args += ["-include", entry["file"]]
                    if form == "command":
                        entry["command"] = shlex.join(args)
                    else:
                        entry["arguments"] = args
                self.mutate_db(replace_source)
                result = self.capture()
                self.assertFalse(result["ready"])
                row = result["entries"][0]
                self.assertEqual(row["source"], str(self.source / "shared.c"))
                self.assertEqual(row["status"], "failed")
                self.assertIn("primary input does not match", row["reason"])
                self.assertNotIn("cache_key", row)

    def test_mismatched_command_cannot_hide_declared_source_changes(self):
        def replace_source(rows):
            entry = rows[0]
            entry["arguments"] = [str(self.source / "unrelated.c") if a == entry["file"] else a
                                  for a in shlex.split(entry["command"])]
        self.mutate_db(replace_source)
        before = self.capture()
        (self.source / "shared.c").write_text("#error declared source must not be ignored\n")
        after = self.capture()
        for result in (before, after):
            self.assertFalse(result["ready"])
            self.assertEqual(result["entries"][0]["status"], "failed")
            self.assertNotIn("cache_key", result["entries"][0])

    def test_primary_source_content_is_hashed_and_invalidates_cache(self):
        before = self.capture()["entries"][0]
        source = self.source / "shared.c"
        source.write_text(source.read_text() + "\n/* source changed */\n")
        after = self.capture()["entries"][0]
        self.assertEqual(after["status"], "validated")
        main_input = after["validation"]["main_input"]
        self.assertEqual(main_input["path"], str(source))
        self.assertIn(main_input, after["dependencies"])
        self.assertNotEqual(before["cache_key"], after["cache_key"])
        self.assertNotEqual(before["validation"]["main_input"]["sha256"], main_input["sha256"])

    def test_multiple_primary_inputs_are_rejected(self):
        self.mutate_db(lambda rows: rows[0].update(arguments=[*shlex.split(rows[0]["command"]),
                                                             str(self.source / "unrelated.c")]))
        result = self.capture()
        self.assertFalse(result["ready"])
        self.assertEqual(result["entries"][0]["status"], "failed")
        self.assertIn("exactly one compiler primary input", result["entries"][0]["reason"])
        self.assertNotIn("cache_key", result["entries"][0])

    def test_absent_primary_input_is_failed_without_cache_key(self):
        self.mutate_db(lambda rows: rows[0].update(arguments=[a for a in shlex.split(rows[0]["command"])
                                                             if a != rows[0]["file"]]))
        result = self.capture()
        self.assertFalse(result["ready"])
        self.assertEqual(result["entries"][0]["status"], "failed")
        self.assertNotIn("cache_key", result["entries"][0])

    def test_primary_input_metadata_and_dependency_membership_are_required(self):
        from onion.build_inputs import run
        for failure in ("metadata_missing", "metadata_malformed", "source_not_hashed"):
            def incomplete_compiler_output(args, cwd=None):
                result = run(args, cwd)
                if "-MJ" in args:
                    if failure == "metadata_missing":
                        Path(args[args.index("-MJ") + 1]).unlink()
                    elif failure == "metadata_malformed":
                        Path(args[args.index("-MJ") + 1]).write_text(json.dumps({
                            "directory": str(cwd), "file": str(self.source / "shared.c"), "arguments": []}) + ",\n")
                    else:
                        depfile = Path(args[args.index("-MF") + 1])
                        depfile.write_text("onion: " + shlex.quote(str(self.source / "unrelated.c")) + "\n")
                return result
            with self.subTest(failure=failure), patch("onion.build_inputs.run", side_effect=incomplete_compiler_output):
                result = self.capture()
            self.assertFalse(result["ready"])
            self.assertEqual(result["summary"]["failed"], 6)
            self.assertTrue(all("cache_key" not in r for r in result["entries"] if r["status"] == "failed"))

    def test_generic_rejects_env_launcher_before_recording_compiler(self):
        self.mutate_db(lambda rows: rows[0].update(arguments=[shutil.which("env"),
                                                             *shlex.split(rows[0]["command"])]))
        result = self.capture()
        self.assertFalse(result["ready"])
        self.assertEqual(result["summary"]["failed"], 1)
        row = result["entries"][0]
        self.assertIn("compiler launcher", row["reason"])
        self.assertNotIn("compiler", row)
        self.assertNotIn("cache_key", row)

    def test_cli_writes_failure_inventory_and_returns_nonzero(self):
        (self.build / "generated/config.h").unlink()
        output = self.root / "result"
        result = subprocess.run([sys.executable, "-m", "onion.build_inputs", "capture",
                                 "--profile", "generic", "--build", str(self.build),
                                 "--target", "nr-softmodem", "--target", "nr-uesoftmodem",
                                 "--out", str(output)], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(json.loads((output / "failures.json").read_text())), 3)
        self.assertTrue((output / "excluded.json").is_file())
        self.assertTrue((output / "targets.json").is_file())

    def test_missing_query_wrong_configuration_and_target_rejected(self):
        with self.assertRaisesRegex(ValueError, "configuration"):
            collect(self.build, ["nr-softmodem"], configuration="absent")
        with self.assertRaisesRegex(ValueError, "target not found"):
            collect(self.build, ["absent"])
        with self.assertRaises((ValueError, subprocess.SubprocessError)):
            collect(self.build, ["nr-softmodem", "nr-uesoftmodem"], oai=True)
        shutil.rmtree(self.build / ".cmake/api/v1/reply")
        with self.assertRaisesRegex(ValueError, "reply missing"):
            self.capture()

    def test_response_files_and_plugins_are_explicitly_unsupported(self):
        for flag in ("@options.rsp", "-fplugin=unknown.so", "-Xclang"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "unsupported"):
                validation_args(["cc", "-c", "x.c", flag])

    def test_external_dependency_versions_and_hashes_affect_cache(self):
        lock = self.root / "dependencies.json"
        directory = self.root / "external-dependency"
        directory.mkdir()
        header = directory / "external.h"
        header.write_text("#define EXTERNAL 1\n")
        lock.write_text(json.dumps([{"name": "example", "version": "commit-123", "path": str(directory)}]))
        before = collect(self.build, ["nr-softmodem"], dependency_lock=lock)
        header.write_text("#define EXTERNAL 2\n")
        after = collect(self.build, ["nr-softmodem"], dependency_lock=lock)
        self.assertNotEqual(before["entries"][0]["cache_key"], after["entries"][0]["cache_key"])
        self.assertEqual(after["context"]["dependency_inventory"]["dependencies"][0]["version"], "commit-123")
        lock.write_text("[]")
        with self.assertRaisesRegex(ValueError, "empty dependency lock"):
            dependency_inventory(lock)

    def test_oai_profile_requires_explicit_dependencies_simu_and_clang18(self):
        with patch("onion.build_inputs.git_provenance", return_value={"commit": "fixture"}):
            with self.assertRaisesRegex(ValueError, "dependency-lock"):
                collect(self.build, ["nr-softmodem", "nr-uesoftmodem"], oai=True)
            lock = self.root / "dependencies.json"
            lock.write_text(json.dumps([{"name": "fixture", "version": "1", "path": str(self.source)}]))
            with self.assertRaisesRegex(ValueError, "OAI_SIMU"):
                collect(self.build, ["nr-softmodem", "nr-uesoftmodem"], oai=True, dependency_lock=lock)
            self.configure("-DOAI_SIMU=ON")
            with patch("onion.build_inputs.compiler_record", return_value={"version": "clang version 19.0"}):
                result = collect(self.build, ["nr-softmodem", "nr-uesoftmodem"], oai=True, dependency_lock=lock)
            self.assertFalse(result["ready"])
            self.assertEqual(result["summary"]["failed"], 6)
            self.assertTrue(all("Clang 18" in r["reason"] for r in result["entries"] if r["status"] == "failed"))


class PreparationTests(unittest.TestCase):
    def test_existing_workspace_is_untouched_and_no_command_runs(self):
        from scripts.prepare_oai import prepare
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "operational-build"
            marker.write_text("keep")
            with patch("scripts.prepare_oai.subprocess.run") as execute:
                with self.assertRaises(FileExistsError):
                    prepare(directory, fetch_only=True)
                execute.assert_not_called()
            self.assertEqual(marker.read_text(), "keep")

    def test_fixed_revision_origin_tag_and_source_cleanliness_are_enforced(self):
        lock = {"commit": "fixed", "tag": "2026.w07", "origin": "https://example.test/oai.git"}
        for values in (["wrong", "fixed", lock["origin"], ""],
                       ["fixed", "wrong", lock["origin"], ""],
                       ["fixed", "fixed", "wrong-origin", ""],
                       ["fixed", "fixed", lock["origin"], " M source.c\n"]):
            with self.subTest(values=values):
                with patch("onion.build_inputs.run", side_effect=[SimpleNamespace(stdout=v) for v in values]):
                    with self.assertRaises(ValueError):
                        git_provenance(Path("."), lock)


if __name__ == "__main__":
    unittest.main()
