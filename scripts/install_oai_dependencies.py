"""Install the two source dependencies at the exact OAI baseline revisions.

OS build dependencies must already be installed. All writes stay in --prefix;
the emitted environment.json can also be used outside GitHub Actions.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def simde_environment(destination):
    destination = Path(destination).resolve()
    source = destination / 'source'
    # simde-no-tests has x86/ at its root. OAI includes <simde/x86/...>.
    if not (source / 'x86/gfni.h').is_file():
        raise ValueError('SIMDe checkout lacks required gfni.h')
    (destination / 'include').mkdir()
    (destination / 'include/simde').symlink_to(source, target_is_directory=True)
    # Match build_helper's system-header installation. CPATH is a user -I
    # path and would ask LibTooling to emit thousands of third-party intrinsics.
    return {variable: os.pathsep.join(filter(None, [str(destination / 'include'), os.environ.get(variable)]))
            for variable in ('C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH')}


def install(prefix, jobs):
    prefix = Path(prefix).resolve()
    prefix.mkdir(parents=True, exist_ok=False)
    baseline = json.loads((ROOT / 'config/oai-2026.w07.json').read_text())['dependency_baseline']
    environment = {}
    for row in baseline:
        destination = prefix / row['name']
        source = destination / 'source'
        destination.mkdir()
        with (destination / 'install.log').open('w') as log:
            def run(args, cwd=None, **kwargs):
                subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True, **kwargs)
            run(['git', 'init', str(source)])
            run(['git', 'remote', 'add', 'origin', row['origin']], source)
            run(['git', 'fetch', '--depth', '1', 'origin', row['revision']], source)
            run(['git', 'checkout', '--detach', 'FETCH_HEAD'], source)
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
            if revision != row['revision']:
                raise ValueError('dependency checkout mismatch: ' + row['name'])
            if row['name'] == 'asn1c':
                run(['autoreconf', '-iv'], source)
                run(['./configure', '--prefix=' + str(destination)], source,
                    env=dict(os.environ, CFLAGS='-O2 -fno-strict-aliasing'))
                run(['make', '-j' + str(jobs)], source)
                run(['make', 'install'], source)
            elif row['name'] == 'simde':
                environment.update(simde_environment(destination))
            environment['ONION_' + row['name'].upper()] = str(destination)
    environment['PATH'] = str(prefix / 'asn1c/bin') + os.pathsep + os.environ['PATH']
    (prefix / 'environment.json').write_text(json.dumps(environment, indent=2) + '\n')
    if os.environ.get('GITHUB_ENV'):
        with Path(os.environ['GITHUB_ENV']).open('a') as stream:
            for key, value in environment.items():
                stream.write(key + '=' + value + '\n')
    return environment


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    install(args.prefix, args.jobs)
