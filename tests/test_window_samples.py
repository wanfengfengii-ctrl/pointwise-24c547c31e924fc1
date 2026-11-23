import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from measure.requests import WindowAssessmentRequest, sign_windows
from prepare.issue_repository import IssueRepository
from prepare.issuance import issue_window
from receive.intake import intake
from receive.repository import ReceiptRepository
from record.capture import open_trial, record_observation
from record.repository import RevisionRepository
from record.revisions import freeze
from report.repository import ReportRepository


SAMPLE_PATH = Path(__file__).resolve().parent.parent / 'samples' / 'window_cases.json'


class WindowSampleTests(unittest.TestCase):
    def test_sample_cases(self):
        data = json.loads(SAMPLE_PATH.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision_ids = {}
            with closing(sqlite3.connect(root / 'station.sqlite')) as station:
                revisions = RevisionRepository(station)
                issues = IssueRepository(station)
                for alias, spec in data['revisions'].items():
                    draft = open_trial(data['trial'])
                    for pat, second, celsius in spec['rows']:
                        record_observation(draft, spec['head'], pat, second, celsius)
                    revision_ids[alias] = freeze(revisions, draft, spec['head'])
                issued = {}
                for case in data['cases']:
                    for side in ('left', 'right'):
                        for alias, window in case[side]:
                            key = (alias, tuple(window))
                            if key not in issued:
                                issued[key] = issue_window(
                                    revisions, issues, revision_ids[alias],
                                    (str(window[0]), str(window[1])))

            with closing(sqlite3.connect(root / 'received.sqlite')) as inbox, \
                    closing(sqlite3.connect(root / 'reports.sqlite')) as report_db:
                receipts = ReceiptRepository(inbox)
                reports = ReportRepository(report_db)
                receipt_ids = {}
                for key, item in issued.items():
                    result = intake(receipts, item.payload)
                    self.assertTrue(result.accepted, result.reason)
                    receipt_ids[key] = result.receipt_id

                for case in data['cases']:
                    with self.subTest(case=case['name']):
                        left_keys = [(alias, tuple(window)) for alias, window in case['left']]
                        right_keys = [(alias, tuple(window)) for alias, window in case['right']]
                        left = [receipt_ids[key] for key in left_keys]
                        right = [receipt_ids[key] for key in right_keys]
                        request = WindowAssessmentRequest(
                            left, right, tuple(case['window']), *case['thresholds'])
                        if 'error' in case['expected']:
                            with self.assertRaises(ValueError):
                                sign_windows(receipts, reports, request)
                            continue
                        committed = sign_windows(receipts, reports, request)
                        snapshot = committed.snapshot
                        segments = [[str(segment.start), str(segment.end),
                                     segment.left_closed, segment.right_closed]
                                    for segment in snapshot.segments]
                        self.assertEqual(segments, case['expected']['segments'])
                        self.assertEqual(str(snapshot.total_seconds),
                                         case['expected']['total_seconds'])
                        self.assertEqual(str(snapshot.longest_seconds),
                                         case['expected']['longest_seconds'])
                        uses = []
                        for segment in snapshot.provenance.segments:
                            uses.append({
                                'left': [[left.index(receipt_id), list(pats)]
                                         for receipt_id, pats in segment.left],
                                'right': [[right.index(receipt_id), list(pats)]
                                          for receipt_id, pats in segment.right],
                            })
                        self.assertEqual(uses, case['expected']['uses'])


if __name__ == '__main__':
    unittest.main()
