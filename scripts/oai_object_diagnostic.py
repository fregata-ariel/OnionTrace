"""Temporary T9 diagnostic: real audited object builds; no full-binary claim."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from onion.build_inputs import (LOCK, collect, query, git_provenance, file_record,
    cmake_model, selection, ownership_index, owner_for, command, absolute, output_path)
from onion.libtooling import extract_manifest, write
from onion.acceptance import export_acceptance
from onion.oai_threads import audit_sources as audit_threads, EXPECTATIONS as THREADS
from onion.oai_callbacks import audit_sources as audit_callbacks, EXPECTATIONS as CALLBACKS
from onion.oai_queues import audit_sources as audit_queues, EXPECTATIONS as QUEUES
from scripts.prepare_oai import prepare


def diagnostic(out, tool, jobs):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    lock = json.loads(LOCK.read_text())
    result = {'kind': 'oai_object_diagnostic', 'passed': False, 'full_binary_build_verified': False,
              'scope': 'only the ten audited translation-unit objects; full binary run remains separate',
              'lock': lock, 'stages': []}

    def stage(name, action):
        record = {'name': name, 'passed': False}
        result['stages'].append(record)
        write(out / 'run.json', result)
        print('START ' + name, flush=True)
        start = time.monotonic()
        try:
            value = action()
            record['passed'] = True
            return value
        except Exception as error:
            record['failure'] = str(error)
            (out / (name + '-error.log')).write_text(traceback.format_exc())
            raise
        finally:
            record['seconds'] = round(time.monotonic() - start, 2)
            write(out / 'run.json', result)
            print('END ' + name + ' ' + str(record), flush=True)

    def run(name, args, cwd=None):
        print('COMMAND ' + shlex.join(map(str, args)), flush=True)
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True)

    try:
        workspace = out / 'workspace'
        stage('fetch', lambda: prepare(workspace, fetch_only=True))
        source = workspace / 'source'
        build = source / 'cmake_targets/oniontrace-analysis/build'
        os.environ['CPM_SOURCE_CACHE'] = str(workspace / 'cpm-cache')
        scope = json.loads((ROOT / 'config/oai-analysis-scope.json').read_text())
        result['analysis_scope'] = scope
        def audit():
            write(out / 'source-audits.json', {name: action(source, json.loads(path.read_text()))
                  for name, action, path in [('threads', audit_threads, THREADS),
                      ('callbacks', audit_callbacks, CALLBACKS), ('queues', audit_queues, QUEUES)]})
        stage('source-audit', audit)
        query(build)
        options = shlex.split(lock['build_oai_arguments'][lock['build_oai_arguments'].index('--cmake-opt') + 1])
        configure = ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
                     '-DOAI_SIMU=ON', '-DAVX512=OFF', *options]
        stage('configure', lambda: run('configure', configure))
        def objects():
            _, model, _, targets, _, _ = cmake_model(build)
            members = selection(targets, lock['targets'])
            index = ownership_index(model, build, targets)
            requested = {(source / p).resolve() for p in scope['sources']}
            rows = []
            for row in json.loads((build / 'compile_commands.json').read_text()):
                args, directory = command(row), Path(row['directory'])
                path = absolute(row['file'], directory)
                if path not in requested:
                    continue
                target, _ = owner_for(row, args, directory, path, model, build, targets, index=index)
                if target['id'] in members:
                    rows.append({'source': str(path), 'object': str(output_path(row, args, directory)),
                                 'target': target['name'], 'selected_by': members[target['id']]})
            if {r['source'] for r in rows} != {str(p) for p in requested}:
                raise ValueError('audited object source absent from executable closure')
            write(out / 'object-targets.json', rows)
            outputs = sorted({str(Path(r['object']).relative_to(build)) for r in rows})
            run('objects-build', ['cmake', '--build', str(build), '--target', *outputs, '--parallel', str(jobs)])
            write(out / 'objects.json', [file_record(build / p) for p in outputs])
            git_provenance(source, lock)
        stage('objects', objects)
        dependencies = []
        for dep in lock['dependency_baseline']:
            prefix = Path(os.environ['ONION_' + dep['name'].upper()])
            revision = subprocess.check_output(['git', '-C', str(prefix / 'source'), 'rev-parse', 'HEAD'], text=True).strip()
            if revision != dep['revision']:
                raise ValueError('dependency revision mismatch')
            dependencies.append({'name': dep['name'], 'version': revision, 'path': str(prefix)})
        dependencies.append({'name': 'cpm-cache', 'version': 'actual cache contents', 'path': os.environ['CPM_SOURCE_CACHE']})
        dependency_lock = out / 'dependencies.json'
        write(dependency_lock, dependencies)
        def capture():
            manifest = collect(build, lock['targets'], oai=True, dependency_lock=dependency_lock, sources=scope['sources'])
            for name, value in [('manifest', manifest), ('targets', [e for e in manifest['entries'] if e['status'] == 'validated']),
                                ('excluded', [e for e in manifest['entries'] if e['status'] == 'excluded']),
                                ('failures', [e for e in manifest['entries'] if e['status'] == 'failed'])]:
                write(out / 'inputs' / (name + '.json'), value)
            if not manifest['ready']:
                raise ValueError('capture failed')
            return manifest
        manifest = stage('capture', capture)
        extraction = stage('extract', lambda: extract_manifest(manifest, tool, out / 'extraction'))
        if not extraction['ready']:
            raise ValueError('extraction failed')
        acceptance = stage('acceptance', lambda: export_acceptance(manifest, extraction, out / 'acceptance', oai=True))
        result['passed'] = acceptance['passed']
    except Exception as error:
        result['failure'] = str(error)
        print(traceback.format_exc(), flush=True)
    finally:
        write(out / 'run.json', result)
        write(out / 'failures.json', [s for s in result['stages'] if not s['passed']])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    raise SystemExit(0 if diagnostic(args.out, args.tool, args.jobs)['passed'] else 1)
