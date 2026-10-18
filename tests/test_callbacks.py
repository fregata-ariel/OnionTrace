"""Native T5 acceptance: registration, alternatives, inline and role contexts."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from onion.build_inputs import collect, query, digest, file_record
from onion.libtooling import extract_manifest
from onion.callbacks import build_callbacks

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    return subprocess.run([str(a) for a in args], text=True, capture_output=True, check=True, timeout=180)


class CallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='onion-callbacks-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.out = Path(cls.temp.name)
        cls.tool = Path(os.environ.get('ONION_EXTRACTOR', ROOT / 'build-libtooling/onion-extract'))
        if not cls.tool.is_file():
            run(['cmake', '-S', ROOT / 'tools/libtooling', '-B', cls.tool.parent, '-G', 'Ninja',
                 '-DLLVM_DIR=' + os.environ.get('LLVM_DIR', '/usr/lib/llvm-18/lib/cmake/llvm'),
                 '-DClang_DIR=' + os.environ.get('Clang_DIR', '/usr/lib/llvm-18/lib/cmake/clang')])
            run(['cmake', '--build', cls.tool.parent, '--parallel', '2'])
        build = cls.out / 'build'
        query(build)
        run(['cmake', '-S', ROOT / 'fixture/callbacks', '-B', build, '-G', 'Ninja',
             '-DCMAKE_C_COMPILER=' + os.environ.get('ONION_CLANG', 'clang-18')])
        cls.manifest = collect(build, ['nr-softmodem', 'nr-uesoftmodem', 'incompatible'])
        if not cls.manifest['ready']:
            raise AssertionError(cls.manifest)
        cls.extraction = extract_manifest(cls.manifest, cls.tool, cls.out / 'extraction')
        if not cls.extraction['ready']:
            raise AssertionError(cls.extraction)
        cls.result = build_callbacks(cls.manifest, cls.extraction, ['nr-softmodem', 'nr-uesoftmodem'])
        cls.gnb, cls.ue = cls.result['graphs']
        cls.names = {f['id']: f['name'] for f in cls.gnb['functions']}

    def test_registration_alternatives_null_and_unknown(self):
        regs = [r for r in self.gnb['registrations'] if r['protocol'] == 'actor']
        self.assertEqual(len(regs), 5)
        self.assertEqual(sorted(r['resolution'] for r in regs), ['candidate', 'candidate', 'null', 'resolved', 'unresolved'])
        self.assertTrue(any(len(r['candidates']) == 2 and not r['unknown'] for r in regs))
        self.assertTrue(any(len(r['candidates']) == 1 and r['nullable'] for r in regs))
        null = next(r for r in regs if r['resolution'] == 'null')
        self.assertFalse(any(e['kind'] == 'binds' and e['source'] == null['id'] for e in self.gnb['relations']))
        unknown = next(r for r in regs if r['resolution'] == 'unresolved')
        self.assertEqual(unknown['candidates'], [])
        self.assertTrue(unknown['reason'])

    def test_task_initializers_parameter_forwarding_and_unknown_writes(self):
        regs = [r for r in self.gnb['registrations'] if r['protocol'] == 'pool' and not r['blocked']]
        self.assertTrue(any(r['resolution'] == 'resolved' and self.names[r['candidates'][0]] == 'process' for r in regs))
        self.assertTrue(any(len(r['candidates']) == 2 for r in regs))
        self.assertTrue(any(r['resolution'] == 'null' for r in regs))
        local = next(r for r in regs if r['values'][0].get('unknown'))
        self.assertTrue(local['unknown'])
        self.assertEqual([self.names[c] for c in local['candidates']], ['process'])
        self.assertEqual(local['resolution'], 'unresolved')
        forwarded = next(r for r in regs if self.names[r['source']] == 'submit')
        self.assertEqual([self.names[c] for c in forwarded['candidates']], ['process'])
        self.assertFalse(forwarded['unknown'])
        self.assertTrue(forwarded['contexts'][0]['call_path'])

    def test_deferred_and_inline_contexts_keep_guards(self):
        invocations = self.gnb['invocations']
        actor = next(i for i in invocations if self.names[i['source']] == 'actor_thread')
        worker = next(i for i in invocations if self.names[i['source']] == 'worker_thread')
        inline = next(i for i in invocations if self.names[i['source']] == 'inline_actor')
        pool_inline = next(i for i in invocations if self.names[i['source']] == 'pushTpool')
        self.assertEqual(len({c['role_id'] for c in actor['contexts']}), 2)
        self.assertEqual(len({c['role_id'] for c in worker['contexts']}), 2)
        self.assertTrue(all(c['role_id'] == 'main/nr-softmodem' for c in inline['contexts']))
        self.assertTrue(any(g['branch'] == 'else' for g in inline['conditions']))
        self.assertTrue(any('len_thr == 0' in g.get('condition', {}).get('text', '') for g in pool_inline['conditions']))
        self.assertTrue(actor['conditions'])

    def test_same_function_in_multiple_roles_and_submitters(self):
        process = next(k for k, v in self.names.items() if v == 'process')
        callbacks = {r['id'] for r in self.gnb['registrations'] if process in r['candidates']}
        invocations = {r['source'] for r in self.gnb['relations'] if r['kind'] == 'invokes' and r['target'] in callbacks}
        owners = {self.names[i['source']] for i in self.gnb['invocations'] if i['id'] in invocations}
        self.assertTrue({'actor_thread', 'worker_thread', 'inline_actor', 'pushTpool'} <= owners)
        self.assertTrue(any(r['kind'] == 'submits' for r in self.gnb['relations']))

    def test_steal_and_protocol_associations_are_candidates(self):
        self.assertEqual({r['operation'] for r in self.gnb['retrievals']}, {'try_pop_not_q', 'pop_not_q'})
        self.assertTrue(any(r['conditions'] for r in self.gnb['retrievals']))
        links = [r for r in self.gnb['relations'] if r['kind'] == 'invokes']
        self.assertTrue(all(r['resolution'] == 'candidate' and r['reason'] and len(r['evidence']) == 2 for r in links))
        self.assertEqual(self.gnb['scheduling']['worker_affinity'], 'not inferred')

    def test_target_scopes_do_not_mix(self):
        gnb = {c for r in self.gnb['registrations'] for c in r['candidates']}
        ue = {c for r in self.ue['registrations'] for c in r['candidates']}
        self.assertTrue(ue)
        self.assertFalse(gnb & ue)
        known = {r['id'] for r in self.ue['registrations']}
        self.assertTrue(all(r['target'] in known or r['kind'] == 'binds' or
                            r['target'] is None and r['resolution'] == 'unresolved'
                            for r in self.ue['relations']))

    def test_unreachable_sites_and_source_evidence(self):
        blocked = [r for r in self.gnb['registrations'] if r['blocked']]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]['resolution'], 'unresolved')
        for row in self.gnb['registrations'] + self.gnb['invocations'] + self.gnb['submissions']:
            self.assertTrue(Path(row['range']['expansion']['begin']['path']).is_file())
        self.assertTrue(self.result['ready'])
        self.assertEqual(self.result, build_callbacks(self.manifest, self.extraction, ['nr-softmodem', 'nr-uesoftmodem']))

    def test_incompatible_names_and_field_types_are_not_protocols(self):
        graph = build_callbacks(self.manifest, self.extraction, ['incompatible'])['graphs'][0]
        self.assertEqual(graph['registrations'], [])
        self.assertEqual(graph['submissions'], [])
        self.assertEqual(graph['invocations'], [])

    def test_missing_tu_and_traversal_bounds_stay_incomplete(self):
        extraction = copy.deepcopy(self.extraction)
        runtime = next(r for r in extraction['entries'] if r['source'].endswith('runtime.c'))
        runtime.update(status='failed', reason='intentional')
        result = build_callbacks(self.manifest, extraction)
        self.assertFalse(result['ready'])
        self.assertTrue(any(g['failures'] for g in result['graphs']))
        limited = build_callbacks(self.manifest, self.extraction, max_contexts=1)
        self.assertFalse(limited['ready'])
        self.assertTrue(any('limit' in f['reason'] for g in limited['graphs'] for f in g['frontiers']))

    def test_requires_t5_semantic_extraction_and_intact_artifacts(self):
        extraction = copy.deepcopy(self.extraction)
        entry = next(r for r in extraction['entries'] if r['status'] == 'extracted')
        raw = json.loads(Path(entry['artifact']['path']).read_text())
        for f in raw['functions']:
            f.pop('callback_writes')
        path = self.out / 'old.json'
        path.write_text(json.dumps(raw))
        entry['artifact'] = file_record(path)
        with self.assertRaisesRegex(ValueError, 'T5 requires re-extraction'):
            build_callbacks(self.manifest, extraction)
        entry['artifact']['sha256'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            build_callbacks(self.manifest, extraction)

    def test_cli_failure_and_success(self):
        manifest = self.out / 'manifest.json'
        extraction = self.out / 'extraction.json'
        manifest.write_text(json.dumps(self.manifest))
        extraction.write_text(json.dumps(self.extraction))
        output = self.out / 'cli'
        command = [os.sys.executable, '-m', 'onion.callbacks', '--manifest', str(manifest),
                   '--extraction', str(extraction), '--out', str(output)]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads((output / 'callbacks.json').read_text())['ready'])
        extraction.write_text('{}')
        self.assertEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 1)
        self.assertTrue(json.loads((output / 'failures.json').read_text()))

    def test_full_gate_matches_actual_offsets_hashes_and_target_set(self):
        from onion.oai_callbacks import verify_extraction
        # Exercise the real native fixture through the gate with fixture-only
        # expectations. This never represents an OAI acceptance result.
        manifest, extraction = copy.deepcopy(self.manifest), copy.deepcopy(self.extraction)
        manifest['profile'] = 'oai'
        manifest['context']['source'] = {'commit': 'fixture-only'}
        manifest['manifest_id'] = digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
        extraction['input_manifest_id'] = manifest['manifest_id']
        registration = next(r for r in self.gnb['registrations'] if r['protocol'] == 'pool' and not r['blocked'])
        span = registration['range']['expansion']
        path = Path(span['begin']['path'])
        relative = 'fixture/callbacks/' + path.name
        expected = {'revision': 'fixture-only', 'sources': [{'path': relative, 'sha256': file_record(path)['sha256']}],
                    'sites': [{'key': 'pool_encoding', 'path': relative, 'function': 'main',
                               'start_offset': span['begin']['offset'], 'end_offset': span['end']['offset']}]}
        self.assertTrue(verify_extraction(manifest, extraction, expected)['full_extraction_verified'])
        expected['sites'][0]['end_offset'] += 1
        result = verify_extraction(manifest, extraction, expected)
        self.assertFalse(result['full_extraction_verified'])
        self.assertEqual(len(result['failures']), 1)
        expected['sites'][0]['end_offset'] -= 1
        expected['sources'][0]['sha256'] = 'sha256:' + '0' * 64
        self.assertFalse(verify_extraction(manifest, extraction, expected)['full_extraction_verified'])


class OaiCallbackAuditTests(unittest.TestCase):
    def test_pinned_source_audit_and_corruption(self):
        from hashlib import sha256
        from onion.oai_callbacks import audit_sources
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.c'
            data = b'void f() { task.func(task.args); }\n'
            path.write_bytes(data)
            text = 'task.func(task.args)'
            start = data.index(text.encode())
            expected = {'revision': 'pinned', 'sources': [{'path': 'source.c', 'sha256': 'sha256:' + sha256(data).hexdigest()}],
                        'sites': [{'path': 'source.c', 'key': 'pool_inline', 'start_offset': start,
                                   'end_offset': start + len(text), 'line': 1, 'text': text}]}
            result = audit_sources(tmp, expected)
            self.assertTrue(result['source_audit_passed'])
            self.assertFalse(result['full_extraction_verified'])
            bad = copy.deepcopy(expected)
            bad['sites'][0]['start_offset'] += 1
            with self.assertRaisesRegex(ValueError, 'position mismatch'):
                audit_sources(tmp, bad)
            path.write_bytes(data + b'/* changed */')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                audit_sources(tmp, expected)

    def test_source_expectations_are_separate_from_full_extraction(self):
        from onion.oai_callbacks import EXPECTATIONS, verify_extraction
        expected = json.loads(EXPECTATIONS.read_text())
        self.assertEqual(expected['revision'], json.loads((ROOT / 'config/oai-2026.w07.json').read_text())['commit'])
        self.assertEqual(len(expected['sites']), 13)
        self.assertTrue(all(site['function'] for site in expected['sites']))
        with self.assertRaisesRegex(ValueError, 'pinned OAI input'):
            verify_extraction({'profile': 'generic'}, {}, expected)


if __name__ == '__main__':
    unittest.main()
