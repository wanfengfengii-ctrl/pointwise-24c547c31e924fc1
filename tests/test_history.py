import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from measure.requests import AssessmentRequest, sign
from prepare.issue_repository import IssueRepository
from prepare.issuance import issue
from receive.repository import ReceiptRepository
from record.repository import RevisionRepository
from report.dispatch import read_dispatch_basis
from report.history import retrieve
from report.repository import ReportRepository
from tests.test_dwell import interval_rows, receive_items
from tests.test_freeze import frozen_head, pats


def block_first_receipts(connection):
    connection.executescript('''
        CREATE TEMP TRIGGER block_first_receipt BEFORE INSERT ON first_receipts
        WHEN EXISTS (SELECT 1 FROM final_reports WHERE report_id = NEW.report_id)
        BEGIN
            SELECT RAISE(ABORT, 'first receipt blocked after staging final');
        END;
    ''')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.station_path = self.root / 'station.sqlite'
        self.receipt_path = self.root / 'received.sqlite'
        self.report_path = self.root / 'reports.sqlite'
        self.receipts = ReceiptRepository(self.connect(self.receipt_path))
        self.report_connection = self.connect(self.report_path)
        self.reports = ReportRepository(self.report_connection)

    def connect(self, path):
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        return connection

    def reopen_reports(self):
        self.report_connection.close()
        self.report_connection = self.connect(self.report_path)
        self.reports = ReportRepository(self.report_connection)

    def first_pair(self, left_rows=None):
        if left_rows is None:
            left_rows = pats('A', (0, 11), (1, 11), (2, 11))
        with closing(sqlite3.connect(self.station_path)) as station:
            revisions, archive = RevisionRepository(station), IssueRepository(station)
            parent = frozen_head(revisions, 'T17', 'A', left_rows)
            right_revision = frozen_head(revisions, 'T17', 'B', pats('B', (0, 11), (2, 11)))
            items = [issue(revisions, archive, version) for version in (parent, right_revision)]
        left, right = receive_items(self, self.receipts, *items)
        return parent, left, right

    def correction(self, parent):
        with closing(sqlite3.connect(self.station_path)) as station:
            revisions, archive = RevisionRepository(station), IssueRepository(station)
            child = frozen_head(revisions, 'T17', 'A', pats('C', (0, 11), (1, 10), (2, 11)), parent)
            item = issue(revisions, archive, child)
        corrected, = receive_items(self, self.receipts, item)
        return corrected

    def assess(self, left, right, window=(0, 2), thresholds=(10, 10)):
        return sign(self.receipts, self.reports, AssessmentRequest(left, right, window, *thresholds))

    def test_old_report_after_correction_and_reopen(self):
        parent, left, right = self.first_pair()
        original = self.assess(left, right)
        old_basis = read_dispatch_basis(self.reports, original.report_id)
        corrected = self.correction(parent)
        new_report = self.assess(corrected, right)
        self.assertEqual(interval_rows(original.snapshot), [(0, 2, True, True)])
        self.assertEqual(interval_rows(new_report.snapshot), [(0, 1, True, False), (1, 2, False, True)])
        self.assertEqual((original.snapshot.total_seconds, new_report.snapshot.total_seconds), (2, 2))
        self.assertEqual((old_basis.longest_seconds, new_report.snapshot.dispatch_seconds), (2, 1))
        self.assertNotEqual(new_report.first_receipt, original.first_receipt)
        self.reopen_reports()
        self.assertEqual(retrieve(self.reports, original.report_id), original)
        self.assertEqual(retrieve(self.reports, new_report.report_id), new_report)
        self.assertEqual(read_dispatch_basis(self.reports, original.report_id), old_basis)
        self.assertEqual(self.reports.counts(), (2, 2))

    def test_corrected_issue(self):
        parent, left, right = self.first_pair()
        original = self.assess(left, right)
        corrected = self.correction(parent)
        latest = self.assess(corrected, right)
        self.assertEqual(original.snapshot.provenance.left.source.revision_id, parent)
        self.assertNotEqual(latest.snapshot.provenance.left.source.revision_id, parent)
        self.assertEqual(latest.snapshot.provenance.left.source, self.receipts.get(corrected).source)
        self.assertEqual(original.snapshot.provenance.right, latest.snapshot.provenance.right)
        self.assertEqual(original.snapshot.provenance.segments[0].left_pats, ('A0', 'A1', 'A2'))
        self.assertEqual([segment.left_pats for segment in latest.snapshot.provenance.segments],
                         [('C0', 'C1'), ('C1', 'C2')])
        self.assertEqual(read_dispatch_basis(self.reports, original.report_id).longest_seconds, 2)
        self.assertEqual(read_dispatch_basis(self.reports, latest.report_id).longest_seconds, 1)

    def test_selected_receipts(self):
        parent, left, right = self.first_pair()
        corrected = self.correction(parent)
        old_result = self.assess(left, right).snapshot
        corrected_result = self.assess(corrected, right).snapshot
        self.assertEqual((old_result.provenance.left.receipt_id, corrected_result.provenance.left.receipt_id),
                         (left, corrected))
        self.assertEqual((old_result.longest_seconds, corrected_result.longest_seconds), (2, 1))

    def test_exact_reopen(self):
        parent, left, right = self.first_pair(pats('A', (0, 9), (2, 11)))
        original = self.assess(left, right, ('1/3', '5/6'), ('19/2', '21/2'))
        self.assess(self.correction(parent), right)
        self.reopen_reports()
        archived = retrieve(self.reports, original.report_id)
        self.assertEqual(archived, original)
        self.assertEqual(interval_rows(archived.snapshot), [(Fraction(1, 2), Fraction(5, 6), False, True)])
        self.assertEqual(archived.snapshot.thresholds, (Fraction(19, 2), Fraction(21, 2)))
        self.assertEqual((archived.snapshot.total_seconds, archived.snapshot.longest_seconds,
                          archived.snapshot.dispatch_seconds), (Fraction(1, 3),) * 3)
        evidence, = archived.snapshot.provenance.segments
        self.assertEqual((evidence.left_pats, evidence.right_pats), (('A0', 'A1'), ('B0', 'B1')))
        self.assertEqual(archived.first_receipt, original.first_receipt)

    def test_reopen_initialization(self):
        _, left, right = self.first_pair()
        original = self.assess(left, right)
        received = self.receipts.get(left)
        for _ in range(2):
            self.reopen_reports()
            self.receipts.connection.close()
            self.receipts = ReceiptRepository(self.connect(self.receipt_path))
            self.assertEqual(self.receipts.get(left), received)
            self.assertEqual(self.receipts.counts(), (2, 0))
            self.assertEqual(retrieve(self.reports, original.report_id), original)
            self.assertEqual(self.reports.counts(), (1, 1))
        later = self.assess(left, right)
        self.assertGreater(later.report_id, original.report_id)
        self.assertGreater(later.first_receipt.receipt_id, original.first_receipt.receipt_id)

    def test_unknown_report(self):
        with self.assertRaisesRegex(LookupError, 'unknown report'):
            retrieve(self.reports, 999)
        with self.assertRaisesRegex(LookupError, 'unknown report'):
            read_dispatch_basis(self.reports, 999)

    def test_commit_failure_keeps_old_report(self):
        _, left, right = self.first_pair()
        original = self.assess(left, right)
        block_first_receipts(self.report_connection)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'after staging final'):
            self.assess(left, right)
        self.assertEqual(self.reports.counts(), (1, 1))
        self.assertEqual(retrieve(self.reports, original.report_id), original)

    def test_atomic_retry(self):
        parent, left, right = self.first_pair()
        original = self.assess(left, right)
        corrected = self.correction(parent)
        block_first_receipts(self.report_connection)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'after staging final'):
            self.assess(corrected, right)
        self.reopen_reports()
        self.assertEqual(self.reports.counts(), (1, 1))
        self.assertEqual(retrieve(self.reports, original.report_id), original)
        retried = self.assess(corrected, right)
        self.assertEqual(self.reports.counts(), (2, 2))
        self.assertEqual(retried.snapshot.provenance.left.receipt_id, corrected)
        self.assertEqual(read_dispatch_basis(self.reports, retried.report_id).longest_seconds, 1)
        self.assertEqual(retrieve(self.reports, retried.report_id), retried)
