import sqlite3

from record.observations import require_number
from report.snapshots import (
    CommittedFinal, FinalSnapshot, FirstReceipt, decode_snapshot, encode_snapshot,
)


class ReportRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS final_reports (
                report_id INTEGER PRIMARY KEY,
                snapshot BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS first_receipts (
                receipt_id INTEGER PRIMARY KEY,
                report_id INTEGER NOT NULL UNIQUE REFERENCES final_reports(report_id)
            );
        ''')

    def commit_final(self, snapshot: FinalSnapshot) -> CommittedFinal:
        payload = encode_snapshot(snapshot)
        with self.connection:
            report_cursor = self.connection.execute(
                'INSERT INTO final_reports (snapshot) VALUES (?)', (payload,),
            )
            report_id = report_cursor.lastrowid
            receipt_cursor = self.connection.execute(
                'INSERT INTO first_receipts (report_id) VALUES (?)', (report_id,),
            )
            receipt_id = receipt_cursor.lastrowid
        return CommittedFinal(report_id, snapshot, FirstReceipt(receipt_id, report_id))

    def get(self, report_id: int) -> CommittedFinal:
        require_number(report_id, 'report_id')
        row = self.connection.execute('''
            SELECT final_reports.snapshot, first_receipts.receipt_id
            FROM final_reports JOIN first_receipts USING (report_id)
            WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            raise LookupError(f'unknown report {report_id}')
        snapshot, receipt_id = row
        return CommittedFinal(report_id, decode_snapshot(snapshot), FirstReceipt(receipt_id, report_id))

    def counts(self) -> tuple[int, int]:
        return self.connection.execute('''
            SELECT (SELECT COUNT(*) FROM final_reports),
                   (SELECT COUNT(*) FROM first_receipts)
        ''').fetchone()
