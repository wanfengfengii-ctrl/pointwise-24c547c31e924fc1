from dataclasses import dataclass
from fractions import Fraction

from measure.intervals import (
    BoundaryInterval, clip_interval, intersect_intervals, linear_threshold_crossing,
)
from receive.envelope import decode_envelope
from receive.pairing import ReceiptPair
from receive.repository import ReceivedItem
from receive.source import validate_source
from record.observations import as_rational, require_label


@dataclass(frozen=True)
class QualifiedSpan:
    interval: BoundaryInterval
    left_pats: tuple[str, ...]
    right_pats: tuple[str, ...]


@dataclass(frozen=True)
class _Evidence:
    support: BoundaryInterval
    first: Fraction
    last: Fraction
    first_pat: str
    last_pat: str

    def hot_support(self, threshold: Fraction) -> BoundaryInterval | None:
        left_hot = self.first > threshold
        right_hot = self.last > threshold
        if left_hot and right_hot:
            return self.support
        if not left_hot and not right_hot:
            return None
        cut = linear_threshold_crossing(
            self.support.start, self.support.end, self.first, self.last, threshold,
        )
        if left_hot:
            return BoundaryInterval(self.support.start, cut, True, False)
        return BoundaryInterval(cut, self.support.end, False, True)

    def pats_for(self, used: BoundaryInterval) -> tuple[str, ...]:
        if used.length == 0:
            if used.start == self.support.start:
                return (self.first_pat,)
            if used.end == self.support.end:
                return (self.last_pat,)
        return (self.first_pat, self.last_pat)


def _wire_rational(value: object, field: str) -> Fraction:
    if not isinstance(value, str):
        raise ValueError(f'{field} must be a rational string in evidence')
    return as_rational(value, field)


def _read_link(row: object) -> _Evidence:
    if not isinstance(row, list) or len(row) != 6:
        raise ValueError('invalid affine link evidence')
    start = _wire_rational(row[0], 'link start')
    end = _wire_rational(row[1], 'link end')
    if start >= end:
        raise ValueError('an affine link must have positive length')
    first = _wire_rational(row[2], 'link temperature')
    slope = _wire_rational(row[3], 'link slope')
    return _Evidence(BoundaryInterval(start, end), first, first + slope * (end - start),
                     require_label(row[4], 'first pat'), require_label(row[5], 'last pat'))


def _read_point(row: object) -> _Evidence:
    if not isinstance(row, list) or len(row) != 3:
        raise ValueError('invalid isolated observation evidence')
    time = _wire_rational(row[0], 'point time')
    temp = _wire_rational(row[1], 'point temperature')
    pat = require_label(row[2], 'point pat')
    return _Evidence(BoundaryInterval(time, time), temp, temp, pat, pat)


def _read_evidence(item: ReceivedItem) -> list[_Evidence]:
    envelope = decode_envelope(item.payload)
    if validate_source(envelope) != item.source:
        raise ValueError('archived receipt identity does not match the item')
    links = envelope.body.get('links')
    points = envelope.body.get('points')
    if not isinstance(links, list) or not isinstance(points, list):
        raise ValueError('item lacks affine links or isolated observation evidence')
    pieces = [_read_link(row) for row in links] + [_read_point(row) for row in points]
    pieces.sort(key=lambda piece: piece.support.start)
    anchors: dict[str, tuple[Fraction, Fraction]] = {}
    previous = None
    for piece in pieces:
        if previous is not None:
            if previous.support.end > piece.support.start:
                raise ValueError('evidence intervals overlap in their interiors')
            if previous.support.end == piece.support.start and (
                not previous.support.length or not piece.support.length
                or previous.last != piece.first or previous.last_pat != piece.first_pat
            ):
                raise ValueError('inconsistent evidence at a shared time')
        for pat, time, temperature in (
            (piece.first_pat, piece.support.start, piece.first),
            (piece.last_pat, piece.support.end, piece.last),
        ):
            if anchors.setdefault(pat, (time, temperature)) != (time, temperature):
                raise ValueError(f'inconsistent evidence for pat {pat!r}')
        previous = piece
    return pieces


def _hot_pieces(
    item: ReceivedItem, threshold: Fraction, window: BoundaryInterval,
) -> list[tuple[BoundaryInterval, _Evidence]]:
    result = []
    for piece in _read_evidence(item):
        hot = piece.hot_support(threshold)
        clipped = clip_interval(hot, window) if hot is not None else None
        if clipped is not None:
            result.append((clipped, piece))
    return result


def qualify_common(
    pair: ReceiptPair, window: BoundaryInterval, left_threshold: Fraction, right_threshold: Fraction,
) -> tuple[QualifiedSpan, ...]:
    left = _hot_pieces(pair.left, left_threshold, window)
    right = _hot_pieces(pair.right, right_threshold, window)
    spans = []
    first_possible_right = 0
    for left_interval, left_piece in left:
        while (first_possible_right < len(right)
               and right[first_possible_right][0].end < left_interval.start):
            first_possible_right += 1
        index = first_possible_right
        while index < len(right) and right[index][0].start <= left_interval.end:
            right_interval, right_piece = right[index]
            common = intersect_intervals(left_interval, right_interval)
            if common is not None:
                spans.append(QualifiedSpan(common, left_piece.pats_for(common), right_piece.pats_for(common)))
            index += 1
    return tuple(spans)
