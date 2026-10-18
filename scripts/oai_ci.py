"""Build pinned OAI in a fresh workspace and retain every acceptance stage.

Run on an isolated Ubuntu 24.04 runner with the dependencies documented in
docs/oai-acceptance.md. Never executes the softmodems or accesses RF hardware.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from onion.build_inputs import LOCK, collect, git_provenance, query, file_record
from onion.libtooling import extract_manifest, write
from onion.acceptance import export_acceptance
from scripts.prepare_oai import prepare


def run_acceptance(out, tool, *, jobs=2):
    out = Path(out).resolve()
    # A previous success must never survive a failed rerun.
    out.mkdir(parents=True, exist_ok=False)
    stages = []
    result = {'schema_version': 1, 'kind': 'oai_ci_run', 'passed': False,
              'full_extraction_verified': False, 'runtime_evidence': False,
              'lock': json.loads(LOCK.read_text()), 'stages': stages,
              'onion_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'runner': {k: os.environ.get(k) for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'ImageOS', 'ImageVersion')}}

    def stage(name, action):
        row = {'name': name, 'passed': False}
        stages.append(row)
        write(out / 'run.json', result)
        try:
            value = action()
            row['passed'] = True
            return value
        except Exception as error:
            row['failure'] = str(error)
            (out / (name + '-error.log')).write_text(traceback.format_exc())
            raise
        finally:
            write(out / 'run.json', result)

    def command(name, args, cwd=None, env=None):
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

    try:
        workspace = out / 'workspace'
        stage('fetch', lambda: prepare(workspace, fetch_only=True))
        source = workspace / 'source'
        lock = result['lock']
        # Tools/dependencies are installed by the workflow from the locked
        # revisions; verify them again before consuming their installed trees.
        dependencies = []
        for dependency in lock['dependency_baseline']:
            prefix = Path(os.environ.get('ONION_' + dependency['name'].upper(), '/opt/' + dependency['name']))
            revision = subprocess.check_output(['git', '-C', str(prefix / 'source'), 'rev-parse', 'HEAD'], text=True).strip()
            if revision != dependency['revision']:
                raise ValueError('dependency revision mismatch: ' + dependency['name'])
            dependencies.append({'name': dependency['name'], 'version': revision, 'path': str(prefix)})
        cache = workspace / 'cpm-cache'
        environment = dict(os.environ, CPM_SOURCE_CACHE=str(cache))
        # The prepared checkout stays clean; build directory is ignored by OAI.
        build = source / 'cmake_targets/oniontrace-analysis/build'
        query(build)
        invocation = ['bash', './build_oai', *lock['build_oai_arguments'], '-d', 'oniontrace-analysis', '--build-tool-opt', '-j' + str(jobs)]
        preparation = json.loads((workspace / 'preparation.json').read_text())
        preparation.update(fetch_only=False, build_oai_command=invocation)
        write(workspace / 'preparation.json', preparation)
        stage('build', lambda: command('build', invocation, source / 'cmake_targets', environment))
        # build_oai can print success after a failed build; require the native
        # build tool's successful exit and both produced executables explicitly.
        def check_build():
            command('build-check', ['cmake', '--build', str(build), '--target', *lock['targets'], '--parallel', str(jobs)], env=environment)
            for target in lock['targets']:
                binary = build / target
                if not binary.is_file() or not os.access(binary, os.X_OK):
                    raise ValueError('missing built executable: ' + target)
            git_provenance(source, lock)
            result['binaries'] = [file_record(build / target) for target in lock['targets']]
        stage('build-check', check_build)
        dependencies.append({'name': 'cpm-cache', 'version': 'actual downloaded CPM/cache contents', 'path': str(cache)})
        dependency_lock = out / 'dependencies.json'
        write(dependency_lock, dependencies)
        # Capture and extraction must see exactly the same environment.
        os.environ['CPM_SOURCE_CACHE'] = str(cache)
        def capture():
            manifest = collect(build, lock['targets'], oai=True, dependency_lock=dependency_lock)
            for name, value in [('manifest', manifest), ('targets', [e for e in manifest['entries'] if e['status'] == 'validated']),
                                ('excluded', [e for e in manifest['entries'] if e['status'] == 'excluded']),
                                ('failures', [e for e in manifest['entries'] if e['status'] == 'failed'])]:
                write(out / 'inputs' / (name + '.json'), value)
            if not manifest['ready']:
                raise ValueError('input capture failed; see inputs/failures.json')
            return manifest
        manifest = stage('capture', capture)
        def extract():
            extraction = extract_manifest(manifest, tool, out / 'extraction')
            if not extraction['ready']:
                raise ValueError('OAI extraction failed; see extraction/failures.json')
            return extraction
        extraction = stage('extract', extract)
        def accept():
            acceptance = export_acceptance(manifest, extraction, out / 'acceptance', oai=True)
            if not acceptance['passed']:
                raise ValueError('OAI acceptance failed; see acceptance/failures.json')
            return acceptance
        stage('acceptance', accept)
        result.update(passed=True, full_extraction_verified=True)
    except Exception as error:
        result['failure'] = str(error)
    finally:
        write(out / 'run.json', result)
        write(out / 'failures.json', [s for s in stages if not s['passed']] +
              ([{'failure': result['failure']}] if 'failure' in result else []))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    return 0 if run_acceptance(args.out, args.tool, jobs=args.jobs)['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
