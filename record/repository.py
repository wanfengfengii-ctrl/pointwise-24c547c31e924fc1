import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from record.observations import Observation, require_number


@dataclass(frozen=True)
class FrozenRevision:
    revision_id: int
    trial_id: str
    head: str
    parent_id: int | None
    observations: tuple[Observation, ...]


class RevisionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS revisions (
                revision_id INTEGER PRIMARY KEY,
                trial_id TEXT NOT NULL,
                head TEXT NOT NULL,
                parent_id INTEGER REFERENCES revisions(revision_id)
            );
            CREATE TABLE IF NOT EXISTS observations (
                revision_id INTEGER NOT NULL REFERENCES revisions(revision_id),
                position INTEGER NOT NULL,
                pat_id TEXT NOT NULL,
                seconds TEXT NOT NULL,
                celsius TEXT,
                PRIMARY KEY (revision_id, position),
                UNIQUE (revision_id, pat_id),
                UNIQUE (revision_id, seconds)
            );
        ''')

    def insert(
        self, trial_id: str, head: str, parent_id: int | None,
        observations: tuple[Observation, ...],
    ) -> int:
        with self.connection:
            cursor = self.connection.execute(
                'INSERT INTO revisions (trial_id, head, parent_id) VALUES (?, ?, ?)',
                (trial_id, head, parent_id),
            )
            revision_id = cursor.lastrowid
            self.connection.executemany(
                '''INSERT INTO observations
                   (revision_id, position, pat_id, seconds, celsius)
                   VALUES (?, ?, ?, ?, ?)''',
                (
                    (revision_id, position, pat.pat_id, str(pat.seconds),
                     None if pat.celsius is None else str(pat.celsius))
                    for position, pat in enumerate(observations)
                ),
            )
        return revision_id

    def get(self, revision_id: int) -> FrozenRevision:
        require_number(revision_id, 'revision_id')
        row = self.connection.execute(
            'SELECT trial_id, head, parent_id FROM revisions WHERE revision_id = ?',
            (revision_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f'unknown revision {revision_id}')
        records = self.connection.execute(
            '''SELECT pat_id, seconds, celsius FROM observations
               WHERE revision_id = ? ORDER BY position''',
            (revision_id,),
        )
        observations = tuple(
            Observation(pat_id, Fraction(seconds), None if celsius is None else Fraction(celsius))
            for pat_id, seconds, celsius in records
        )
        return FrozenRevision(revision_id, *row, observations)
