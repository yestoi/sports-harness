"""External hidden checks. Submitted code MUST run inside the pilot sandbox."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


SOLUTION = None


class DurationTests(unittest.TestCase):
    def test_compound(self):
        result = SOLUTION.parse_duration('2d03h4m5s006ms')
        self.assertIs(type(result), int)
        self.assertEqual(result, 183845006)

    def test_spaces_zeroes_and_nonclock_amounts(self):
        self.assertEqual(SOLUTION.parse_duration('  000d  90m  0s  '), 5400000)

    def test_millisecond_token(self):
        self.assertEqual(SOLUTION.parse_duration('1m1ms'), 60001)

    def test_upper_boundary(self):
        result = SOLUTION.parse_duration('365d')
        self.assertIs(type(result), int)
        self.assertEqual(result, 31536000000)
        with self.assertRaises(ValueError):
            SOLUTION.parse_duration('365d1ms')

    def test_order_and_repetition(self):
        for value in ['1s2m', '1ms1s', '1h0h', '1m1m', '0ms0d']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                SOLUTION.parse_duration(value)

    def test_non_ascii_and_whitespace(self):
        for value in ['١s', '１s', '1s\t', '\n1s', '1\u00a0s', '1 h']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                SOLUTION.parse_duration(value)

    def test_complete_syntax(self):
        for value in ['', '   ', '+1s', '-1s', '1.0s', '1S', '1sec', 's1', '1s!']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                SOLUTION.parse_duration(value)

    def test_type_errors(self):
        for value in [None, True, 1, b'1s', ['1s']]:
            with self.subTest(value=value), self.assertRaises(TypeError):
                SOLUTION.parse_duration(value)

    def test_raw_length(self):
        result = SOLUTION.parse_duration(' ' * 126 + '0s')
        self.assertIs(type(result), int)
        self.assertEqual(result, 0)
        with self.assertRaises(ValueError):
            SOLUTION.parse_duration(' ' * 127 + '0s')

    def test_large_decimal(self):
        with self.assertRaises(ValueError):
            SOLUTION.parse_duration('9' * 100 + 'ms')


class TailTests(unittest.TestCase):
    def test_complete_character_suffix(self):
        for limit, expected in [(0, ''), (1, 'B'), (2, 'B'), (3, 'B'),
                                (4, '€B'), (5, 'A€B'), (20, 'A€B')]:
            with self.subTest(limit=limit):
                self.assertEqual(SOLUTION.utf8_tail([b'A\xe2\x82\xacB'], limit), expected)

    def test_four_byte_character_split(self):
        self.assertEqual(SOLUTION.utf8_tail(iter([b'X\xf0', b'\x9f', b'\x98', b'\x80']), 4), '😀')
        self.assertEqual(SOLUTION.utf8_tail([b'X\xf0\x9f\x98\x80'], 3), '')

    def test_multiple_character_sizes(self):
        self.assertEqual(SOLUTION.utf8_tail(['é€😀'.encode('utf-8')], 7), '€😀')
        self.assertEqual(SOLUTION.utf8_tail(['é€😀'.encode('utf-8')], 6), '😀')

    def test_empty_chunks(self):
        self.assertEqual(SOLUTION.utf8_tail([b'', b'a', b'', b'b', b''], 2), 'ab')
        self.assertEqual(SOLUTION.utf8_tail([], 5), '')

    def test_invalid_discarded_input(self):
        with self.assertRaises(UnicodeDecodeError):
            SOLUTION.utf8_tail([b'\xff', b'good suffix'], 4)

    def test_zero_still_validates(self):
        for chunks in [[b'\xc0\x80'], [b'\xed\xa0\x80'], [b'\xe2', b'\x82']]:
            with self.subTest(chunks=chunks), self.assertRaises(UnicodeDecodeError):
                SOLUTION.utf8_tail(chunks, 0)

    def test_bad_chunk_types(self):
        for chunk in ['text', bytearray(b'abc'), memoryview(b'a'), None]:
            with self.subTest(kind=type(chunk).__name__), self.assertRaises(TypeError):
                SOLUTION.utf8_tail([b'prefix', chunk], 0)

    def test_limit_types(self):
        for limit in [True, False, 1.0, None, '2']:
            with self.subTest(limit=limit), self.assertRaises(TypeError):
                SOLUTION.utf8_tail([], limit)
        with self.assertRaises(ValueError):
            SOLUTION.utf8_tail([], -1)

    def test_limit_checked_before_iteration(self):
        def forbidden():
            raise AssertionError('invalid limit consumed input')
            yield b''
        with self.assertRaises(ValueError):
            SOLUTION.utf8_tail(forbidden(), -1)

    def test_one_shot_many_chunks(self):
        chunks = (b'abc' for _ in range(10000))
        self.assertEqual(SOLUTION.utf8_tail(chunks, 7), 'cabcabc')


class AtomicTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'snapshot.json'

    def test_canonical_unicode(self):
        self.assertIsNone(SOLUTION.write_snapshot(self.path, {'z': 'é', 'a': [1, None]}))
        self.assertEqual(self.path.read_bytes(), '{\n  "a": [\n    1,\n    null\n  ],\n  "z": "é"\n}\n'.encode())

    def test_replace_existing_longer_file(self):
        self.path.write_bytes(b'x' * 10000)
        SOLUTION.write_snapshot(str(self.path), 3)
        self.assertEqual(self.path.read_bytes(), b'3\n')
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_serialization_failure_preserves_old(self):
        self.path.write_bytes(b'old valid snapshot')
        with self.assertRaises(TypeError):
            SOLUTION.write_snapshot(self.path, {'x': object()})
        self.assertEqual(self.path.read_bytes(), b'old valid snapshot')
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_nonfinite_rejected(self):
        for value in [math.nan, math.inf, -math.inf]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                SOLUTION.write_snapshot(self.path, {'number': value})
            self.assertEqual(list(self.root.iterdir()), [])

    def test_replacement_failure_cleans_temporary(self):
        self.path.mkdir()
        sentinel = self.path / 'keep'
        sentinel.write_bytes(b'untouched')
        with self.assertRaises(OSError):
            SOLUTION.write_snapshot(self.path, {'new': 1})
        self.assertEqual(set(self.root.iterdir()), {self.path})
        self.assertEqual(sentinel.read_bytes(), b'untouched')

    def test_missing_parent_not_created(self):
        with self.assertRaises(FileNotFoundError):
            SOLUTION.write_snapshot(self.root / 'missing' / 'snapshot', {'x': 1})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unrelated_temporary_looking_files_preserved(self):
        others = [self.root / 'snapshot.json.tmp', self.root / '.snapshot.json.next']
        for path in others:
            path.write_bytes(b'belongs to someone else')
        SOLUTION.write_snapshot(self.path, [])
        self.assertEqual(set(self.root.iterdir()), {self.path, *others})
        for path in others:
            self.assertEqual(path.read_bytes(), b'belongs to someone else')

    def test_input_unchanged(self):
        value = {'z': [3, {'q': 'text'}], 'a': False}
        before = copy.deepcopy(value)
        SOLUTION.write_snapshot(self.path, value)
        self.assertEqual(value, before)


SHA = '0123456789abcdef0123456789abcdef01234567'


def valid_receipt():
    return dict(run_id='run-7', origin='trusted-runner', head=SHA, head_after=SHA,
                profile='profile-2', exit_code=0, scope=[], dirty_before='', dirty_after='',
                environment={'addopts': '', 'plugins': ''})


class ReceiptTests(unittest.TestCase):
    def test_valid_and_extras(self):
        receipt = valid_receipt()
        receipt['extra'] = ['anything']
        receipt['environment']['extra'] = 9
        self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), True)

    def test_missing_each_required_field(self):
        for field in valid_receipt():
            receipt = valid_receipt()
            del receipt[field]
            with self.subTest(field=field):
                self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), False)

    def test_mismatched_identity(self):
        for field, value in [('head', 'a' * 40), ('head_after', 'a' * 40),
                             ('profile', 'other'), ('origin', 'worker'), ('run_id', '')]:
            receipt = valid_receipt()
            receipt[field] = value
            with self.subTest(field=field):
                self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), False)

    def test_exact_scalar_types(self):
        for field, value in [('exit_code', False), ('exit_code', 0.0), ('exit_code', '0'),
                             ('dirty_before', False), ('dirty_after', []), ('run_id', 3)]:
            receipt = valid_receipt()
            receipt[field] = value
            with self.subTest(field=field, value=value):
                self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), False)

    def test_scope_exactly_empty_list(self):
        for value in [None, (), {}, '', ['one-test']]:
            receipt = valid_receipt()
            receipt['scope'] = value
            with self.subTest(value=value):
                self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), False)

    def test_environment_fields(self):
        for value in [None, [], {}, {'addopts': ''}, {'plugins': ''},
                      {'addopts': '-k one', 'plugins': ''}, {'addopts': '', 'plugins': False}]:
            receipt = valid_receipt()
            receipt['environment'] = value
            with self.subTest(value=value):
                self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), False)

    def test_candidate_validation_even_when_fields_match(self):
        for candidate in ['', 'a' * 39, 'a' * 41, 'A' * 40, 'g' * 40, '０' * 40, None, 42]:
            receipt = valid_receipt()
            receipt['head'] = receipt['head_after'] = candidate
            with self.subTest(candidate=candidate):
                self.assertIs(SOLUTION.eligible(receipt, candidate, 'profile-2'), False)

    def test_malformed_outer_values(self):
        for receipt in [None, [], 'receipt', 0, False]:
            self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), False)
        for profile in ['', None, 9, []]:
            receipt = valid_receipt()
            receipt['profile'] = profile
            self.assertIs(SOLUTION.eligible(receipt, SHA, profile), False)

    def test_nonempty_whitespace_not_trimmed(self):
        receipt = valid_receipt()
        receipt['run_id'] = ' '
        receipt['profile'] = ' '
        self.assertIs(SOLUTION.eligible(receipt, SHA, ' '), True)

    def test_nonmutation_and_dirty_rejection(self):
        receipt = valid_receipt()
        receipt['dirty_after'] = '?? file'
        before = copy.deepcopy(receipt)
        self.assertIs(SOLUTION.eligible(receipt, SHA, 'profile-2'), False)
        self.assertEqual(receipt, before)


def job(identifier, priority=1, submitted=0, deps=()):
    return dict(id=identifier, priority=priority, submitted=submitted, depends_on=list(deps))


class OrderTests(unittest.TestCase):
    def test_all_three_order_keys(self):
        jobs = [job('z', 5, 8), job('b', 9, 2), job('a', 9, 2), job('x', 9, 1)]
        self.assertEqual(SOLUTION.ready_jobs(jobs, set()), ['x', 'a', 'b', 'z'])

    def test_dependencies_not_speculatively_completed(self):
        jobs = [job('a'), job('b', 9, deps=['a']), job('c', 9, deps=['b'])]
        self.assertEqual(SOLUTION.ready_jobs(jobs, set()), ['a'])
        self.assertEqual(SOLUTION.ready_jobs(jobs, {'a'}), ['b'])

    def test_completed_not_dependency_closed(self):
        jobs = [job('a'), job('b', deps=['a']), job('c', 9, deps=['b'])]
        self.assertEqual(SOLUTION.ready_jobs(jobs, {'b'}), ['c', 'a'])

    def test_multiple_dependencies(self):
        jobs = [job('a'), job('b'), job('c', 9, deps=['a', 'b'])]
        self.assertEqual(SOLUTION.ready_jobs(jobs, {'a'}), ['b'])
        self.assertEqual(SOLUTION.ready_jobs(jobs, {'a', 'b'}), ['c'])

    def test_cycles_even_when_completed(self):
        jobs = [job('a', deps=['c']), job('b', deps=['a']), job('c', deps=['b'])]
        for completed in [set(), {'a', 'b', 'c'}]:
            with self.subTest(completed=completed), self.assertRaises(ValueError):
                SOLUTION.ready_jobs(jobs, completed)

    def test_invalid_graph_references(self):
        for jobs in [[job('a'), job('a')], [job('a', deps=['missing'])],
                     [job('a', deps=['a'])], [job('a'), job('b', deps=['a', 'a'])]]:
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                SOLUTION.ready_jobs(jobs, set())

    def test_invalid_job_fields(self):
        cases = [('id', ''), ('id', 3), ('priority', True), ('priority', 10),
                 ('priority', -1), ('submitted', 1.0), ('submitted', -1),
                 ('submitted', False), ('depends_on', ()), ('depends_on', [None])]
        for field, value in cases:
            entry = job('a')
            entry[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                SOLUTION.ready_jobs([entry], set())

    def test_outer_shapes_and_missing_keys(self):
        for jobs, completed in [(None, set()), ({}, set()), ([], []),
                                 ([job('a')], {'missing'}), ([None], set()),
                                 ([{'id': 'a'}], set())]:
            with self.subTest(jobs=jobs, completed=completed), self.assertRaises(ValueError):
                SOLUTION.ready_jobs(jobs, completed)

    def test_validate_completed_job(self):
        broken = job('a')
        broken['priority'] = 'high'
        with self.assertRaises(ValueError):
            SOLUTION.ready_jobs([broken], {'a'})

    def test_empty_extras_and_nonmutation(self):
        self.assertEqual(SOLUTION.ready_jobs([], set()), [])
        jobs = [job('z'), job('a')]
        jobs[0]['note'] = 'permitted'
        before = copy.deepcopy(jobs)
        completed = {'z'}
        self.assertEqual(SOLUTION.ready_jobs(jobs, completed), ['a'])
        self.assertEqual(jobs, before)
        self.assertEqual(completed, {'z'})


HEADER = 'status\tcount\ttotal_ms\tavg_ms\n'


class SummaryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(SOLUTION.summarize(iter([])), HEADER)

    def test_fixed_order_and_absent_omission(self):
        records = [{'status': 'failed', 'duration_ms': 8}, {'status': 'queued', 'duration_ms': None},
                   {'status': 'done', 'duration_ms': 3}]
        self.assertEqual(SOLUTION.summarize(records), HEADER + 'queued\t1\t0\t-\ndone\t1\t3\t3\nfailed\t1\t8\t8\n')

    def test_missing_duration_excluded_from_denominator(self):
        records = [{'status': 'running', 'duration_ms': x} for x in [None, 2, 3, None]]
        self.assertEqual(SOLUTION.summarize(records), HEADER + 'running\t4\t5\t3\n')

    def test_half_up_and_below_half(self):
        records = [{'status': 'done', 'duration_ms': x} for x in [0, 1]]
        records += [{'status': 'failed', 'duration_ms': x} for x in [0, 0, 1]]
        self.assertEqual(SOLUTION.summarize(records), HEADER + 'done\t2\t1\t1\nfailed\t3\t1\t0\n')

    def test_large_integer_precision(self):
        records = [{'status': 'done', 'duration_ms': 9007199254740992},
                   {'status': 'done', 'duration_ms': 9007199254740993}]
        self.assertEqual(SOLUTION.summarize(records), HEADER + 'done\t2\t18014398509481985\t9007199254740993\n')

    def test_zero_is_measurement(self):
        self.assertEqual(SOLUTION.summarize([{'status': 'queued', 'duration_ms': 0}]),
                         HEADER + 'queued\t1\t0\t0\n')

    def test_repeated_objects_and_generator(self):
        record = {'status': 'done', 'duration_ms': 4, 'extra': 'ignored'}
        self.assertEqual(SOLUTION.summarize(record for _ in range(3)), HEADER + 'done\t3\t12\t4\n')
        self.assertEqual(record, {'status': 'done', 'duration_ms': 4, 'extra': 'ignored'})

    def test_invalid_status(self):
        for status in ['Done', '', None, [], 1]:
            with self.subTest(status=status), self.assertRaises(ValueError):
                SOLUTION.summarize([{'status': status, 'duration_ms': 0}])

    def test_invalid_duration(self):
        for duration in [False, True, -1, 1.0, '1', [], {}]:
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                SOLUTION.summarize([{'status': 'done', 'duration_ms': duration}])

    def test_malformed_records(self):
        for records in [None, 3, [None], [{}], [{'status': 'done'}], [{'duration_ms': 1}]]:
            with self.subTest(records=records), self.assertRaises(ValueError):
                SOLUTION.summarize(records)


SUITES = dict(duration=DurationTests, utf8_tail=TailTests, atomic_json=AtomicTests,
              receipt=ReceiptTests, job_order=OrderTests, tsv_summary=SummaryTests)


def main():
    global SOLUTION
    if len(sys.argv) != 3 or sys.argv[1] not in SUITES:
        raise SystemExit('usage: hidden_evaluator.py TASK_ID /absolute/path/to/solution.py')
    task_id, path = sys.argv[1:]
    spec = importlib.util.spec_from_file_location('pilot_solution', path)
    SOLUTION = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(SOLUTION)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SUITES[task_id])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps(dict(task_id=task_id, tests=result.testsRun,
                          failures=len(result.failures), errors=len(result.errors),
                          passed=result.wasSuccessful()), sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
