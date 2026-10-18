"""Pinned T6 source audit and actual-extraction acceptance gate for Issue #11."""
import argparse
import json
from pathlib import Path

from .build_inputs import digest
from .libtooling import write
from .oai_callbacks import audit_sources as _audit_sources
from .queues import build_queues

EXPECTATIONS = Path(__file__).resolve().parents[1] / 'config/oai-queue-sites.json'


def audit_sources(root, expected):
    result = _audit_sources(root, expected)
    result['kind'] = 'oai_queue_source_audit'
    result['scope'] = 'pinned source positions and hashes only; no actual OAI extraction/runtime acceptance'
    return result


def verify_extraction(manifest, extraction, expected):
    if (manifest.get('profile') != 'oai' or not manifest.get('ready') or
            manifest['context']['source'].get('commit') != expected['revision']):
        raise ValueError('full OAI acceptance needs ready pinned OAI input manifest')
    result = build_queues(manifest, extraction)
    if not {'nr-softmodem', 'nr-uesoftmodem'} <= {g['target'] for g in result['graphs']}:
        raise ValueError('full OAI acceptance needs both executable targets')
    graph = next(g for g in result['graphs'] if g['target'] == expected['target'])
    functions = {f['id']: f for f in graph['functions']}
    registrations = {r['id']: r for r in graph['callback_registrations']}
    matches, failures, ids = [], [], {}
    for site in expected['sites']:
        if site['group'] == 'contract':
            # Contract statements/macros are audited at source level. Do not
            # pretend they are standalone queue API calls in native output.
            continue
        rows = graph[site['group']] if site['group'] != 'calls' else [
            {**c, 'source': f['id']} for f in graph['functions'] for c in f['calls']]
        hits = []
        for row in rows:
            span = row['range']['expansion']
            begin, end = span['begin'], span['end']
            relative, path = Path(site['path']), Path(begin['path'])
            owner = functions[row['source']]
            if (site.get('operation') and row.get('operation') != site['operation'] or row.get('blocked')):
                continue
            if site.get('nullable') and not any(
                    registrations[r]['nullable'] and not registrations[r]['candidates'] and not registrations[r]['unknown']
                    for r in row.get('callback_registration_ids', [])):
                continue
            expected_hash = next(s['sha256'] for s in expected['sources'] if s['path'] == site['path'])
            dependencies = [d for e in manifest['entries'] if e.get('cache_key') == owner['_compilation_id']
                            for d in e['dependencies'] if d['path'] == str(path)]
            if (owner['name'] == site['function'] and path.parts[-len(relative.parts):] == relative.parts and
                    begin['offset'] == site['start_offset'] and end['offset'] == site['end_offset'] and
                    any(d['sha256'] == expected_hash for d in dependencies)):
                hits.append(row['id'])
        if hits:
            ids[site['key']] = set(hits)
            matches.append({'key': site['key'], 'ids': sorted(set(hits))})
        else:
            failures.append({'key': site['key'], 'reason': 'audited semantic site missing from actual target extraction'})
    # Every source, including the publication/acquisition contract header, must
    # appear with its exact hash in a validated selected-target compilation.
    for source in expected['sources']:
        suffix = Path(source['path']).parts
        if not any(d['sha256'] == source['sha256'] and Path(d['path']).parts[-len(suffix):] == suffix
                   for e in manifest['entries'] if e['status'] == 'validated' and expected['target'] in e['selected_by']
                   for d in e['dependencies']):
            failures.append({'path': source['path'], 'reason': 'pinned contract/source hash absent from selected input'})
    verified_relations = []
    for required in expected.get('required_relations', []):
        found = [r for r in graph['relations'] if r['kind'] == required['kind'] and
                 r['source'] in ids.get(required['source'], set()) and r['target'] in ids.get(required['target'], set())]
        if found:
            verified_relations.append(required)
        else:
            failures.append({**required, 'reason': 'required candidate connection absent from recomputed graph'})
    return {'schema_version': 1, 'kind': 'oai_queue_acceptance', 'revision': expected['revision'],
            'full_extraction_verified': not failures and result['ready'], 'matches': matches,
            'verified_relations': verified_relations, 'failures': failures, 'queue_graph_digest': digest(result),
            'scope': 'actual static extraction and candidate links; no runtime transfer or queue timing proof'}


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
