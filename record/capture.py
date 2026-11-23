from dataclasses import dataclass, field

from record.observations import Observation, make_observation, require_label


@dataclass
class CaptureDraft:
    trial_id: str
    heads: tuple[str, str]
    _by_head: dict[str, list[Observation]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        require_label(self.trial_id, 'trial_id')
        if not isinstance(self.heads, (tuple, list)) or len(self.heads) != 2:
            raise ValueError('a trial must name two heads')
        left, right = (require_label(head, 'head') for head in self.heads)
        if left == right:
            raise ValueError('a trial must name two different heads')
        self.heads = (left, right)
        self._by_head = {left: [], right: []}

    def _observations(self, head: str) -> list[Observation]:
        require_label(head, 'head')
        if head not in self._by_head:
            raise ValueError(f'head {head!r} does not belong to this draft')
        return self._by_head[head]

    def observations(self, head: str) -> tuple[Observation, ...]:
        return tuple(self._observations(head))


def open_trial(trial_id: str, heads: tuple[str, str] = ('A', 'B')) -> CaptureDraft:
    return CaptureDraft(trial_id, heads)


def record_observation(
    draft: CaptureDraft, head: str, pat_id: str, seconds: object, celsius: object
) -> Observation:
    observations = draft._observations(head)
    observation = make_observation(pat_id, seconds, celsius)
    observations.append(observation)
    return observation
