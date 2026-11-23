import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from measure.intervals import BoundaryInterval
from measure.qualification import qualify_common
from measure.requests import AssessmentRequest, sign
from prepare.issue_repository import IssueRepository
from prepare.issuance import issue
from receive.envelope import V1Envelope, decode_envelope, encode_envelope
from receive.intake import intake
from receive.pairing import select_pair
from receive.repository import ReceiptRepository
from record.repository import RevisionRepository
from report.dispatch import read_dispatch_basis
from report.repository import ReportRepository
from tests.test_freeze import frozen_head, pats


def issue_heads(station_path, *selections):
    with closing(sqlite3.connect(station_path)) as station:
        revisions, issues = RevisionRepository(station), IssueRepository(station)
        return [issue(revisions, issues, frozen_head(revisions, trial, head, rows))
                for trial, head, rows in selections]


def receive_items(test, repository, *issued):
    numbers = []
    for item in issued:
        result = intake(repository, item.payload)
        test.assertTrue(result.accepted, result.reason)
        numbers.append(result.receipt_id)
    return numbers


def interval_rows(snapshot):
    return [(segment.start, segment.end, segment.left_closed, segment.right_closed)
            for segment in snapshot.segments]


class DwellTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.station = self.root / 'station.sqlite'
        self.intake_path = self.root / 'received.sqlite'
        self.receipts = ReceiptRepository(self.connect(self.intake_path))
        self.reports = ReportRepository(self.connect(self.root / 'reports.sqlite'))

    def connect(self, path):
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        return connection

    def offline(self, *selections):
        items = issue_heads(self.station, *selections)
        receipts = receive_items(self, self.receipts, *items)
        self.station.unlink()
        return receipts

    def assess(self, left, right, window=(0, 2), limits=(10, 10)):
        return sign(self.receipts, self.reports, AssessmentRequest(left, right, window, *limits))

    def test_constant_hot_pair_offline(self):
        left, right = self.offline(('T17', 'A', pats('A', (0, 11), (1, 11), (2, 11))),
                                   ('T17', 'B', pats('B', (0, 11), (2, 11))))
        report = self.assess(left, right)
        snapshot = report.snapshot
        self.assertEqual(interval_rows(snapshot), [(0, 2, True, True)])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (2, 2))
        self.assertEqual((snapshot.provenance.left.receipt_id, snapshot.provenance.right.receipt_id), (left, right))
        self.assertEqual(snapshot.provenance.left.source, self.receipts.get(left).source)
        self.assertEqual(snapshot.provenance.right.source, self.receipts.get(right).source)
        evidence, = snapshot.provenance.segments
        self.assertEqual((evidence.left_pats, evidence.right_pats), (('A0', 'A1', 'A2'), ('B0', 'B1')))
        self.assertEqual(report.first_receipt.report_id, report.report_id)
        self.assertEqual(self.reports.counts(), (1, 1))

    def test_intake_source(self):
        item, = issue_heads(self.station, ('T17', 'A', pats('A', (0, 11), (2, 11))))
        envelope = decode_envelope(item.payload)
        payload = b' ' + item.payload + b'\n'
        accepted = intake(self.receipts, payload)
        self.assertTrue(accepted.accepted)
        mismatch = encode_envelope(V1Envelope({**envelope.source, 'trial_id': 'T18'}, envelope.body))
        invalid_origin = encode_envelope(V1Envelope(envelope.source, {**envelope.body, 'origin': {}}))
        for invalid in (item.payload.replace(b'"version":1', b'"version":2'), mismatch,
                        invalid_origin, item.payload.replace(b'T17', b'T18'), b'not JSON'):
            with self.subTest(invalid=invalid):
                rejected = intake(self.receipts, invalid)
                self.assertFalse(rejected.accepted)
                self.assertIsNone(rejected.receipt_id)
                self.assertTrue(rejected.reason)
        self.receipts.connection.close()
        reopened = ReceiptRepository(self.connect(self.intake_path))
        received = reopened.get(accepted.receipt_id)
        self.assertEqual(received.payload, payload)
        self.assertEqual((received.source.trial_id, received.source.head), ('T17', 'A'))
        self.assertEqual((received.source.issue_id, received.source.revision_id), (item.issue_id, item.revision_id))
        self.assertEqual(reopened.counts(), (1, 5))
        with self.assertRaises(LookupError):
            reopened.get(accepted.receipt_id + 1)

    def test_phase_alignment(self):
        left, falling, rising = self.offline(
            ('T17', 'A', [('A0', 0, 9), ('A2', 2, 11)]),
            ('T17', 'B', [('B0', 0, 11), ('B2', 2, 9)]),
            ('T17', 'B', [('C0', 0, 9), ('C2', 2, 11)]),
        )
        anti_phase = self.assess(left, falling).snapshot
        in_phase = self.assess(left, rising).snapshot
        self.assertEqual(interval_rows(anti_phase), [])
        self.assertEqual((anti_phase.total_seconds, anti_phase.longest_seconds), (0, 0))
        self.assertEqual(anti_phase.provenance.right.receipt_id, falling)
        self.assertEqual(anti_phase.provenance.segments, ())
        self.assertEqual(interval_rows(in_phase), [(1, 2, False, True)])
        self.assertEqual((in_phase.total_seconds, in_phase.longest_seconds), (1, 1))
        self.assertEqual(in_phase.provenance.left.receipt_id, left)
        self.assertEqual(in_phase.provenance.right.receipt_id, rising)
        evidence, = in_phase.provenance.segments
        self.assertEqual((evidence.left_pats, evidence.right_pats), (('A0', 'A2'), ('C0', 'C2')))

    def test_fractional_window(self):
        left, right = self.offline(('T17', 'A', pats('A', (0, 9), (2, 11))),
                                   ('T17', 'B', pats('B', (0, 11), (2, 11))))
        report = self.assess(left, right, ('1/3', '5/6'), ('19/2', '21/2'))
        self.assertEqual(interval_rows(report.snapshot), [(Fraction(1, 2), Fraction(5, 6), False, True)])
        self.assertEqual((report.snapshot.total_seconds, report.snapshot.longest_seconds), (Fraction(1, 3),) * 2)
        evidence, = report.snapshot.provenance.segments
        self.assertEqual((evidence.left_pats, evidence.right_pats), (('A0', 'A1'), ('B0', 'B1')))
        reversed_report = self.assess(right, left, ('1/3', '5/6'), ('21/2', '19/2'))
        self.assertEqual(interval_rows(reversed_report.snapshot), interval_rows(report.snapshot))
        self.assertEqual(reversed_report.snapshot.provenance.left.receipt_id, right)

    def test_request_bounds(self):
        left, right = self.offline(('T17', 'A', pats('A', (0, 11), (2, 11))),
                                   ('T17', 'B', pats('B', (0, 11), (2, 11))))
        for window, limits in [((2, 0), (10, 10)), ((0, 'bad'), (10, 10)), ((0, 2), (10, '1/0')),
                               ((0, 2), (True, 10)), ((0, 2), (10, 10.0))]:
            with self.subTest(window=window, limits=limits), self.assertRaises(ValueError):
                self.assess(left, right, window, limits)
        self.assertEqual(self.reports.counts(), (0, 0))
        empty = self.assess(left, right, (1, 1)).snapshot
        self.assertEqual(interval_rows(empty), [])
        self.assertEqual((empty.total_seconds, empty.longest_seconds), (0, 0))
        at_flat_limit = self.assess(left, right, limits=(10, 11)).snapshot
        self.assertEqual(interval_rows(at_flat_limit), [])
        self.assertEqual((at_flat_limit.total_seconds, at_flat_limit.longest_seconds), (0, 0))

    def split_report(self):
        left, right = self.offline(('T17', 'A', pats('A', (0, 11), (1, 10), (2, 11))),
                                   ('T17', 'B', pats('B', (0, 11), (2, 11))))
        return self.assess(left, right)

    def test_strict_touch_splits_run(self):
        snapshot = self.split_report().snapshot
        self.assertEqual(interval_rows(snapshot), [(0, 1, True, False), (1, 2, False, True)])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (2, 1))
        self.assertEqual([(e.left_pats, e.right_pats) for e in snapshot.provenance.segments],
                         [(('A0', 'A1'), ('B0', 'B1')), (('A1', 'A2'), ('B0', 'B1'))])

    def test_hot_contact(self):
        left, right = self.offline(('T17', 'A', pats('A', (0, 12), (1, 11), (2, 12))),
                                   ('T17', 'B', pats('B', (0, 12), (1, 11), (2, 12))))
        snapshot = self.assess(left, right).snapshot
        self.assertEqual(interval_rows(snapshot), [(0, 2, True, True)])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (2, 2))

    def test_dispatch_longest(self):
        report = self.split_report()
        self.assertEqual(report.snapshot.total_seconds, 2)
        self.assertEqual(report.snapshot.longest_seconds, 1)
        basis = read_dispatch_basis(self.reports, report.report_id)
        self.assertEqual((basis.report_id, basis.longest_seconds), (report.report_id, 1))

    def test_missing_sample_is_not_interpolated(self):
        left, right = self.offline(
            ('T17', 'A', pats('A', (0, 11), (1, 11), (2, None), (3, 11), (4, 11))),
            ('T17', 'B', pats('B', (0, 11), (4, 11))),
        )
        snapshot = self.assess(left, right, (0, 4)).snapshot
        self.assertEqual(interval_rows(snapshot), [(0, 1, True, True), (3, 4, True, True)])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (2, 1))
        self.assertEqual([e.left_pats for e in snapshot.provenance.segments], [('A0', 'A1'), ('A3', 'A4')])

    def test_isolated_observation(self):
        left, right = self.offline(('T17', 'A', pats('A', (0, None), (1, 11), (2, None))),
                                   ('T17', 'B', pats('B', (0, 11), (1, 11), (2, 11))))
        pair = select_pair(self.receipts, left, right)
        candidates = qualify_common(pair, BoundaryInterval(0, 2), Fraction(10), Fraction(10))
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(candidate.interval, BoundaryInterval(1, 1))
            self.assertEqual((candidate.left_pats, candidate.right_pats), (('A1',), ('B1',)))
        snapshot = self.assess(left, right).snapshot
        self.assertEqual(interval_rows(snapshot), [])
        self.assertEqual((snapshot.total_seconds, snapshot.longest_seconds), (0, 0))

    def test_reject_cross_trial_pair(self):
        left, right = self.offline(('T17', 'A', pats('A', (0, 11), (2, 11))),
                                   ('T18', 'B', pats('B', (0, 11), (2, 11))))
        self.assertEqual(self.receipts.counts(), (2, 0))
        with self.assertRaisesRegex(ValueError, 'different trials'):
            self.assess(left, right)
        self.assertEqual(self.reports.counts(), (0, 0))

    def test_pair_guards(self):
        left, other_version = self.offline(('T17', 'A', pats('A', (0, 11), (2, 11))),
                                           ('T17', 'A', pats('C', (0, 12), (2, 12))))
        with self.assertRaisesRegex(ValueError, 'same head'):
            self.assess(left, other_version)
        with self.assertRaisesRegex(LookupError, 'unknown receipt'):
            self.assess(left, 999)
        self.assertEqual(self.reports.counts(), (0, 0))

    def test_invalid_evidence_cannot_be_signed(self):
        issued, reference = issue_heads(self.station, ('T17', 'A', pats('A', (0, 11), (2, 11))),
                                        ('T17', 'B', pats('B', (0, 11), (2, 11))))
        envelope = decode_envelope(issued.payload)
        body = {**envelope.body, 'points': [['1', '11', 'extra']]}
        invalid = intake(self.receipts, encode_envelope(V1Envelope(envelope.source, body)))
        self.assertTrue(invalid.accepted)
        right, = receive_items(self, self.receipts, reference)
        self.station.unlink()
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.assess(invalid.receipt_id, right)
        self.assertEqual(self.reports.counts(), (0, 0))
