"""T5: conservative Actor/task callback candidates with source execution contexts.

Consumes actual T2/T3 inputs and recomputes T4; never manufactures runtime facts.
Protocol matching is a may-association within one executable, not queue aliasing.
"""
import argparse
import copy
import json
from pathlib import Path

from .build_inputs import digest
from .libtooling import write
from .threads import build_threads, _entry, _reachable


def _storage_key(value):
    """Declaration identity shared by an object and its member effects."""
    while value.get('value_kind') == 'member':
        value = value['base']
    if 'parameter_index' in value:
        return 'parameter', value['parameter_index']
    if value.get('variable_usr'):
        return 'variable', value['variable_usr']
    return None


def _expand(value, function, bindings=(), seen=()):
    """Flow-insensitive local write alternatives; unknown writes are not killed.

    No single initializer implies immutable storage. Aliases/escapes and later
    writes remain possible; all storage-derived function targets are candidates.
    """
    value = copy.deepcopy(value)
    kind = value.get('value_kind')
    value.setdefault('origin_compilation', function['_compilation_id'])
    key = _storage_key(value)
    effects = [w for w in function['callback_writes']
               if key is not None and _storage_key(w['target']) == key]
    if kind == 'parameter':
        if effects:
            # Also defend against older parameter/member extraction shapes:
            # the original actual is only one candidate after a member effect.
            value['value_kind'] = kind = 'variable'
        elif value['parameter_index'] < len(bindings):
            return copy.deepcopy(bindings[value['parameter_index']])
    if kind == 'variable':
        if key in seen:
            return {**value, 'alternatives': [], 'unknown': True}
        writes = [w for w in effects if w['target'].get('value_kind') != 'member']
        value['alternatives'] = [_expand(w['value'], function, bindings, (*seen, key)) for w in writes]
        index = value.get('parameter_index')
        if index is not None and index < len(bindings):
            value['alternatives'].append(copy.deepcopy(bindings[index]))
        value['write_evidence'] = [{'id': w['id'], 'range': w['range'], 'conditions': w['conditions'],
                                    'effect': w.get('effect', 'write')} for w in effects]
        value['unknown'] = True  # no alias/escape/definite-assignment proof
    elif kind == 'member':
        value['base'] = _expand(value['base'], function, bindings, seen)
    elif kind == 'record':
        for field in value['fields']:
            field['value'] = _expand(field['value'], function, bindings, seen)
    elif kind == 'call_result':
        value['arguments'] = [_expand(a, function, bindings, seen) for a in value['arguments']]
    if 'candidates' in value:
        value['candidates'] = [_expand(a, function, bindings, seen) for a in value['candidates']]
    return value


def _fields(value, protocol):
    """Extract callback slots from a task record, retaining opaque alternatives."""
    kind = value.get('value_kind')
    if kind == 'record':
        return [f['value'] for f in value['fields'] if f.get('callback_protocol') == protocol]
    if kind == 'variable':
        found = [v for a in value.get('alternatives', []) for v in _fields(a, protocol)]
        return [{'value_kind': 'opaque', 'candidates': found, 'unknown': True,
                 'storage': value,
                 'reason': 'local storage/alias writes not proven complete'}]
    if 'candidates' in value:
        return [v for a in value['candidates'] for v in _fields(a, protocol)]
    return [{'value_kind': 'opaque', 'reason': 'callback slot unavailable in computed task'}]


def _choices(value, functions, definitions, compilation):
    kind = value.get('value_kind')
    if kind == 'function':
        target, candidates, reason = _entry(functions, definitions, value, compilation)
        return candidates, False, target is None, reason
    if kind == 'null':
        return [], True, False, None
    alternatives = value.get('candidates', value.get('alternatives', []))
    candidates, nullable, unknown = set(), False, kind != 'opaque' or not alternatives
    reasons = [value['reason']] if value.get('reason') else []
    for child in alternatives:
        found, null, opaque, reason = _choices(child, functions, definitions, compilation)
        candidates.update(found)
        nullable |= null
        unknown |= opaque
        if reason:
            reasons.append(reason)
    # Conditional expressions enumerate alternatives; computed/storage values
    # remain uncertain even if only one function designator was found.
    unknown |= bool(value.get('unknown')) or (kind == 'opaque' and not value.get('candidates'))
    return sorted(candidates), nullable, unknown, '; '.join(sorted(set(reasons))) or None


def _contexts(graph, max_contexts, max_depth):
    functions = {f['id']: f for f in graph['functions']}
    edges = {r['id']: r for r in graph['relations'] if r['kind'] == 'calls'}
    roots = [(graph['root_id'], 'main/' + graph['target'])] if graph['root_id'] else []
    roots += [(s['start_id'], s['id']) for s in graph['spawns'] if s['resolution'] == 'resolved']
    pending = [(f, role, [], [], [], []) for f, role in roots]
    contexts, frontiers, count = {}, [], 0
    while pending:
        fid, role, path, bindings, conditions, stack = pending.pop()
        count += 1
        if count > max_contexts:
            frontiers.append({'reason': 'callback context limit reached', 'call_path': path})
            break
        if fid in stack or len(path) >= max_depth:
            frontiers.append({'reason': 'callback recursion/depth limit reached', 'call_path': path})
            continue
        function = functions[fid]
        contexts.setdefault(fid, []).append({'role_id': role, 'call_path': path,
                                            'conditions': conditions, 'bindings': bindings})
        for call in function['calls']:
            edge = edges[call['id']]
            if edge['target'] and not edge['api']:
                actuals = [_expand(a, function, bindings) for a in call['arguments']]
                pending.append((edge['target'], role, path + [call['id']], actuals,
                                conditions + call['conditions'], stack + [fid]))
    return contexts, frontiers


def build_callbacks(manifest, extraction, targets=None, *, max_contexts=10000, max_depth=128):
    threads = build_threads(manifest, extraction, targets, max_contexts=max_contexts, max_depth=max_depth)
    graphs = []
    for parent in threads['graphs']:
        functions = {f['id']: f for f in parent['functions']}
        definitions = {}
        for f in functions.values():
            if 'callback_writes' not in f or any('callee_value' not in c for c in f['calls']):
                raise ValueError('T5 requires re-extraction with callback values/writes')
            key = (f['usr'], f['linkage'], f['canonical_type'],
                   None if f['linkage'] == 'external' else f['_compilation_id'])
            definitions.setdefault(key, []).append(f['id'])
        contexts, frontiers = _contexts(parent, max_contexts, max_depth)
        graph = {'target': parent['target'], 'root_id': parent['root_id'], 'functions': parent['functions'],
                 'spawns': parent['spawns'], 'registrations': [], 'submissions': [],
                 'invocations': [], 'relations': [], 'retrievals': [],
                 'frontiers': frontiers, 'thread_frontiers': parent['frontiers'], 'failures': parent['failures'],
                 'traversal_complete': parent['traversal_complete'] and not frontiers}
        graphs.append(graph)
        for function in functions.values():
            compilation = function['_compilation_id']
            available = contexts.get(function['id'], [{'role_id': None, 'call_path': [], 'conditions': [], 'bindings': [],
                                                       'reason': 'source site has no proven main/thread call path'}])
            reached = _reachable(function)
            incomplete = any(u.get('affects_completeness') for u in function['uncertainties'])
            for call in function['calls']:
                api = call.get('callback_api')
                protocol = call.get('callee_value', {}).get('callback_protocol')
                if not api and protocol not in ('actor', 'pool'):
                    if call.get('callee_name') in ('try_pop_not_q', 'pop_not_q'):
                        graph['retrievals'].append({'call_id': call['id'], 'source': function['id'],
                                                   'range': call['range'], 'conditions': call['conditions'],
                                                   'queue_expression': call['arguments'], 'operation': call['callee_name']})
                    continue
                blocked = incomplete or call['block_id'] not in reached
                if blocked:
                    graph['frontiers'].append({'call_id': call['id'], 'range': call['range'],
                                               'reason': 'callback site has incomplete semantics or no reachable CFG placement'})
                public_contexts = [{k: copy.deepcopy(v) for k, v in c.items() if k != 'bindings'} for c in available]
                common = {'source': function['id'], 'call_id': call['id'], 'range': call['range'],
                          'conditions': call['conditions'], 'contexts': public_contexts, 'blocked': blocked}
                if api == 'actor_submit':
                    graph['submissions'].append({**common, 'id': call['id'] + '/submit', 'protocol': 'actor',
                                                 'destination': call['arguments'][0], 'object': call['arguments'][1]})
                elif api in ('actor_register', 'pool_submit'):
                    protocol = 'actor' if api == 'actor_register' else 'pool'
                    values = []
                    for context in available:
                        actual = _expand(call['arguments'][3 if protocol == 'actor' else 1], function, context['bindings'])
                        values.extend([actual] if protocol == 'actor' else _fields(actual, protocol))
                    # Explicit callback field writes are kept alongside task
                    # initializers. Same declaration is source evidence, not a
                    # proof that this is the last write before submission.
                    assignment_evidence = []
                    if protocol == 'pool':
                        obj = call['arguments'][1]
                        for assignment in function['callback_writes']:
                            lhs = assignment['target']
                            if ((lhs.get('callback_protocol') == 'pool' or assignment.get('effect') == 'address_escape') and
                                    _storage_key(obj) is not None and _storage_key(lhs) == _storage_key(obj)):
                                for context in available:
                                    values.append(_expand(assignment['value'], function, context['bindings']))
                                assignment_evidence.append(assignment)
                    choices = [_choices(v, functions, definitions, compilation) for v in values]
                    candidates = sorted({c for found, _, _, _ in choices for c in found})
                    nullable = any(n for _, n, _, _ in choices)
                    unknown = any(u for _, _, u, _ in choices) or blocked
                    reasons = sorted({reason for _, _, _, reason in choices if reason})
                    state = ('unresolved' if unknown or not candidates and not nullable else
                             'null' if nullable and not candidates else
                             'resolved' if len(candidates) == 1 and not nullable else 'candidate')
                    registration = {**common, 'id': call['id'] + '/callback', 'protocol': protocol,
                                    'values': values, 'candidates': candidates, 'nullable': nullable,
                                    'assignment_evidence': assignment_evidence,
                                    'unknown': unknown, 'resolution': state,
                                    'reason': ('; '.join(['computed/storage callback or incomplete source; candidates are not exhaustive', *reasons]) if unknown
                                               else 'multiple callback alternatives' if state == 'candidate' else None)}
                    graph['registrations'].append(registration)
                    for target in candidates:
                        graph['relations'].append({'kind': 'binds', 'source': registration['id'], 'target': target,
                                                   'resolution': 'resolved' if state == 'resolved' else 'candidate',
                                                   'range': call['range'], 'conditions': call['conditions']})
                    if protocol == 'pool':
                        graph['submissions'].append({**common, 'id': call['id'] + '/submit', 'protocol': 'pool',
                                                     'callback_id': registration['id'], 'destination': call['arguments'][0],
                                                     'object': call['arguments'][1]})
                elif protocol:
                    graph['invocations'].append({**common, 'id': call['id'] + '/invoke', 'protocol': protocol,
                                                 'receiver': call['callee_value'], 'arguments': call['arguments']})
        # The channel/type protocol is intentionally a may-join. T6 refines
        # element/queue aliases. Never attach a task to one fixed worker index.
        for invocation in graph['invocations']:
            matches = [r for r in graph['registrations'] if r['protocol'] == invocation['protocol']]
            if not matches:
                graph['relations'].append({'kind': 'invokes', 'source': invocation['id'], 'target': None,
                                           'resolution': 'unresolved', 'reason': 'no callback registration in selected target',
                                           'evidence': [invocation['range']]})
            for registration in matches:
                graph['relations'].append({'kind': 'invokes', 'source': invocation['id'], 'target': registration['id'],
                                           'resolution': 'candidate',
                                           'reason': 'same target/protocol; object/queue alias and path feasibility unresolved',
                                           'conditions': {'registration': registration['conditions'], 'invocation': invocation['conditions']},
                                           'evidence': [registration['range'], invocation['range']],
                                           'scheduling': 'any eligible pool worker; acquisition/steal not a fixed worker mapping'
                                                         if invocation['protocol'] == 'pool' else 'actor or caller inline context'})
        for submission in graph['submissions']:
            for registration in graph['registrations']:
                if registration['protocol'] != submission['protocol']:
                    continue
                if submission['protocol'] == 'pool' and submission['callback_id'] != registration['id']:
                    continue
                graph['relations'].append({'kind': 'submits', 'source': submission['id'], 'target': registration['id'],
                                           'resolution': 'candidate',
                                           'reason': 'task supplied at this call' if submission['protocol'] == 'pool' else
                                                     'same target/protocol; element/queue alias unresolved',
                                           'evidence': [submission['range'], registration['range']]})
        graph['scheduling'] = {'worker_affinity': 'not inferred', 'ordering': 'not inferred',
                               'retrieval_policy': 'source acquisition/steal candidates; queue ownership unresolved'}
    return {'schema_version': 1, 'kind': 'callback_execution_graph',
            'input_manifest_id': manifest['manifest_id'], 'extraction_digest': digest(extraction),
            'thread_graph_digest': digest(threads), 'graphs': graphs,
            'ready': threads['ready'] and all(g['traversal_complete'] and not g['failures'] for g in graphs),
            'scope': 'static callback alternatives and source execution contexts; no runtime evidence',
            'limitations': ['protocol may-joins do not prove object or queue identity',
                            'local writes are flow-insensitive candidates; aliases/escapes remain unknown',
                            'NULL is a value alternative, never a callable function',
                            'no callback target is used to extend a proven main/thread path',
                            'source guards do not prove feasible paths or execution order']}


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
        result = build_callbacks(json.loads(args.manifest.read_text()), json.loads(args.extraction.read_text()),
                                 args.target, max_contexts=args.max_contexts, max_depth=args.max_depth)
        write(args.out / 'callbacks.json', result)
        write(args.out / 'failures.json', [f for g in result['graphs'] for f in g['failures']])
        return 0 if result['ready'] else 1
    except (ValueError, KeyError, TypeError, OSError) as error:
        failure = {'reason': str(error)}
        write(args.out / 'callbacks.json', {'kind': 'callback_execution_graph', 'schema_version': 1,
                                          'ready': False, 'graphs': [], 'failures': [failure]})
        write(args.out / 'failures.json', [failure])
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
