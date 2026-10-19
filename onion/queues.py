"""T6 notifiedFIFO source candidates; no runtime instances, transfers or timing.

This is an intermediate source graph, separate from T1 and function-local CFGs.
Names identify trusted API contracts only; queue matching uses declaration USRs,
base objects, fields and source call contexts. All inter-site links are may-links.
"""
import argparse
import copy
import json
from pathlib import Path

from .build_inputs import digest
from .libtooling import write
from .threads import build_threads, _reachable
from .callbacks import _callbacks_from_threads


CONTRACTS = {
    'initialize': {'ownership': 'initialize queue storage before concurrent use'},
    'create': {'ownership': 'creator owns newly allocated element; response queue is metadata, not a transfer'},
    'enqueue': {
        'ownership': 'producer relinquishes element only on successful publication; aborted queue may reject it',
        'publication': 'inside pushNotifiedFIFO lock, before mutex unlock; function entry is only an attempt',
        'safe_information': 'copy key/response metadata before publication; do not dereference element after unlock',
        'success_condition': 'queue is not aborted while holding its lock',
    },
    'dequeue': {
        'ownership': 'consumer acquires a returned non-NULL element; NULL is abort, not a job',
        'acquisition': 'successful removal inside lock; non-NULL return is a later safe observation',
        'safe_information': 'element metadata/payload only after non-NULL check and before re-publication/destruction',
    },
    'poll': {
        'ownership': 'consumer acquires only a non-NULL result; NULL may mean contention, empty or abort',
        'acquisition': 'successful removal under try-lock; non-NULL return is a later safe observation',
        'safe_information': 'element metadata only after a non-NULL check',
    },
    'destroy': {'ownership': 'element may be freed if malloced; no subsequent element reads'},
    'abort': {'ownership': 'marks queue aborted and may destroy queued elements; wakes waiters'},
}


def _key(v):
    """Exact syntactic storage identity, never equality of unknown bases."""
    if v.get('shape') == 'storage':
        return ('storage', v['scope'], v['usr'])
    base = _key(v['base']) if 'base' in v else None
    if base is None:
        return None
    if v.get('shape') == 'member':
        return ('member', base, v['field_usr'], v['arrow'])
    if v.get('shape') in ('address', 'deref'):
        return (v['shape'], base)
    if v.get('shape') == 'index' and v['index'].get('shape') == 'integer':
        return ('index', base, v['index']['literal'])
    return None


def _expand(value, function, bindings=(), seen=(), depth=0):
    expanded = _expand_value(value, function, bindings, seen, depth)
    key = _key(value)
    if key is None or key in seen or depth > 32:
        return expanded
    # Member effects do not overwrite the element pointer itself. Carry them
    # with that element so direct-call forwarding and aliases retain producer
    # response-field writes all the way to submission and the return may-join.
    for w in function['callback_writes']:
        target = w.get('queue_target', {})
        if (target.get('shape') != 'member' or target.get('field_name') != 'reponseFifo' or
                _key(target['base']) != key):
            continue
        written = _expand(w['queue_value'], function, bindings, (*seen, key), depth + 1)
        record = {'id': w['id'], 'source': function['id'], 'range': w['range'],
                  'conditions': copy.deepcopy(w['conditions']), 'effect': w['effect'],
                  'value': written, 'queue_candidates': _references(written), 'unknown': True,
                  'reason': 'element response-field write; flow-insensitive candidate, final value/aliases unproven'}
        if record not in expanded.setdefault('response_writes', []):
            expanded['response_writes'].append(record)
    return expanded


def _expand_value(value, function, bindings=(), seen=(), depth=0):
    """Flow-insensitive alternatives, retaining unknown writes and escapes.

    Addressed concrete storage is a source location, not a pointer value to
    substitute. Pointer-valued storage never becomes a proven immutable alias.
    """
    v = copy.deepcopy(value)
    v.setdefault('origin_function', function['id'])
    if depth > 32:
        return {'shape': 'opaque', 'reason': 'value expansion limit'}
    key = _key(v)
    if v.get('shape') == 'address' and v['base'].get('shape') == 'storage' and v['base'].get('pointer'):
        # &p denotes the pointer slot, not p's current pointee. Do not silently
        # collapse an extra level of indirection while forwarding q**.
        v['reason'] = 'address of pointer storage; indirect loads/writes unresolved'
        return v
    if v.get('shape') == 'storage' and v.get('pointer'):
        effects = [w for w in function['callback_writes'] if _key(w.get('queue_target', {})) == key]
        index = v.get('parameter_index')
        bound = index is not None and index < len(bindings)
        if bound and not effects:
            return {**copy.deepcopy(bindings[index]), 'forwarded_at': v.get('range')}
        if key in seen:
            return {**v, 'shape': 'opaque', 'reason': 'cyclic pointer storage'}
        if effects:
            choices = [_expand(w['queue_value'], function, bindings, (*seen, key), depth + 1) for w in effects]
            if bound:
                choices.append(copy.deepcopy(bindings[index]))
            return {**v, 'shape': 'alternatives', 'choices': choices, 'unknown': True,
                    'reason': 'pointer storage writes/escapes are not a complete alias proof',
                    'write_evidence': [{'id': w['id'], 'range': w['range'], 'conditions': w['conditions'],
                                        'effect': w['effect']} for w in effects]}
    # Explicit pointer-field writes are alternatives too. In particular,
    # reponseFifo may be changed after construction or acquisition.
    if v.get('shape') == 'member' and v.get('pointer') and key is not None and key not in seen:
        effects = [w for w in function['callback_writes'] if _key(w.get('queue_target', {})) == key]
        if effects:
            original = {**v, 'base': _expand(v['base'], function, bindings, (*seen, key), depth + 1)}
            return {'shape': 'alternatives', 'choices': [original] + [
                _expand(w['queue_value'], function, bindings, (*seen, key), depth + 1) for w in effects],
                    'unknown': True, 'reason': 'pointer field writes/escapes; last value is not proven',
                    'write_evidence': [{'id': w['id'], 'range': w['range'], 'conditions': w['conditions'],
                                        'effect': w['effect']} for w in effects]}
    if 'base' in v:
        v['base'] = _expand(v['base'], function, bindings, seen, depth + 1)
    if 'choices' in v:
        v['choices'] = [_expand(c, function, bindings, seen, depth + 1) for c in v['choices']]
    if 'arguments' in v:
        v['arguments'] = [_expand(c, function, bindings, seen, depth + 1) for c in v['arguments']]
    return v


def _references(v):
    """Logical source object paths. No field-name-only equivalence."""
    shape = v.get('shape')
    if shape == 'alternatives':
        return [r for c in v['choices'] for r in _references(c)]
    if shape == 'storage':
        return [{'base': {'scope': v['scope'], 'usr': v['usr']}, 'fields': [], 'indices': [],
                 'concrete': not v.get('pointer', False),
                 'reason': 'pointer pointee is unresolved' if v.get('pointer') else 'source object declaration'}]
    if shape in ('address', 'deref', 'cast'):
        refs = _references(v['base'])
        if shape == 'cast':
            for r in refs:
                r['reason'] += '; explicit cast retained as candidate'
        return refs
    if shape == 'member':
        refs = _references(v['base'])
        if not refs:
            refs = [{'base': None, 'fields': [], 'indices': [], 'concrete': False,
                     'reason': 'computed base object is unresolved'}]
        for r in refs:
            if v.get('arrow') and r.get('pointer_field'):
                # A pointer-valued field is a new object indirection. Retain
                # its storage path as provenance, not as embedded-object fields
                # of the pointee. info->gNB->fifo and RC.gNB[0]->fifo share the
                # final field family while their actual gNB alias is unknown.
                previous = copy.deepcopy(r)
                r.clear()
                r.update(base=None, fields=[], indices=[], concrete=False,
                         reason='pointer field/array pointee is unresolved', via=previous)
            r['fields'].append({'usr': v['field_usr'], 'name': v['field_name']})
            # A pointer field is another indirection, not embedded storage.
            if v.get('pointer'):
                r['concrete'] = False
                r['reason'] += '; pointer field pointee unresolved'
            r['pointer_field'] = bool(v.get('pointer'))
        return refs
    if shape == 'index':
        refs = _references(v['base'])
        index = v['index'].get('literal') if v['index'].get('shape') == 'integer' else None
        for r in refs:
            r['indices'].append(index)
            if v.get('pointer'):
                r['concrete'] = False
                r['pointer_field'] = True
            if index is None:
                r['concrete'] = False
                r['reason'] += '; dynamic index does not identify a runtime instance'
        return refs
    return []


def _compatible(a, b):
    fields_a = [f['usr'] for f in a['fields']]
    fields_b = [f['usr'] for f in b['fields']]
    if fields_a != fields_b:
        return False
    if a['concrete'] and b['concrete']:
        return a['base'] == b['base'] and a['indices'] == b['indices']
    # A known field declaration offers a family of candidates, never object
    # equality. Completely opaque pointers have no basis for a cross-site link.
    return bool(fields_a) or a['base'] is not None and a['base'] == b['base']


def _matches(a, b):
    return any(_compatible(x, y) for x in a for y in b)


def _origins(v):
    if v.get('shape') == 'call':
        return [v]
    if v.get('shape') == 'alternatives':
        return [o for c in v['choices'] for o in _origins(c)]
    if v.get('shape') == 'cast':
        return _origins(v['base'])
    return []


def _members(v):
    if v.get('shape') == 'member':
        return [v]
    if v.get('shape') == 'alternatives':
        return [m for c in v['choices'] for m in _members(c)]
    return []


def _response_writes(value):
    """Only alternatives/casts of this element, not unrelated call arguments."""
    rows = list(value.get('response_writes', []))
    if value.get('shape') == 'alternatives':
        rows += [w for c in value['choices'] for w in _response_writes(c)]
    if value.get('shape') == 'cast':
        rows += _response_writes(value['base'])
    unique = []
    for row in rows:
        if row not in unique:
            unique.append(row)
    return unique


def _site(v):
    begin = v['range']['expansion']['begin']
    return v.get('origin_function'), begin['path'], begin['offset']


def _contexts(parent, max_contexts, max_depth):
    functions = {f['id']: f for f in parent['functions']}
    edges = {r['id']: r for r in parent['relations'] if r['kind'] == 'calls'}
    pending = [(parent['root_id'], 'main/' + parent['target'], [], [], [], [])] if parent['root_id'] else []
    # Replay the creation path using T6 expressions, preserving explicit casts
    # and argument provenance that T4 deliberately leaves opaque.
    for spawn in parent['spawns']:
        if spawn['resolution'] != 'resolved':
            continue
        bindings = []
        for cid in spawn['call_path']:
            call = next(c for f in functions.values() for c in f['calls'] if c['id'] == cid)
            owner = functions[call['caller_id']]
            bindings = [_expand(a, owner, bindings) for a in call['queue_arguments']]
        index = 3 if spawn['api'] == 'pthread_create' else 2
        pending.append((spawn['start_id'], spawn['id'], [], [bindings[index]], spawn['conditions'], []))
    result, frontiers, count = {}, [], 0
    while pending:
        fid, role, path, bindings, conditions, stack = pending.pop()
        count += 1
        if count > max_contexts:
            frontiers.append({'reason': 'queue context limit reached', 'call_path': path})
            break
        if fid in stack or len(path) >= max_depth:
            frontiers.append({'reason': 'queue recursion/depth limit reached', 'call_path': path})
            continue
        function = functions[fid]
        result.setdefault(fid, []).append({'role_id': role, 'call_path': path,
                                           'bindings': bindings, 'conditions': conditions})
        for call in function['calls']:
            edge = edges[call['id']]
            # Semantic queue calls are terminals, not a second operation in
            # every inline implementation body.
            if edge['target'] and not edge['api'] and not call['queue_api']:
                actuals = [_expand(a, function, bindings) for a in call['queue_arguments']]
                pending.append((edge['target'], role, path + [call['id']], actuals,
                                conditions + call['conditions'], stack + [fid]))
    return result, frontiers


def build_queues(manifest, extraction, targets=None, *, max_contexts=10000, max_depth=128):
    threads = build_threads(manifest, extraction, targets, max_contexts=max_contexts, max_depth=max_depth)
    callbacks = _callbacks_from_threads(manifest, extraction, threads, max_contexts=max_contexts, max_depth=max_depth)
    return _queues_from_sources(manifest, extraction, threads, callbacks,
                               max_contexts=max_contexts, max_depth=max_depth)


def _queues_from_sources(manifest, extraction, threads, callbacks, *, max_contexts=10000, max_depth=128):
    """Use already derived source graphs; never cache across public calls."""
    graphs = []
    for parent in threads['graphs']:
        functions = {f['id']: f for f in parent['functions']}
        for f in functions.values():
            if (f.get('queue_semantics_version') != 1 or
                    any('queue_arguments' not in c or 'queue_api' not in c or 'queue_callee' not in c for c in f['calls']) or
                    any('queue_value' not in w or 'queue_target' not in w for w in f['callback_writes'])):
                raise ValueError('T6 requires re-extraction with queue semantics v1')
        contexts, frontiers = _contexts(parent, max_contexts, max_depth)
        graph = {'target': parent['target'], 'root_id': parent['root_id'], 'functions': parent['functions'],
                 'spawns': parent['spawns'], 'operations': [], 'relations': [], 'processing_calls': [],
                 'call_relations': parent['relations'],
                 'frontiers': frontiers, 'thread_frontiers': parent['frontiers'], 'failures': parent['failures'],
                 'traversal_complete': parent['traversal_complete'] and not frontiers}
        graphs.append(graph)
        for f in functions.values():
            available = contexts.get(f['id'], [{'role_id': None, 'call_path': [], 'bindings': [], 'conditions': [],
                                                'reason': 'source site has no proven main/thread call path'}])
            reachable = _reachable(f)
            for call in f['calls']:
                api = call['queue_api']
                callee = call['callee_value']
                callback = callee.get('callback_protocol') == 'actor'
                if not api and not callback:
                    continue
                blocked = (call['block_id'] not in reachable or
                           any(u.get('affects_completeness') for u in f['uncertainties']))
                for context in available:
                    args = [_expand(a, f, context['bindings']) for a in call['queue_arguments']]
                    common = {'id': call['id'] + '/queue/' + digest({k: v for k, v in context.items() if k != 'bindings'}),
                              'source': f['id'], 'call_id': call['id'], 'range': call['range'],
                              'block_id': call['block_id'], 'conditions': call['conditions'], 'blocked': blocked,
                              'context': {k: v for k, v in context.items() if k != 'bindings'}}
                    if callback:
                        graph['processing_calls'].append({**common, 'callee': _expand(call['queue_callee'], f, context['bindings'])})
                        continue
                    queue = args[2] if api == 'create' else args[0] if api not in ('destroy',) else {'shape': 'null'}
                    refs = _references(queue)
                    graph['operations'].append({**common, 'operation': api, 'queue': queue, 'queue_candidates': refs,
                                                'arguments': args, 'semantics': CONTRACTS[api],
                                                'element_response_writes': _response_writes(args[1]) if api == 'enqueue' else [],
                                                'resolution': 'candidate' if refs and not blocked else 'unresolved',
                                                'reason': 'unreachable CFG placement or incomplete function semantics' if blocked else
                                                          'logical source locations; runtime instance and aliases unproven' if refs else
                                                          'no queue location recovered (NULL response or opaque alias)',
                                                'runtime_instance': None})
        _connect(graph)
        callback_graph = next(g for g in callbacks['graphs'] if g['target'] == graph['target'])
        graph['callback_registrations'] = callback_graph['registrations']
        for creation in [o for o in graph['operations'] if o['operation'] == 'create']:
            creation['callback_registration_ids'] = [r['id'] for r in graph['callback_registrations']
                                                       if r['call_id'] == creation['call_id']]
        # Consumers can inspect the T5 candidate/null/unknown set at an actual
        # allocation and then follow T6 submits/transfers/processes/returns_to.
    return {'schema_version': 1, 'kind': 'notified_fifo_graph', 'input_manifest_id': manifest['manifest_id'],
            'extraction_digest': digest(extraction), 'graphs': graphs,
            'callback_graph_digest': digest(callbacks),
            'ready': all(g['root_id'] and not g['failures'] and g['traversal_complete'] for g in graphs),
            'api_contracts': CONTRACTS, 'scope': 'static source candidates only; no runtime observations',
            'limitations': ['all links are candidates, including identical logical source paths',
                            'field USRs with unresolved bases identify a family, never an object instance',
                            'flow-insensitive pointer writes and escapes retain unknown alternatives',
                            'no inferred job IDs, actual transfers, worker affinity or queue residence time',
                            'bounded contexts; no complete interprocedural heap/points-to analysis',
                            'API contracts trust OAI names and compatible record tags/prototypes']}


def _connect(graph):
    operations = graph['operations']
    by_site = {}
    for row in operations:
        by_site.setdefault(_site({**row, 'origin_function': row['source']}), []).append(row)

    def origins(value, operation, user):
        def same_context(row):
            a, b = row['context'], user['context']
            return a['role_id'] == b['role_id'] and b['call_path'][:len(a['call_path'])] == a['call_path']
        return [r for v in _origins(value) for r in by_site.get(_site(v), [])
                if r['operation'] in operation and not r['blocked'] and same_context(r)]

    def link(kind, source, target, reason, response_writes=()):
        if source['blocked'] or target['blocked']:
            return
        record = {'kind': kind, 'source': source['id'], 'target': target['id'], 'resolution': 'candidate',
                  'reason': reason, 'evidence': [source['range'], target['range']]}
        if response_writes:
            record['response_write_evidence'] = [{k: w[k] for k in ('id', 'source', 'range', 'conditions', 'effect')}
                                                 for w in response_writes]
            record['evidence'] += [w['range'] for w in response_writes]
        if record not in graph['relations']:
            graph['relations'].append(record)

    producers = [o for o in operations if o['operation'] == 'enqueue']
    consumers = [o for o in operations if o['operation'] in ('dequeue', 'poll')]
    for init in [o for o in operations if o['operation'] == 'initialize']:
        for row in producers + consumers:
            if _matches(init['queue_candidates'], row['queue_candidates']):
                link('initializes', init, row, 'compatible logical queue paths; object lifetime/instance not established')
    for producer in producers:
        created = origins(producer['arguments'][1], {'create'}, producer)
        for creation in created:
            link('submits', creation, producer, 'element expression contains this allocation; writes/aliases may add alternatives')
        for consumer in consumers:
            if _matches(producer['queue_candidates'], consumer['queue_candidates']):
                link('transfers', producer, consumer, 'compatible declaration/field paths; static handoff candidate, no executed job movement')
    # Explicit return through elt->reponseFifo, connected through the actual
    # retrieval and candidate submissions, not every constructor in a target.
    for response in producers:
        queue = response['queue']
        acquired_elements = [o for m in _members(queue) if m.get('field_name') == 'reponseFifo'
                             for o in origins(m['base'], {'dequeue', 'poll'}, response)]
        for acquired in acquired_elements:
            if acquired['id'] not in {o['id'] for o in origins(response['arguments'][1], {'dequeue', 'poll'}, response)}:
                continue
            link('response_destination', acquired, response, 'response field of an acquired element; may be NULL or overwritten')
            for submission in producers:
                if submission['blocked']:
                    continue
                if not _matches(submission['queue_candidates'], acquired['queue_candidates']):
                    continue
                for creation in origins(submission['arguments'][1], {'create'}, submission):
                    link('returns_to', creation, response, 'constructor response queue is a candidate; field writes/aliasing and non-NULL guard retained')
                    for destination in consumers:
                        writes = [w for w in submission['element_response_writes']
                                  if _matches(w['queue_candidates'], destination['queue_candidates'])]
                        if (_matches(creation['queue_candidates'], destination['queue_candidates']) or
                                _matches(response['queue_candidates'], destination['queue_candidates']) or writes):
                            link('response_transfer', response, destination,
                                 'candidate response queue from allocation or producer/consumer field writes; no element identity or execution proof', writes)
    for invoke in graph['processing_calls']:
        callee = invoke['callee']
        for member in _members(callee):
            for acquired in origins(member['base'], {'dequeue', 'poll'}, invoke):
                link('processes', acquired, invoke, 'callback on a retrieved element; callback targets remain T5 candidates')
    # Preserve direct processing calls and related CFG references. This also
    # expresses gNB's NULL-callback rx_func path, whose completion is a new job.
    functions = {f['id']: f for f in graph['functions']}
    for consumer in consumers:
        f = functions[consumer['source']]
        for call in f['calls']:
            if call['queue_api'] or call['kind'] != 'direct':
                continue
            if call.get('callee_name') in ('NotifiedFifoData',):
                continue
            args = [_expand(v, f) for v in call['queue_arguments']]
            # Follow a single data-access wrapper through variable initializers.
            data_origins = [v for a in args for v in _origins(a)]
            if not any(any(o['id'] == consumer['id'] for arg in v.get('arguments', [])
                           for o in origins(arg, {'dequeue', 'poll'}, consumer)) for v in data_origins):
                continue
            edge = next(r for r in graph['call_relations'] if r['id'] == call['id'])
            target = {'id': call['id'] + '/processing/' + digest(consumer['context']),
                      'blocked': bool(edge['blocked']), 'range': call['range']}
            graph['processing_calls'].append({**target, 'source': f['id'], 'call_id': call['id'],
                                               'target_id': edge['target'], 'target_candidates': edge['candidates'],
                                               'callee_name': call['callee_name'],
                                               'conditions': call['conditions'], 'block_id': call['block_id'],
                                               'context': consumer['context']})
            link('processes', consumer, target, 'direct call consumes data derived from retrieval; static dataflow candidate')
            for completion in producers:
                if completion['source'] == edge['target']:
                    link('completion_candidate', target, completion,
                         'processing function contains another enqueue; may allocate a different element, not same-job return')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--extraction', type=Path, required=True)
    parser.add_argument('--target', action='append')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-contexts', type=int, default=10000)
    parser.add_argument('--max-depth', type=int, default=128)
    args = parser.parse_args()
    try:
        result = build_queues(json.loads(args.manifest.read_text()), json.loads(args.extraction.read_text()),
                              args.target, max_contexts=args.max_contexts, max_depth=args.max_depth)
        write(args.out / 'queues.json', result)
        write(args.out / 'failures.json', [f for g in result['graphs'] for f in g['failures'] + g['frontiers']])
        return 0 if result['ready'] else 1
    except (ValueError, KeyError, TypeError, OSError) as error:
        write(args.out / 'queues.json', {'schema_version': 1, 'kind': 'notified_fifo_graph', 'ready': False,
                                        'graphs': [], 'failures': [{'reason': str(error)}]})
        write(args.out / 'failures.json', [{'reason': str(error)}])
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
