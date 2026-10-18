"""Compile, execute, refine and export a self-contained observation demo."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .cfg import build_model, parse_dump
from .core import reconstruct, recommend, accumulate
from .build_inputs import json_chunks

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def command(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}): {argv!r}\n{result.stderr[-5000:]}')
    return result


def render_report(data, destination):
    template = (ROOT / 'viewer' / 'template.html').read_text()
    template = template.replace('/*__STRUCTURE_CSS__*/', (ROOT / 'viewer/structure.css').read_text())
    template = template.replace('/*__STRUCTURE_JS__*/', (ROOT / 'viewer/structure.js').read_text())
    # Prevent script termination even when source snippets contain HTML.
    before, after = template.split('/*__ONION_DATA__*/null')
    with destination.open('w') as stream:
        stream.write(before)
        for part in json_chunks(data, json.JSONEncoder(ensure_ascii=False)):
            stream.write(part.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
        stream.write(after)


def run_demo(out, compiler, oai_dump=None):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which(compiler) or compiler
    source = ROOT / 'fixture' / 'fixture.c'
    version = command([compiler, '--version']).stdout.splitlines()[0]
    if 'clang' not in version.lower():
        raise ValueError('Clang is required for CFG extraction; select --cc clang-18 or clang')
    flags = ['-std=c11', '-O2', '-g', '-pthread', '-Wall', '-Wextra', '-Werror']
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    build_id = hashlib.sha256((source_hash + version + json.dumps(flags)).encode()).hexdigest()[:24]
    executable = out / 'onion_fixture'
    args = [compiler, *flags, f'-DBUILD_ID="{build_id}"', str(source), '-o', str(executable)]
    command(args)
    dump(out / 'compile_commands.json', [{'directory': str(ROOT), 'file': str(source), 'arguments': args}])
    cfg_result = command([compiler, '-std=c11', '-pthread', '--analyze',
                          '-Xanalyzer', '-analyzer-checker=debug.DumpCFG',
                          '-Xanalyzer', '-analyzer-output=text', str(source), '-o', os.devnull])
    cfg_text = cfg_result.stderr + cfg_result.stdout
    (out / 'fixture-cfg.txt').write_text(cfg_text)
    catalog = json.loads((ROOT / 'fixture' / 'catalog.json').read_text())
    provenance = {'kind': 'clang-textual-cfg-adapter', 'compiler': version,
                  'source_sha256': source_hash, 'source': 'fixture/fixture.c',
                  'runtime': 'actual pthread fixture execution; NOT OAI runtime',
                  'mapping': 'explicit source marker calls; no automatic source insertion'}
    model = build_model(cfg_text, 'process_job', catalog['sites'], build_id, provenance)
    dump(out / 'model.json', model)
    plan = {'schema_version': 1, 'plan_id': 'plan-0', 'build_id': build_id,
            'parent_plan_id': None, 'enabled_sites': ['work.begin', 'work.end'],
            'anchor_sites': ['work.begin', 'work.end'],
            'rationale': 'Keep job boundary anchors; discover internal routes gradually.',
            'budget': {'max_new_sites': 1, 'cost_unit': 'relative proxy; NOT measured latency'}}
    stages = []
    for stage in range(3):
        # Same job number is intentionally NOT assumed to identify the same work across runs.
        seed = 17 if stage < 2 else 18
        run_id = f'fixture-run-{stage}'
        result = command([str(executable), '--run-id', run_id, '--plan-id', plan['plan_id'],
                          '--sites', ','.join(plan['enabled_sites']), '--seed', str(seed), '--jobs', '12'])
        trace = json.loads(result.stdout)
        analysis = reconstruct(model, plan, trace)
        next_plan = recommend(model, plan, analysis, max_new_sites=1)
        # Prefix views never use unobserved future events; completeness becomes true at EOF only.
        frames = []
        for index in range(len(trace['events']) + 1):
            prefix = dict(trace, events=trace['events'][:index], complete=bool(trace['complete'] and index == len(trace['events'])))
            frame = reconstruct(model, plan, prefix)
            frames.append({'jobs': frame['jobs'], 'status': frame['status'], 'warnings': frame.get('warnings', [])})
        item = {'stage': stage, 'seed': seed, 'plan': plan, 'trace': trace,
                'analysis': analysis, 'next_plan': next_plan, 'frames': frames}
        stages.append(item)
        for name, obj in [('plan', plan), ('trace', trace), ('analysis', analysis), ('next-plan', next_plan)]:
            dump(out / f'stage-{stage}' / f'{name}.json', obj)
        plan = next_plan
    static_oai = []
    if oai_dump:
        oai_dump = Path(oai_dump)
        files = sorted(oai_dump.glob('*.txt')) if oai_dump.is_dir() else [oai_dump]
        manifest_path = (oai_dump if oai_dump.is_dir() else oai_dump.parent) / 'provenance.json'
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        for file in files:
            dump_hash = hashlib.sha256(file.read_bytes()).hexdigest()
            recorded = manifest.get('files', {}).get(file.name) == dump_hash
            for function in parse_dump(file.read_text()):
                if function['name'] in ('actor_thread', 'worker_thread'):
                    static_oai.append(dict(function, source_dump=file.name,
                                           status='static-only; no OAI execution events',
                                           source_dump_sha256=dump_hash,
                                           source_commit=manifest.get('source_commit') if recorded else None,
                                           source_tag=manifest.get('source_tag') if recorded else None,
                                           provenance_status=('matches recorded dump manifest; source tree not revalidated here'
                                                              if recorded else 'supplied dump; source commit unverified')))
    data = {'schema_version': 1, 'title': 'Onion / 段階的な実動作の再構成',
            'prototype': 'v0.1', 'model': model, 'stages': stages,
            'cumulative': accumulate([s['analysis'] for s in stages]), 'oai_cfg': static_oai,
            'limits': ['実行データは小規模pthread fixture。OAI本体の実行記録ではありません。',
                       'CFGはClangテキスト出力の暫定取込。プローブ位置は明示したマーカーです。',
                       '候補経路は静的CFG上の候補。実行可能性の証明ではありません。',
                       '負荷は相対コスト。USRPのリアルタイム性は未評価です。',
                       '各段階は別run。同じjob番号でも実行履歴を接合しません。']}
    dump(out / 'session.json', data)
    render_report(data, out / 'onion-demo.html')
    summary = {'build_id': build_id, 'compiler': version, 'oai_static_functions': [x['name'] for x in static_oai],
               'stages': [{'run_id': s['trace']['run_id'], 'enabled_sites': s['plan']['enabled_sites'],
                           'events': len(s['trace']['events']), 'dropped': s['trace']['dropped'],
                           'status': s['analysis']['status'],
                           'candidate_counts': [j['candidate_count'] for j in s['analysis']['jobs']]} for s in stages]}
    dump(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='out')
    parser.add_argument('--cc', default='clang-18')
    parser.add_argument('--oai-dump', help='Existing native OAI Clang CFG dump directory, read-only')
    options = parser.parse_args()
    run_demo(Path(options.out), options.cc, options.oai_dump)


if __name__ == '__main__':
    main()
