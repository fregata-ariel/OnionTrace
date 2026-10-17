"""Native Clang 18 acceptance tests. Required: missing tools are test failures."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from onion.build_inputs import collect, digest, query
from onion.cfg import build_model as text_model, parse_dump
from onion.core import _enumerate, _path_sites, reconstruct, recommend
from onion.demo import run_demo
from onion.libtooling import build_model, extract_manifest, validate_extraction

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], text=True, capture_output=True, check=True, timeout=180, **kwargs)


class LibToolingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='onion-tooling-test-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.cc = os.environ.get('ONION_CLANG', 'clang-18')
        cls.tool = Path(os.environ.get('ONION_EXTRACTOR', ROOT / 'build-libtooling/onion-extract'))
        if not cls.tool.is_file():
            run(['cmake', '-S', ROOT / 'tools/libtooling', '-B', cls.tool.parent, '-G', 'Ninja',
                 '-DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm', '-DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang'])
            run(['cmake', '--build', cls.tool.parent, '--parallel', '2'])
        cls.source = cls.root / 'source with spaces'
        shutil.copytree(ROOT / 'fixture/libtooling', cls.source)
        cls.build = cls.root / 'build with spaces'
        query(cls.build)
        run(['cmake', '-S', cls.source, '-B', cls.build, '-G', 'Ninja', '-DCMAKE_C_COMPILER=' + cls.cc])
        run(['cmake', '--build', cls.build])
        cls.manifest = collect(cls.build, ['call_fixture', 'variant_b'])
        if not cls.manifest['ready']:
            raise AssertionError(cls.manifest)
        cls.output = cls.root / 'extracted'
        cls.result = extract_manifest(cls.manifest, cls.tool, cls.output)
        if not cls.result['ready']:
            raise AssertionError(cls.result)
        cls.raws = [json.loads(Path(r['artifact']['path']).read_text()) for r in cls.result['entries']]

    def dispatches(self):
        return [(raw, f) for raw in self.raws for f in raw['functions'] if f['name'] == 'dispatch']

    def test_multiple_conditions_have_separate_function_and_call_ids(self):
        dispatches = self.dispatches()
        self.assertEqual(len(dispatches), 2)
        self.assertNotEqual(dispatches[0][1]['id'], dispatches[1][1]['id'])
        counts = sorted(len(f['calls']) for _, f in dispatches)
        self.assertEqual(counts, [3, 4])
        ids = [c['id'] for _, f in dispatches for c in f['calls']]
        self.assertEqual(len(ids), len(set(ids)))
        for entry in self.result['entries']:
            self.assertEqual(entry['status'], 'extracted')
            self.assertIn('tooling_arguments', entry)
            self.assertEqual(entry['original_arguments'], self.manifest['entries'][entry['manifest_entry']]['arguments'])

    def test_direct_indirect_and_cross_tu_resolution_are_distinct(self):
        for _, function in self.dispatches():
            direct = [c for c in function['calls'] if c['callee_name'] == 'leaf']
            self.assertEqual(len(direct), 2)
            self.assertTrue(all(c['resolution'] == 'definition_in_tu' and c['target_id'] for c in direct))
            indirect = [c for c in function['calls'] if c['kind'] == 'indirect']
            self.assertEqual(len(indirect), 1)
            self.assertIsNone(indirect[0]['target_id'])
            self.assertIn('points-to', indirect[0]['reason'])
        external = next(c for _, f in self.dispatches() for c in f['calls'] if c['callee_name'] == 'external')
        self.assertEqual(external['resolution'], 'unresolved')
        self.assertIsNone(external['target_id'])
        definition = next(f for raw in self.raws for f in raw['functions'] if f['name'] == 'external')
        self.assertEqual(external['callee_usr'], definition['usr'])
        leaves = [f for raw in self.raws for f in raw['functions'] if f['name'] == 'leaf']
        self.assertEqual(len({f['id'] for f in leaves}), 3)
        self.assertTrue(all(f['linkage'] == 'internal' for f in leaves))

    def test_macros_keep_physical_expansion_and_spelling_ranges(self):
        for _, f in self.dispatches():
            call = next(c for c in f['calls'] if c['range']['macro'])
            span = call['range']
            self.assertEqual(span['expansion']['begin']['line'], 5)
            self.assertIn(span['spelling']['begin']['line'], (1, 5))
            self.assertIsNotNone(span['spelling']['end'])
            self.assertTrue(call['block_id'])
            self.assertTrue(call['callee_type'])
            self.assertTrue(call['arguments'])

    def test_cfg_terminators_loop_targets_and_textual_correspondence(self):
        raw, function = self.dispatches()[0]
        row = next(r for r in self.manifest['entries'] if r['cache_key'] == raw['compilation_id'])
        from onion.build_inputs import validation_args
        args = [self.cc, *validation_args(row['arguments']), '--analyze', '-Xanalyzer',
                '-analyzer-checker=debug.DumpCFG', '-o', os.devnull]
        completed = run(args, cwd=row['directory'])
        dump = parse_dump(completed.stderr + completed.stdout)
        textual = next(f for f in dump if f['name'] == 'dispatch')
        self.assertEqual({b['clang_id'] for b in function['blocks']}, {b['clang_id'] for b in textual['nodes']})
        native_edges = {(e['source'].rsplit('/', 1)[1], e['target'].rsplit('/', 1)[1]) for e in function['edges']}
        text_edges = {(e['source'].rsplit(':', 1)[1], e['target'].rsplit(':', 1)[1]) for e in textual['edges']}
        self.assertEqual(native_edges, text_edges)
        self.assertTrue(any(b['condition'] for b in function['blocks']))
        self.assertTrue(any(b['loop_target'] for b in function['blocks']))

    def test_changed_input_is_failed_instead_of_reusing_output(self):
        source = self.source / 'calls.c'
        original = source.read_text()
        try:
            source.write_text(original + '\n/* stale manifest */\n')
            result = extract_manifest(self.manifest, self.tool, self.root / 'stale')
            self.assertFalse(result['ready'])
            self.assertEqual(result['summary']['failed'], 2)
            self.assertTrue(all('changed' in r['reason'] for r in result['entries'] if r['status'] == 'failed'))
        finally:
            source.write_text(original)

    def test_failed_and_excluded_entries_are_not_lost(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['entries'][0].update(status='excluded', reason='outside requested closure')
        manifest['entries'][1].update(status='failed', reason='unknown compiler argument')
        manifest['manifest_id'] = digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
        result = extract_manifest(manifest, self.tool, self.root / 'partial')
        self.assertFalse(result['ready'])
        self.assertEqual(len(result['entries']), len(manifest['entries']))
        self.assertEqual(result['entries'][0]['status'], 'excluded')
        self.assertEqual(result['entries'][1]['reason'], 'unknown compiler argument')

    def test_malformed_references_and_tampered_manifest_are_rejected(self):
        raw = copy.deepcopy(self.raws[0])
        f = next(f for f in raw['functions'] if f['calls'])
        f['calls'][0]['block_id'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'dangling call'):
            validate_extraction(raw)
        manifest = copy.deepcopy(self.manifest)
        manifest['entries'][0]['arguments'].append('-DCHANGED=1')
        with self.assertRaisesRegex(ValueError, 'manifest_id'):
            extract_manifest(manifest, self.tool, self.root / 'tampered')

    def test_unsupported_asm_is_explicit_and_unknown_options_fail(self):
        source = self.root / 'unsupported.c'
        source.write_text('int opaque(void) { __asm__ volatile ("" ::: "memory"); return 1; }\n')
        db = self.root / 'unsupported-db'
        db.mkdir(exist_ok=True)
        def invoke(extra):
            (db / 'compile_commands.json').write_text(json.dumps([
                {'directory': str(self.root), 'file': str(source), 'arguments': [self.cc, '-c', *extra, str(source)]}]))
            out = db / 'raw.json'
            completed = subprocess.run([str(self.tool), '-p', str(db), '--compilation-id=test',
                                        '--output=' + str(out), str(source)], text=True, capture_output=True)
            return completed, json.loads(out.read_text()) if out.is_file() else None
        completed, raw = invoke([])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(any(u['kind'] == 'GCCAsmStmt' and u['affects_completeness'] for f in raw['functions'] for u in f['uncertainties']))
        completed, raw = invoke(['-x', 'c++'])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(any(u['kind'] == 'cxx_semantics_not_complete' for f in raw['functions'] for u in f['uncertainties']))
        completed, raw = invoke(['-fonion-unsupported'])
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('onion-unsupported', completed.stderr)

    def test_pthread_fixture_three_paths_reconstruction_and_recommendation(self):
        directory = self.root / 'pthread'
        query(directory)
        run(['cmake', '-S', ROOT / 'fixture', '-B', directory, '-G', 'Ninja', '-DCMAKE_C_COMPILER=' + self.cc])
        manifest = collect(directory, ['onion_fixture'])
        result = extract_manifest(manifest, self.tool, directory / 'extraction')
        self.assertTrue(result['ready'], result)
        raw = json.loads(Path(result['entries'][0]['artifact']['path']).read_text())
        supplied = os.environ.get('ONION_TEST_SESSION')
        if supplied:
            data = json.loads(Path(supplied).read_text())
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                data = run_demo(directory / 'demo', self.cc)
        # Both adapters are fed exactly the same compile command and BUILD_ID
        # for the runtime fixture, instead of comparing unrelated build scopes.
        runtime = json.loads((Path(supplied).parent if supplied else directory / 'demo').joinpath('compile_commands.json').read_text())[0]
        from onion.build_inputs import validation_args
        native_db = directory / 'runtime-db'
        native_db.mkdir()
        (native_db / 'compile_commands.json').write_text(json.dumps([runtime]))
        out = native_db / 'raw.json'
        resource = run([self.cc, '-print-resource-dir']).stdout.strip()
        run([self.tool, '-p', native_db, '--compilation-id=fixture-runtime', '--output=' + str(out),
             '--extra-arg=-resource-dir=' + resource, runtime['file']])
        raw = json.loads(out.read_text())
        completed = run([self.cc, *validation_args(runtime['arguments']), '--analyze', '-Xanalyzer',
                         '-analyzer-checker=debug.DumpCFG', '-o', os.devnull], cwd=runtime['directory'])
        catalog = json.loads((ROOT / 'fixture/catalog.json').read_text())['sites']
        model = build_model(raw, 'process_job', catalog, data['model']['build_id'], {'input': 'same runtime compile DB'})
        textual = text_model(completed.stderr + completed.stdout, 'process_job', catalog, model['build_id'], {})
        paths, status = _enumerate(model, {n['id']: n for n in model['nodes']})
        self.assertTrue(status['complete'], status)
        self.assertEqual(len(paths), 3)
        self.assertEqual(sum(n['entry'] for n in model['nodes']), 1)
        self.assertEqual(sum(n['exit'] for n in model['nodes']), 1)
        text_paths, _ = _enumerate(textual, {n['id']: n for n in textual['nodes']})
        projections = lambda m, ps: {_path_sites(p, {n['id']: n for n in m['nodes']}) for p in ps}
        self.assertEqual(projections(model, paths), projections(textual, text_paths))
        stages = data['stages']
        for stage in stages:
            native = reconstruct(model, stage['plan'], stage['trace'])
            old = reconstruct(textual, stage['plan'], stage['trace'])
            self.assertEqual([j['candidate_count'] for j in native['jobs']], [j['candidate_count'] for j in old['jobs']])
            self.assertEqual(recommend(model, stage['plan'], native)['enabled_sites'], stage['next_plan']['enabled_sites'])
        final = reconstruct(model, stages[-1]['plan'], stages[-1]['trace'])
        self.assertEqual({j['status'] for j in final['jobs']}, {'unique'})
        missing = copy.deepcopy(catalog)
        next(s for s in missing if s['id'] == 'work.begin')['symbol'] = 'ABSENT_MARKER'
        with self.assertRaisesRegex(ValueError, 'exactly once'):
            build_model(raw, 'process_job', missing, model['build_id'], {})


if __name__ == '__main__':
    unittest.main()
