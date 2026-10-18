"""Native T4 acceptance: complete source paths, explicit unresolved frontiers."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from onion.build_inputs import collect, query
from onion.libtooling import extract_manifest
from onion.threads import build_threads

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    return subprocess.run([str(a) for a in args], text=True, capture_output=True, check=True, timeout=180)


class ThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='onion-threads-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.out = Path(cls.temp.name)
        cls.cc = os.environ.get('ONION_CLANG', 'clang-18')
        cls.tool = Path(os.environ.get('ONION_EXTRACTOR', ROOT / 'build-libtooling/onion-extract'))
        if not cls.tool.is_file():
            run(['cmake', '-S', ROOT / 'tools/libtooling', '-B', cls.tool.parent, '-G', 'Ninja',
                 '-DLLVM_DIR=' + os.environ.get('LLVM_DIR', '/usr/lib/llvm-18/lib/cmake/llvm'),
                 '-DClang_DIR=' + os.environ.get('Clang_DIR', '/usr/lib/llvm-18/lib/cmake/clang')])
            run(['cmake', '--build', cls.tool.parent, '--parallel', '2'])
        build = cls.out / 'build'
        query(build)
        run(['cmake', '-S', ROOT / 'fixture/threads', '-B', build, '-G', 'Ninja',
             '-DCMAKE_C_COMPILER=' + cls.cc])
        cls.manifest = collect(build, ['nr-softmodem', 'nr-uesoftmodem', 'ambiguous', 'recursive'])
        if not cls.manifest['ready']:
            raise AssertionError(cls.manifest)
        cls.extraction = extract_manifest(cls.manifest, cls.tool, cls.out / 'extraction')
        if not cls.extraction['ready']:
            raise AssertionError(cls.extraction)
        cls.result = build_threads(cls.manifest, cls.extraction, ['nr-softmodem', 'nr-uesoftmodem'])
        cls.gnb, cls.ue = cls.result['graphs']

    def test_direct_wrapper_and_repeated_start_sites(self):
        self.assertTrue(self.result['ready'])
        spawns = self.gnb['spawns']
        self.assertEqual(len(spawns), 8)
        self.assertEqual(sum(s['resolution'] == 'resolved' for s in spawns), 5)
        self.assertEqual(len({s['id'] for s in spawns}), len(spawns))
        names = {f['id']: f['name'] for f in self.gnb['functions']}
        worker = [s for s in spawns if s['start_id'] and names[s['start_id']] == 'worker']
        self.assertEqual(len(worker), 4)
        wrapped = next(s for s in worker if len(s['call_path']) == 3)
        calls = {c['id']: c for f in self.gnb['functions'] for c in f['calls']}
        self.assertEqual([calls[c]['callee_name'] for c in wrapped['call_path']], ['nested', 'launch', 'pthread_create'])
        self.assertEqual(wrapped['argument']['text'], 'argv')
        self.assertEqual(wrapped['entry_expression']['value_kind'], 'function')
        self.assertIn('forwarded_at', wrapped['entry_expression'])
        self.assertEqual({r['kind'] for r in self.gnb['relations']}, {'calls', 'spawns', 'starts'})
        # threadCreate's implementation must not generate a duplicate spawn.
        self.assertEqual(sum(s['api'] == 'threadCreate' for s in spawns), 2)

    def test_pruned_null_macro_successor_does_not_block_reachable_thread_creation(self):
        source = self.out / 'pruned-macro'
        source.mkdir()
        (source / 'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.20)\nproject(pruned C)\n'
                                            'set(CMAKE_EXPORT_COMPILE_COMMANDS ON)\n'
                                            'add_executable(pruned main.c)\n')
        (source / 'main.c').write_text('''
typedef unsigned long pthread_t;
extern int pthread_create(pthread_t *, const void *, void *(*)(void *), void *);
static void *worker(void *p) { return p; }
int main(void) {
  pthread_t thread;
  do { (void)0; } while (0);
  if (0) pthread_create(&thread, 0, worker, 0);
  return pthread_create(&thread, 0, worker, 0);
}
''')
        build = source / 'build'
        query(build)
        run(['cmake', '-S', source, '-B', build, '-G', 'Ninja', '-DCMAKE_C_COMPILER=' + self.cc])
        manifest = collect(build, ['pruned'])
        extraction = extract_manifest(manifest, self.tool, source / 'extraction')
        graph = build_threads(manifest, extraction)['graphs'][0]
        self.assertEqual(len(graph['spawns']), 1)
        self.assertEqual(graph['spawns'][0]['resolution'], 'resolved')
        main = next(f for f in graph['functions'] if f['name'] == 'main')
        self.assertTrue(any(s['kind'] == 'unreachable' and s['target'] is None
                            for b in main['blocks'] for s in b['successors']))
        self.assertFalse(any(u['affects_completeness'] for u in main['uncertainties']))
        self.assertTrue(any('CFG placement' in f['reason'] for f in graph['frontiers']))

    def test_conditions_loops_and_source_evidence(self):
        guards = [g for s in self.gnb['spawns'] for g in s['conditions']]
        self.assertTrue({'then', 'else', 'loop_body'} <= {g['branch'] for g in guards})
        for spawn in self.gnb['spawns'] + self.ue['spawns']:
            self.assertTrue(Path(spawn['range']['expansion']['begin']['path']).is_file())
            self.assertGreater(spawn['range']['expansion']['begin']['line'], 0)
        self.assertTrue(all(any(g['branch'] == 'loop_body' for g in s['conditions']) for s in self.ue['spawns']))
        # Pruned if(0) remains a call frontier, never a resolved spawn.
        self.assertTrue(any('CFG placement' in f['reason'] for f in self.gnb['frontiers']))

    def test_unknown_candidates_and_mutated_parameter_stay_unresolved(self):
        unresolved = [s for s in self.gnb['spawns'] if s['resolution'] == 'unresolved']
        self.assertEqual(len(unresolved), 3)
        self.assertTrue(all(s['start_id'] is None and s['reason'] for s in unresolved))
        self.assertEqual(sorted(len(s['candidates']) for s in unresolved), [0, 0, 2])
        starts = [r for r in self.gnb['relations'] if r['kind'] == 'starts' and r['resolution'] == 'unresolved']
        self.assertTrue(all(r['target'] is None for r in starts))
        self.assertTrue(any('indirect call' in f['reason'] for f in self.gnb['frontiers']))

    def test_recursion_and_incompatible_api_prototype(self):
        result = build_threads(self.manifest, self.extraction, ['recursive'])
        graph = result['graphs'][0]
        self.assertFalse(result['ready'])
        self.assertFalse(graph['traversal_complete'])
        self.assertTrue(any('recursion' in f['reason'] for f in graph['frontiers']))
        self.assertFalse(graph['spawns'])
        spelled_like_api = next(c for f in graph['functions'] for c in f['calls'] if c['callee_name'] == 'threadCreate')
        self.assertIsNone(spelled_like_api['thread_api'])
        checks = [c for f in graph['functions'] for c in f['calls'] if c['callee_name'] == 'check']
        self.assertEqual({g['branch'] for c in checks for g in c['conditions']}, {'then', 'rhs_if_true'})

    def test_target_and_internal_linkage_scopes_are_isolated(self):
        self.assertEqual(len(self.ue['spawns']), 2)
        locals_ = []
        for graph in (self.gnb, self.ue):
            local = next(f for f in graph['functions'] if f['name'] == 'local')
            self.assertTrue(any(s['start_id'] == local['id'] for s in graph['spawns']))
            locals_.append(local['id'])
        self.assertNotEqual(*locals_)
        self.assertNotEqual(self.gnb['root_id'], self.ue['root_id'])
        ambiguous = build_threads(self.manifest, self.extraction, ['ambiguous'])['graphs'][0]
        edge = next(r for r in ambiguous['relations'] if r['kind'] == 'calls' and r['candidates'])
        self.assertEqual(len(edge['candidates']), 2)
        self.assertIsNone(edge['target'])
        self.assertIn('variants', edge['reason'])
        self.assertEqual(ambiguous['spawns'], [])

    def test_missing_tu_does_not_jump_over_wrapper(self):
        extraction = copy.deepcopy(self.extraction)
        helper = next(r for r in extraction['entries'] if r['source'].endswith('helpers.c'))
        helper.update(status='failed', reason='intentional extraction failure')
        result = build_threads(self.manifest, extraction, ['nr-softmodem'])
        self.assertFalse(result['ready'])
        graph = result['graphs'][0]
        calls = {c['id']: c for f in graph['functions'] for c in f['calls']}
        frontier = next(f for f in graph['frontiers'] if calls[f['call_id']]['callee_name'] == 'nested')
        self.assertEqual(len(frontier['call_path']), 1)
        self.assertIn('unavailable', frontier['reason'])
        self.assertFalse(any(len(s['call_path']) > 1 for s in graph['spawns']))
        self.assertTrue(graph['failures'])
        omitted = copy.deepcopy(self.extraction)
        omitted['entries'].remove(next(r for r in omitted['entries'] if r['source'].endswith('helpers.c')))
        self.assertFalse(build_threads(self.manifest, omitted, ['nr-softmodem'])['ready'])

    def test_artifact_integrity_and_expansion_limits(self):
        extraction = copy.deepcopy(self.extraction)
        next(r for r in extraction['entries'] if r['status'] == 'extracted')['artifact']['sha256'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            build_threads(self.manifest, extraction)
        limited = build_threads(self.manifest, self.extraction, ['nr-softmodem'], max_contexts=1)
        self.assertFalse(limited['ready'])
        self.assertTrue(any('limit' in f['reason'] for f in limited['graphs'][0]['frontiers']))

    def test_cli_retains_failures_and_returns_nonzero(self):
        path = self.out / 'manifest.json'
        path.write_text(json.dumps(self.manifest))
        extraction = self.out / 'extraction.json'
        extraction.write_text(json.dumps(self.extraction))
        output = self.out / 'cli'
        command = [os.sys.executable, '-m', 'onion.threads', '--manifest', str(path),
                   '--extraction', str(extraction), '--target', 'nr-uesoftmodem', '--out', str(output)]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads((output / 'threads.json').read_text())['ready'])
        extraction.write_text('{}')
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 1)
        self.assertFalse(json.loads((output / 'threads.json').read_text())['ready'])
        self.assertTrue(json.loads((output / 'failures.json').read_text()))

    def test_oai_gate_checks_selected_chain_while_retaining_other_frontiers(self):
        from onion.oai_threads import verify_paths
        from onion.build_inputs import digest
        manifest = copy.deepcopy(self.manifest)
        manifest.update(profile='oai')
        manifest['context']['source']['commit'] = 'synthetic-test-revision'
        manifest['manifest_id'] = digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
        graph = copy.deepcopy(self.result)
        graph.update(input_manifest_id=manifest['manifest_id'], ready=False)
        target = next(g for g in graph['graphs'] if g['target'] == 'nr-uesoftmodem')
        target['traversal_complete'] = False  # Unrelated bounded frontier.
        functions = {f['id']: f for f in target['functions']}
        spawn = next(s for s in target['spawns'] if functions[s['start_id']]['name'] == 'worker')
        calls = {c['id']: (f, c) for f in target['functions'] for c in f['calls']}
        steps, sources = [], {}
        for key in spawn['call_path']:
            function, call = calls[key]
            span = call['range']['expansion']
            relative = Path(span['begin']['path']).relative_to(ROOT).as_posix()
            row = next(r for r in manifest['entries'] if r.get('cache_key') == function['_compilation_id'])
            record = next(d for d in row['dependencies'] if d['path'] == span['begin']['path'])
            sources[relative] = {'path': relative, 'sha256': record['sha256']}
            steps.append({'caller': function['name'], 'callee': call['callee_name'],
                          'site': {'path': relative, 'start_offset': span['begin']['offset'],
                                   'end_offset': span['end']['offset']}})
        expected = {'revision': 'synthetic-test-revision', 'sources': list(sources.values()),
                    'paths': [{'target': target['target'], 'start': 'worker', 'calls': steps}]}
        accepted = verify_paths(manifest, graph, expected)
        self.assertTrue(accepted['full_extraction_verified'])
        self.assertFalse(accepted['traversal_complete'])
        edge = next(r for r in target['relations'] if r['id'] == spawn['call_path'][0])
        edge.update(target=None, resolution='unresolved')
        with self.assertRaisesRegex(ValueError, 'path absent'):
            verify_paths(manifest, graph, expected)

class OAIBoundaryTests(unittest.TestCase):
    def test_source_audit_never_claims_full_extraction_and_rejects_changed_sources(self):
        from hashlib import sha256
        from onion.oai_threads import audit_sources
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.c'
            data = b'int main(void) { return 0; }\n'
            path.write_bytes(data)
            expected = {'revision': 'test-revision', 'sources': [
                {'path': 'source.c', 'sha256': 'sha256:' + sha256(data).hexdigest()}], 'paths': [],
                'api_forwarding': {'path': 'source.c', 'start_offset': 17, 'end_offset': 25,
                                   'line': 1, 'text': 'return 0'}}
            result = audit_sources(directory, expected)
            self.assertTrue(result['source_audit_passed'])
            self.assertFalse(result['full_extraction_verified'])
            path.write_bytes(data + b'/* changed */')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                audit_sources(directory, expected)

    def test_fixture_cannot_pass_full_oai_gate(self):
        from onion.oai_threads import verify_paths
        expected = json.loads((ROOT / 'config/oai-thread-paths.json').read_text())
        with self.assertRaisesRegex(ValueError, 'pinned OAI artifacts'):
            verify_paths({'profile': 'generic', 'ready': True}, {'ready': True}, expected)


if __name__ == '__main__':
    unittest.main()
