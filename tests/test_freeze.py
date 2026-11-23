import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from prepare.issue_repository import IssueRepository
from prepare.issuance import issue, reissue
from prepare.online_preview import preview
from receive.envelope import decode_envelope
from receive.source import validate_source
from record.capture import open_trial, record_observation
from record.repository import RevisionRepository
from record.revisions import freeze


def frozen_head(revisions, trial, head, rows, parent_id=None):
    draft = open_trial(trial)
    for pat, time, temperature in rows:
        record_observation(draft, head, pat, time, temperature)
    return freeze(revisions, draft, head, parent_id)


def pats(head, *samples):
    return [(f'{head}{index}', time, temperature) for index, (time, temperature) in enumerate(samples)]


class FreezeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'station.sqlite'
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        self.revisions = RevisionRepository(self.connection)
        self.issues = IssueRepository(self.connection)

    def test_issue_replays_original_bytes(self):
        parent = frozen_head(self.revisions, 'T17', 'A', [('old', 0, 11), ('A1', 1, 11)])
        original = issue(self.revisions, self.issues, parent)
        child = frozen_head(self.revisions, 'T17', 'A', [('new', 0, 12), ('A1', 1, 10)], parent)
        corrected = issue(self.revisions, self.issues, child)
        replayed = reissue(self.issues, original.issue_id)
        self.assertEqual(replayed, original)
        self.assertNotEqual(original.payload, corrected.payload)
        self.assertEqual(self.issues.replay_count(original.issue_id), 1)
        old_envelope = decode_envelope(replayed.payload)
        new_envelope = decode_envelope(corrected.payload)
        self.assertEqual(validate_source(old_envelope).revision_id, parent)
        self.assertEqual(validate_source(new_envelope).revision_id, child)
        self.assertEqual(old_envelope.body['links'][0][-2:], ['old', 'A1'])
        self.assertEqual(new_envelope.body['links'][0][-2:], ['new', 'A1'])
        self.assertEqual(self.revisions.get(child).parent_id, parent)
        with self.assertRaises(LookupError):
            reissue(self.issues, 999)
        with self.assertRaises(LookupError):
            issue(self.revisions, self.issues, 999)

    def test_preview_window(self):
        original = frozen_head(self.revisions, 'T17', 'A', pats('A', (0, 9), (2, 11)))
        selected = frozen_head(self.revisions, 'T17', 'A', pats('C', (0, 11), (2, 11)), original)
        right = frozen_head(self.revisions, 'T17', 'B', pats('B', (0, 11), (2, 11)))
        result = preview(self.revisions, selected, right, (0, 2), 10, 10)
        self.assertEqual((result.left_revision.revision_id, result.right_revision.revision_id), (selected, right))
        self.assertEqual(result.left_revision.observations[0].pat_id, 'C0')
        segment, = result.dwell.segments
        interval = segment.interval
        self.assertEqual((interval.start, interval.end, interval.left_closed, interval.right_closed),
                         (0, 2, True, True))
        self.assertEqual((segment.left_pats, segment.right_pats), (('C0', 'C1'), ('B0', 'B1')))
        self.assertEqual((result.dwell.total_seconds, result.dwell.longest_seconds), (2, 2))
        with self.assertRaises(LookupError):
            preview(self.revisions, 999, right, (0, 2), 10, 10)
        with self.assertRaises(ValueError):
            preview(self.revisions, selected, right, (2, 0), 10, 10)

    def test_reopen_initialization(self):
        parent = frozen_head(self.revisions, 'T17', 'A', pats('A', (0, 11)))
        child = frozen_head(self.revisions, 'T17', 'A', pats('C', (0, 10)), parent)
        original, corrected = (issue(self.revisions, self.issues, number) for number in (parent, child))
        self.assertEqual(reissue(self.issues, original.issue_id), original)
        for _ in range(2):
            with closing(sqlite3.connect(self.path)) as connection:
                revisions = RevisionRepository(connection)
                issues = IssueRepository(connection)
                self.assertEqual(revisions.get(parent).observations[0].celsius, 11)
                self.assertEqual(revisions.get(child).parent_id, parent)
                self.assertEqual(revisions.get(child).observations[0].celsius, 10)
                self.assertEqual(issues.get(original.issue_id), original)
                self.assertEqual(issues.get(corrected.issue_id), corrected)
                self.assertEqual(issues.replay_count(original.issue_id), 1)

    def test_capture_identity(self):
        draft = open_trial('T17')
        record_observation(draft, 'A', 'zero', '1/3', 0)
        record_observation(draft, 'A', 'gap', 1, None)
        record_observation(draft, 'B', 'B0', 0, 12)
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            record_observation(draft, 'C', 'wrong', 2, 12)
        with self.assertRaisesRegex(ValueError, 'exact rational'):
            record_observation(draft, 'A', 'invalid', 2, 0.5)
        revision = self.revisions.get(freeze(self.revisions, draft, 'A'))
        self.assertEqual((revision.trial_id, revision.head), ('T17', 'A'))
        self.assertEqual([(pat.pat_id, pat.seconds, pat.celsius) for pat in revision.observations],
                         [('zero', Fraction(1, 3), 0), ('gap', 1, None)])
        self.assertEqual([pat.pat_id for pat in draft.observations('B')], ['B0'])

    def test_reject_duplicate_time(self):
        parent = frozen_head(self.revisions, 'T17', 'A', pats('A', (0, 11), (1, 12)))
        before = self.revisions.get(parent)
        with self.assertRaisesRegex(ValueError, 'strictly increasing'):
            frozen_head(self.revisions, 'T17', 'A', pats('A', (1, 9), (1, 12)), parent)
        self.assertEqual(self.revisions.get(parent), before)
        with self.assertRaises(LookupError):
            self.revisions.get(parent + 1)

    def test_rational_duplicate(self):
        parent = frozen_head(self.revisions, 'T17', 'A', pats('A', (1, 11)))
        before = self.revisions.get(parent)
        with self.assertRaisesRegex(ValueError, 'strictly increasing'):
            frozen_head(self.revisions, 'T17', 'A', pats('A', (1, 9), ('2/2', None)), parent)
        self.assertEqual(self.revisions.get(parent), before)
        with self.assertRaises(LookupError):
            self.revisions.get(parent + 1)

    def test_freeze_guard(self):
        parent = frozen_head(self.revisions, 'T17', 'A', pats('A', (0, 11)))
        for rows in [[('A0', 1, 10), ('A1', 0, 11)], [('A0', 0, 10), ('A0', 1, 11)]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                frozen_head(self.revisions, 'T17', 'A', rows, parent)
        for trial, head in [('T17', 'B'), ('T18', 'A')]:
            with self.subTest(trial=trial, head=head), self.assertRaisesRegex(ValueError, 'parent'):
                frozen_head(self.revisions, trial, head, pats(head, (0, 11)), parent)
        with self.assertRaises(LookupError):
            frozen_head(self.revisions, 'T17', 'A', pats('A', (0, 11)), 999)
        with self.assertRaises(LookupError):
            self.revisions.get(parent + 1)
