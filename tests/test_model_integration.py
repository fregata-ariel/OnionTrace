"""T7 interval identity, uncertainty propagation and immutable stage history."""
import copy
import json
import unittest

from onion.build_inputs import digest
from onion.integration import (connect, references_from, open_region, initial_plan,
    reconstruct_region, recommend_region, accumulate_regions, _seal, _scope)
from test_execution import fixture_structure


def sites(inner=False):
    names = [('inner.begin', 'inner_begin'), ('inner.end', 'inner_end')] if inner else [
        ('work.begin', 'outer_begin'), ('work.end', 'outer_end')]
    names += [('left', 'left_probe'), ('right', 'right_probe')]
    return [{'id': name, 'symbol': symbol, 'kind': 'region', 'anchor': i < 2,
             'order': i, 'estimated_cost': 1} for i, (name, symbol) in enumerate(names)]


def workspace():
    structure = fixture_structure()
    start = next(r for r in structure['relations'] if r['kind'] == 'starts')
    fid, role = start['target'], start['source']
    entity = next(e for e in structure['entities'] if e['id'] == fid)
    comp = next(e for e in structure['entities'] if e['id'] == entity['identity']['compilation_id'])
    symbols = {'begin': 'outer_begin', 'ib': 'inner_begin', 'left': 'left_probe',
               'right': 'right_probe', 'ie': 'inner_end', 'end': 'outer_end'}
    blocks = [{'id': n, 'clang_id': n, 'entry': n == 'entry', 'exit': n == 'exit',
               'terminator': None, 'elements': ([{'kind': 'DeclRefExpr', 'index': 0,
               'referenced_symbol': symbols[n]}] if n in symbols else [])}
              for n in ['entry', 'begin', 'ib', 'left', 'right', 'ie', 'end', 'exit']]
    function = {'id': 'raw/process', 'name': 'process', 'qualified_name': 'process', 'type': 'void(void *)',
                'blocks': blocks, 'edges': [{'source': a, 'target': b} for a, b in [
                    ('entry', 'begin'), ('begin', 'ib'), ('ib', 'left'), ('ib', 'right'),
                    ('left', 'ie'), ('right', 'ie'), ('ie', 'end'), ('end', 'exit')]],
                'calls': [], 'uncertainties': []}
    raw = {'kind': 'clang_source_extraction', 'schema_version': 1, 'compilation_id': 'raw',
           'complete': True, 'clang_version': 'clang version 18.fixture',
           'functions': [function], 'limitations': ['hand-authored semantic unit fixture']}
    ref = _seal('function_reference', {'origin_id': role, 'function_id': fid,
        'relation_ids': [start['id']], 'category': 'thread_start', 'source_record': {'call_path': []}})
    return _seal('execution_workspace', {'structure': structure, 'build_id': comp['identity']['build_id'],
        'target': 'fixture', 'functions': {fid: {'raw': raw, 'source_function_id': function['id']}},
        'references': [ref], 'incompleteness': [], 'frontiers': [],
        'sources': {'threads': {'graphs': [{'relations': []}]}}})


def reseal(value):
    return _seal(value['kind'], {k: v for k, v in value.items() if k not in ('id', 'schema_version', 'kind')})


def trace(region, plan, observations=None, run_id='run-0'):
    observations = observations if observations is not None else list(region['anchors'])
    return {'schema_version': 1, **_scope(region), 'plan_id': plan['plan_id'], 'plan_digest': digest(plan),
        'run_id': run_id, 'clock': 'CLOCK_MONOTONIC', 'complete': True, 'dropped': 0,
        'enabled_sites': plan['enabled_sites'][:], 'events': [
            {'thread': 'worker-0', 'thread_seq': i, 'site': site, 'job': 0, 'timestamp_ns': i}
            for i, site in enumerate(observations)]}


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.workspace = workspace()
        self.ref = self.workspace['references'][0]
        self.region = open_region(self.workspace, self.ref['id'], sites())
        self.plan = initial_plan(self.region)

    def test_thread_to_cfg_to_staged_exploration_keeps_previous_candidates(self):
        before = copy.deepcopy((self.workspace, self.region, self.plan))
        first = reconstruct_region(self.region, self.plan, trace(self.region, self.plan))
        self.assertEqual(first['analysis']['status'], 'ambiguous')
        self.assertEqual(first['analysis']['jobs'][0]['candidate_count'], 2)
        snapshot = copy.deepcopy(first)
        next_plan = recommend_region(self.region, self.plan, first)
        self.assertEqual(next_plan['parent_plan_id'], self.plan['plan_id'])
        self.assertEqual(len(next_plan['rationale']['selected_sites']), 1)
        chosen = next_plan['rationale']['selected_sites'][0]
        later = reconstruct_region(self.region, next_plan,
            trace(self.region, next_plan, ['work.begin', chosen, 'work.end'], 'run-1'))
        self.assertEqual(later['analysis']['status'], 'unique')
        result = accumulate_regions([first, later])
        self.assertEqual([r['analysis']['status'] for r in result['runs']], ['ambiguous', 'unique'])
        self.assertEqual(first, snapshot)
        self.assertFalse(result['merges_timelines'])
        self.assertEqual(before, (self.workspace, self.region, self.plan))
        self.assertTrue(any(s['evidence'] == 'candidate_only' for e in result['edge_support']['edges'] for s in e['support']))
        self.assertFalse(self.region['boundary']['asynchronous_links_are_cfg_edges'])
        self.assertFalse(self.region['boundary']['runtime_context_proven'])

    def test_nested_interval_has_separate_plan_and_keeps_parent(self):
        original = copy.deepcopy(self.region)
        child = open_region(self.workspace, self.ref['id'], sites(True),
                            anchors=('inner.begin', 'inner.end'), parent=self.region)
        self.assertEqual(child['parent_region_id'], self.region['id'])
        self.assertEqual(self.region, original)
        child_plan = initial_plan(child)
        self.assertNotEqual(child_plan['region_id'], self.plan['region_id'])
        self.assertEqual(reconstruct_region(child, child_plan, trace(child, child_plan))['analysis']['status'], 'ambiguous')
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            reconstruct_region(child, self.plan, trace(self.region, self.plan))
        with self.assertRaisesRegex(ValueError, 'narrower boundary'):
            open_region(self.workspace, self.ref['id'], sites(), parent=self.region)
        with self.assertRaisesRegex(ValueError, 'nest'):
            open_region(self.workspace, self.ref['id'], sites(), parent=child)

    def test_unknown_or_candidate_connection_never_earns_context_uniqueness(self):
        for resolution in ('unresolved', 'candidate'):
            w = copy.deepcopy(self.workspace)
            relation = next(r for r in w['structure']['relations'] if r['kind'] == 'calls' and r['resolution'] == resolution)
            w['references'][0]['relation_ids'].append(relation['id'])
            w['references'][0] = reseal(w['references'][0])
            w = reseal(w)
            region = open_region(w, w['references'][0]['id'], sites())
            plan = initial_plan(region, ['work.begin', 'work.end', 'left', 'right'])
            result = reconstruct_region(region, plan, trace(region, plan, ['work.begin', 'left', 'work.end']))
            self.assertEqual(result['analysis']['jobs'][0]['candidate_count'], 1)
            self.assertEqual(result['analysis']['status'], 'incomplete')
            self.assertTrue(all(e['evidence'] == 'candidate_only' for e in result['analysis']['jobs'][0]['edge_evidence']))
            self.assertFalse(recommend_region(region, plan, result)['rationale']['selected_sites'])

    def test_async_may_link_blocks_even_with_resolved_relation_references(self):
        w = copy.deepcopy(self.workspace)
        w['references'][0]['category'] = 'queue_processing_candidate'
        w['references'][0] = reseal(w['references'][0])
        w = reseal(w)
        region = open_region(w, w['references'][0]['id'], sites())
        plan = initial_plan(region, ['work.begin', 'work.end', 'left'])
        result = reconstruct_region(region, plan, trace(region, plan, ['work.begin', 'left', 'work.end']))
        self.assertEqual(result['analysis']['status'], 'incomplete')
        self.assertTrue(region['constraints'])

    def test_build_structure_function_region_plan_and_run_mixups(self):
        for field in ('structure_id', 'build_id', 'function_id', 'region_id', 'plan_id', 'plan_digest'):
            t = trace(self.region, self.plan)
            t[field] = 'wrong'
            with self.subTest(field=field), self.assertRaises(ValueError):
                reconstruct_region(self.region, self.plan, t)
        for field in ('run_id', 'plan_id', 'build_id', 'region_id', 'function_id'):
            t = trace(self.region, self.plan)
            t['events'][0][field] = 'wrong'
            with self.subTest(event=field), self.assertRaises(ValueError):
                reconstruct_region(self.region, self.plan, t)
        changed_plan = dict(self.plan, enabled_sites=self.plan['enabled_sites'] + ['left'])
        with self.assertRaisesRegex(ValueError, 'plan content'):
            reconstruct_region(self.region, changed_plan, trace(self.region, self.plan))

    def test_loss_continuity_and_manifest_mismatch_block_probe_certainty(self):
        for field, value in [('dropped', 1), ('complete', False), ('enabled_sites', []), ('events', [])]:
            t = trace(self.region, self.plan)
            t[field] = value
            result = reconstruct_region(self.region, self.plan, t)
            self.assertEqual(result['analysis']['status'], 'incomplete')
            self.assertFalse(recommend_region(self.region, self.plan, result)['rationale']['selected_sites'])
        t = trace(self.region, self.plan)
        t['events'][-1]['thread_seq'] += 1
        self.assertEqual(reconstruct_region(self.region, self.plan, t)['analysis']['status'], 'incomplete')

    def test_mutation_duplicate_runs_cross_regions_and_wrong_plan_rejected(self):
        first = reconstruct_region(self.region, self.plan, trace(self.region, self.plan))
        with self.assertRaisesRegex(ValueError, 'duplicate run_id'):
            accumulate_regions([first, first])
        child = open_region(self.workspace, self.ref['id'], sites(True), anchors=('inner.begin', 'inner.end'), parent=self.region)
        plan = initial_plan(child)
        later = reconstruct_region(child, plan, trace(child, plan, run_id='other'))
        with self.assertRaisesRegex(ValueError, 'different regions'):
            accumulate_regions([first, later])
        first['analysis']['status'] = 'unique'
        with self.assertRaisesRegex(ValueError, 'changed'):
            accumulate_regions([first])
        with self.assertRaisesRegex(ValueError, 'invalid|changed'):
            open_region(dict(self.workspace, target='other'), self.ref['id'], sites())

    def test_references_are_detached_and_serializable(self):
        restored = json.loads(json.dumps(self.workspace))
        refs = references_from(restored, self.ref['origin_id'])
        self.assertEqual(refs, [self.ref])
        refs[0]['relation_ids'].clear()
        self.assertTrue(self.ref['relation_ids'])

    def test_escape_and_cycle_remain_incomplete(self):
        for edge in ({'source': 'left', 'target': 'exit'}, {'source': 'left', 'target': 'left'}):
            w = copy.deepcopy(self.workspace)
            raw = next(iter(w['functions'].values()))['raw']
            raw['functions'][0]['edges'].append(edge)
            w = reseal(w)
            region = open_region(w, self.ref['id'], sites())
            plan = initial_plan(region)
            result = reconstruct_region(region, plan, trace(region, plan))
            self.assertFalse(result['analysis']['enumeration']['complete'])
            self.assertEqual(result['analysis']['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
