from collections.abc import Iterator, Sequence
from itertools import pairwise

from record.observations import Observation


def iter_valid_links(
    observations: Sequence[Observation],
) -> Iterator[tuple[Observation, Observation]]:
    for left, right in pairwise(observations):
        if left.celsius is not None and right.celsius is not None:
            yield left, right


def iter_isolated_observations(observations: Sequence[Observation]) -> Iterator[Observation]:
    for index, observation in enumerate(observations):
        if observation.celsius is None:
            continue
        has_left = index > 0 and observations[index - 1].celsius is not None
        has_right = index + 1 < len(observations) and observations[index + 1].celsius is not None
        if not has_left and not has_right:
            yield observation
