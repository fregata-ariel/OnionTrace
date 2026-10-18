"""T7 native T2/T3 -> T4/T5/T6 -> T1 -> Core acceptance (no skips)."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from onion.build_inputs import collect, query
from onion.execution import validate_structure
from onion.integration import (connect, open_region, initial_plan, reconstruct_region,
    recommend_region, accumulate_regions)
from onion.libtooling import extract_manifest
from test_model_integration import sites, trace
from test_queues import run, ROOT


class ModelNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='onion-model-')
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
        run(['cmake', '-S', ROOT / 'fixture/model-integration', '-B', build, '-G', 'Ninja',
             '-DCMAKE_C_COMPILER=' + os.environ.get('ONION_CLANG', 'clang-18')])
        run(['cmake', '--build', build])
        cls.manifest = collect(build, ['integration_fixture', 'integration_other'])
        cls.extraction = extract_manifest(cls.manifest, tool, cls.out / 'extraction')
        if not cls.manifest['ready'] or not cls.extraction['ready']:
            raise AssertionError((cls.manifest, cls.extraction))
        cls.workspace = connect(cls.manifest, cls.extraction, 'integration_fixture')

    def ref(self, category, name='process'):
        w = self.workspace
        names = {f['id']: f['name'] for raw in w['extractions'].values() for f in raw['functions']}
        return next(r for r in w['references'] if r['category'] == category and
            names[w['functions'][r['function_id']]['source_function_id']] == name)

    def test_thread_and_queue_select_real_extracted_cfg_and_keep_typed_boundaries(self):
        w = self.workspace
        validate_structure(w['structure'])
        self.assertTrue({'calls', 'spawns', 'starts', 'submits', 'dequeues', 'binds'} <=
                        {r['kind'] for r in w['structure']['relations']})
        role = next(e['id'] for e in w['structure']['entities'] if e['kind'] == 'thread_role')
        thread = next(r for r in w['references'] if r['origin_id'] == role and r['category'] == 'synchronous_descendant'
                      and w['functions'][r['function_id']]['source_function_id'].endswith('@F@process'))
        region = open_region(w, thread['id'], sites())
        plan = initial_plan(region)
        first = reconstruct_region(region, plan, trace(region, plan))
        self.assertEqual(first['analysis']['status'], 'ambiguous')
        self.assertEqual(first['analysis']['jobs'][0]['candidate_count'], 3)
        saved = copy.deepcopy(first)
        next_plan = recommend_region(region, plan, first)
        chosen = next_plan['rationale']['selected_sites'][0]
        later = reconstruct_region(region, next_plan, trace(region, next_plan, ['work.begin', chosen, 'work.end'], 'next'))
        self.assertEqual(later['analysis']['status'], 'unique')
        self.assertEqual(accumulate_regions([first, later])['runs'][0], saved)
        queue = self.ref('queue_processing_candidate')
        selected = open_region(w, queue['id'], sites())
        queue_plan = initial_plan(selected, ['work.begin', 'work.end', 'left', 'right'])
        result = reconstruct_region(selected, queue_plan,
            trace(selected, queue_plan, ['work.begin', 'left', 'work.end']))
        self.assertEqual(result['analysis']['status'], 'incomplete')
        self.assertFalse(recommend_region(selected, queue_plan, result)['rationale']['selected_sites'])
        self.assertTrue(selected['constraints'])
        self.assertEqual(selected['function_id'], region['function_id'])
        self.assertEqual({e['source'] for e in region['model']['edges']} - {n['id'] for n in region['model']['nodes']}, set())

    def test_child_interval_build_isolation_and_serialization(self):
        ref = self.ref('callee')
        region = open_region(self.workspace, ref['id'], sites())
        child = open_region(self.workspace, ref['id'], sites(True), anchors=('inner.begin', 'inner.end'), parent=region)
        self.assertEqual(child['parent_region_id'], region['id'])
        self.assertLess(len(child['model']['nodes']), len(region['model']['nodes']))
        other = connect(self.manifest, self.extraction, 'integration_other')
        self.assertNotEqual(other['build_id'], self.workspace['build_id'])
        self.assertFalse(set(other['functions']) & set(self.workspace['functions']))
        self.assertEqual(json.loads(json.dumps(self.workspace)), self.workspace)
        with self.assertRaisesRegex(ValueError, 'select exactly one'):
            open_region(other, ref['id'], sites())

    def test_existing_queue_fixture_diagnostics_remain_valid_t1(self):
        # Its unreachable queue operations, NULL/unknown callbacks, response
        # rewrites and multiple role contexts exercise conservative conversion.
        build = self.out / 'queues'
        query(build)
        run(['cmake', '-S', ROOT / 'fixture/queues', '-B', build, '-G', 'Ninja',
             '-DCMAKE_C_COMPILER=' + os.environ.get('ONION_CLANG', 'clang-18')])
        manifest = collect(build, ['nr-softmodem', 'nr-uesoftmodem'])
        tool = Path(os.environ.get('ONION_EXTRACTOR', ROOT / 'build-libtooling/onion-extract'))
        extraction = extract_manifest(manifest, tool, self.out / 'queues-extraction')
        w = connect(manifest, extraction, 'nr-softmodem')
        self.assertTrue(validate_structure(w['structure']))
        self.assertTrue(any(r['resolution'] == 'unresolved' and r['target'] is None
                            for r in w['structure']['relations'] if r['kind'] == 'submits'))
        self.assertTrue(w['sources']['queues']['graphs'][0]['relations'])


if __name__ == '__main__':
    unittest.main()
