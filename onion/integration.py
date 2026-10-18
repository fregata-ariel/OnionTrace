"""T7: connect source execution structure to bounded function-local exploration.

A workspace keeps T4/T5/T6 documents as static provenance. References select a
function and its connection context; they never splice concurrency into a CFG.
"""
import argparse
import copy
import json
from pathlib import Path

from .build_inputs import digest
from .callbacks import build_callbacks
from .core import accumulate, reconstruct, recommend, _validate
from .execution import make_entity, make_relation, make_structure, validate_structure
from .libtooling import build_model, write
from .queues import build_queues
from .threads import build_threads, _load


def _seal(kind, payload):
    return {'schema_version': 1, 'kind': kind, **payload,
            'id': kind + '/' + digest(payload)}


def _intact(value, kind):
    payload = {k: v for k, v in value.items() if k not in ('id', 'kind', 'schema_version')}
    if value.get('schema_version') != 1 or value.get('kind') != kind or value != _seal(kind, payload):
        raise ValueError('changed or invalid ' + kind)


def connect(manifest, extraction, target):
    """Build one target-scoped T1 structure plus navigable source references.

    Build identity includes the full manifest; shared TUs are scoped separately
    per executable. Absolute source paths are represented relative to filesystem
    root in T1 identities. Original ranges, guards and ownership stay in sources.
    """
    threads = build_threads(manifest, extraction, [target])
    callbacks = build_callbacks(manifest, extraction, [target])
    queues = build_queues(manifest, extraction, [target])
    rows, _ = _load(manifest, extraction)
    tg, cg, qg = (doc['graphs'][0] for doc in (threads, callbacks, queues))
    entities, relations, references, functions = {}, {}, [], {}
    source = manifest['context']['source']

    def entity(kind, identity, label=None):
        row = make_entity(kind, identity, label=label)
        entities[row['id']] = row
        return row['id']

    program = entity('program', {'origin': source.get('origin', 'fixture:' + digest(source)),
                                 'revision': source.get('commit', manifest['manifest_id'])})
    target_id = entity('target', {'program_id': program, 'name': target})
    build = entity('build', {'target_id': target_id,
                            'configuration': {'input_manifest_id': manifest['manifest_id']}})
    compilations, dependencies, raws = {}, {}, {}
    for row, raw in rows:
        if target not in row['selected_by']:
            continue
        def path(value):
            return Path(value).resolve().as_posix().lstrip('/') or '.'
        compilation = entity('compilation', {
            'build_id': build, 'source': path(row['source']), 'directory': path(row['directory']),
            'compiler': json.dumps(row['compiler'], sort_keys=True), 'arguments': row['arguments'],
            'input_digest': digest(row['dependencies'])})
        raws[raw['compilation_id']] = raw
        compilations[raw['compilation_id']] = compilation
        dependencies[raw['compilation_id']] = {str(Path(d['path']).resolve()): d['sha256'] for d in row['dependencies']}
        for f in raw['functions']:
            fid = entity('function', {'compilation_id': compilation, 'symbol': f['usr'],
                                      'linkage': f['linkage']}, f['name'])
            if fid in functions:
                raise ValueError('duplicate scoped function identity')
            functions[fid] = {'compilation_id': raw['compilation_id'], 'source_function_id': f['id']}
    function_ids = {v['source_function_id']: k for k, v in functions.items()}
    raw_functions = {f['id']: f for f in tg['functions']}

    def location(fid, span):
        comp = raw_functions[fid]['_compilation_id']
        span = span['expansion']
        begin, end = span['begin'], span['end']
        filename = str(Path(begin['path']).resolve())
        if filename != str(Path(end['path']).resolve()) or filename not in dependencies[comp]:
            raise ValueError('source range lacks one recorded dependency')
        return entity('location', {'compilation_id': compilations[comp], 'path': filename.lstrip('/'),
                                  'content_digest': dependencies[comp][filename],
                                  'start_offset': begin['offset'], 'end_offset': end['offset']})

    def relation(kind, a, b, row, owner, *, resolution=None, reason=None):
        state = resolution or row['resolution']
        if a is None or b is None:
            state = 'unresolved'
        why = None if state == 'resolved' else reason or row.get('reason') or 'source candidate; execution/aliases unproven'
        evidence = {'location_ids': [location(owner, row['range'])],
                    'conditions': [json.dumps(c, sort_keys=True) for c in row.get('conditions', [])],
                    'method': {'name': 'onion-t7-source-adapter', 'version': '1'}, 'reason': why}
        rel = make_relation(kind, a, b, state, evidence)
        relations[rel['id']] = rel
        return rel['id']

    def reference(origin, fid, relation_ids, source_record, category):
        if fid not in function_ids:
            return
        payload = {'origin_id': origin, 'function_id': function_ids[fid],
                   'relation_ids': list(dict.fromkeys(relation_ids)), 'category': category,
                   'source_record': copy.deepcopy(source_record)}
        item = _seal('function_reference', payload)
        if item not in references:
            references.append(item)
        return item

    for row in tg['relations']:
        if row['kind'] != 'calls':
            continue
        rid = relation('calls', function_ids[row['source']], function_ids.get(row['target']), row, row['source'])
        reference(rid, row['source'], [rid], row, 'call_site')
        for fid in list(dict.fromkeys([row['target'], *row.get('candidates', [])])):
            reference(rid, fid, [rid], row, 'callee')
    for spawn in tg['spawns']:
        role = entity('thread_role', {'build_id': build, 'key': spawn['id']})
        spawned = relation('spawns', function_ids[spawn['source']], role, spawn, spawn['source'], resolution='resolved')
        starts = relation('starts', role, function_ids.get(spawn['start_id']), spawn, spawn['source'])
        for fid in list(dict.fromkeys([spawn['start_id'], *spawn['candidates']])):
            reference(role, fid, [spawned, starts], spawn, 'thread_start')
    callback_ids = {}
    for row in cg['registrations']:
        callback = entity('callback', {'compilation_id': compilations[raw_functions[row['source']]['_compilation_id']],
                                        'key': row['id']})
        callback_ids[row['id']] = callback
        for fid in row['candidates']:
            rid = relation('binds', callback, function_ids[fid], row, row['source'],
                           resolution='resolved' if row['resolution'] == 'resolved' else 'candidate')
            reference(callback, fid, [rid], row, 'callback_target')
        if row['unknown'] or row['nullable'] or not row['candidates']:
            relation('binds', callback, None, row, row['source'], reason='NULL or unknown callback alternative retained')
    invocations = {r['id']: r for r in cg['invocations']}
    for row in cg['relations']:
        if row['kind'] != 'invokes':
            continue
        invocation = invocations[row['source']]
        rid = relation('invokes', function_ids[invocation['source']], callback_ids.get(row['target']),
                       {**invocation, 'resolution': row['resolution'], 'reason': row.get('reason')}, invocation['source'])
        reference(rid, invocation['source'], [rid], invocation, 'callback_invocation')
        for ref in list(references):
            if ref['origin_id'] == callback_ids.get(row['target']):
                fid = next(k for k, v in function_ids.items() if v == ref['function_id'])
                reference(rid, fid, [rid, *ref['relation_ids']], row, 'callback_execution_candidate')
    def queue_key(candidate):
        if isinstance(candidate, dict):
            return {k: queue_key(v) for k, v in candidate.items() if k not in ('reason', 'name', 'concrete', 'pointer_field')}
        if isinstance(candidate, list):
            return [queue_key(v) for v in candidate]
        return candidate

    operation_relations = {}
    operation_origins = {}
    for row in qg['operations']:
        if row['operation'] not in ('enqueue', 'dequeue', 'poll'):
            continue
        # No reference is created for NULL response metadata or an opaque queue.
        # A dequeue with unknown source is still a valid T1 unresolved relation.
        refs = row['queue_candidates'] or [None]
        for candidate in refs:
            queue = None if candidate is None or row['resolution'] == 'unresolved' else entity('queue', {'build_id': build, 'key': digest(queue_key(candidate))})
            kind = 'submits' if row['operation'] == 'enqueue' else 'dequeues'
            a, b = (function_ids[row['source']], queue) if kind == 'submits' else (queue, function_ids[row['source']])
            rid = relation(kind, a, b, row, row['source'])
            operation_relations.setdefault(row['id'], []).append(rid)
            operation_origins.setdefault(row['id'], []).append(queue or rid)
            reference(queue or rid, row['source'], [rid], row, 'queue_' + row['operation'])
    operations = {r['id']: r for r in qg['operations']}
    processing = {r['id']: r for r in qg['processing_calls']}
    # Preserve static may-links as separate navigation context, never CFG edges.
    for row in qg['relations']:
        if row['kind'] not in ('transfers', 'response_transfer', 'processes'):
            continue
        source_op = operations.get(row['source'])
        target_op = operations.get(row['target']) or processing.get(row['target'])
        if source_op is None or target_op is None:
            continue
        ids = operation_relations.get(row['source'], []) + operation_relations.get(row['target'], [])
        for origin in operation_origins.get(row['source'], []):
            reference(origin, target_op['source'], ids, row, row['kind'])
            for fid in list(dict.fromkeys([target_op.get('target_id'), *target_op.get('target_candidates', [])])):
                reference(origin, fid, ids, row, 'queue_processing_candidate')
    # Follow synchronous calls beneath a selected thread/Queue/callback origin.
    # Each hop remains a relation reference; asynchronous links never enter CFGs.
    by_call = {}
    for row in tg['relations']:
        if row['kind'] == 'calls':
            rid = relation('calls', function_ids[row['source']], function_ids.get(row['target']), row, row['source'])
            by_call[row['id']] = rid
    navigation_gaps = []
    seeds = [r for r in references if r['category'] not in ('call_site', 'callee')]
    todo = [(r, [r['function_id']]) for r in seeds]
    while todo:
        ref, stack = todo.pop()
        if len(references) >= 10000:
            navigation_gaps.append({'reason': 'navigation reference limit reached'})
            break
        raw_id = functions[ref['function_id']]['source_function_id']
        for row in tg['relations']:
            if row['kind'] != 'calls' or row['source'] != raw_id or row['api']:
                continue
            if row['target'] is None:
                continue
            fid = function_ids[row['target']]
            if fid in stack or len(stack) >= 128:
                navigation_gaps.append({'origin_id': ref['origin_id'], 'reason': 'navigation recursion/depth limit'})
                continue
            record = {'parent_reference_id': ref['id'], 'call_path': [*ref['source_record'].get('call_path', []), row['id']],
                      'entry_context': ref['source_record'], 'call': row}
            child = reference(ref['origin_id'], row['target'], [*ref['relation_ids'], by_call[row['id']]], record, 'synchronous_descendant')
            todo.append((child, [*stack, fid]))
    structure = make_structure(list(entities.values()), list(relations.values()))
    return _seal('execution_workspace', {'structure': structure, 'build_id': build, 'target': target,
        'functions': functions, 'extractions': raws, 'references': references,
        'sources': {'threads': threads, 'callbacks': callbacks, 'queues': queues},
        'incompleteness': copy.deepcopy(tg['failures'] + navigation_gaps),
        'frontiers': copy.deepcopy(tg['frontiers'] + cg['frontiers'] + qg['frontiers']),
        'input_manifest_id': manifest['manifest_id'], 'extraction_digest': digest(extraction)})


def references_from(workspace, origin_id):
    _intact(workspace, 'execution_workspace')
    return copy.deepcopy([r for r in workspace['references'] if r['origin_id'] == origin_id])


def open_region(workspace, reference_id, sites, *, anchors=('work.begin', 'work.end'), parent=None):
    """Select a local anchor interval and retain the exact static context.

    An unresolved context inhibits *contextual* uniqueness even if there is one
    enumerated local path. Parent/child intervals share the same function and
    context and must nest in its CFG. Each interval has its own plan namespace.
    """
    _intact(workspace, 'execution_workspace')
    validate_structure(workspace['structure'])
    refs = [r for r in workspace['references'] if r['id'] == reference_id]
    if len(refs) != 1:
        raise ValueError('select exactly one function reference')
    ref = refs[0]
    source = workspace['functions'][ref['function_id']]
    raw = workspace['extractions'][source['compilation_id']]
    model = build_model(raw, source['source_function_id'], sites, workspace['build_id'],
                        {'structure_id': workspace['structure']['structure_id'], 'function_id': ref['function_id']}, anchors=anchors)
    # Local block IDs include compilation but not executable target identity.
    # Scope them by T1 function before accumulating evidence across regions.
    scoped = {n['id']: ref['function_id'] + '/' + n['id'] for n in model['nodes']}
    for node in model['nodes']:
        node['id'] = scoped[node['id']]
    for edge in model['edges']:
        edge['source'], edge['target'] = scoped[edge['source']], scoped[edge['target']]
    for site in model['sites']:
        site['node'] = scoped[site['node']]
    model['region'] = {k: scoped[v] for k, v in model['region'].items()}
    if model['region']['entry'] == model['region']['exit']:
        raise ValueError('anchors must occupy distinct CFG blocks; expression-level slicing unavailable')
    # Stop at the closing anchor. Dead ends/escape routes remain in the model
    # so the existing enumerator reports them instead of proving uniqueness.
    outgoing = {}
    for edge in model['edges']:
        outgoing.setdefault(edge['source'], []).append(edge['target'])
    reached, pending = set(), [model['region']['entry']]
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        if node != model['region']['exit']:
            pending.extend(outgoing.get(node, []))
    if model['region']['exit'] not in reached:
        raise ValueError('closing anchor is unreachable')
    model['nodes'] = [n for n in model['nodes'] if n['id'] in reached]
    model['edges'] = [e for e in model['edges'] if e['source'] in reached and e['target'] in reached and e['source'] != model['region']['exit']]
    if any(s['node'] not in reached for s in model['sites']):
        raise ValueError('probe outside selected region')
    if parent is not None:
        _intact(parent, 'analysis_region')
        if (parent['workspace_id'] != workspace['id'] or parent['reference']['id'] != ref['id'] or
                not reached <= {n['id'] for n in parent['model']['nodes']}):
            raise ValueError('child region must nest in the same function/context')
        if model['region'] == parent['model']['region']:
            raise ValueError('child must have a narrower boundary')
    constraints = copy.deepcopy(workspace['incompleteness'])
    if parent is not None:
        constraints.extend(copy.deepcopy(parent['constraints']))
    # Only gaps on this selection's context or inside its local interval block
    # certainty; unrelated source diagnostics remain visible in the workspace.
    record = ref['source_record']
    chain = record.get('call_path', record.get('context', {}).get('call_path', []))
    local_calls = {c['id'] for c in next(f for f in raw['functions']
        if f['id'] == source['source_function_id'])['calls']
        if c['block_id'] in {old for old, new in scoped.items() if new in reached}}
    relevant_calls = set(chain) | local_calls
    for edge in workspace['sources']['threads']['graphs'][0]['relations']:
        if edge['kind'] == 'calls' and edge['id'] in relevant_calls and edge['resolution'] != 'resolved':
            constraints.append({'call_id': edge['id'], 'reason': edge['reason']})
    for frontier in workspace['frontiers']:
        if (not frontier.get('call_path') and not frontier.get('call_id') or
                frontier.get('call_id') in relevant_calls or
                set(frontier.get('call_path', [])) & set(chain)):
            constraints.append(copy.deepcopy(frontier))
    for rid in ref['relation_ids']:
        relation = next(r for r in workspace['structure']['relations'] if r['id'] == rid)
        if relation['resolution'] != 'resolved':
            constraints.append({'relation_id': rid, 'reason': relation['evidence']['reason']})
    if ref['category'] in ('transfers', 'response_transfer', 'processes', 'queue_processing_candidate'):
        constraints.append({'reason': 'asynchronous source may-link; runtime handoff unproven'})
    if constraints:
        model['uncertainties'].append({'kind': 'execution_context', 'affects_completeness': True,
                                       'constraints': constraints})
    return _seal('analysis_region', {'workspace_id': workspace['id'],
        'structure_id': workspace['structure']['structure_id'], 'build_id': workspace['build_id'],
        'function_id': ref['function_id'], 'reference': copy.deepcopy(ref), 'model': model,
        'parent_region_id': parent['id'] if parent else None, 'anchors': list(anchors),
        'constraints': constraints, 'conditions': [copy.deepcopy(r['evidence']['conditions'])
            for r in workspace['structure']['relations'] if r['id'] in ref['relation_ids']], 'boundary': {'ordering': 'one thread per job inside local interval',
        'asynchronous_links_are_cfg_edges': False, 'runtime_context_proven': False}})


def _scope(region):
    return {k: region[k] for k in ('structure_id', 'build_id', 'function_id')} | {'region_id': region['id']}


def initial_plan(region, enabled_sites=None):
    _intact(region, 'analysis_region')
    anchors = [s['id'] for s in region['model']['sites'] if s.get('anchor')]
    enabled = anchors if enabled_sites is None else list(enabled_sites)
    payload = {'schema_version': 1, **_scope(region), 'enabled_sites': enabled, 'anchor_sites': anchors,
               'parent_plan_id': None, 'rationale': {}, 'budget': {}}
    plan = {**payload, 'plan_id': 'region-plan/' + digest(payload)}
    _validate(region['model'], plan)
    return plan


def _check_scope(region, value):
    _intact(region, 'analysis_region')
    if any(value.get(k) != v for k, v in _scope(region).items()):
        raise ValueError('structure/build/function/region identity mismatch')


def reconstruct_region(region, plan, trace):
    _check_scope(region, plan)
    _check_scope(region, trace)
    if trace.get('plan_digest') != digest(plan):
        raise ValueError('trace plan content mismatch')
    # Core validates raw build/plan/run fields, drops and thread continuity.
    for event in trace['events']:
        if any(k in event and event[k] != v for k, v in _scope(region).items()):
            raise ValueError('event region identity mismatch')
    result = reconstruct(region['model'], plan, trace)
    return _seal('region_reconstruction', {**_scope(region), 'plan': copy.deepcopy(plan),
        'trace_digest': digest(trace), 'analysis': result,
        'provenance': {'reference': copy.deepcopy(region['reference']),
                       'constraints': copy.deepcopy(region['constraints']),
                       'candidate_basis': 'bounded static function CFG; never direct traversal',
                       'observation_basis': 'raw thread/thread_seq events in this run'}})


def recommend_region(region, plan, result, max_new_sites=1):
    _check_scope(region, plan)
    _check_scope(region, result)
    _intact(result, 'region_reconstruction')
    if result['plan'] != plan:
        raise ValueError('reconstruction plan content mismatch')
    next_plan = recommend(region['model'], plan, result['analysis'], max_new_sites)
    return {**next_plan, **_scope(region)}


def accumulate_regions(results):
    """Keep previous candidates and observations as immutable per-run snapshots."""
    for result in results:
        _intact(result, 'region_reconstruction')
    if len({r['region_id'] for r in results}) > 1:
        raise ValueError('different regions require explicit mapping; no cross-region timeline')
    combined = accumulate([r['analysis'] for r in results])
    return {'schema_version': 1, 'kind': 'region_evidence',
            'region_id': results[0]['region_id'] if results else None,
            'runs': copy.deepcopy(results), 'edge_support': combined, 'merges_timelines': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--extraction', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = connect(json.loads(args.manifest.read_text()), json.loads(args.extraction.read_text()), args.target)
    write(args.out / 'workspace.json', result)
    write(args.out / 'structure.json', result['structure'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
