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
from onion.queues import build_queues, _references, _compatible, _expand, _key, CONTRACTS

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
        cls.operations = {o['id']: o for o in cls.graph['operations']}

    def ops(self, operation, function=None):
        return [o for o in self.operations.values() if o['operation'] == operation and
                (function is None or self.names[o['source']] == function)]

    def test_all_operations_native_source_evidence_and_cfg(self):
        self.assertTrue(self.result['ready'])
        self.assertEqual({o['operation'] for o in self.operations.values()}, set(CONTRACTS))
        for op in self.operations.values():
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

    def test_constant_array_zero_and_one_are_distinct_source_objects(self):
        producer = next(o for o in self.ops('enqueue', 'main') if o['queue_candidates'] and
                        o['queue_candidates'][0]['indices'] == ['0'])
        consumer = next(o for o in self.ops('dequeue', 'main') if o['queue_candidates'] and
                        o['queue_candidates'][0]['indices'] == ['1'])
        self.assertTrue(producer['queue_candidates'][0]['concrete'])
        self.assertFalse(_compatible(producer['queue_candidates'][0], consumer['queue_candidates'][0]))
        self.assertFalse(any(r['source'] == producer['id'] and r['target'] == consumer['id'] for r in self.graph['relations']))

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

    def test_response_field_override_keeps_original_and_written_destinations(self):
        response = self.ops('enqueue', 'override_response')[0]
        self.assertTrue(response['queue']['unknown'])
        self.assertTrue(response['queue']['write_evidence'])
        targets = [self.operations[r['target']] for r in self.graph['relations']
                   if r['kind'] == 'response_transfer' and r['source'] == response['id']]
        self.assertEqual({r['operation'] for r in targets}, {'dequeue', 'poll'})
        self.assertEqual(len(targets), 2)

    def test_producer_response_override_reaches_consumer_with_write_evidence(self):
        direct = self.ops('enqueue', 'producer_override')[0]
        forwarded = self.ops('enqueue', 'forward_element')[0]
        for submission in (direct, forwarded):
            writes = submission['element_response_writes']
            self.assertEqual(len(writes), 1)
            self.assertTrue(writes[0]['conditions'])
            self.assertEqual(self.names[writes[0]['source']], 'producer_override')
            self.assertTrue(writes[0]['range'])
            self.assertTrue(writes[0]['unknown'])
            self.assertEqual({r['base']['usr'] for r in writes[0]['queue_candidates']}, {'c:@producer_reply_B'})
            acquired = next(o for o in self.ops('dequeue', 'producer_response')
                            if _compatible(submission['queue_candidates'][0], o['queue_candidates'][0]))
            response_id = next(r['target'] for r in self.graph['relations']
                               if r['kind'] == 'response_destination' and r['source'] == acquired['id'])
            returns = [r for r in self.graph['relations'] if r['kind'] == 'response_transfer' and r['source'] == response_id]
            self.assertEqual({self.operations[r['target']]['queue_candidates'][0]['base']['usr'] for r in returns},
                             {'c:@producer_reply_A', 'c:@producer_reply_B'})
            changed = next(r for r in returns if self.operations[r['target']]['queue_candidates'][0]['base']['usr'] == 'c:@producer_reply_B')
            self.assertEqual(changed['resolution'], 'candidate')
            self.assertEqual(changed['response_write_evidence'][0]['id'], writes[0]['id'])
            self.assertEqual(changed['response_write_evidence'][0]['conditions'], writes[0]['conditions'])
            self.assertIn(writes[0]['range'], changed['evidence'])

    def test_array_pointer_field_writes_keep_array_and_constant_index_identity(self):
        submissions = self.ops('enqueue', 'array_pointer_writes')
        known = next(o for o in submissions if o['queue'].get('write_evidence'))
        writes = known['queue']['write_evidence']
        self.assertEqual(len(writes), 1)
        concrete = [r for r in known['queue_candidates'] if r['concrete']]
        self.assertEqual({r['base']['usr'] for r in concrete}, {'c:@array_reply_A'})
        targets = [self.operations[r['target']] for r in self.graph['relations']
                   if r['kind'] == 'transfers' and r['source'] == known['id']]
        self.assertEqual({o['queue_candidates'][0]['base']['usr'] for o in targets}, {'c:@array_reply_A'})
        unknown = [o for o in submissions if o is not known]
        self.assertEqual(len(unknown), 2)
        for row in unknown:
            self.assertNotIn('write_evidence', row['queue'])
            self.assertFalse(any(r['concrete'] for r in row['queue_candidates']))

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
        completion = self.ops('enqueue', 'rx_func')[0]
        wait = self.ops('dequeue', 'wait_free_rx_tti')[0]
        self.assertTrue(any(r['kind'] == 'transfers' and r['source'] == completion['id'] and r['target'] == wait['id']
                            for r in self.graph['relations']))
        self.assertFalse(completion['queue_candidates'][0]['concrete'])
        self.assertTrue(completion['queue_candidates'][0]['via'])

    def test_blocked_sites_and_incompatible_api_are_not_connected(self):
        blocked = [o for o in self.operations.values() if o['blocked']]
        self.assertTrue(blocked)
        self.assertFalse(any(r['source'] in {o['id'] for o in blocked} or r['target'] in {o['id'] for o in blocked}
                             for r in self.graph['relations']))
        incompatible = build_queues(self.manifest, self.extraction, ['incompatible'])['graphs'][0]
        self.assertEqual(incompatible['operations'], [])

    def test_targets_artifact_integrity_and_limits(self):
        ue = self.result['graphs'][1]
        self.assertFalse(set(self.operations) & {o['id'] for o in ue['operations']})
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

    def test_actual_extraction_gate_checks_sites_hashes_and_connections(self):
        from onion.oai_queues import verify_extraction
        from onion.build_inputs import digest
        manifest, extraction = copy.deepcopy(self.manifest), copy.deepcopy(self.extraction)
        manifest['profile'] = 'oai'
        manifest['context']['source'] = {'commit': 'fixture-only'}
        manifest['manifest_id'] = digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
        extraction['input_manifest_id'] = manifest['manifest_id']
        dequeue = self.ops('dequeue', 'L1_rx_thread')[0]
        process = next(p for p in self.graph['processing_calls'] if p.get('callee_name') == 'rx_func')
        completion = self.ops('enqueue', 'rx_func')[0]
        path = Path(dequeue['range']['expansion']['begin']['path'])
        relative = 'fixture/queues/' + path.name
        expected = {'revision': 'fixture-only', 'target': 'nr-softmodem',
                    'sources': [{'path': relative, 'sha256': file_record(path)['sha256']}], 'sites': [],
                    'required_relations': [{'kind': 'processes', 'source': 'pull', 'target': 'process'},
                                           {'kind': 'completion_candidate', 'source': 'process', 'target': 'completion'}]}
        for key, row, group in [('pull', dequeue, 'operations'), ('process', process, 'processing_calls'),
                                ('completion', completion, 'operations')]:
            span = row['range']['expansion']
            expected['sites'].append({'key': key, 'path': relative, 'function': self.names[row['source']], 'group': group,
                                       'start_offset': span['begin']['offset'], 'end_offset': span['end']['offset']})
        self.assertTrue(verify_extraction(manifest, extraction, expected)['full_extraction_verified'])
        expected['required_relations'][0]['kind'] = 'response_transfer'
        self.assertFalse(verify_extraction(manifest, extraction, expected)['full_extraction_verified'])
        expected['required_relations'][0]['kind'] = 'processes'
        expected['sites'][0]['end_offset'] += 1
        self.assertFalse(verify_extraction(manifest, extraction, expected)['full_extraction_verified'])
        expected['sites'][0]['end_offset'] -= 1
        expected['sources'][0]['sha256'] = 'sha256:' + '0' * 64
        self.assertFalse(verify_extraction(manifest, extraction, expected)['full_extraction_verified'])


class QueueValueTests(unittest.TestCase):
    def test_element_carries_producer_response_write_through_parameter_forwarding(self):
        element = {'shape': 'storage', 'scope': 'tu', 'usr': 'element', 'pointer': True}
        reply = {'shape': 'address', 'base': {'shape': 'storage', 'scope': 'tu', 'usr': 'reply_B', 'pointer': False}}
        target = {'shape': 'member', 'base': element, 'field_usr': 'elt.reponseFifo',
                  'field_name': 'reponseFifo', 'arrow': True, 'pointer': True}
        guard = [{'branch': 'then', 'condition': {'text': 'change'}}]
        function = {'id': 'producer', 'callback_writes': [
            {'id': 'init', 'queue_target': element, 'queue_value': {'shape': 'call', 'arguments': []},
             'range': {}, 'conditions': [], 'effect': 'write'},
            {'id': 'response', 'queue_target': target, 'queue_value': reply,
             'range': {'test': 'write'}, 'conditions': guard, 'effect': 'write'}]}
        expanded = _expand(element, function)
        writes = expanded['response_writes']
        self.assertEqual([w['id'] for w in writes], ['response'])
        self.assertEqual(writes[0]['conditions'], guard)
        self.assertEqual(writes[0]['queue_candidates'][0]['base']['usr'], 'reply_B')
        parameter = {'shape': 'storage', 'scope': 'tu', 'usr': 'arg', 'pointer': True, 'parameter_index': 0}
        forwarded = _expand(parameter, {'id': 'forward', 'callback_writes': []}, [expanded])
        self.assertEqual(forwarded['response_writes'], writes)

    def test_write_keys_keep_array_base_and_index_and_reject_unknown_bases(self):
        def member(name, index):
            return {'shape': 'member', 'field_usr': 'reply', 'field_name': 'reply', 'arrow': False, 'pointer': True,
                    'base': {'shape': 'index', 'base': {'shape': 'storage', 'scope': 'tu', 'usr': name}, 'index': index}}
        zero, one = {'shape': 'integer', 'literal': '0'}, {'shape': 'integer', 'literal': '1'}
        self.assertNotEqual(_key(member('a', zero)), _key(member('a', one)))
        self.assertNotEqual(_key(member('a', zero)), _key(member('b', zero)))
        dynamic = member('a', {'shape': 'opaque'})
        self.assertIsNone(_key(dynamic))
        computed = {**dynamic, 'base': {'shape': 'call'}}
        self.assertIsNone(_key(computed))
        f = {'id': 'f', 'callback_writes': [{'id': 'wrong', 'queue_target': dynamic, 'queue_value': {'shape': 'null'},
                                           'range': {}, 'conditions': [], 'effect': 'write'}]}
        self.assertNotIn('write_evidence', _expand(computed, f))

    def test_unknown_bases_match_field_usr_but_names_alone_never_match(self):
        def member(usr, base):
            return {'shape': 'member', 'field_usr': usr, 'field_name': 'fifo', 'arrow': True,
                    'base': {'shape': 'storage', 'scope': 'tu', 'usr': base, 'pointer': True}}
        a, b = _references(member('Owner.fifo', 'arg1'))[0], _references(member('Owner.fifo', 'arg2'))[0]
        self.assertTrue(_compatible(a, b))
        self.assertFalse(a['concrete'])
        c = _references(member('Different.fifo', 'arg2'))[0]
        self.assertFalse(_compatible(a, c))

    def test_pointer_field_indirection_keeps_final_family_and_original_path(self):
        def field(base, name, pointer=False):
            return {'shape': 'member', 'base': base, 'field_usr': name, 'field_name': name,
                    'arrow': True, 'pointer': pointer}
        root = {'shape': 'storage', 'scope': 'tu', 'usr': 'info', 'pointer': True}
        payload = field(field(root, 'Payload.gNB', True), 'Gnb.fifo')
        global_path = field(field(root, 'RC.gNB', True), 'Gnb.fifo')
        a, b = _references(payload)[0], _references(global_path)[0]
        self.assertEqual([f['usr'] for f in a['fields']], ['Gnb.fifo'])
        self.assertNotEqual(a['via']['fields'], b['via']['fields'])
        self.assertTrue(_compatible(a, b))
        self.assertFalse(a['concrete'])

    def test_cycle_is_bounded_and_unknown(self):
        p = {'shape': 'storage', 'scope': 'tu', 'usr': 'p', 'pointer': True}
        f = {'id': 'f', 'callback_writes': [{'id': 'w', 'queue_target': p, 'queue_value': p,
                                           'effect': 'write', 'range': {}, 'conditions': []}]}
        value = _expand(p, f)
        self.assertTrue(value['unknown'])
        self.assertEqual(value['choices'][0]['reason'], 'cyclic pointer storage')

    def test_address_of_pointer_storage_does_not_become_its_pointee(self):
        p = {'shape': 'storage', 'scope': 'tu', 'usr': 'p', 'pointer': True}
        obj = {'shape': 'storage', 'scope': 'tu', 'usr': 'queue', 'pointer': False}
        value = {'shape': 'address', 'base': p}
        function = {'id': 'f', 'callback_writes': [{'queue_target': p, 'queue_value': {'shape': 'address', 'base': obj}}]}
        expanded = _expand(value, function)
        self.assertEqual(expanded['base'], p)
        self.assertFalse(_references(expanded)[0]['concrete'])

    def test_contracts_do_not_equate_attempts_returns_or_elapsed_to_residence(self):
        self.assertIn('attempt', CONTRACTS['enqueue']['publication'])
        self.assertIn('aborted', CONTRACTS['enqueue']['success_condition'])
        self.assertIn('NULL', CONTRACTS['dequeue']['ownership'])
        self.assertIn('contention', CONTRACTS['poll']['ownership'])


class OaiQueueAuditTests(unittest.TestCase):
    def test_pinned_source_audit_is_not_full_acceptance_and_detects_changes(self):
        from hashlib import sha256
        from onion.oai_queues import audit_sources
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.c'
            data = b'void f() { pullNotifiedFIFO(q); }\n'
            path.write_bytes(data)
            text = 'pullNotifiedFIFO(q)'
            start = data.index(text.encode())
            expected = {'revision': 'pinned', 'sources': [{'path': 'source.c', 'sha256': 'sha256:' + sha256(data).hexdigest()}],
                        'sites': [{'path': 'source.c', 'key': 'pull', 'start_offset': start, 'end_offset': start + len(text),
                                   'line': 1, 'text': text}]}
            result = audit_sources(tmp, expected)
            self.assertTrue(result['source_audit_passed'])
            self.assertFalse(result['full_extraction_verified'])
            expected['sites'][0]['end_offset'] += 1
            with self.assertRaisesRegex(ValueError, 'position mismatch'):
                audit_sources(tmp, expected)
            expected['sites'][0]['end_offset'] -= 1
            path.write_bytes(data + b'/* changed */')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                audit_sources(tmp, expected)

    def test_expectations_pin_revision_route_contract_and_no_fixture_acceptance(self):
        from onion.oai_queues import EXPECTATIONS, verify_extraction
        expected = json.loads(EXPECTATIONS.read_text())
        self.assertEqual(expected['revision'], json.loads((ROOT / 'config/oai-2026.w07.json').read_text())['commit'])
        self.assertEqual(len(expected['sites']), 18)
        self.assertEqual(len(expected['sources']), 4)
        self.assertTrue(any(s['group'] == 'contract' for s in expected['sites']))
        self.assertEqual(len(expected['required_relations']), 6)
        with self.assertRaisesRegex(ValueError, 'pinned OAI input'):
            verify_extraction({'profile': 'generic'}, {}, expected)


if __name__ == '__main__':
    unittest.main()
