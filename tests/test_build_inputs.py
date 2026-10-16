"""Real CMake File API/compile DB regressions; no external OAI checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from onion.build_inputs import collect, dependency_inventory, git_provenance, query, validation_args

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
        import shlex
        before = self.capture()
        def convert(rows):
            for row in rows:
                row["arguments"] = shlex.split(row.pop("command"))
        self.mutate_db(convert)
        after = self.capture()
        self.assertTrue(after["ready"])
        self.assertEqual([r.get("arguments") for r in before["entries"]],
                         [r.get("arguments") for r in after["entries"]])

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
