from bisect import bisect_left
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise

from measure.dwell import DwellResult, measure_dwell
from measure.intervals import (
    BoundaryInterval, linear_threshold_crossing, linear_value_at, parse_parameters,
)
from measure.qualification import QualifiedSpan
from prepare.continuity import iter_isolated_observations, iter_valid_links
from record.repository import FrozenRevision, RevisionRepository


@dataclass(frozen=True)
class PreviewResult:
    left_revision: FrozenRevision
    right_revision: FrozenRevision
    window: BoundaryInterval
    thresholds: tuple[Fraction, Fraction]
    dwell: DwellResult


class _StationTrace:
    def __init__(self, revision: FrozenRevision) -> None:
        self.observations = revision.observations
        self.times = tuple(pat.seconds for pat in self.observations)

    def cuts(self, threshold: Fraction, window: BoundaryInterval) -> set[Fraction]:
        cuts = {pat.seconds for pat in iter_isolated_observations(self.observations)}
        for left, right in iter_valid_links(self.observations):
            cuts.update((left.seconds, right.seconds))
            if min(left.celsius, right.celsius) < threshold < max(left.celsius, right.celsius):
                cuts.add(linear_threshold_crossing(
                    left.seconds, right.seconds, left.celsius, right.celsius, threshold,
                ))
        return {time for time in cuts if window.contains(time)}

    def hot_pats(self, time: Fraction, threshold: Fraction) -> tuple[str, ...]:
        index = bisect_left(self.times, time)
        if index < len(self.times) and self.times[index] == time:
            observation = self.observations[index]
            if observation.celsius is not None and observation.celsius > threshold:
                return (observation.pat_id,)
            return ()
        if index == 0 or index == len(self.times):
            return ()
        left, right = self.observations[index - 1:index + 1]
        if left.celsius is None or right.celsius is None:
            return ()
        value = linear_value_at(left.seconds, right.seconds, left.celsius, right.celsius, time)
        return (left.pat_id, right.pat_id) if value > threshold else ()


def preview(
    repository: RevisionRepository, left_revision_id: int, right_revision_id: int,
    window: object, left_threshold: object, right_threshold: object,
) -> PreviewResult:
    closed, left_limit, right_limit = parse_parameters(window, left_threshold, right_threshold)
    left_revision = repository.get(left_revision_id)
    right_revision = repository.get(right_revision_id)
    if left_revision.trial_id != right_revision.trial_id or left_revision.head == right_revision.head:
        raise ValueError('preview requires different heads of the same trial')
    left = _StationTrace(left_revision)
    right = _StationTrace(right_revision)
    times = sorted({closed.start, closed.end} | left.cuts(left_limit, closed)
                   | right.cuts(right_limit, closed))
    # Qualification is constant in the interior between consecutive cuts.
    cells = [(BoundaryInterval(time, time), time) for time in times]
    cells.extend((BoundaryInterval(a, b, False, False), (a + b) / 2) for a, b in pairwise(times))
    spans = []
    for interval, probe in cells:
        left_pats = left.hot_pats(probe, left_limit)
        right_pats = right.hot_pats(probe, right_limit)
        if left_pats and right_pats:
            spans.append(QualifiedSpan(interval, left_pats, right_pats))
    return PreviewResult(
        left_revision, right_revision, closed, (left_limit, right_limit), measure_dwell(spans),
    )
