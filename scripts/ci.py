"""Rebuild the fixture and run all required tests; skipped tests fail CI."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from onion.core import reconstruct, recommend
from onion.demo import run_demo, render_report


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def incomplete_view(data, destination):
    """Create a coherent loss case for browser regression tests."""
    data = copy.deepcopy(data)
    stage = data['stages'][2]
    stage['trace']['dropped'] = 1
    stage['trace']['complete'] = False
    stage['analysis'] = reconstruct(data['model'], stage['plan'], stage['trace'])
    stage['next_plan'] = recommend(data['model'], stage['plan'], stage['analysis'])
    stage['frames'] = []
    for index in range(len(stage['trace']['events']) + 1):
        prefix = dict(stage['trace'], events=stage['trace']['events'][:index])
        result = reconstruct(data['model'], stage['plan'], prefix)
        stage['frames'].append({key: result[key] for key in ('jobs', 'status', 'warnings')})
    render_report(data, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default=os.environ.get('ONION_CLANG', 'clang-18'))
    parser.add_argument('--out', type=Path, default=ROOT / 'out')
    options = parser.parse_args()
    output = options.out.resolve()
    data = run_demo(output, options.cc)
    incomplete_view(data, output / 'incomplete-demo.html')
    os.environ['ONION_TEST_SESSION'] = str(output / 'session.json')
    os.environ['ONION_CLANG'] = options.cc
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
    with (output / 'tests.log').open('w') as log:
        result = unittest.TextTestRunner(stream=Tee(sys.stdout, log), verbosity=2).run(suite)
    passed = result.wasSuccessful() and not result.skipped and result.testsRun > 0
    summary = {'passed': passed, 'tests': result.testsRun,
               'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': len(result.skipped), 'compiler': options.cc}
    (output / 'test-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if result.skipped:
        print('Required tests were skipped; this is a CI failure.', file=sys.stderr)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
