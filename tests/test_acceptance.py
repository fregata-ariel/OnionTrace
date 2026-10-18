"""T9 end-to-end artifact acceptance and fail-closed regression tests (native)."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from onion.acceptance import export_acceptance
from onion.build_inputs import collect, query, digest, file_record
from onion.libtooling import extract_manifest
from test_queues import run, ROOT
from scripts.oai_ci import run_acceptance, resume_acceptance
from scripts.install_oai_dependencies import simde_environment


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='onion-acceptance-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.out = Path(cls.temp.name)
        tool = Path(os.environ.get('ONION_EXTRACTOR', ROOT / 'build-libtooling/onion-extract'))
        if not tool.is_file():
            run(['cmake', '-S', ROOT / 'tools/libtooling', '-B', tool.parent, '-G', 'Ninja',
                 '-DLLVM_DIR=' + os.environ.get('LLVM_DIR', '/usr/lib/llvm-18/lib/cmake/llvm'),
                 '-DClang_DIR=' + os.environ.get('Clang_DIR', '/usr/lib/llvm-18/lib/cmake/clang')])
            run(['cmake', '--build', tool.parent, '--parallel', '2'])
        build = cls.out / 'build'
        query(build)
        run(['cmake', '-S', ROOT / 'fixture/structure-viewer', '-B', build, '-G', 'Ninja',
             '-DCMAKE_C_COMPILER=' + os.environ.get('ONION_CLANG', 'clang-18'), '-DCMAKE_C_FLAGS=-rdynamic -DKEEP_SEMANTIC_FLAG=1'])
        cls.manifest = collect(build, ['nr-softmodem', 'nr-uesoftmodem'])
        cls.extraction = extract_manifest(cls.manifest, tool, cls.out / 'extraction')

    def test_real_both_main_thread_queue_callback_cfg_and_offline_export(self):
        destination = self.out / 'acceptance'
        result = export_acceptance(self.manifest, self.extraction, destination)
        self.assertTrue(result['passed'], result)
        for row in self.extraction['entries']:
            if row['status'] == 'extracted':
                self.assertIn('-rdynamic', row['original_arguments'])
                self.assertNotIn('-rdynamic', row['tooling_arguments'])
                self.assertIn('-DKEEP_SEMANTIC_FLAG=1', row['tooling_arguments'])
                self.assertEqual(row['adjustments']['removed_link_arguments'], ['-rdynamic'])
        self.assertFalse(result['full_extraction_verified'])
        self.assertFalse(result['runtime_evidence'])
        self.assertEqual({t['target'] for t in result['targets']}, {'nr-softmodem', 'nr-uesoftmodem'})
        for target in result['targets']:
            self.assertIn('thread_start', target['categories'])
            self.assertIn('callback_target', target['categories'])
            self.assertIn('queue_enqueue', target['categories'])
            self.assertGreater(target['unresolved_links'], 0)
            view = json.loads((destination / target['target'] / 'view.json').read_text())
            self.assertEqual(len(view['roots']), 1)
            self.assertTrue(all(view['cfgs'][r['function_id']]['nodes'] for r in view['references']))
            # Callback and Queue may-links never become observed execution.
            self.assertFalse(view['runtime_evidence'])
            self.assertTrue(any(r['resolution'] == 'unresolved' for r in view['links']))
        html = (destination / 'structure.html').read_text()
        self.assertNotIn('<script src=', html)
        self.assertIn('nr-uesoftmodem', html)
        self.assertEqual(json.loads((destination / 'failures.json').read_text()), [])

    def test_fixture_or_source_only_success_cannot_pass_pinned_oai_gate(self):
        result = export_acceptance(self.manifest, self.extraction, self.out / 'not-oai', oai=True)
        self.assertFalse(result['passed'])
        self.assertFalse(result['full_extraction_verified'])
        self.assertEqual({g['name'] for g in result['gates'] if not g['passed']},
                         {'oai-threads', 'oai-callbacks', 'oai-queues'})

    def test_failed_or_missing_extraction_never_reuses_success(self):
        destination = self.out / 'failed'
        self.assertTrue(export_acceptance(self.manifest, self.extraction, destination)['passed'])
        (destination / 'oai-queues.json').write_text('{"full_extraction_verified": true}')
        extraction = copy.deepcopy(self.extraction)
        extraction['entries'].pop()
        # Even a misleading ready flag cannot hide a missing selected TU.
        result = export_acceptance(self.manifest, extraction, destination)
        self.assertFalse(result['passed'])
        self.assertFalse(json.loads((destination / 'acceptance.json').read_text())['passed'])
        self.assertTrue(json.loads((destination / 'failures.json').read_text()))
        extraction['ready'] = False
        self.assertFalse(export_acceptance(self.manifest, extraction, destination)['passed'])
        self.assertFalse((destination / 'structure.html').exists())
        self.assertFalse((destination / 'viewer.json').exists())
        self.assertFalse((destination / 'oai-queues.json').exists())

    def test_input_condition_change_and_mismatched_artifacts_fail(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['entries'][0]['arguments'].append('-DCHANGED_CONDITION=1')
        result = export_acceptance(manifest, self.extraction, self.out / 'changed')
        self.assertFalse(result['passed'])
        manifest['manifest_id'] = digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
        result = export_acceptance(manifest, self.extraction, self.out / 'recaptured')
        self.assertFalse(result['passed'])

    def test_resumed_acceptance_verifies_unchanged_evidence_and_actual_oai_gates(self):
        destination = self.out / 'resume'
        (destination / 'inputs').mkdir(parents=True)
        (destination / 'extraction').mkdir()
        manifest = destination / 'inputs/manifest.json'
        extraction = destination / 'extraction/extraction.json'
        manifest.write_text(json.dumps(self.manifest))
        extraction.write_text(json.dumps(self.extraction))
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        tool = Path(os.environ.get('ONION_EXTRACTOR', ROOT / 'build-libtooling/onion-extract'))
        record = {'kind': 'oai_ci_run', 'passed': False, 'full_extraction_verified': False,
                  'extraction_verified': True, 'pending_stages': ['acceptance'], 'onion_commit': commit,
                  'stages': [{'name': 'extract', 'passed': True}],
                  'binaries': [file_record(tool)],
                  'captured_inputs': [file_record(manifest), file_record(extraction)]}
        (destination / 'run.json').write_text(json.dumps(record))
        # An interrupted split run cannot turn a fixture into fixed-OAI evidence.
        result = resume_acceptance(destination)
        self.assertFalse(result['passed'])
        self.assertFalse(result['full_extraction_verified'])
        self.assertTrue((destination / 'acceptance/oai-threads.json').exists())
        (destination / 'run.json').write_text(json.dumps(record))
        extraction.write_text('{}')
        with patch('scripts.oai_ci.export_acceptance') as accept:
            result = resume_acceptance(destination)
        accept.assert_not_called()
        self.assertIn('captured input or binary changed', result['failure'])

    def test_isolated_simde_layout_keeps_external_headers_out_of_oai_function_definitions(self):
        destination = self.out / 'isolated-simde'
        header = destination / 'source/x86/gfni.h'
        header.parent.mkdir(parents=True)
        header.write_text('static inline int external_intrinsic(void) { return 7; }\n')
        source = self.out / 'system-header-check.c'
        source.write_text('#include <simde/x86/gfni.h>\nint main(void) { return external_intrinsic(); }\n')
        environment = simde_environment(destination)
        self.assertNotIn('CPATH', environment)
        output = self.out / 'system-header-check.json'
        tool = Path(os.environ.get('ONION_EXTRACTOR', ROOT / 'build-libtooling/onion-extract'))
        compiler = os.environ.get('ONION_CLANG', 'clang-18')
        resource = run([compiler, '-print-resource-dir']).stdout.strip()
        with patch.dict(os.environ, environment):
            run([tool, '--compilation-id=system-header-check', '--output=' + str(output), source,
                 '--', '-std=gnu11', '-resource-dir=' + resource])
        raw = json.loads(output.read_text())
        self.assertEqual([f['name'] for f in raw['functions']], ['main'])
        self.assertIsNone(raw['functions'][0]['calls'][0]['target_id'])


class RunnerFailureTests(unittest.TestCase):
    def test_fetch_failure_leaves_explicit_failed_run_and_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'oai'
            with patch('scripts.oai_ci.prepare', side_effect=ValueError('pinned checkout mismatch')):
                result = run_acceptance(out, Path('missing-tool'))
            self.assertFalse(result['passed'])
            self.assertFalse(result['full_extraction_verified'])
            self.assertEqual(result['stages'][0]['name'], 'fetch')
            self.assertEqual(result['stages'][0]['failure'], 'pinned checkout mismatch')
            self.assertFalse(result['stages'][0]['passed'])
            self.assertTrue((out / 'fetch-error.log').is_file())
            self.assertTrue(json.loads((out / 'failures.json').read_text()))
            self.assertFalse(json.loads((out / 'run.json').read_text())['passed'])
            # Existing outputs / operational directories are not overwritten.
            with self.assertRaises(FileExistsError):
                run_acceptance(out, Path('missing-tool'))


if __name__ == '__main__':
    unittest.main()
