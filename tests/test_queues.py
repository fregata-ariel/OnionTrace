"""T6 native extraction acceptance and conservative alias regressions."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from onion.build_inputs import collect, query, file_record
from onion.libtooling import extract_manifest
from onion.queues import build_queues, _references, _compatible, _expand, CONTRACTS

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    return subprocess.run([str(a) for a in args], text=True, capture_output=True, check=True, timeout=180)


class QueueNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='onion-queues-')
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
        run(['cmake', '-S', ROOT / 'fixture/queues', '-B', build, '-G', 'Ninja',
             '-DCMAKE_C_COMPILER=' + os.environ.get('ONION_CLANG', 'clang-18')])
        cls.manifest = collect(build, ['nr-softmodem', 'nr-uesoftmodem', 'incompatible'])
        cls.extraction = extract_manifest(cls.manifest, tool, cls.out / 'extraction')
        if not cls.manifest['ready'] or not cls.extraction['ready']:
            raise AssertionError((cls.manifest, cls.extraction))
        cls.result = build_queues(cls.manifest, cls.extraction, ['nr-softmodem', 'nr-uesoftmodem'])
        cls.graph = cls.result['graphs'][0]
        cls.names = {f['id']: f['name'] for f in cls.graph['functions']}
        cls.ops = {o['id']: o for o in cls.graph['operations']}

    def ops(self, operation, function=None):
        return [o for o in self.ops.values() if o['operation'] == operation and
                (function is None or self.names[o['source']] == function)]

    def test_all_operations_native_source_evidence_and_cfg(self):
        self.assertTrue(self.result['ready'])
        self.assertEqual({o['operation'] for o in self.ops.values()}, set(CONTRACTS))
        for op in self.ops.values():
            self.assertTrue(op['range']['expansion']['begin']['path'])
            self.assertIsNone(op['runtime_instance'])
            if not op['blocked']:
                self.assertTrue(op['block_id'])
        self.assertTrue(all(r['resolution'] == 'candidate' and r['evidence'] and r['reason']
                            for r in self.graph['relations']))
        self.assertEqual(self.result, build_queues(self.manifest, self.extraction, ['nr-softmodem', 'nr-uesoftmodem']))

    def test_same_field_on_distinct_objects_never_merges(self):
        submits = self.ops('enqueue', 'submit')
        self.assertEqual(len(submits), 2)
        first, second = [o['queue_candidates'][0] for o in submits]
        self.assertNotEqual(first['base'], second['base'])
        self.assertEqual(first['fields'], second['fields'])
        self.assertFalse(_compatible(first, second))
        # The explicit second.fifo retrieval cannot receive first.fifo's submit.
        pull = next(o for o in self.ops('dequeue', 'main') if o['queue_candidates'][0]['fields'][-1]['name'] == 'fifo')
        sources = {r['source'] for r in self.graph['relations'] if r['kind'] == 'transfers' and r['target'] == pull['id']}
        for submit in submits:
            self.assertEqual(submit['id'] in sources, _compatible(submit['queue_candidates'][0], pull['queue_candidates'][0]))

    def test_argument_forwarding_and_thread_object_bindings(self):
        submits = self.ops('enqueue', 'submit')
        actors = self.ops('dequeue', 'actor_thread')
        self.assertEqual(len(actors), 2)
        self.assertEqual(len({a['context']['role_id'] for a in actors}), 2)
        for actor in actors:
            compatible = [s for s in submits if _compatible(s['queue_candidates'][0], actor['queue_candidates'][0])]
            self.assertEqual(len(compatible), 1)
            self.assertTrue(actor['queue_candidates'][0]['concrete'])
            self.assertTrue(actor['context']['role_id'].startswith('thread/'))
        self.assertTrue(all(o['context']['call_path'] for o in submits))

    def test_response_metadata_and_callback_links_remain_candidates(self):
        relations = self.graph['relations']
        self.assertTrue(any(r['kind'] == 'returns_to' for r in relations))
        self.assertTrue(any(r['kind'] == 'response_transfer' for r in relations))
        self.assertTrue(any(r['kind'] == 'processes' for r in relations))
        constructors = self.ops('create', 'submit')
        self.assertTrue(all(o['callback_registration_ids'] for o in constructors))
        registrations = {r['id']: r for r in self.graph['callback_registrations']}
        self.assertTrue(all(registrations[r]['candidates'] for o in constructors for r in o['callback_registration_ids']))
        for response in self.ops('enqueue', 'actor_thread'):
            self.assertTrue(response['conditions'])
            destinations = [r['target'] for r in relations if r['kind'] == 'response_transfer' and r['source'] == response['id']]
            self.assertEqual(len(destinations), 1)

    def test_opaque_aliases_and_mutated_parameter_keep_unknowns(self):
        opaque = self.ops('enqueue', 'opaque')
        self.assertEqual(len(opaque), 2)
        self.assertTrue(any(o['resolution'] == 'unresolved' and o['reason'] for o in opaque))
        known = next(o for o in opaque if o['queue_candidates'])
        self.assertTrue(known['queue']['unknown'])
        self.assertTrue(any(w['effect'] == 'address_escape' for w in known['queue']['write_evidence']))
        modified = self.ops('enqueue', 'modified')[0]
        self.assertTrue(modified['queue']['unknown'])
        self.assertEqual(len({json.dumps(r['base'], sort_keys=True) for r in modified['queue_candidates']}), 2)

    def test_gnb_null_callback_direct_processing_and_new_completion(self):
        registrations = {r['id']: r for r in self.graph['callback_registrations']}
        for op in self.ops('create', 'rx_func'):
            self.assertTrue(all(registrations[r]['nullable'] and not registrations[r]['candidates']
                                for r in op['callback_registration_ids']))
        process = next(p for p in self.graph['processing_calls'] if p.get('callee_name') == 'rx_func')
        pull = self.ops('dequeue', 'L1_rx_thread')[0]
        self.assertTrue(any(r['kind'] == 'processes' and r['source'] == pull['id'] and r['target'] == process['id']
                            for r in self.graph['relations']))
        self.assertTrue(any(r['kind'] == 'completion_candidate' and r['source'] == process['id']
                            for r in self.graph['relations']))

    def test_blocked_sites_and_incompatible_api_are_not_connected(self):
        blocked = [o for o in self.ops.values() if o['blocked']]
        self.assertTrue(blocked)
        self.assertFalse(any(r['source'] in {o['id'] for o in blocked} or r['target'] in {o['id'] for o in blocked}
                             for r in self.graph['relations']))
        incompatible = build_queues(self.manifest, self.extraction, ['incompatible'])['graphs'][0]
        self.assertEqual(incompatible['operations'], [])

    def test_targets_artifact_integrity_and_limits(self):
        ue = self.result['graphs'][1]
        self.assertFalse(set(self.ops) & {o['id'] for o in ue['operations']})
        self.assertFalse(build_queues(self.manifest, self.extraction, max_contexts=1)['ready'])
        broken = copy.deepcopy(self.extraction)
        broken['entries'][0]['status'] = 'failed'
        broken['entries'][0]['reason'] = 'intentional TU failure'
        self.assertFalse(build_queues(self.manifest, broken)['ready'])
        broken = copy.deepcopy(self.extraction)
        row = next(e for e in broken['entries'] if e['status'] == 'extracted')
        raw = json.loads(Path(row['artifact']['path']).read_text())
        for f in raw['functions']:
            f.pop('queue_semantics_version')
        path = self.out / 'old.json'
        path.write_text(json.dumps(raw))
        row['artifact'] = file_record(path)
        with self.assertRaisesRegex(ValueError, 'T6 requires re-extraction'):
            build_queues(self.manifest, broken)
        row['artifact']['sha256'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            build_queues(self.manifest, broken)

    def test_cli_success_and_failure(self):
        manifest, extraction = self.out / 'manifest.json', self.out / 'extraction.json'
        manifest.write_text(json.dumps(self.manifest))
        extraction.write_text(json.dumps(self.extraction))
        command = [os.sys.executable, '-m', 'onion.queues', '--manifest', str(manifest), '--extraction', str(extraction),
                   '--target', 'nr-softmodem', '--out', str(self.out / 'cli')]
        self.assertEqual(run(command).returncode, 0)
        self.assertTrue(json.loads((self.out / 'cli/queues.json').read_text())['ready'])
        extraction.write_text('{}')
        self.assertEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 1)
        self.assertTrue(json.loads((self.out / 'cli/failures.json').read_text()))


class QueueValueTests(unittest.TestCase):
    def test_unknown_bases_match_field_usr_but_names_alone_never_match(self):
        def member(usr, base):
            return {'shape': 'member', 'field_usr': usr, 'field_name': 'fifo', 'arrow': True,
                    'base': {'shape': 'storage', 'scope': 'tu', 'usr': base, 'pointer': True}}
        a, b = _references(member('Owner.fifo', 'arg1'))[0], _references(member('Owner.fifo', 'arg2'))[0]
        self.assertTrue(_compatible(a, b))
        self.assertFalse(a['concrete'])
        c = _references(member('Different.fifo', 'arg2'))[0]
        self.assertFalse(_compatible(a, c))

    def test_cycle_is_bounded_and_unknown(self):
        p = {'shape': 'storage', 'scope': 'tu', 'usr': 'p', 'pointer': True}
        f = {'id': 'f', 'callback_writes': [{'id': 'w', 'queue_target': p, 'queue_value': p,
                                           'effect': 'write', 'range': {}, 'conditions': []}]}
        value = _expand(p, f)
        self.assertTrue(value['unknown'])
        self.assertEqual(value['choices'][0]['reason'], 'cyclic pointer storage')

    def test_contracts_do_not_equate_attempts_returns_or_elapsed_to_residence(self):
        self.assertIn('attempt', CONTRACTS['enqueue']['publication'])
        self.assertIn('aborted', CONTRACTS['enqueue']['success_condition'])
        self.assertIn('NULL', CONTRACTS['dequeue']['ownership'])
        self.assertIn('contention', CONTRACTS['poll']['ownership'])


if __name__ == '__main__':
    unittest.main()
