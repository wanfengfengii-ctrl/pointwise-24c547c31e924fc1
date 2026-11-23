import sqlite3
from dataclasses import dataclass

from receive.source import SourceIdentity
from record.observations import require_number


@dataclass(frozen=True)
class ReceivedItem:
    receipt_id: int
    source: SourceIdentity
    payload: bytes


class ReceiptRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS received_items (
                receipt_id INTEGER PRIMARY KEY,
                payload BLOB NOT NULL,
                trial_id TEXT NOT NULL,
                head TEXT NOT NULL,
                revision_id INTEGER NOT NULL,
                issue_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS intake_rejections (
                rejection_id INTEGER PRIMARY KEY,
                reason TEXT NOT NULL,
                payload BLOB
            );
        ''')

    def register(self, payload: bytes, source: SourceIdentity) -> ReceivedItem:
        with self.connection:
            cursor = self.connection.execute(
                '''INSERT INTO received_items (payload, trial_id, head, revision_id, issue_id)
                   VALUES (?, ?, ?, ?, ?)''',
                (payload, source.trial_id, source.head, source.revision_id, source.issue_id),
            )
        return ReceivedItem(cursor.lastrowid, source, payload)

    def get(self, receipt_id: int) -> ReceivedItem:
        require_number(receipt_id, 'receipt_id')
        row = self.connection.execute(
            '''SELECT trial_id, head, revision_id, issue_id, payload
               FROM received_items WHERE receipt_id = ?''', (receipt_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f'unknown receipt {receipt_id}')
        trial_id, head, revision_id, issue_id, payload = row
        return ReceivedItem(receipt_id, SourceIdentity(trial_id, head, revision_id, issue_id), payload)

    def record_rejection(self, reason: str, payload: bytes | None) -> None:
        with self.connection:
            self.connection.execute(
                'INSERT INTO intake_rejections (reason, payload) VALUES (?, ?)', (reason, payload),
            )

    def counts(self) -> tuple[int, int]:
        return self.connection.execute('''
            SELECT (SELECT COUNT(*) FROM received_items),
                   (SELECT COUNT(*) FROM intake_rejections)
        ''').fetchone()
