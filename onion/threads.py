"""T4: build-scoped static main-to-thread paths from T2/T3 artifacts.

This intermediate graph is not T1 execution_structure or runtime evidence.
Issue #9 owns that adapter. Function pointers are resolved only for designators and
unmodified parameter forwarding; no variable/heap/return-value points-to claims.
"""
import argparse
import copy
import json
from pathlib import Path

from .build_inputs import digest, file_record
from .libtooling import validate_extraction, write


APIS = {
    'pthread_create': {'arity': 4, 'handle': 0, 'entry': 2, 'argument': 3, 'name': None},
    'threadCreate': {'arity': 6, 'handle': 0, 'entry': 1, 'argument': 2, 'name': 3},
}


def _load(manifest, extraction):
    if (manifest.get('kind') != 'build_input_manifest' or manifest.get('schema_version') != 1
            or manifest.get('manifest_id') != digest({k: v for k, v in manifest.items() if k != 'manifest_id'})):
        raise ValueError('expected intact T2 build_input_manifest v1')
    if (extraction.get('kind') != 'source_extraction_manifest' or extraction.get('schema_version') != 1
            or extraction.get('input_manifest_id') != manifest['manifest_id']):
        raise ValueError('expected matching T3 source_extraction_manifest v1')
    rows, failures, seen = [], [], set()
    for result in extraction['entries']:
        ordinal = result['manifest_entry']
        if type(ordinal) is not int or ordinal < 0 or ordinal >= len(manifest['entries']) or ordinal in seen:
            raise ValueError('invalid or duplicate extraction manifest_entry')
        seen.add(ordinal)
        row = manifest['entries'][ordinal]
        if result['status'] == 'extracted':
            if row['status'] != 'validated' or result['compilation_id'] != row['cache_key']:
                raise ValueError('extracted compilation does not match input')
            record = result['artifact']
            if file_record(record['path']) != record:
                raise ValueError('source extraction artifact changed')
            raw = validate_extraction(json.loads(Path(record['path']).read_text()))
            if raw['compilation_id'] != row['cache_key']:
                raise ValueError('raw compilation_id mismatch')
            # Old T3 files can still feed the local CFG adapter, but must be
            # re-extracted to supply T4's semantic argument/guard fields.
            for function in raw['functions']:
                if 'parameter_count' not in function or any('conditions' not in c for c in function['calls']):
                    raise ValueError('T4 requires re-extraction with semantic arguments and conditions')
            rows.append((row, raw))
        elif row['status'] != 'excluded':
            failures.append({'manifest_entry': ordinal, 'source': row.get('source'),
                             'selected_by': row.get('selected_by', []),
                             'reason': result.get('reason') or 'compilation not extracted'})
    for ordinal, row in enumerate(manifest['entries']):
        if ordinal not in seen and row['status'] != 'excluded':
            failures.append({'manifest_entry': ordinal, 'source': row.get('source'),
                             'selected_by': row.get('selected_by', []), 'reason': 'missing extraction result'})
    return rows, failures


def _resolve(functions, compilation, usr, linkage, signature, local=None):
    if local is not None and linkage != 'external':
        candidates = [local] if local in functions else []
    elif usr:
        candidates = [f['id'] for f in functions.values() if f['usr'] == usr
                      and f['linkage'] == linkage
                      and (linkage == 'external' or f['_compilation_id'] == compilation)
                      and f['canonical_type'] == signature]
    else:
        candidates = []
    if len(candidates) == 1:
        return candidates[0], candidates, None
    return None, sorted(candidates), ('multiple compatible definitions/compilation variants' if candidates
                                      else 'definition unavailable in selected target closure')


def _substitute(value, bindings):
    if value.get('value_kind') == 'parameter':
        index = value['parameter_index']
        if index < len(bindings):
            # Keep both the actual expression's origin and the use at this call.
            return {**copy.deepcopy(bindings[index]), 'forwarded_at': value['range']}
    result = copy.deepcopy(value)
    if 'candidates' in result:
        result['candidates'] = [_substitute(c, bindings) for c in result['candidates']]
    return result


def _entry(functions, value, compilation):
    origin = value.get('origin_compilation', compilation)
    if value.get('value_kind') == 'function':
        return _resolve(functions, origin, value['function_usr'], value['function_linkage'], value['function_type'])
    candidates = set()
    for candidate in value.get('candidates', []):
        target, possible, _ = _entry(functions, candidate, origin)
        candidates.update(possible)
    return None, sorted(candidates), 'computed/unknown function pointer; points-to analysis not performed'


def _reachable(function):
    reached = {n['id'] for n in function['blocks'] if n['entry']}
    pending = list(reached)
    outgoing = {}
    for edge in function['edges']:
        outgoing.setdefault(edge['source'], []).append(edge['target'])
    while pending:
        for child in outgoing.get(pending.pop(), []):
            if child not in reached:
                reached.add(child)
                pending.append(child)
    return reached


def build_threads(manifest, extraction, targets=None, *, max_contexts=10000, max_depth=128):
    """Keep call-site paths; candidate edges never extend a resolved path.

    All path bounds and failed/missing TUs are explicit. Different main targets
    use separate definition indexes, even for a shared library compilation.
    """
    if max_contexts < 1 or max_depth < 1:
        raise ValueError('traversal bounds must be positive')
    rows, failures = _load(manifest, extraction)
    selected = sorted({name for row in manifest['entries'] for name in row.get('selected_by', [])})
    targets = sorted(set(targets if targets is not None else selected))
    if not targets or set(targets) - set(selected):
        raise ValueError('select captured executable targets')
    graphs = []
    for target_name in targets:
        functions = {}
        for row, raw in rows:
            if target_name not in row['selected_by']:
                continue
            for function in raw['functions']:
                if function['id'] in functions:
                    raise ValueError('duplicate function across extraction artifacts')
                functions[function['id']] = {**copy.deepcopy(function), '_compilation_id': raw['compilation_id']}
        mains = [f['id'] for f in functions.values() if f['name'] == 'main' and f['linkage'] == 'external']
        graph = {'target': target_name, 'root_id': mains[0] if len(mains) == 1 else None,
                 'root_candidates': sorted(mains), 'functions': list(functions.values()),
                 'relations': [], 'spawns': [], 'frontiers': [],
                 'failures': [f for f in failures if not f['selected_by'] or target_name in f['selected_by']],
                 'traversal_complete': True}
        graphs.append(graph)
        if len(mains) != 1:
            graph['frontiers'].append({'call_path': [], 'reason': 'main missing or ambiguous', 'candidates': mains})
            graph['traversal_complete'] = False
            continue
        calls, relations = {}, {}
        for function in functions.values():
            reachable = _reachable(function)
            for call in function['calls']:
                calls[call['id']] = call
                resolved, candidates, reason = _resolve(
                    functions, function['_compilation_id'], call['callee_usr'], call['callee_linkage'],
                    call.get('callee_canonical_type'), call['target_id'])
                if call['kind'] != 'direct':
                    resolved, candidates, reason = None, [], 'indirect call; points-to analysis not performed'
                api = APIS.get(call.get('thread_api')) if call['callee_linkage'] == 'external' else None
                if api and (call['callee_usr'] != 'c:@F@' + call['callee_name'] or len(call['arguments']) != api['arity']):
                    api = None
                blocked = None
                if call['block_id'] not in reachable:
                    blocked = 'call has no reachable CFG placement'
                elif any(u.get('affects_completeness') for u in function['uncertainties']):
                    blocked = 'enclosing function has incomplete semantics/CFG'
                if blocked:
                    resolved, reason = None, blocked
                relation = {'id': call['id'], 'kind': 'calls', 'source': function['id'], 'target': resolved,
                            'resolution': 'resolved' if resolved else 'unresolved', 'candidates': candidates,
                            'reason': reason, 'range': call['range'], 'conditions': call['conditions'],
                            'api': call['callee_name'] if api else None, 'blocked': blocked}
                relations[call['id']] = relation
                graph['relations'].append(relation)
        pending = [(mains[0], [], [], [], [])]
        contexts = 0
        while pending:
            function_id, bindings, path, conditions, stack = pending.pop()
            contexts += 1
            if contexts > max_contexts:
                graph['frontiers'].append({'call_path': path, 'reason': 'context limit reached',
                                          'remaining_contexts': len(pending) + 1})
                graph['traversal_complete'] = False
                break
            if function_id in stack or len(path) >= max_depth:
                graph['frontiers'].append({'call_path': path, 'reason': 'recursion/depth limit; expansion stopped'})
                graph['traversal_complete'] = False
                continue
            function = functions[function_id]
            for call in function['calls']:
                relation = relations[call['id']]
                chain = path + [call['id']]
                guards = conditions + [{'call_id': call['id'], **copy.deepcopy(c)} for c in call['conditions']]
                actuals = [_substitute({**a, 'origin_compilation': function['_compilation_id']}, bindings)
                           for a in call['arguments']]
                if relation['api'] and not relation['blocked']:
                    api = APIS[relation['api']]
                    entry = actuals[api['entry']]
                    start, candidates, reason = _entry(functions, entry, function['_compilation_id'])
                    role = 'thread/' + digest({'target': target_name, 'call_path': chain})
                    spawn = {'id': role, 'kind': 'thread_role', 'api': relation['api'],
                             'creation_call_id': call['id'], 'call_path': chain,
                             'source': function_id, 'range': call['range'], 'conditions': guards,
                             'handle': actuals[api['handle']], 'entry_expression': entry,
                             'argument': actuals[api['argument']],
                             'role_name': actuals[api['name']] if api['name'] is not None else None,
                             'start_id': start, 'candidates': candidates,
                             'resolution': 'resolved' if start else 'unresolved', 'reason': reason}
                    graph['spawns'].append(spawn)
                    graph['relations'].extend([
                        {'id': role + '/spawns', 'kind': 'spawns', 'source': function_id,
                         'target': role, 'resolution': 'resolved', 'range': call['range'], 'conditions': guards},
                        {'id': role + '/starts', 'kind': 'starts', 'source': role,
                         'target': start, 'candidates': candidates, 'resolution': spawn['resolution'],
                         'reason': reason, 'range': entry['range'], 'conditions': guards}])
                    # The API is a semantic terminal. Do not traverse its body
                    # and count its implementation pthread_create a second time.
                elif relation['target']:
                    pending.append((relation['target'], actuals, chain, guards, stack + [function_id]))
                else:
                    graph['frontiers'].append({'call_path': chain, 'call_id': call['id'],
                                              'reason': relation['reason'], 'candidates': relation['candidates'],
                                              'range': call['range'], 'conditions': guards})
        graph['contexts'] = min(contexts, max_contexts)
        graph['extraction_complete'] = not graph['failures']
    return {'schema_version': 1, 'kind': 'thread_entrypoint_graph',
            'input_manifest_id': manifest['manifest_id'], 'extraction_digest': digest(extraction),
            'api_contracts': APIS, 'graphs': graphs,
            'ready': all(g['root_id'] and g.get('extraction_complete') and g['traversal_complete'] for g in graphs),
            'scope': 'static source call paths and creation sites; no execution or path-feasibility claims',
            'limitations': ['opaque variables/casts/returns and indirect calls are unresolved',
                            'lexical guards and CFG placement do not establish feasible paths',
                            'API names/prototypes are trusted pthread_create/OAI threadCreate contracts',
                            'no dynamic thread IDs, creation counts or scheduling',
                            'bounded call-site expansion; recursion/limits retained as frontiers']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--extraction', type=Path, required=True)
    parser.add_argument('--target', action='append')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-contexts', type=int, default=10000)
    parser.add_argument('--max-depth', type=int, default=128)
    options = parser.parse_args()
    try:
        result = build_threads(json.loads(options.manifest.read_text()),
                               json.loads(options.extraction.read_text()), options.target,
                               max_contexts=options.max_contexts, max_depth=options.max_depth)
        write(options.out / 'threads.json', result)
        write(options.out / 'failures.json', [f for g in result['graphs'] for f in g['failures']])
        return 0 if result['ready'] else 1
    except (ValueError, KeyError, TypeError, OSError) as error:
        write(options.out / 'threads.json', {'schema_version': 1, 'kind': 'thread_entrypoint_graph',
                                           'ready': False, 'graphs': [], 'failures': [{'reason': str(error)}]})
        write(options.out / 'failures.json', [{'reason': str(error)}])
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
