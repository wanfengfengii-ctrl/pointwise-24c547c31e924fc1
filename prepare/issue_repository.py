import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from record.observations import require_number


@dataclass(frozen=True)
class IssuedItem:
    issue_id: int
    revision_id: int
    payload: bytes


class IssueRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS issues (
                issue_id INTEGER PRIMARY KEY,
                revision_id INTEGER NOT NULL REFERENCES revisions(revision_id),
                payload BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS issue_replays (
                replay_id INTEGER PRIMARY KEY,
                issue_id INTEGER NOT NULL REFERENCES issues(issue_id)
            );
        ''')

    def store(self, revision_id: int, package: Callable[[int], bytes]) -> IssuedItem:
        with self.connection:
            cursor = self.connection.execute(
                'INSERT INTO issues (revision_id, payload) VALUES (?, ?)', (revision_id, b''),
            )
            issue_id = cursor.lastrowid
            payload = package(issue_id)
            self.connection.execute('UPDATE issues SET payload = ? WHERE issue_id = ?', (payload, issue_id))
        return IssuedItem(issue_id, revision_id, payload)

    def get(self, issue_id: int) -> IssuedItem:
        require_number(issue_id, 'issue_id')
        row = self.connection.execute(
            'SELECT revision_id, payload FROM issues WHERE issue_id = ?', (issue_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f'unknown issue {issue_id}')
        return IssuedItem(issue_id, *row)

    def record_replay(self, issue_id: int) -> None:
        with self.connection:
            self.connection.execute('INSERT INTO issue_replays (issue_id) VALUES (?)', (issue_id,))

    def replay_count(self, issue_id: int) -> int:
        self.get(issue_id)
        return self.connection.execute(
            'SELECT COUNT(*) FROM issue_replays WHERE issue_id = ?', (issue_id,),
        ).fetchone()[0]
