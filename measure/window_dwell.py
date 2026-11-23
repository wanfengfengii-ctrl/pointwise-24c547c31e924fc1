"""Merge windowed qualified spans into positive-length continuous segments."""
from dataclasses import dataclass
from fractions import Fraction

from measure.dwell import _connected, _union
from measure.intervals import BoundaryInterval
from measure.windows import WindowQualifiedSpan


@dataclass(frozen=True)
class WindowDwellSegment:
    interval: BoundaryInterval
    # receipt_id -> pats actually used inside this segment
    left: dict[int, set[str]]
    right: dict[int, set[str]]


@dataclass(frozen=True)
class WindowDwellResult:
    segments: tuple[WindowDwellSegment, ...]
    total_seconds: Fraction
    longest_seconds: Fraction


def _absorb(side: dict[int, set[str]], entries: tuple[tuple[int, tuple[str, ...]], ...]) -> None:
    for receipt_id, pats in entries:
        side.setdefault(receipt_id, set()).update(pats)


def measure_window_dwell(spans: tuple[WindowQualifiedSpan, ...]) -> WindowDwellResult:
    ordered = sorted(spans, key=lambda span: (
        span.interval.start, not span.interval.left_closed, span.interval.end,
    ))
    segments: list[WindowDwellSegment] = []
    current: BoundaryInterval | None = None
    left: dict[int, set[str]] = {}
    right: dict[int, set[str]] = {}

    def save_positive() -> None:
        if current is not None and current.length:
            segments.append(WindowDwellSegment(current, dict(left), dict(right)))

    for span in ordered:
        if current is not None and _connected(current, span.interval):
            current = _union(current, span.interval)
        else:
            save_positive()
            current = span.interval
            left, right = {}, {}
        _absorb(left, span.left)
        _absorb(right, span.right)
    save_positive()
    lengths = [segment.interval.length for segment in segments]
    return WindowDwellResult(
        tuple(segments), sum(lengths, Fraction(0)), max(lengths, default=Fraction(0)),
    )
