"""Export T7 workspaces as an offline, progressively expanded structure viewer.

This is a display projection, not a reconstruction: static resolution never
creates runtime evidence. CFGs need no probes merely to be inspected.
"""
import argparse
import copy
import json
from pathlib import Path

from .demo import render_report
from .execution import validate_structure
from .integration import _intact


def project_workspace(workspace):
    _intact(workspace, 'execution_workspace')
    validate_structure(workspace['structure'])
    entities = {e['id']: copy.deepcopy(e) for e in workspace['structure']['entities']
                if e['kind'] in ('function', 'thread_role', 'queue', 'callback')}
    raw_ids, cfgs = {}, {}
    for fid, source in workspace['functions'].items():
        raw = workspace['extractions'][source['compilation_id']]
        function = next(f for f in raw['functions'] if f['id'] == source['source_function_id'])
        raw_ids[function['id']] = fid
        scoped = {b['id']: fid + '/' + b['id'] for b in function['blocks']}
        cfgs[fid] = {'function': function['name'], 'build_id': workspace['build_id'],
            'function_id': fid, 'nodes': [
                {'id': scoped[b['id']], 'clang_id': b['clang_id'], 'entry': b['entry'],
                 'exit': b['exit'], 'sites': [], 'label': b['clang_id'],
                 'text': '\n'.join(e.get('text', e['kind']) for e in b['elements']),
                 'source_block': copy.deepcopy(b)} for b in function['blocks']],
            'edges': [{**e, 'source': scoped[e['source']], 'target': scoped[e['target']]}
                      for e in function['edges']],
            'uncertainties': copy.deepcopy(function.get('uncertainties', [])),
            'provenance': {'compilation_id': source['compilation_id'],
                           'clang_version': raw.get('clang_version'), 'range': function.get('range'),
                           'complete': raw.get('complete'), 'limitations': copy.deepcopy(raw.get('limitations', []))}}
        # Display the entry neighborhood first even when Clang emits exit-first
        # block order. Keep unreachable blocks for later pages and inspection.
        by_id = {n['id']: n for n in cfgs[fid]['nodes']}
        outgoing = {}
        for edge in cfgs[fid]['edges']:
            outgoing.setdefault(edge['source'], []).append(edge['target'])
        order = [n['id'] for n in cfgs[fid]['nodes'] if n['entry']]
        reached = set(order)
        for node in order:
            for successor in outgoing.get(node, []):
                if successor not in reached:
                    reached.add(successor)
                    order.append(successor)
        cfgs[fid]['nodes'] = [by_id[n] for n in order] + [n for n in by_id.values() if n['id'] not in reached]
    links = copy.deepcopy(workspace['structure']['relations'])
    locations = {e['id']: e['identity'] for e in workspace['structure']['entities'] if e['kind'] == 'location'}
    for link in links:
        link['locations'] = [locations[k] for k in link['evidence']['location_ids']]
    # T1 binds describes callback -> target. Expose its source registration as
    # a display attachment, retaining the original record rather than inventing
    # a call or an executed relation from the registering function.
    refs = copy.deepcopy(workspace['references'])
    for entity in entities.values():
        candidates = [r for r in refs if r['origin_id'] == entity['id']]
        if entity['kind'] == 'thread_role' and candidates:
            record = candidates[0]['source_record']
            entity['label'] = 'thread: ' + (record.get('role_name') or {}).get('text',
                cfgs.get(candidates[0]['function_id'], {}).get('function', entity['id'][-8:]))
        elif entity['kind'] == 'queue':
            expressions = list(dict.fromkeys(r['source_record'].get('queue', {}).get('text', '')
                               for r in candidates if r['category'].startswith('queue_')))
            entity['label'] = 'Queue: ' + (' / '.join(x for x in expressions if x) or entity['id'][-8:])
        elif entity['kind'] == 'callback':
            targets = list(dict.fromkeys(cfgs[r['function_id']]['function'] for r in candidates))
            entity['label'] = 'callback: ' + (' / '.join(targets) or 'NULL / 未解決')
            # NULL-only registrations have no function_reference, but remain
            # visible through their original callback registration record.
            records = [row for g in workspace['sources'].get('callbacks', {}).get('graphs', [])
                       for row in g['registrations'] if row['id'] == entity['identity']['key']]
            for record in records:
                source = raw_ids.get(record['source'])
                if source:
                    links.append({'id': 'registration/' + entity['id'], 'kind': 'registration',
                        'source': source, 'target': entity['id'],
                        'resolution': 'unresolved' if record['resolution'] == 'null' else record['resolution'],
                        'source_record': copy.deepcopy(record), 'locations': [],
                        'evidence': {'conditions': record.get('conditions', []),
                                     'reason': record.get('reason') or ('NULL callback alternative' if record['nullable'] else None),
                                     'method': 'source registration attachment'}})
    thread_graphs = workspace['sources']['threads']['graphs']
    roots = list(dict.fromkeys(raw_ids[r] for g in thread_graphs
                for r in g.get('root_candidates', [g.get('root_id')]) if r in raw_ids))
    return {'id': workspace['id'], 'target': workspace['target'], 'build_id': workspace['build_id'],
        'structure_id': workspace['structure']['structure_id'], 'roots': roots,
        'entities': list(entities.values()), 'links': links, 'references': refs, 'cfgs': cfgs,
        'incompleteness': copy.deepcopy(workspace['incompleteness']),
        'frontiers': copy.deepcopy(workspace['frontiers']), 'runtime_evidence': False}


def export_viewer(workspaces, destination, session=None):
    """Keep an optional existing fixture timeline detached from static builds."""
    return _export_views([project_workspace(w) for w in workspaces], destination, session)


def _focus_view(view, function_names):
    """Keep full structure; scope offline CFG bodies and refs explicitly."""
    names = set(function_names) | {'main'}
    cfgs = {key: cfg for key, cfg in view['cfgs'].items() if cfg['function'] in names}
    return {**view, 'cfgs': cfgs,
            'references': [r for r in view['references'] if r['function_id'] in cfgs],
            'display_scope': {'kind': 'audited_function_cfg_subset',
                'function_names': sorted(names), 'displayed_functions': len(cfgs),
                'extracted_functions': len(view['cfgs']),
                'scope': 'HTML contains audited function CFGs; all functions remain in workspace/native JSON',
                'excluded_functions': [{'function_id': key, 'function': cfg['function']}
                                       for key, cfg in view['cfgs'].items() if key not in cfgs]}}


def _export_views(views, destination, session=None):
    """Serialize projections already validated in the current operation."""
    data = copy.deepcopy(session) if session is not None else {
        'schema_version': 1, 'stages': [], 'oai_cfg': [],
        'limits': ['ソース抽出の静的構造です。実行証跡・実行時間は含みません。',
                   '非同期接続は関数内CFGの辺へ追加しません。',
                   '抽出・検証の対象範囲と未解決点は付属のmanifest・受入結果で確認してください。']}
    data['execution_views'] = views
    if not data['execution_views']:
        raise ValueError('provide at least one workspace')
    if len({v['id'] for v in data['execution_views']}) != len(data['execution_views']):
        raise ValueError('duplicate workspace')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    render_report(data, destination)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, action='append', required=True)
    parser.add_argument('--session', type=Path, help='Optional existing fixture session.json (separate build/run)')
    parser.add_argument('--out', type=Path, required=True)
    options = parser.parse_args()
    export_viewer([json.loads(p.read_text()) for p in options.workspace], options.out,
                  json.loads(options.session.read_text()) if options.session else None)


if __name__ == '__main__':
    main()
