"""T9 artifact gates shared by the small CI and pinned OAI acceptance run."""
import argparse
import json
from pathlib import Path
import shutil

from .integration import connect
from .libtooling import write
from .viewer import _export_views, project_workspace
from .oai_threads import verify_paths, EXPECTATIONS as THREADS
from .oai_callbacks import verify_extraction as verify_callbacks, EXPECTATIONS as CALLBACKS
from .oai_queues import verify_extraction as verify_queues, EXPECTATIONS as QUEUES


def export_acceptance(manifest, extraction, out, *, oai=False):
    """Fail closed, retaining per-gate diagnostics and inspectable partial output.

    Passing means the named audited scope was found in actual static extraction.
    It never means every indirect call, traversal frontier or alias was resolved.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # A failed rerun must not leave a previous HTML or target model as evidence.
    for target in ('nr-softmodem', 'nr-uesoftmodem'):
        if (out / target).exists():
            shutil.rmtree(out / target)
    (out / 'structure.html').unlink(missing_ok=True)
    for name in ('acceptance', 'failures', 'nr-softmodem', 'nr-uesoftmodem', 'viewer',
                 'oai-threads', 'oai-callbacks', 'oai-queues'):
        (out / (name + '.json')).unlink(missing_ok=True)
    summary = {'schema_version': 1, 'kind': 'structure_acceptance', 'passed': False,
               'full_extraction_verified': False, 'runtime_evidence': False,
               'input_manifest_id': manifest.get('manifest_id'),
               'analysis_scope': manifest.get('context', {}).get('analysis_scope'),
               'gates': [], 'targets': []}

    def gate(name, action):
        try:
            result = action()
            passed = result.get('full_extraction_verified', result.get('passed', False))
            write(out / (name + '.json'), result)
            summary['gates'].append({'name': name, 'passed': bool(passed)})
        except (ValueError, KeyError, TypeError, OSError) as error:
            result = {'passed': False, 'failure': str(error)}
            write(out / (name + '.json'), result)
            summary['gates'].append({'name': name, **result})

    if not manifest.get('ready') or not extraction.get('ready'):
        summary['gates'].append({'name': 'inputs', 'passed': False,
                                 'failure': 'capture and extraction must both be ready'})
    else:
        views = []
        for target in ('nr-softmodem', 'nr-uesoftmodem'):
            def export_target(target=target):
                workspace = connect(manifest, extraction, target)
                write(out / target / 'workspace.json', workspace, compact=oai)
                write(out / target / 'structure.json', workspace['structure'], compact=oai)
                view = project_workspace(workspace)
                write(out / target / 'view.json', view, compact=oai)
                views.append(view)
                categories = {r['category'] for r in view['references']}
                # References must open a real CFG, never a manufactured empty view.
                valid = all(r['function_id'] in view['cfgs'] and
                            view['cfgs'][r['function_id']]['nodes'] for r in view['references'])
                passed = (len(view['roots']) == 1 and valid and
                          'thread_start' in categories and
                          any(c.startswith('queue_') for c in categories) and
                          not view['runtime_evidence'] and not workspace['incompleteness'])
                result = {'target': target, 'passed': passed, 'roots': view['roots'],
                          'functions': len(view['cfgs']), 'references': len(view['references']),
                          'categories': sorted(categories),
                          'unresolved_links': sum(r['resolution'] != 'resolved' for r in view['links']),
                          'frontiers': len(view['frontiers']),
                          'incompleteness': workspace['incompleteness'], 'runtime_evidence': False}
                summary['targets'].append(result)
                return result
            gate(target, export_target)
        if len(views) == 2:
            def viewer():
                _export_views(views, out / 'structure.html')
                return {'passed': True, 'file': 'structure.html', 'runtime_evidence': False}
            gate('viewer', viewer)
        if oai:
            # Recompute every semantic gate; source-audit JSON cannot pass these.
            def threads():
                from .threads import build_threads
                return verify_paths(manifest, build_threads(manifest, extraction), json.loads(THREADS.read_text()))
            gate('oai-threads', threads)
            gate('oai-callbacks', lambda: verify_callbacks(manifest, extraction, json.loads(CALLBACKS.read_text())))
            gate('oai-queues', lambda: verify_queues(manifest, extraction, json.loads(QUEUES.read_text())))
    summary['passed'] = bool(summary['gates']) and all(g['passed'] for g in summary['gates'])
    summary['full_extraction_verified'] = oai and summary['passed']
    write(out / 'acceptance.json', summary)
    write(out / 'failures.json', [g for g in summary['gates'] if not g['passed']])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--extraction', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--oai', action='store_true')
    args = parser.parse_args()
    try:
        result = export_acceptance(json.loads(args.manifest.read_text()),
                                   json.loads(args.extraction.read_text()), args.out, oai=args.oai)
        return 0 if result['passed'] else 1
    except (ValueError, OSError) as error:
        write(args.out / 'acceptance.json', {'passed': False, 'full_extraction_verified': False, 'failure': str(error)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
