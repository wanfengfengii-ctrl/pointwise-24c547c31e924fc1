import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from measure.requests import (
    AssessmentRequest, WindowAssessmentRequest, sign, sign_windows,
)
from prepare.issue_repository import IssueRepository
from prepare.issuance import issue, issue_window, reissue
from receive.envelope import V1Envelope, decode_envelope, encode_envelope
from receive.intake import intake
from receive.repository import ReceiptRepository
from record.capture import open_trial, record_observation
from record.repository import RevisionRepository
from record.revisions import freeze
from report.dispatch import read_dispatch_basis
from report.history import retrieve
from report.repository import ReportRepository
from tests.test_freeze import frozen_head, pats


def block_first_receipts(connection):
    connection.executescript('''
        CREATE TEMP TRIGGER block_first_receipt BEFORE INSERT ON first_receipts
        WHEN EXISTS (SELECT 1 FROM final_reports WHERE report_id = NEW.report_id)
        BEGIN
            SELECT RAISE(ABORT, 'first receipt blocked after staging final');
        END;
    ''')


class WindowIssueTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'station.sqlite'
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        self.revisions = RevisionRepository(self.connection)
        self.issues = IssueRepository(self.connection)

    def test_window_issue_fixes_identity_window_and_evidence(self):
        revision = frozen_head(self.revisions, 'W23', 'A',
                               [('a0', 0, 11), ('a3', 3, 11)])
        item = issue_window(self.revisions, self.issues, revision, (0, 1))
        self.assertEqual(item.revision_id, revision)
        envelope = decode_envelope(item.payload)
        self.assertEqual(envelope.body['window'], ['0', '1'])
        self.assertEqual(envelope.body['origin'], envelope.source)
        self.assertEqual(envelope.body['links'][0][-2:], ['a0', 'a3'])
        # zero-width closed windows are allowed
        singleton = issue_window(self.revisions, self.issues, revision, (1, 1))
        self.assertEqual(decode_envelope(singleton.payload).body['window'], ['1', '1'])
        with self.assertRaises(ValueError):
            issue_window(self.revisions, self.issues, revision, (2, 0))
        with self.assertRaises(ValueError):
            issue_window(self.revisions, self.issues, revision, (0, 'nonsense'))

    def test_reissue_replays_identical_bytes_and_registers(self):
        revision = frozen_head(self.revisions, 'W23', 'A', [('a0', 0, 11)])
        item = issue_window(self.revisions, self.issues, revision, (0, 1))
        replayed = reissue(self.issues, item.issue_id)
        self.assertEqual(replayed, item)
        self.assertEqual(replayed.payload, item.payload)
        self.assertEqual(self.issues.replay_count(item.issue_id), 1)

    def test_windowed_and_whole_issues_are_distinguishable(self):
        revision = frozen_head(self.revisions, 'W23', 'A', [('a0', 0, 11), ('a1', 1, 11)])
        whole = issue(self.revisions, self.issues, revision)
        windowed = issue_window(self.revisions, self.issues, revision, (0, 1))
        self.assertNotIn('window', decode_envelope(whole.payload).body)
        self.assertIn('window', decode_envelope(windowed.payload).body)


class WindowIntakeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.station = root / 'station.sqlite'
        self.inbox_path = root / 'received.sqlite'
        with closing(sqlite3.connect(self.station)) as connection:
            revisions = RevisionRepository(connection)
            issues = IssueRepository(connection)
            revision = frozen_head(revisions, 'W23', 'A', [('a0', 0, 11), ('a3', 3, 11)])
            self.item = issue_window(revisions, issues, revision, (0, 1))
        self.connection = sqlite3.connect(self.inbox_path)
        self.addCleanup(self.connection.close)
        self.receipts = ReceiptRepository(self.connection)

    def test_accepts_window_issue_and_stores_bytes(self):
        result = intake(self.receipts, self.item.payload)
        self.assertTrue(result.accepted)
        received = self.receipts.get(result.receipt_id)
        self.assertEqual(received.payload, self.item.payload)
        self.assertEqual((received.source.trial_id, received.source.head), ('W23', 'A'))

    def test_rejects_illegal_window_and_bad_source(self):
        envelope = decode_envelope(self.item.payload)
        illegal_window = encode_envelope(
            V1Envelope(envelope.source, {**envelope.body, 'window': ['2', '0']}))
        malformed_window = encode_envelope(
            V1Envelope(envelope.source, {**envelope.body, 'window': ['0']}))
        other_trial = encode_envelope(
            V1Envelope({**envelope.source, 'trial_id': 'W24'}, envelope.body))
        for payload in (illegal_window, malformed_window, other_trial, b'not json'):
            with self.subTest(payload=payload):
                result = intake(self.receipts, payload)
                self.assertFalse(result.accepted)
                self.assertIsNone(result.receipt_id)
                self.assertTrue(result.reason)
        self.assertEqual(self.receipts.counts(), (0, 4))

    def test_repeated_intake_gets_distinct_receipt_numbers(self):
        first = intake(self.receipts, self.item.payload)
        second = intake(self.receipts, self.item.payload)
        self.assertNotEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(self.receipts.get(first.receipt_id).payload,
                         self.receipts.get(second.receipt_id).payload)


class WindowSignTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.station_path = self.root / 'station.sqlite'
        self.report_path = self.root / 'reports.sqlite'
        self.receipts = ReceiptRepository(sqlite3.connect(self.root / 'received.sqlite'))
        self.addCleanup(self.receipts.connection.close)
        self.reports = ReportRepository(sqlite3.connect(self.report_path))
        self.addCleanup(self.reports.connection.close)
        self._rev = {}

    def freeze(self, alias, head, rows):
        with closing(sqlite3.connect(self.station_path)) as connection:
            revisions = RevisionRepository(connection)
            self._rev[alias] = frozen_head(revisions, 'W23', head, rows)

    def issue_and_receive(self, alias, window):
        with closing(sqlite3.connect(self.station_path)) as connection:
            revisions = RevisionRepository(connection)
            issues = IssueRepository(connection)
            item = issue_window(revisions, issues, self._rev[alias], window)
        result = intake(self.receipts, item.payload)
        self.assertTrue(result.accepted, result.reason)
        return result.receipt_id

    def receive_whole(self, alias):
        with closing(sqlite3.connect(self.station_path)) as connection:
            revisions = RevisionRepository(connection)
            issues = IssueRepository(connection)
            item = issue(revisions, issues, self._rev[alias])
        result = intake(self.receipts, item.payload)
        self.assertTrue(result.accepted)
        return result.receipt_id

    def request(self, left, right, window=(0, 3), limits=(10, 10)):
        return WindowAssessmentRequest(left, right, window, *limits)

    def test_gap_signs_two_one_second_segments(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        left_one = self.issue_and_receive('a', (0, 1))
        left_two = self.issue_and_receive('a', (2, 3))
        right = self.issue_and_receive('b', (0, 3))
        committed = sign_windows(
            self.receipts, self.reports, self.request([left_one, left_two], [right]))
        snapshot = committed.snapshot
        rows = [(s.start, s.end, s.left_closed, s.right_closed) for s in snapshot.segments]
        self.assertEqual(rows, [(0, 1, True, True), (2, 3, True, True)])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (2, 1))
        self.assertEqual(snapshot.dispatch_seconds, 1)
        first, second = snapshot.provenance.segments
        self.assertEqual(first.left, ((left_one, ('a0', 'a3')),))
        self.assertEqual(second.left, ((left_two, ('a0', 'a3')),))
        self.assertEqual(first.right, ((right, ('b0', 'b3')),))
        self.assertEqual(second.right, ((right, ('b0', 'b3')),))

    def test_overlapping_revisions_must_agree_and_merge_support(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('aligned', 'A', [('q0', 0, 11), ('q1', 1, 11), ('q3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        l0 = self.issue_and_receive('a', (0, Fraction(3, 2)))
        l1 = self.issue_and_receive('aligned', (Fraction(1, 2), 3))
        l2 = self.issue_and_receive('a', (1, 1))
        l3 = self.issue_and_receive('aligned', (1, 1))
        right = self.issue_and_receive('b', (0, 3))
        committed = sign_windows(
            self.receipts, self.reports, self.request([l0, l1, l2, l3], [right]))
        snapshot = committed.snapshot
        rows = [(s.start, s.end, s.left_closed, s.right_closed) for s in snapshot.segments]
        self.assertEqual(rows, [(0, 3, True, True)])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (3, 3))
        segment, = snapshot.provenance.segments
        self.assertEqual(dict(segment.left), {
            l0: ('a0', 'a3'), l1: ('q0', 'q1', 'q3'),
            l2: ('a0', 'a3'), l3: ('q1',),
        })
        self.assertEqual(segment.right, ((right, ('b0', 'b3')),))
        self.assertEqual(tuple(s.receipt_id for s in snapshot.provenance.left),
                         (l0, l1, l2, l3))

    def test_missing_against_value_is_conflict_even_at_single_point(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('missing', 'A', [('m0', 0, 11), ('m1', 1, None), ('m3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        l0 = self.issue_and_receive('a', (0, 1))
        l1 = self.issue_and_receive('missing', (1, 1))
        right = self.issue_and_receive('b', (0, 3))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            sign_windows(self.receipts, self.reports, self.request([l0, l1], [right]))
        self.assertEqual(self.reports.counts(), (0, 0))

    def test_cool_against_hot_overlap_is_conflict(self):
        self.freeze('cool', 'A', [('c0', 0, 8), ('c3', 3, 8)])
        self.freeze('peak', 'A', [('s0', 0, 8), ('s1', 1, 9), ('s3', 3, 8)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        l0 = self.issue_and_receive('cool', (0, 3))
        l1 = self.issue_and_receive('peak', (0, 3))
        right = self.issue_and_receive('b', (0, 3))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            sign_windows(self.receipts, self.reports, self.request([l0, l1], [right]))
        self.assertEqual(self.reports.counts(), (0, 0))

    def test_conflict_outside_assessment_window_is_ignored(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('missing', 'A', [('m0', 0, 11), ('m1', 1, None), ('m3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        l0 = self.issue_and_receive('a', (0, 1))
        l1 = self.issue_and_receive('missing', (1, 1))
        right = self.issue_and_receive('b', (0, 3))
        committed = sign_windows(
            self.receipts, self.reports, self.request([l0, l1], [right], (0, Fraction(1, 2))))
        snapshot = committed.snapshot
        rows = [(s.start, s.end, s.left_closed, s.right_closed) for s in snapshot.segments]
        self.assertEqual(rows, [(0, Fraction(1, 2), True, True)])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds),
                         (Fraction(1, 2), Fraction(1, 2)))
        segment, = snapshot.provenance.segments
        self.assertEqual([entry[0] for entry in segment.left], [l0])

    def test_singleton_assessment_window_has_no_positive_segment(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        left = self.issue_and_receive('a', (0, 3))
        right = self.issue_and_receive('b', (0, 3))
        snapshot = sign_windows(
            self.receipts, self.reports, self.request([left], [right], (1, 1))).snapshot
        self.assertEqual(snapshot.segments, ())
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (0, 0))
        self.assertEqual(snapshot.provenance.segments, ())

    def test_unknown_receipt_is_lookup_error(self):
        self.freeze('a', 'A', [('a0', 0, 11)])
        self.freeze('b', 'B', [('b0', 0, 11)])
        left = self.issue_and_receive('a', (0, 1))
        right = self.issue_and_receive('b', (0, 1))
        with self.assertRaises(LookupError):
            sign_windows(self.receipts, self.reports,
                         self.request([left, 999], [right]))
        self.assertEqual(self.reports.counts(), (0, 0))

    def test_request_group_validation(self):
        self.freeze('a', 'A', [('a0', 0, 11)])
        self.freeze('b', 'B', [('b0', 0, 11)])
        left = self.issue_and_receive('a', (0, 1))
        right = self.issue_and_receive('b', (0, 1))
        for kwargs in (
            dict(left_receipt_ids=[], right_receipt_ids=[right]),
            dict(left_receipt_ids=(left,), right_receipt_ids=()),
            dict(left_receipt_ids={left}, right_receipt_ids=[right]),
            dict(left_receipt_ids=[left, left], right_receipt_ids=[right]),
            dict(left_receipt_ids=[left], right_receipt_ids=[left]),
            dict(left_receipt_ids=[0], right_receipt_ids=[right]),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    WindowAssessmentRequest(window=(0, 1), left_threshold=10,
                                            right_threshold=10, **kwargs)

    def test_head_and_trial_guards(self):
        self.freeze('a', 'A', [('a0', 0, 11)])
        self.freeze('b', 'B', [('b0', 0, 11)])
        left = self.issue_and_receive('a', (0, 1))
        right = self.issue_and_receive('b', (0, 1))
        # same head on both groups
        other_a = self.issue_and_receive('a', (0, 1))
        with self.assertRaisesRegex(ValueError, 'different heads'):
            sign_windows(self.receipts, self.reports, self.request([left], [other_a]))
        # whole-item receipt cannot be used with window signing
        whole_a = self.receive_whole('a')
        with self.assertRaisesRegex(ValueError, 'windowed'):
            sign_windows(self.receipts, self.reports, self.request([whole_a], [right]))
        # old whole-item sign refuses windowed receipts
        with self.assertRaisesRegex(ValueError, 'windowed'):
            sign(self.receipts, self.reports,
                 AssessmentRequest(left, right, (0, 1), 10, 10))
        self.assertEqual(self.reports.counts(), (0, 0))

    def test_cross_trial_group_rejected(self):
        with closing(sqlite3.connect(self.station_path)) as connection:
            revisions = RevisionRepository(connection)
            issues = IssueRepository(connection)
            w23 = frozen_head(revisions, 'W23', 'A', [('a0', 0, 11)])
            w24 = frozen_head(revisions, 'W24', 'A', [('a0', 0, 11)])
            item23 = issue_window(revisions, issues, w23, (0, 1))
            item24 = issue_window(revisions, issues, w24, (0, 1))
        left = intake(self.receipts, item23.payload).receipt_id
        other = intake(self.receipts, item24.payload).receipt_id
        self.freeze('b', 'B', [('b0', 0, 11)])
        right = self.issue_and_receive('b', (0, 1))
        with self.assertRaisesRegex(ValueError, 'single trial'):
            sign_windows(self.receipts, self.reports, self.request([left, other], [right]))

    def test_commit_failure_leaves_no_half_item_and_retry_succeeds(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        left = self.issue_and_receive('a', (0, 1))
        right = self.issue_and_receive('b', (0, 3))
        request = self.request([left], [right])
        original = sign_windows(self.receipts, self.reports, request)
        block_first_receipts(self.reports.connection)
        with self.assertRaises(sqlite3.IntegrityError):
            sign_windows(self.receipts, self.reports, request)
        self.assertEqual(self.reports.counts(), (1, 1))
        self.assertEqual(retrieve(self.reports, original.report_id), original)
        # reopen the official ledger, then re-sign by the same receipt numbers
        self.reports.connection.close()
        self.reports = ReportRepository(sqlite3.connect(self.report_path))
        self.addCleanup(self.reports.connection.close)
        retried = sign_windows(self.receipts, self.reports, request)
        self.assertGreater(retried.report_id, original.report_id)
        self.assertEqual(retried.snapshot.segments, original.snapshot.segments)
        self.assertEqual(read_dispatch_basis(self.reports, retried.report_id).longest_seconds, 1)
        reopened = retrieve(self.reports, original.report_id)
        self.assertEqual(reopened, original)

    def test_later_errata_do_not_change_signed_records(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        left = self.issue_and_receive('a', (0, 3))
        right = self.issue_and_receive('b', (0, 3))
        original = sign_windows(
            self.receipts, self.reports, self.request([left], [right]))
        with closing(sqlite3.connect(self.station_path)) as connection:
            revisions = RevisionRepository(connection)
            issues = IssueRepository(connection)
            cool = frozen_head(revisions, 'W23', 'A', [('c0', 0, 8), ('c3', 3, 8)],
                               self._rev['a'])
            errata = issue_window(revisions, issues, cool, (0, 3))
        intake(self.receipts, errata.payload)
        self.assertEqual(retrieve(self.reports, original.report_id), original)
        self.assertEqual(self.reports.counts(), (1, 1))

    def test_bad_window_evidence_cannot_be_signed(self):
        self.freeze('a', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        good_left = self.issue_and_receive('a', (0, 3))
        right = self.issue_and_receive('b', (0, 3))
        with closing(sqlite3.connect(self.station_path)) as connection:
            revisions = RevisionRepository(connection)
            issues = IssueRepository(connection)
            item = issue_window(revisions, issues, self._rev['a'], (0, 3))
        envelope = decode_envelope(item.payload)
        # Two links covering the same interior: invalid evidence.
        body = {**envelope.body, 'links': envelope.body['links'] + envelope.body['links']}
        bad = intake(self.receipts, encode_envelope(V1Envelope(envelope.source, body)))
        self.assertTrue(bad.accepted)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            sign_windows(self.receipts, self.reports,
                         self.request([good_left, bad.receipt_id], [right]))
        self.assertEqual(self.reports.counts(), (0, 0))

    def test_missing_inside_window_differs_from_silence_outside(self):
        # m1 is an explicit gap: the link m0-m3 cannot be interpolated across it.
        self.freeze('missing', 'A',
                    [('m0', 0, 11), ('m1', 1, None), ('m3', 3, 11)])
        self.freeze('b', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        left = self.issue_and_receive('missing', (0, 3))
        right = self.issue_and_receive('b', (0, 3))
        snapshot = sign_windows(
            self.receipts, self.reports, self.request([left], [right])).snapshot
        # m0 is isolated (neighbour m1 missing): only t=0 qualifies.
        # m3 is isolated: only t=3 qualifies. No positive-length segment.
        self.assertEqual(snapshot.segments, ())
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (0, 0))
        # A second head window that only covers the silent region never conflicts
        # and simply produces no qualified time.
        other = self.issue_and_receive('missing', (Fraction(3, 2), 2))
        snapshot = sign_windows(
            self.receipts, self.reports, self.request([left, other], [right])).snapshot
        self.assertEqual(snapshot.segments, ())
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (0, 0))


if __name__ == '__main__':
    unittest.main()
