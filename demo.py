import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from measure.requests import AssessmentRequest, sign
from prepare.issue_repository import IssueRepository
from prepare.issuance import issue, reissue
from prepare.online_preview import preview
from receive.intake import intake
from receive.repository import ReceiptRepository
from record.capture import open_trial, record_observation
from record.repository import RevisionRepository
from record.revisions import freeze
from report.dispatch import read_dispatch_basis
from report.history import retrieve
from report.repository import ReportRepository


def capture(revisions, a_middle, a_last, parent_id=None):
    draft = open_trial('T17')
    for second, value in enumerate((11, a_middle, 11, None, a_last)):
        record_observation(draft, 'A', f'A{second}', second, value)
    return freeze(revisions, draft, 'A', parent_id)


def receive_item(repository, item):
    result = intake(repository, item.payload)
    if not result.accepted:
        raise RuntimeError(result.reason)
    return result.receipt_id


def segment_description(interval):
    left = '[' if interval.left_closed else '('
    right = ']' if interval.right_closed else ')'
    return f'{left}{interval.start},{interval.end}{right}'


def demonstrate(root):
    station_path = root / 'station.sqlite'
    report_path = root / 'reports.sqlite'
    with closing(sqlite3.connect(station_path)) as station:
        revisions, issues = RevisionRepository(station), IssueRepository(station)
        initial_a = capture(revisions, 11, 12)
        selected_a = capture(revisions, 11, 11, initial_a)
        draft = open_trial('T17')
        for second in (0, 2, 4):
            record_observation(draft, 'B', f'B{second}', second, 11)
        selected_b = freeze(revisions, draft, 'B')
        check = preview(revisions, selected_a, selected_b, (0, 2), 10, 10)
        assert check.dwell.total_seconds == check.dwell.longest_seconds == 2
        issued_a = issue(revisions, issues, selected_a)
        issued_b = issue(revisions, issues, selected_b)

    with closing(sqlite3.connect(root / 'received.sqlite')) as inbox:
        receipts = ReceiptRepository(inbox)
        left = receive_item(receipts, issued_a)
        right = receive_item(receipts, issued_b)
        with closing(sqlite3.connect(report_path)) as connection:
            reports = ReportRepository(connection)
            original = sign(receipts, reports, AssessmentRequest(left, right, (0, 2), 10, 10))
            assert [segment_description(s) for s in original.snapshot.segments] == ['[0,2]']
            assert read_dispatch_basis(reports, original.report_id).longest_seconds == 2

            with closing(sqlite3.connect(station_path)) as station:
                revisions, issues = RevisionRepository(station), IssueRepository(station)
                corrected_a = capture(revisions, 10, 11, selected_a)
                correction = issue(revisions, issues, corrected_a)
                assert reissue(issues, issued_a.issue_id) == issued_a
            corrected_left = receive_item(receipts, correction)
            assert retrieve(reports, original.report_id) == original
            corrected = sign(receipts, reports, AssessmentRequest(corrected_left, right, (0, 2), 10, 10))

    with closing(sqlite3.connect(report_path)) as connection:
        reports = ReportRepository(connection)
        assert retrieve(reports, original.report_id) == original
        retrieved = retrieve(reports, corrected.report_id)
        assert retrieved == corrected
        snapshot = retrieved.snapshot
        evidence = [{"left": list(span.left_pats), "right": list(span.right_pats)}
                    for span in snapshot.provenance.segments]
        actual = {'segments': [segment_description(s) for s in snapshot.segments],
                  'total_seconds': str(snapshot.total_seconds),
                  'longest_seconds': str(snapshot.longest_seconds), 'pats': evidence}
        expected = {'segments': ['[0,1)', '(1,2]'], 'total_seconds': '2', 'longest_seconds': '1',
                    'pats': [{'left': ['A0', 'A1'], 'right': ['B0', 'B2']},
                             {'left': ['A1', 'A2'], 'right': ['B0', 'B2']}]}
        downstream_actual = {
            'old_dispatch_seconds': str(read_dispatch_basis(reports, original.report_id).longest_seconds),
            'new_dispatch_seconds': str(read_dispatch_basis(reports, corrected.report_id).longest_seconds),
        }
        downstream_expected = {'old_dispatch_seconds': '2', 'new_dispatch_seconds': '1'}
        assert actual == expected
        assert downstream_actual == downstream_expected
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v4',
            'input': {'trial': 'T17', 'left': [['A0', 0, 11], ['A1', 1, 10], ['A2', 2, 11],
                                             ['A3', 3, None], ['A4', 4, 11]],
                      'right': [['B0', 0, 11], ['B2', 2, 11], ['B4', 4, 11]],
                      'window': [0, 2], 'thresholds': [10, 10], 'receipt_ids': [corrected_left, right]},
            'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
            'code_paths': ['prepare/issuance.py', 'receive/intake.py', 'measure/qualification.py',
                           'measure/dwell.py', 'report/history.py', 'report/dispatch.py'],
            'oracle': '乙头在窗内恒为 11。甲头两条线段只在第 1 秒等于门槛，排除该点。两段各长 1 秒。',
        }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        demonstrate(Path(directory))
