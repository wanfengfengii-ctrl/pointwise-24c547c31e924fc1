from dataclasses import dataclass

from receive.envelope import V1Envelope
from record.observations import require_label, require_number


@dataclass(frozen=True)
class SourceIdentity:
    trial_id: str
    head: str
    revision_id: int
    issue_id: int


def _parse_identity(value: object) -> SourceIdentity:
    if not isinstance(value, dict) or set(value) != {'trial_id', 'head', 'revision_id', 'issue_id'}:
        raise ValueError('invalid source identity fields')
    return SourceIdentity(
        require_label(value['trial_id'], 'trial_id'),
        require_label(value['head'], 'head'),
        require_number(value['revision_id'], 'revision_id'),
        require_number(value['issue_id'], 'issue_id'),
    )


def validate_source(envelope: V1Envelope) -> SourceIdentity:
    source = _parse_identity(envelope.source)
    if source != _parse_identity(envelope.body.get('origin')):
        raise ValueError('envelope source and evidence origin differ')
    return source
