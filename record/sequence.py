from collections.abc import Iterable

from record.observations import Observation


def validate_sequence(observations: Iterable[Observation]) -> tuple[Observation, ...]:
    sequence = tuple(observations)
    seen = set()
    previous_time = None
    for observation in sequence:
        if not isinstance(observation, Observation):
            raise ValueError('expected an observation')
        if previous_time is not None and observation.seconds <= previous_time:
            raise ValueError('observation times must be strictly increasing')
        if observation.pat_id in seen:
            raise ValueError(f'duplicate pat ID {observation.pat_id!r}')
        seen.add(observation.pat_id)
        previous_time = observation.seconds
    return sequence
