"""Cross-module checks against generated real fixture execution (run demo first)."""
import copy
import json
import contextlib
import io
import os
import tempfile
from pathlib import Path
import unittest

from onion.core import reconstruct, accumulate
from onion.demo import run_demo

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('ONION_TEST_SESSION')
        if supplied:
            cls.data = json.loads(Path(supplied).read_text())
        else:
            temporary = tempfile.TemporaryDirectory(prefix='onion-integration-')
            cls.addClassCleanup(temporary.cleanup)
            with contextlib.redirect_stdout(io.StringIO()):
                cls.data = run_demo(Path(temporary.name), os.environ.get('ONION_CLANG', 'clang-18'))

    def test_real_runs_progress_from_three_paths_to_unique(self):
        a, b, c = self.data['stages']
        self.assertEqual({j['candidate_count'] for j in a['analysis']['jobs']}, {3})
        self.assertEqual({j['candidate_count'] for j in b['analysis']['jobs']}, {1, 2})
        self.assertEqual({j['status'] for j in c['analysis']['jobs']}, {'unique'})
        for stage in self.data['stages']:
            self.assertEqual(stage['trace']['dropped'], 0)
            self.assertEqual(len(stage['analysis']['jobs']), 12)
            self.assertEqual({t['status'] for t in stage['analysis']['transfers']}, {'paired'})
        self.assertLess(set(a['plan']['enabled_sites']), set(b['plan']['enabled_sites']))
        self.assertLess(set(b['plan']['enabled_sites']), set(c['plan']['enabled_sites']))

    def test_different_input_same_job_is_not_historical_recovery(self):
        first, last = self.data['stages'][0], self.data['stages'][2]
        def input_zero(stage):
            return next(e['value'] for e in stage['trace']['events'] if e['site'] == 'work.begin' and e['job'] == 0)
        self.assertNotEqual(input_zero(first), input_zero(last))
        self.assertFalse(self.data['cumulative']['merges_timelines'])
        # Later certainty never modifies earlier ambiguity.
        self.assertEqual(first['analysis']['jobs'][0]['candidate_count'], 3)
        result = accumulate([first['analysis'], last['analysis']])
        self.assertEqual(len(result['runs']), 2)

    def test_removed_event_cannot_be_negative_evidence(self):
        stage = self.data['stages'][2]
        trace = copy.deepcopy(stage['trace'])
        index = next(i for i,e in enumerate(trace['events']) if e['site'] == 'retry.ok')
        trace['events'].pop(index)
        trace['dropped'] = 1
        result = reconstruct(self.data['model'], stage['plan'], trace)
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('unique', {j['status'] for j in result['jobs']})

    def test_playback_does_not_use_future_events(self):
        for stage in self.data['stages']:
            self.assertEqual(stage['frames'][0]['jobs'], [])
            for frame in stage['frames'][:-1]:
                self.assertNotIn('unique', {j['status'] for j in frame['jobs']})
            self.assertEqual(stage['frames'][-1]['jobs'], stage['analysis']['jobs'])


if __name__ == '__main__':
    unittest.main()
