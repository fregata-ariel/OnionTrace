"""Pinned OAI source audit and full-build T4 acceptance gate for Issue #11.

A source audit is not a successful OAI build/extraction. The full gate requires
T2's OAI manifest and T4's resolved, source-position-matched paths for both mains.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from .build_inputs import digest
from .libtooling import write

EXPECTATIONS = Path(__file__).resolve().parents[1] / 'config/oai-thread-paths.json'


def audit_sources(root, expected):
    root = Path(root).resolve()
    sources = {}
    for record in expected['sources']:
        path = root / record['path']
        data = path.read_bytes()
        if 'sha256:' + sha256(data).hexdigest() != record['sha256']:
            raise ValueError('pinned source hash mismatch: ' + record['path'])
        sources[record['path']] = data
    sites = [c['site'] for path in expected['paths'] for c in path['calls']] + [expected['api_forwarding']]
    for site in sites:
        data = sources[site['path']]
        if (data[site['start_offset']:site['end_offset']].decode() != site['text']
                or data[:site['start_offset']].count(b'\n') + 1 != site['line']):
            raise ValueError('pinned source position mismatch: ' + site['path'])
    return {'kind': 'oai_thread_source_audit', 'revision': expected['revision'],
            'sources': expected['sources'], 'paths': expected['paths'],
            'source_audit_passed': True, 'full_extraction_verified': False,
            'scope': 'reviewed source expectations only; no compile conditions or execution claims'}


def verify_paths(manifest, graph, expected):
    if (manifest.get('profile') != 'oai' or not manifest.get('ready')
            or manifest.get('manifest_id') != digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
            or manifest['context']['source'].get('commit') != expected['revision']
            or graph.get('kind') != 'thread_entrypoint_graph'
            or graph.get('input_manifest_id') != manifest['manifest_id']):
        raise ValueError('full OAI acceptance needs ready matching pinned OAI artifacts')
    verified = []
    for path in expected['paths']:
        graphs = [g for g in graph['graphs'] if g['target'] == path['target']]
        if len(graphs) != 1:
            raise ValueError('expected exactly one target graph: ' + path['target'])
        target = graphs[0]
        if not target.get('root_id') or not target.get('extraction_complete') or target.get('failures'):
            raise ValueError('selected target needs a main and complete TU extraction')
        functions = {f['id']: f for f in target['functions']}
        calls = {c['id']: (f, c) for f in target['functions'] for c in f['calls']}
        relations = {r['id']: r for r in target['relations'] if r['kind'] == 'calls'}
        root = functions[target['root_id']]
        if root['name'] != 'main':
            raise ValueError('target root must be main')
        matches = []
        for spawn in target['spawns']:
            if (spawn['resolution'] != 'resolved' or functions[spawn['start_id']]['name'] != path['start']
                    or len(spawn['call_path']) != len(path['calls'])):
                continue
            valid = True
            for index, (key, step) in enumerate(zip(spawn['call_path'], path['calls'])):
                caller, call = calls[key]
                position = call['range']['expansion']
                actual_path = Path(position['begin']['path'])
                relative = Path(step['site']['path'])
                # Exact root-relative suffix + hashed dependency, not a basename.
                dependency = [d for row in manifest['entries'] if row.get('cache_key') == caller['_compilation_id']
                              for d in row['dependencies'] if Path(d['path']) == actual_path]
                expected_hash = next(s['sha256'] for s in expected['sources'] if s['path'] == str(relative))
                valid &= (caller['name'] == step['caller'] and call['callee_name'] == step['callee']
                          and actual_path.parts[-len(relative.parts):] == relative.parts
                          and position['begin']['offset'] == step['site']['start_offset']
                          and position['end']['offset'] == step['site']['end_offset']
                          and any(d['sha256'] == expected_hash for d in dependency))
                if index == 0:
                    valid &= caller['id'] == target['root_id']
                if index < len(path['calls']) - 1:
                    edge = relations[key]
                    next_caller = calls[spawn['call_path'][index + 1]][0]
                    valid &= edge['resolution'] == 'resolved' and edge['target'] == next_caller['id']
                else:
                    valid &= call.get('thread_api') == 'threadCreate' and spawn['creation_call_id'] == key
            if valid:
                matches.append(spawn['id'])
        if not matches:
            raise ValueError('resolved pinned source path absent: ' + path['target'] + ' -> ' + path['start'])
        verified.append({'target': path['target'], 'start': path['start'], 'spawn_ids': matches})
    return {'kind': 'oai_thread_path_acceptance', 'revision': expected['revision'],
            'full_extraction_verified': True, 'verified_paths': verified,
            'scope': 'five audited paths from actual OAI inputs; unrelated frontiers remain explicit',
            'traversal_complete': all(g['traversal_complete'] for g in graph['graphs'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--threads', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(EXPECTATIONS.read_text())
    try:
        result = {}
        if args.source_root:
            result['source_audit'] = audit_sources(args.source_root, expected)
        if args.manifest or args.threads:
            if not args.manifest or not args.threads:
                raise ValueError('both --manifest and --threads are required for full acceptance')
            result['acceptance'] = verify_paths(json.loads(args.manifest.read_text()),
                                                json.loads(args.threads.read_text()), expected)
        if not result:
            raise ValueError('provide --source-root or both --manifest and --threads')
        write(args.out, result)
        return 0
    except (ValueError, KeyError, OSError) as error:
        write(args.out, {'full_extraction_verified': False, 'failure': str(error)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
