import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from measure.requests import (
    AssessmentRequest, WindowAssessmentRequest, sign, sign_windows,
)
from prepare.issue_repository import IssueRepository
from prepare.issuance import issue, issue_window, reissue
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


def _block_first_receipts(connection):
    connection.executescript('''
        CREATE TEMP TRIGGER block_first_receipt BEFORE INSERT ON first_receipts
        WHEN EXISTS (SELECT 1 FROM final_reports WHERE report_id = NEW.report_id)
        BEGIN
            SELECT RAISE(ABORT, 'first receipt blocked after staging final');
        END;
    ''')


def _freeze_rows(revisions, trial, head, rows, parent_id=None):
    draft = open_trial(trial)
    for pat, second, value in rows:
        record_observation(draft, head, pat, second, value)
    return freeze(revisions, draft, head, parent_id)


def demonstrate_windows(root):
    station_path = root / 'window_station.sqlite'
    report_path = root / 'window_reports.sqlite'
    # 站端：冻结同一试验 W23 的甲头（含缺测与峰值修订）和乙头修订。
    with closing(sqlite3.connect(station_path)) as station:
        revisions, issues = RevisionRepository(station), IssueRepository(station)
        rev_a = _freeze_rows(revisions, 'W23', 'A', [('a0', 0, 11), ('a3', 3, 11)])
        rev_aligned = _freeze_rows(
            revisions, 'W23', 'A',
            [('q0', 0, 11), ('q1', 1, 11), ('q3', 3, 11)])
        rev_missing = _freeze_rows(
            revisions, 'W23', 'A',
            [('m0', 0, 11), ('m1', 1, None), ('m3', 3, 11)])
        rev_b = _freeze_rows(revisions, 'W23', 'B', [('b0', 0, 11), ('b3', 3, 11)])
        # 分窗发行：甲头只交 [0,1] 与 [2,3]，乙头交全窗。
        issued = [
            issue_window(revisions, issues, rev_a, (0, 1)),
            issue_window(revisions, issues, rev_a, (2, 3)),
            issue_window(revisions, issues, rev_b, (0, 3)),
        ]
        # 按原号补发，逐字节相同并登记。
        replayed = reissue(issues, issued[0].issue_id)
        assert replayed == issued[0]
        assert issues.replay_count(issued[0].issue_id) == 1
        conflict_issue = issue_window(revisions, issues, rev_missing, (1, 1))

    # 站库关闭后，夜班只持有实收档与正式账。
    station_path.unlink()
    with closing(sqlite3.connect(root / 'window_received.sqlite')) as inbox:
        receipts = ReceiptRepository(inbox)
        numbers = []
        for item in issued:
            result = intake(receipts, item.payload)
            assert result.accepted, result.reason
            numbers.append(result.receipt_id)
        left_one, left_two, right_full = numbers
        conflict_receipt = intake(receipts, conflict_issue.payload).receipt_id

        with closing(sqlite3.connect(report_path)) as connection:
            reports = ReportRepository(connection)
            request = WindowAssessmentRequest(
                [left_one, left_two], [right_full], (0, 3), 10, 10)
            committed = sign_windows(receipts, reports, request)
            snapshot = committed.snapshot
            assert [segment_description(s) for s in snapshot.segments] == ['[0,1]', '[2,3]']
            assert (str(snapshot.total_seconds), str(snapshot.longest_seconds)) == ('2', '1')
            assert read_dispatch_basis(reports, committed.report_id).longest_seconds == 1

            # 窗内缺测对插值有值：单点冲突同样拒签，不产生定稿或首执。
            before = reports.counts()
            try:
                sign_windows(receipts, reports, WindowAssessmentRequest(
                    [left_one, conflict_receipt], [right_full], (0, 3), 10, 10))
            except ValueError as conflict:
                conflict_reason = str(conflict)
            else:  # pragma: no cover - 演示必须拒签
                raise AssertionError('point conflict must be rejected')
            assert reports.counts() == before

            # 首执写入失败：无半件，原定稿与首执保持原样。
            _block_first_receipts(connection)
            try:
                sign_windows(receipts, reports, request)
            except sqlite3.IntegrityError as write_error:
                failure_reason = str(write_error)
            else:  # pragma: no cover - 触发器必须拦截
                raise AssertionError('staged commit must fail')
            assert reports.counts() == before

    # 关闭并重开正式库：按原号复领定稿、首执与固定派工依据后重签。
    with closing(sqlite3.connect(report_path)) as connection:
        reports = ReportRepository(connection)
        assert retrieve(reports, committed.report_id) == committed
        assert read_dispatch_basis(reports, committed.report_id).longest_seconds == 1
        with closing(sqlite3.connect(root / 'window_received.sqlite')) as inbox_reopened:
            reopened_receipts = ReceiptRepository(inbox_reopened)
            retried = sign_windows(reopened_receipts, reports, request)
        assert retrieve(reports, committed.report_id) == committed
        assert retrieve(reports, retried.report_id) == retried

    provenance = []
    for segment in committed.snapshot.provenance.segments:
        provenance.append({
            'left': [[receipt_id, list(pats)] for receipt_id, pats in segment.left],
            'right': [[receipt_id, list(pats)] for receipt_id, pats in segment.right],
        })
    print('POINTWISE_WINDOW_SCENARIO ' + json.dumps({
        'case_id': 'window-gap',
        'trial': 'W23',
        'receipt_ids': {'left': [left_one, left_two], 'right': [right_full]},
        'segments': [segment_description(s) for s in snapshot.segments],
        'total_seconds': str(snapshot.total_seconds),
        'longest_seconds': str(snapshot.longest_seconds),
        'first_receipt_id': committed.first_receipt.receipt_id,
        'retry_report_id': retried.report_id,
        'reissue_count': 1,
        'provenance': provenance,
        'rejected_point_conflict': conflict_reason,
        'commit_failure_no_half_item': failure_reason,
        'code_paths': ['prepare/issuance.py', 'receive/intake.py', 'measure/windows.py',
                       'measure/window_dwell.py', 'measure/requests.py',
                       'report/snapshots.py', 'report/history.py', 'report/dispatch.py'],
        'oracle': '站库关闭后凭实收号核定：甲头两段声明与乙头全窗仅在 [0,1]、[2,3] 同时有效且严格超 10℃，签两段各 1 秒。',
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        demonstrate(Path(directory))
        demonstrate_windows(Path(directory))
