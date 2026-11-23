from dataclasses import dataclass
from fractions import Fraction

from record.observations import as_rational


@dataclass(frozen=True)
class BoundaryInterval:
    start: Fraction
    end: Fraction
    left_closed: bool = True
    right_closed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, 'start', as_rational(self.start, 'interval start'))
        object.__setattr__(self, 'end', as_rational(self.end, 'interval end'))
        if self.start > self.end:
            raise ValueError('interval start exceeds end')
        if self.start == self.end and not (self.left_closed and self.right_closed):
            raise ValueError('a singleton must include its point')

    @property
    def length(self) -> Fraction:
        return self.end - self.start

    def contains(self, point: Fraction) -> bool:
        after_start = point > self.start or (point == self.start and self.left_closed)
        before_end = point < self.end or (point == self.end and self.right_closed)
        return after_start and before_end


def intersect_intervals(
    first: BoundaryInterval, second: BoundaryInterval,
) -> BoundaryInterval | None:
    start = max(first.start, second.start)
    end = min(first.end, second.end)
    if start > end:
        return None
    left_closed = first.contains(start) and second.contains(start)
    right_closed = first.contains(end) and second.contains(end)
    if start == end and not left_closed:
        return None
    return BoundaryInterval(start, end, left_closed, right_closed)


def clip_interval(interval: BoundaryInterval, window: BoundaryInterval) -> BoundaryInterval | None:
    return intersect_intervals(interval, window)


def linear_value_at(
    start: Fraction, end: Fraction, first: Fraction, last: Fraction, time: Fraction,
) -> Fraction:
    return first + (last - first) * (time - start) / (end - start)


def linear_threshold_crossing(
    start: Fraction, end: Fraction, first: Fraction, last: Fraction, threshold: Fraction,
) -> Fraction:
    if min(first, last) <= threshold <= max(first, last) and first != last:
        return start + (end - start) * (threshold - first) / (last - first)
    raise ValueError('threshold does not have a unique crossing on this link')


def parse_parameters(
    window: object, left_threshold: object, right_threshold: object,
) -> tuple[BoundaryInterval, Fraction, Fraction]:
    if not isinstance(window, (tuple, list)) or len(window) != 2:
        raise ValueError('window must supply a start and an end')
    closed = BoundaryInterval(window[0], window[1])
    return closed, as_rational(left_threshold, 'left threshold'), as_rational(right_threshold, 'right threshold')
