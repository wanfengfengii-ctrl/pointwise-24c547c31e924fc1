from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

from measure.intervals import BoundaryInterval
from measure.qualification import QualifiedSpan


@dataclass(frozen=True)
class DwellSegment:
    interval: BoundaryInterval
    left_pats: tuple[str, ...]
    right_pats: tuple[str, ...]


@dataclass(frozen=True)
class DwellResult:
    segments: tuple[DwellSegment, ...]
    total_seconds: Fraction
    longest_seconds: Fraction


def _connected(first: BoundaryInterval, second: BoundaryInterval) -> bool:
    return second.start < first.end or (
        second.start == first.end and (first.right_closed or second.left_closed)
    )


def _union(first: BoundaryInterval, second: BoundaryInterval) -> BoundaryInterval:
    left_closed = first.left_closed or (first.start == second.start and second.left_closed)
    if second.end > first.end:
        return BoundaryInterval(first.start, second.end, left_closed, second.right_closed)
    right_closed = first.right_closed or (first.end == second.end and second.right_closed)
    return BoundaryInterval(first.start, first.end, left_closed, right_closed)


def measure_dwell(spans: Iterable[QualifiedSpan]) -> DwellResult:
    ordered = sorted(spans, key=lambda span: (
        span.interval.start, not span.interval.left_closed, span.interval.end,
    ))
    segments = []
    current = None
    left_pats: set[str] = set()
    right_pats: set[str] = set()

    def save_positive() -> None:
        if current is not None and current.length:
            segments.append(DwellSegment(current, tuple(sorted(left_pats)), tuple(sorted(right_pats))))

    # Keep singleton spans through union. A shared point can connect two open sides.
    for span in ordered:
        if current is not None and _connected(current, span.interval):
            current = _union(current, span.interval)
        else:
            save_positive()
            current = span.interval
            left_pats.clear()
            right_pats.clear()
        left_pats.update(span.left_pats)
        right_pats.update(span.right_pats)
    save_positive()
    lengths = [segment.interval.length for segment in segments]
    return DwellResult(tuple(segments), sum(lengths, Fraction(0)), max(lengths, default=Fraction(0)))
