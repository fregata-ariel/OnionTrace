"""Pinned OAI T5 source audit and actual-extraction gate for Issue #11."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from .build_inputs import digest
from .libtooling import write
from .callbacks import build_callbacks

EXPECTATIONS = Path(__file__).resolve().parents[1] / 'config/oai-callback-sites.json'


def audit_sources(root, expected):
    root = Path(root).resolve()
    sources = {}
    for record in expected['sources']:
        data = (root / record['path']).read_bytes()
        if 'sha256:' + sha256(data).hexdigest() != record['sha256']:
            raise ValueError('pinned source hash mismatch: ' + record['path'])
        sources[record['path']] = data
    for site in expected['sites']:
        data = sources[site['path']]
        if (data[site['start_offset']:site['end_offset']].decode() != site['text']
                or data[:site['start_offset']].count(b'\n') + 1 != site['line']):
            raise ValueError('pinned source position mismatch: ' + site['key'])
    return {'schema_version': 1, 'kind': 'oai_callback_source_audit', 'revision': expected['revision'],
            'source_audit_passed': True, 'full_extraction_verified': False,
            'sources': expected['sources'], 'sites': expected['sites']}


def verify_extraction(manifest, extraction, expected):
    """Recompute graph from intact inputs; a hand-written may-link cannot pass.

    Requires the audited sites themselves and their semantic role, not function
    names alone. Missing dynamic-library compilations are explicit failures.
    """
    if (manifest.get('profile') != 'oai' or not manifest.get('ready') or
            manifest['context']['source'].get('commit') != expected['revision']):
        raise ValueError('full OAI acceptance needs ready pinned OAI input manifest')
    graph = build_callbacks(manifest, extraction)
    required = {'nr-softmodem', 'nr-uesoftmodem'}
    if not required <= {g['target'] for g in graph['graphs']}:
        raise ValueError('full OAI acceptance needs both executable targets')
    matches, failures = [], []
    groups = {'actor_invoke': 'invocations', 'actor_inline': 'invocations', 'actor_tx_inline': 'invocations',
              'pool_inline': 'invocations', 'pool_invoke': 'invocations',
              'pool_steal': 'retrievals', 'pool_acquire': 'retrievals', 'actor_submission': 'submissions'}
    for site in expected['sites']:
        hits = []
        for target in graph['graphs']:
            if site['path'] == 'executables/nr-ue.c' and target['target'] != 'nr-uesoftmodem':
                continue
            functions = {f['id']: f for f in target['functions']}
            for row in target[groups.get(site['key'], 'registrations')]:
                position = row['range']['expansion']
                begin, end = position['begin'], position['end']
                path = Path(begin['path'])
                relative = Path(site['path'])
                owner = functions[row['source']]
                if (site.get('callback_symbol') and site['callback_symbol'] not in
                        {functions[c]['name'] for c in row.get('candidates', [])}):
                    continue
                if site.get('nullable') and not row.get('nullable'):
                    continue
                if site.get('thread_start'):
                    roles = {s['id'] for s in target['spawns'] if s['resolution'] == 'resolved' and
                             functions[s['start_id']]['name'] == site['thread_start']}
                    if not any(c['role_id'] in roles for c in row.get('contexts', [])):
                        continue
                dependencies = [d for e in manifest['entries'] if e.get('cache_key') == owner['_compilation_id']
                                for d in e['dependencies'] if d['path'] == str(path)]
                expected_hash = next(s['sha256'] for s in expected['sources'] if s['path'] == site['path'])
                if (owner['name'] == site['function'] and path.parts[-len(relative.parts):] == relative.parts and
                        begin['offset'] == site['start_offset'] and end['offset'] == site['end_offset'] and
                        any(d['sha256'] == expected_hash for d in dependencies) and not row.get('blocked')):
                    hits.append({'target': target['target'], 'id': row.get('id', row['call_id'])})
        if hits:
            matches.append({'key': site['key'], 'matches': hits})
        else:
            failures.append({'key': site['key'], 'reason': 'audited semantic site absent from actual target extraction'})
    return {'schema_version': 1, 'kind': 'oai_callback_acceptance', 'revision': expected['revision'],
            'full_extraction_verified': not failures and graph['ready'], 'matches': matches, 'failures': failures,
            'callback_graph_digest': digest(graph),
            'scope': 'actual audited callback sites; protocol associations remain candidates; no queue alias proof'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--extraction', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        expected = json.loads(EXPECTATIONS.read_text())
        if args.manifest and args.extraction:
            result = verify_extraction(json.loads(args.manifest.read_text()), json.loads(args.extraction.read_text()), expected)
            passed = result['full_extraction_verified']
        elif args.source_root:
            result = audit_sources(args.source_root, expected)
            passed = result['source_audit_passed']
        else:
            raise ValueError('provide --source-root or both --manifest and --extraction')
        write(args.out, result)
        return 0 if passed else 1
    except (ValueError, KeyError, TypeError, OSError) as error:
        write(args.out, {'full_extraction_verified': False, 'failure': str(error)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
