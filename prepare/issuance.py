from dataclasses import asdict

from measure.intervals import BoundaryInterval
from prepare.continuity import iter_isolated_observations, iter_valid_links
from prepare.issue_repository import IssuedItem, IssueRepository
from receive.envelope import V1Envelope, encode_envelope
from receive.source import SourceIdentity
from record.observations import as_rational
from record.repository import FrozenRevision, RevisionRepository


def _derive_evidence(revision: FrozenRevision) -> dict:
    links = []
    for left, right in iter_valid_links(revision.observations):
        slope = (right.celsius - left.celsius) / (right.seconds - left.seconds)
        links.append([
            str(left.seconds), str(right.seconds), str(left.celsius), str(slope),
            left.pat_id, right.pat_id,
        ])
    points = [
        [str(pat.seconds), str(pat.celsius), pat.pat_id]
        for pat in iter_isolated_observations(revision.observations)
    ]
    return {'links': links, 'points': points}


def issue(revisions: RevisionRepository, archive: IssueRepository, revision_id: int) -> IssuedItem:
    revision = revisions.get(revision_id)
    evidence = _derive_evidence(revision)

    def package(issue_id: int) -> bytes:
        identity = SourceIdentity(revision.trial_id, revision.head, revision.revision_id, issue_id)
        source = asdict(identity)
        return encode_envelope(V1Envelope(source, {'origin': source, **evidence}))

    return archive.store(revision.revision_id, package)


def issue_window(
    revisions: RevisionRepository, archive: IssueRepository, revision_id: int, window: object,
) -> IssuedItem:
    revision = revisions.get(revision_id)
    if not isinstance(window, (tuple, list)) or len(window) != 2:
        raise ValueError('window must supply a start and an end')
    declared = BoundaryInterval(as_rational(window[0], 'window start'),
                                as_rational(window[1], 'window end'))
    evidence = _derive_evidence(revision)

    def package(issue_id: int) -> bytes:
        identity = SourceIdentity(revision.trial_id, revision.head, revision.revision_id, issue_id)
        source = asdict(identity)
        body = {
            'origin': source,
            'window': [str(declared.start), str(declared.end)],
            **evidence,
        }
        return encode_envelope(V1Envelope(source, body))

    return archive.store(revision.revision_id, package)


def reissue(archive: IssueRepository, issue_id: int) -> IssuedItem:
    archived = archive.get(issue_id)
    archive.record_replay(issue_id)
    return archived
