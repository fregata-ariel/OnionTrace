"""Exercise the actual C producer/consumer, including loss and invalid input."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SITES = 'work.begin,work.end,branch.slow,branch.fast,retry.ok,retry.fail'


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='onion-runtime-')
        cls.addClassCleanup(temporary.cleanup)
        cls.directory = Path(temporary.name)
        compiler = os.environ.get('ONION_CLANG', 'clang-18')
        flags = shlex.split(os.environ.get('ONION_TEST_CFLAGS', ''))
        for name, extra in [('normal', []), ('tiny', ['-DTRACE_CAPACITY=2'])]:
            subprocess.run([compiler, '-std=c11', '-O2', '-g', '-pthread',
                            '-Wall', '-Wextra', '-Werror', *flags, *extra,
                            '-DBUILD_ID="runtime-test"', str(ROOT / 'fixture' / 'fixture.c'),
                            '-o', str(cls.directory / name)],
                           check=True, capture_output=True, text=True, timeout=60)

    def execute(self, *arguments, binary='normal', check=True):
        result = subprocess.run([str(self.directory / binary), *arguments],
                                capture_output=True, text=True, timeout=20)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return result

    def trace(self, sites=SITES, jobs=12, seed=17, binary='normal'):
        return self.execute('--run-id', 'runtime-run', '--plan-id', 'runtime-plan',
                            '--sites', sites, '--jobs', str(jobs), '--seed', str(seed), binary=binary)

    def test_enabling_probes_preserves_computed_results(self):
        for seed in (0, 17, 18446744073709551615):
            with self.subTest(seed=seed):
                baseline = self.trace(sites='', jobs=1024, seed=seed)
                observed = self.trace(jobs=1024, seed=seed)
                self.assertEqual(baseline['results'], observed['results'])
                self.assertEqual(len(observed['results']), 1024)
                self.assertTrue(observed['complete'])
                self.assertEqual(observed['dropped'], 0)

    def test_thread_order_and_queue_handoff(self):
        trace = self.trace(jobs=33)
        for thread in ('main', 'worker'):
            values = [e['thread_seq'] for e in trace['events'] if e['thread'] == thread]
            self.assertEqual(values, list(range(len(values))))
        for job in range(33):
            events = [e for e in trace['events'] if e['job'] == job]
            by_site = {e['site']: e for e in events}
            self.assertLessEqual(by_site['queue.enqueue']['timestamp_ns'], by_site['queue.dequeue']['timestamp_ns'])
            self.assertLess(by_site['queue.dequeue']['thread_seq'], by_site['work.begin']['thread_seq'])
            self.assertLess(by_site['work.begin']['thread_seq'], by_site['work.end']['thread_seq'])

    def test_full_buffer_reports_loss_and_still_finishes_work(self):
        tiny = self.trace(binary='tiny')
        normal = self.trace()
        self.assertFalse(tiny['complete'])
        self.assertGreater(tiny['dropped'], 0)
        self.assertEqual(len(tiny['events']), 4)
        self.assertEqual(tiny['results'], normal['results'])

    def test_queue_boundary_job_counts(self):
        for count in (1, 8, 9, 1024):
            with self.subTest(jobs=count):
                trace = self.trace(jobs=count)
                self.assertEqual(len(trace['results']), count)
                self.assertEqual(sum(e['site'] == 'work.end' for e in trace['events']), count)

    def test_invalid_arguments_fail_without_a_trace(self):
        base = ['--run-id', 'R', '--plan-id', 'P']
        for suffix in (['--jobs', '0'], ['--jobs', '1025'], ['--jobs', '-1'],
                       ['--seed', '18446744073709551616'], ['--unknown'],
                       ['--sites', 'unknown'], ['--sites', 'work.begin,work.begin'],
                       ['--run-id', 'duplicate']):
            with self.subTest(arguments=suffix):
                result = self.execute(*base, *suffix, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
