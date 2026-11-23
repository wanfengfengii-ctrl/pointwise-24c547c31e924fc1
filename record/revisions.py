from record.capture import CaptureDraft
from record.observations import require_label
from record.repository import RevisionRepository
from record.sequence import validate_sequence


def validate_parent(
    repository: RevisionRepository, trial_id: str, head: str, parent_id: int | None
) -> None:
    if parent_id is None:
        return
    parent = repository.get(parent_id)
    if (parent.trial_id, parent.head) != (trial_id, head):
        raise ValueError('parent revision belongs to a different trial or head')


def freeze(
    repository: RevisionRepository, draft: CaptureDraft, head: str, parent_id: int | None = None
) -> int:
    trial_id = require_label(draft.trial_id, 'trial_id')
    observations = validate_sequence(draft.observations(head))
    validate_parent(repository, trial_id, head, parent_id)
    return repository.insert(trial_id, head, parent_id, observations)
