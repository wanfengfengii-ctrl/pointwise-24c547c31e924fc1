"""Windowed assessment: declared-window claims, agreement checks and qualification.

A windowed item only speaks inside its declared closed window. Times inside the
window without a valid link or isolated point are explicitly missing; times
outside the window carry no declaration at all and never participate.
"""
from dataclasses import dataclass
from fractions import Fraction

from measure.intervals import BoundaryInterval, clip_interval, intersect_intervals
from measure.qualification import _Evidence, _read_evidence
from measure.provenance import EvidenceSource
from receive.envelope import decode_envelope
from receive.repository import ReceivedItem
from receive.source import validate_source


@dataclass(frozen=True)
class WindowClaim:
    receipt: ReceivedItem
    window: BoundaryInterval
    pieces: tuple[_Evidence, ...]
    evidence_source: EvidenceSource


def _as_evidence_source(receipt: ReceivedItem) -> EvidenceSource:
    return EvidenceSource(receipt.receipt_id, receipt.source)


def read_window_claim(receipt: ReceivedItem) -> WindowClaim:
    envelope = decode_envelope(receipt.payload)
    if validate_source(envelope) != receipt.source:
        raise ValueError('archived receipt identity does not match the item')
    window_value = envelope.body.get('window')
    if not isinstance(window_value, (list, tuple)) or len(window_value) != 2:
        raise ValueError('receipt is not a windowed issue')
    window = BoundaryInterval(Fraction(window_value[0]), Fraction(window_value[1]))
    pieces = tuple(_read_evidence(receipt))
    return WindowClaim(receipt, window, pieces, _as_evidence_source(receipt))


def _piece_at(claim: WindowClaim, time: Fraction) -> _Evidence | None:
    for piece in claim.pieces:
        support = piece.support
        if support.start <= time <= support.end and support.contains(time):
            return piece
    return None


def point_state(claim: WindowClaim, time: Fraction) -> str:
    """Return 'valid', 'missing' or 'silent' for an exact time."""
    if not claim.window.contains(time):
        return 'silent'
    return 'valid' if _piece_at(claim, time) is not None else 'missing'


def point_value(claim: WindowClaim, time: Fraction) -> Fraction:
    piece = _piece_at(claim, time)
    if piece is None:
        raise ValueError('missing declaration has no value')
    if not piece.support.length:
        return piece.first
    if time == piece.support.start:
        return piece.first
    if time == piece.support.end:
        return piece.last
    return piece.first + (piece.last - piece.first) * (
        time - piece.support.start
    ) / (piece.support.length)


def _cell_state(claim: WindowClaim, start: Fraction, end: Fraction) -> str:
    overlap = intersect_intervals(
        claim.window, BoundaryInterval(start, end, False, False),
    )
    if overlap is None:
        return 'silent'
    for piece in claim.pieces:
        cover = intersect_intervals(piece.support, overlap)
        if cover is not None and cover.length > 0:
            return 'valid'
    return 'missing'


def _piece_on_cell(claim: WindowClaim, start: Fraction, end: Fraction) -> _Evidence | None:
    overlap = intersect_intervals(
        claim.window, BoundaryInterval(start, end, False, False),
    )
    if overlap is None:
        return None
    for piece in claim.pieces:
        cover = intersect_intervals(piece.support, overlap)
        if cover is not None and cover.length > 0:
            return piece
    return None


def _cell_value(claim: WindowClaim, start: Fraction, end: Fraction) -> Fraction:
    piece = _piece_on_cell(claim, start, end)
    if piece is None:
        raise ValueError('missing declaration has no value')
    probe = (start + end) / 2
    if not piece.support.length:
        return piece.first
    return piece.first + (piece.last - piece.first) * (
        probe - piece.support.start
    ) / piece.support.length


def _conflict(detail: str) -> ValueError:
    return ValueError(f'window declarations conflict: {detail}')


def check_head_agreement(
    claims: tuple[WindowClaim, ...], assessment_window: BoundaryInterval,
) -> None:
    """Verify same-head claims agree on overlaps inside the assessment window.

    Missing-vs-value and value-vs-value mismatches are conflicts. Missing vs
    missing agrees, and a claim silent outside its window takes no part.
    Declarations outside the assessment window are not reviewed.
    """
    events = set()
    for claim in claims:
        declared = intersect_intervals(claim.window, assessment_window)
        if declared is not None:
            events.add(declared.start)
            events.add(declared.end)
    for claim in claims:
        if intersect_intervals(claim.window, assessment_window) is None:
            continue
        for piece in claim.pieces:
            clipped = intersect_intervals(piece.support, assessment_window)
            if clipped is not None:
                events.add(clipped.start)
                events.add(clipped.end)
    ordered = sorted(events)

    for time in ordered:
        speakers = [
            claim for claim in claims
            if claim.window.contains(time) and assessment_window.contains(time)
        ]
        states = {point_state(claim, time) for claim in speakers}
        if 'missing' in states and 'valid' in states:
            raise _conflict(f'missing and value at {time}')
        valued = [claim for claim in speakers if point_state(claim, time) == 'valid']
        if len({point_value(claim, time) for claim in valued}) > 1:
            raise _conflict(f'distinct temperatures at {time}')

    for start, end in zip(ordered, ordered[1:]):
        if start >= end:
            continue
        speakers = [
            claim for claim in claims
            if intersect_intervals(claim.window, BoundaryInterval(start, end, False, False))
            is not None
        ]
        states = {_cell_state(claim, start, end) for claim in speakers}
        if 'missing' in states and 'valid' in states:
            raise _conflict(f'missing and value inside ({start}, {end})')
        valued = [
            claim for claim in speakers
            if _cell_state(claim, start, end) == 'valid'
        ]
        if len({_cell_value(claim, start, end) for claim in valued}) > 1:
            raise _conflict(f'distinct temperatures inside ({start}, {end})')


def _hot_pieces(
    claim: WindowClaim, threshold: Fraction, window: BoundaryInterval,
) -> list[tuple[BoundaryInterval, _Evidence]]:
    result = []
    for piece in claim.pieces:
        hot = piece.hot_support(threshold)
        if hot is None:
            continue
        declared = intersect_intervals(hot, claim.window)
        if declared is None:
            continue
        clipped = clip_interval(declared, window)
        if clipped is not None:
            result.append((clipped, piece))
    return result


def _pats_on_interval(piece: _Evidence, used: BoundaryInterval) -> tuple[str, ...]:
    if used.length == 0:
        if used.start == piece.support.start:
            return (piece.first_pat,)
        if used.end == piece.support.end:
            return (piece.last_pat,)
    return (piece.first_pat, piece.last_pat)


@dataclass(frozen=True)
class WindowQualifiedSpan:
    interval: BoundaryInterval
    left: tuple[tuple[int, tuple[str, ...]], ...]
    right: tuple[tuple[int, tuple[str, ...]], ...]


def qualify_windows(
    left_claims: tuple[WindowClaim, ...],
    right_claims: tuple[WindowClaim, ...],
    window: BoundaryInterval,
    left_threshold: Fraction,
    right_threshold: Fraction,
) -> tuple[WindowQualifiedSpan, ...]:
    check_head_agreement(left_claims, window)
    check_head_agreement(right_claims, window)
    left_hot = []
    right_hot = []
    for claim in left_claims:
        left_hot.extend((interval, claim, piece)
                        for interval, piece in _hot_pieces(claim, left_threshold, window))
    for claim in right_claims:
        right_hot.extend((interval, claim, piece)
                         for interval, piece in _hot_pieces(claim, right_threshold, window))
    spans = []
    for left_interval, left_claim, left_piece in left_hot:
        for right_interval, right_claim, right_piece in right_hot:
            common = intersect_intervals(left_interval, right_interval)
            if common is None:
                continue
            left_entry = (left_claim.receipt.receipt_id, _pats_on_interval(left_piece, common))
            right_entry = (right_claim.receipt.receipt_id, _pats_on_interval(right_piece, common))
            spans.append(WindowQualifiedSpan(common, (left_entry,), (right_entry,)))
    return tuple(spans)
